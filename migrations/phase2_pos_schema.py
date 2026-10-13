"""Add nullable POS attribution fields to the existing SQLite database.

The migration first creates and verifies a complete SQLite backup, then
applies additive column/index changes in one transaction.

Run with:
    python migrations/phase2_pos_schema.py

An alternate database can be supplied with --database.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"


class MigrationError(RuntimeError):
    """Raised when the database or backup is not safe to migrate."""


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
        )
    else:
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def _table_schema(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        raise MigrationError(f"Required table is missing: {table}")
    return row["sql"]


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {
        row["name"]: row
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _row_snapshot(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str] | None = None,
) -> list[tuple]:
    selected = "*" if columns is None else ", ".join(
        f'"{column}"' for column in columns
    )
    rows = connection.execute(
        f'SELECT {selected} FROM "{table}"'
    ).fetchall()
    return sorted((tuple(row) for row in rows), key=repr)


def _verify_backup(
    source_path: Path,
    backup_path: Path,
    original_tables: list[str],
    original_schemas: dict[str, str],
    original_rows: dict[str, list[tuple]],
) -> None:
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise MigrationError(f"Backup file is missing or empty: {backup_path}")

    backup = _connect(backup_path, read_only=True)
    try:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError(f"Backup integrity_check failed: {integrity}")
        violations = backup.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"Backup foreign_key_check found violations: "
                f"{[tuple(row) for row in violations]}"
            )
        if _table_names(backup) != original_tables:
            raise MigrationError("Backup table list differs from source.")
        for table in original_tables:
            if _table_schema(backup, table) != original_schemas[table]:
                raise MigrationError(f"Backup schema differs for {table}.")
            if _row_snapshot(backup, table) != original_rows[table]:
                raise MigrationError(f"Backup data differs for {table}.")
    finally:
        backup.close()

    if not source_path.is_file():
        raise MigrationError("Source database disappeared after backup.")


def _create_verified_backup(
    database_path: Path,
    tables: list[str],
    schemas: dict[str, str],
    rows: dict[str, list[tuple]],
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = database_path.with_name(
        f"{database_path.stem}-pre-pos-schema-{timestamp}{database_path.suffix}"
    )
    if backup_path.exists():
        raise MigrationError(f"Refusing to overwrite existing backup: {backup_path}")

    source = _connect(database_path, read_only=True)
    destination = sqlite3.connect(backup_path, timeout=30)
    try:
        source.backup(destination)
        destination.commit()
    except Exception:
        destination.close()
        if backup_path.exists():
            backup_path.unlink()
        raise
    finally:
        source.close()
        try:
            destination.close()
        except sqlite3.Error:
            pass

    _verify_backup(database_path, backup_path, tables, schemas, rows)
    return backup_path


def _ensure_nullable_fk(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    target_table: str,
) -> bool:
    current = _columns(connection, table).get(column)
    if current is None:
        connection.execute(
            f'ALTER TABLE "{table}" ADD COLUMN "{column}" INTEGER '
            f'REFERENCES "{target_table}" (id)'
        )
        return True

    if current["type"].upper() != "INTEGER" or current["notnull"]:
        raise MigrationError(
            f"{table}.{column} exists but is not a nullable INTEGER."
        )
    foreign_keys = {
        row["from"]: row["table"]
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
    }
    if foreign_keys.get(column) != target_table:
        raise MigrationError(
            f"{table}.{column} exists without the expected FK to {target_table}."
        )
    return False


def _ensure_employee_unique_index(connection: sqlite3.Connection) -> bool:
    index_name = "uq_users_employee_id"
    index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    ).fetchone()
    if index is not None:
        columns = tuple(
            row["name"]
            for row in connection.execute(
                f'PRAGMA index_info("{index_name}")'
            )
        )
        unique = next(
            row["unique"]
            for row in connection.execute('PRAGMA index_list("users")')
            if row["name"] == index_name
        )
        if columns != ("employee_id",) or not unique:
            raise MigrationError(
                "uq_users_employee_id exists with an unexpected definition."
            )
        return False

    existing_duplicates = connection.execute(
        """
        SELECT employee_id
        FROM users
        WHERE employee_id IS NOT NULL
        GROUP BY employee_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if existing_duplicates:
        raise MigrationError(
            "Cannot enforce employee_id uniqueness: duplicate mappings exist."
        )
    connection.execute(
        "CREATE UNIQUE INDEX uq_users_employee_id ON users (employee_id)"
    )
    return True


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int | str]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    source = _connect(database_path, read_only=True)
    try:
        tables = _table_names(source)
        required = {
            "users",
            "employees",
            "branches",
            "branch_inventory",
            "inventory_transactions",
            "sales",
        }
        missing = required.difference(tables)
        if missing:
            raise MigrationError(
                f"Required table(s) are missing: {', '.join(sorted(missing))}"
            )
        schemas = {table: _table_schema(source, table) for table in tables}
        original_columns = {
            table: tuple(_columns(source, table))
            for table in tables
        }
        rows = {table: _row_snapshot(source, table) for table in tables}
        counts_before = {
            table: len(rows[table])
            for table in (
                "users",
                "employees",
                "branches",
                "branch_inventory",
                "inventory_transactions",
                "sales",
                "sale_items",
            )
            if table in tables
        }
        source_integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        source_fk_violations = source.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if source_integrity != "ok":
            raise MigrationError(
                f"Source integrity_check failed: {source_integrity}"
            )
        if source_fk_violations:
            raise MigrationError(
                "Source foreign_key_check found violations: "
                f"{[tuple(row) for row in source_fk_violations]}"
            )
    finally:
        source.close()

    backup_path = _create_verified_backup(
        database_path,
        tables,
        schemas,
        rows,
    )

    connection = _connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise MigrationError("Could not enable SQLite foreign-key checks.")
        connection.execute("BEGIN IMMEDIATE")

        added_employee_id = _ensure_nullable_fk(
            connection,
            "users",
            "employee_id",
            "employees",
        )
        created_employee_index = _ensure_employee_unique_index(connection)
        added_created_by_user_id = _ensure_nullable_fk(
            connection,
            "sales",
            "created_by_user_id",
            "users",
        )
        added_sale_id = _ensure_nullable_fk(
            connection,
            "inventory_transactions",
            "sale_id",
            "sales",
        )

        post_tables = _table_names(connection)
        if post_tables != tables:
            raise MigrationError("Migration unexpectedly changed the table list.")

        for table in tables:
            if _row_snapshot(
                connection,
                table,
                list(original_columns[table]),
            ) != rows[table]:
                raise MigrationError(f"Existing data changed in {table}.")

        for was_added, table, column in (
            (added_employee_id, "users", "employee_id"),
            (added_created_by_user_id, "sales", "created_by_user_id"),
            (added_sale_id, "inventory_transactions", "sale_id"),
        ):
            if was_added:
                null_count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NULL'
                ).fetchone()[0]
                if null_count != counts_before[table]:
                    raise MigrationError(
                        f"Existing {table} rows were unexpectedly attributed."
                    )

        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise MigrationError(
                "Post-migration foreign_key_check found violations: "
                f"{[tuple(row) for row in foreign_key_violations]}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    verified = _connect(database_path, read_only=True)
    try:
        integrity = verified.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = verified.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise MigrationError(f"Post-migration integrity_check failed: {integrity}")
        if fk_violations:
            raise MigrationError(
                f"Post-migration foreign_key_check found violations: "
                f"{[tuple(row) for row in fk_violations]}"
            )
        post_counts = {
            table: verified.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in counts_before
        }
        if post_counts != counts_before:
            raise MigrationError(
                f"Table row counts changed: before={counts_before}, after={post_counts}"
            )
        user_employee = _columns(verified, "users")["employee_id"]
        sale_actor = _columns(verified, "sales")["created_by_user_id"]
        transaction_sale = _columns(
            verified,
            "inventory_transactions",
        )["sale_id"]
        if user_employee["notnull"] or sale_actor["notnull"] or transaction_sale["notnull"]:
            raise MigrationError("One or more new foreign-key fields are not nullable.")
        employee_index_columns = {
            tuple(
                row["name"]
                for row in verified.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                )
            )
            for index in verified.execute('PRAGMA index_list("users")')
            if index["unique"]
        }
        if ("employee_id",) not in employee_index_columns:
            raise MigrationError("employee_id uniqueness is not enforced.")
        null_counts = {
            "users.employee_id": verified.execute(
                "SELECT COUNT(*) FROM users WHERE employee_id IS NULL"
            ).fetchone()[0],
            "sales.created_by_user_id": verified.execute(
                "SELECT COUNT(*) FROM sales WHERE created_by_user_id IS NULL"
            ).fetchone()[0],
            "inventory_transactions.sale_id": verified.execute(
                "SELECT COUNT(*) FROM inventory_transactions WHERE sale_id IS NULL"
            ).fetchone()[0],
        }
    finally:
        verified.close()

    return {
        "database": str(database_path),
        "backup": str(backup_path),
        "users_before_after": counts_before["users"],
        "employees_before_after": counts_before["employees"],
        "branches_before_after": counts_before["branches"],
        "branch_inventory_before_after": counts_before["branch_inventory"],
        "inventory_transactions_before_after": counts_before[
            "inventory_transactions"
        ],
        "sales_before_after": counts_before["sales"],
        "sale_items_before_after": counts_before.get("sale_items", 0),
        "employee_id_null_existing_users": null_counts["users.employee_id"],
        "created_by_user_id_null_existing_sales": null_counts[
            "sales.created_by_user_id"
        ],
        "sale_id_null_existing_transactions": null_counts[
            "inventory_transactions.sale_id"
        ],
        "added_employee_id": int(added_employee_id),
        "created_employee_unique_index": int(created_employee_index),
        "added_created_by_user_id": int(added_created_by_user_id),
        "added_sale_id": int(added_sale_id),
        "integrity_check": integrity,
        "foreign_key_violations": len(fk_violations),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database to migrate (default: {DEFAULT_DATABASE})",
    )
    args = parser.parse_args()
    for key, value in migrate(args.database).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
