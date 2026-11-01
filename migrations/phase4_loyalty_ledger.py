"""Add the append-only loyalty transaction ledger to an existing SQLite DB.

Run with:
    python migrations/phase4_loyalty_ledger.py

An alternate database can be supplied with --database. The migration makes a
verified backup, changes no existing tables, and refuses incompatible schemas.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"
TABLE_NAME = "loyalty_transactions"
INDEXES = {
    "uq_loyalty_transactions_sale_operation": (
        ("customer_id", "sale_id", "transaction_type"),
        True,
        "sale_id IS NOT NULL AND transaction_type IN ('earn', 'redeem')",
    ),
    "ix_loyalty_transactions_customer_created_at": (
        ("customer_id", "created_at"),
        False,
        None,
    ),
    "ix_loyalty_transactions_sale_id": (("sale_id",), False, None),
    "ix_loyalty_transactions_branch_id": (("branch_id",), False, None),
}
TRIGGERS = {
    "loyalty_transactions_validate_insert": """CREATE TRIGGER loyalty_transactions_validate_insert
        BEFORE INSERT ON loyalty_transactions BEGIN
        SELECT CASE WHEN NEW.sale_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM sales WHERE id = NEW.sale_id
            AND customer_id = NEW.customer_id
            AND (NEW.branch_id IS NULL OR branch_id = NEW.branch_id))
            THEN RAISE(ABORT, 'loyalty sale reference mismatch') END;
        SELECT CASE WHEN NEW.sale_id IS NOT NULL
            AND NEW.transaction_type IN ('earn', 'redeem')
            AND EXISTS (SELECT 1 FROM loyalty_transactions
                WHERE customer_id = NEW.customer_id AND sale_id = NEW.sale_id
                AND transaction_type IN ('earn', 'redeem')
                AND transaction_type != NEW.transaction_type)
            THEN RAISE(ABORT, 'earn and redeem cannot share a sale') END;
        SELECT CASE WHEN (SELECT COALESCE(SUM(points), 0)
            FROM loyalty_transactions WHERE customer_id = NEW.customer_id)
            + NEW.points < 0
            THEN RAISE(ABORT, 'loyalty balance cannot be negative') END;
        END""",
    "loyalty_transactions_prevent_update": """CREATE TRIGGER loyalty_transactions_prevent_update
        BEFORE UPDATE ON loyalty_transactions BEGIN
        SELECT RAISE(ABORT, 'loyalty transactions are immutable'); END""",
    "loyalty_transactions_prevent_delete": """CREATE TRIGGER loyalty_transactions_prevent_delete
        BEFORE DELETE ON loyalty_transactions BEGIN
        SELECT RAISE(ABORT, 'loyalty transactions are immutable'); END""",
}
REDEMPTION_COMPATIBLE_VALIDATE_TRIGGER = """CREATE TRIGGER loyalty_transactions_validate_insert
    BEFORE INSERT ON loyalty_transactions BEGIN
    SELECT CASE WHEN NEW.sale_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sales WHERE id = NEW.sale_id
        AND customer_id = NEW.customer_id
        AND (NEW.branch_id IS NULL OR branch_id = NEW.branch_id))
        THEN RAISE(ABORT, 'loyalty sale reference mismatch') END;
    SELECT CASE WHEN (SELECT COALESCE(SUM(points), 0)
        FROM loyalty_transactions WHERE customer_id = NEW.customer_id)
        + NEW.points < 0
        THEN RAISE(ABORT, 'loyalty balance cannot be negative') END;
    END"""


class MigrationError(RuntimeError):
    """Raised when the existing database is not safe to migrate."""


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
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _schema(connection: sqlite3.Connection, name: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = ? "
        "AND type IN ('table', 'index', 'trigger')",
        (name,),
    ).fetchone()
    return row["sql"] if row else None


def _snapshot(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return sorted(
        (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')),
        key=repr,
    )


def _normalize_sql(value: str | None) -> str:
    return "".join((value or "").lower().replace('"', "").split())


def _validate_parent_tables(connection: sqlite3.Connection) -> None:
    for table in ("customers", "sales", "branches"):
        columns = {
            row["name"]: row
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if "id" not in columns:
            raise MigrationError(f"Required column is missing: {table}.id")
        if columns["id"]["type"].upper() != "INTEGER" or not columns["id"]["pk"]:
            raise MigrationError(f"Required primary key is incompatible: {table}.id")
    sale_columns = {
        row["name"]
        for row in connection.execute('PRAGMA table_info("sales")')
    }
    if not {"customer_id", "branch_id"}.issubset(sale_columns):
        raise MigrationError("sales must contain customer_id and branch_id.")


def _validate_loyalty_schema(connection: sqlite3.Connection) -> None:
    table_sql = _schema(connection, TABLE_NAME)
    if table_sql is None:
        raise MigrationError("Loyalty table is missing.")

    expected_columns = {
        "id": ("INTEGER", 1, 1),
        "customer_id": ("INTEGER", 1, 0),
        "sale_id": ("INTEGER", 0, 0),
        "branch_id": ("INTEGER", 0, 0),
        "transaction_type": ("VARCHAR(20)", 1, 0),
        "points": ("INTEGER", 1, 0),
        "operation_id": ("VARCHAR(100)", 1, 0),
        "created_at": ("DATETIME", 1, 0),
    }
    actual_columns = {
        row["name"]: (row["type"].upper(), row["notnull"], row["pk"])
        for row in connection.execute(f'PRAGMA table_info("{TABLE_NAME}")')
    }
    if actual_columns != expected_columns:
        raise MigrationError("loyalty_transactions has incompatible columns.")

    foreign_keys = {
        row["from"]: (row["table"], row["to"], row["on_delete"].upper())
        for row in connection.execute(
            f'PRAGMA foreign_key_list("{TABLE_NAME}")'
        )
    }
    expected_foreign_keys = {
        "customer_id": ("customers", "id", "RESTRICT"),
        "sale_id": ("sales", "id", "RESTRICT"),
        "branch_id": ("branches", "id", "RESTRICT"),
    }
    if foreign_keys != expected_foreign_keys:
        raise MigrationError("loyalty_transactions has incompatible foreign keys.")

    normalized_table = _normalize_sql(table_sql)
    required_checks = (
        "transaction_typein('earn','redeem','reversal')",
        "typeof(points)='integer'andpoints!=0",
        "(transaction_type='earn'andpoints>0)or"
        "(transaction_type='redeem'andpoints<0)ortransaction_type='reversal'",
        "length(trim(operation_id))>0",
    )
    if any(check not in normalized_table for check in required_checks):
        raise MigrationError("loyalty_transactions is missing required checks.")

    index_rows = {
        row["name"]: row
        for row in connection.execute(f'PRAGMA index_list("{TABLE_NAME}")')
    }
    for name, (expected_columns, unique, where) in INDEXES.items():
        index = index_rows.get(name)
        if index is None or bool(index["unique"]) != unique:
            raise MigrationError(f"Missing or incompatible index: {name}")
        actual_index_columns = tuple(
            row["name"]
            for row in connection.execute(f'PRAGMA index_info("{name}")')
        )
        if actual_index_columns != expected_columns:
            raise MigrationError(f"Index has incompatible columns: {name}")
        index_sql = _schema(connection, name)
        if where and _normalize_sql(where) not in _normalize_sql(index_sql):
            raise MigrationError(f"Index has incompatible predicate: {name}")

    operation_id_unique = any(
        row["unique"]
        and tuple(
            column["name"]
            for column in connection.execute(
                f'PRAGMA index_info("{row["name"]}")'
            )
        ) == ("operation_id",)
        for row in index_rows.values()
    )
    if not operation_id_unique:
        raise MigrationError("operation_id must have a unique constraint.")

    for name, expected_sql in TRIGGERS.items():
        actual_sql = _schema(connection, name)
        compatible_sql = (
            REDEMPTION_COMPATIBLE_VALIDATE_TRIGGER
            if name == "loyalty_transactions_validate_insert"
            else expected_sql
        )
        if (
            actual_sql is None
            or _normalize_sql(actual_sql)
            not in {
                _normalize_sql(expected_sql),
                _normalize_sql(compatible_sql),
            }
        ):
            raise MigrationError(f"Missing or incompatible trigger: {name}")


def _make_backup(
    database_path: Path,
    tables: list[str],
    schemas: dict[str, str],
    rows: dict[str, list[tuple]],
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = database_path.with_name(
        f"{database_path.stem}-pre-loyalty-{timestamp}{database_path.suffix}"
    )
    source = _connect(database_path, read_only=True)
    destination = sqlite3.connect(backup_path, timeout=30)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        source.close()
        destination.close()

    backup = _connect(backup_path, read_only=True)
    try:
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Backup integrity_check failed.")
        if _table_names(backup) != tables:
            raise MigrationError("Backup table list differs from source.")
        for table in tables:
            if _schema(backup, table) != schemas[table]:
                raise MigrationError(f"Backup schema differs for {table}.")
            if _snapshot(backup, table) != rows[table]:
                raise MigrationError(f"Backup data differs for {table}.")
    finally:
        backup.close()
    return backup_path


def _create_loyalty_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE loyalty_transactions (
            id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
            sale_id INTEGER REFERENCES sales (id) ON DELETE RESTRICT,
            branch_id INTEGER REFERENCES branches (id) ON DELETE RESTRICT,
            transaction_type VARCHAR(20) NOT NULL,
            points INTEGER NOT NULL,
            operation_id VARCHAR(100) NOT NULL UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_loyalty_transactions_type CHECK
                (transaction_type IN ('earn', 'redeem', 'reversal')),
            CONSTRAINT ck_loyalty_transactions_nonzero CHECK
                (typeof(points) = 'integer' AND points != 0),
            CONSTRAINT ck_loyalty_transactions_sign CHECK
                ((transaction_type = 'earn' AND points > 0) OR
                 (transaction_type = 'redeem' AND points < 0) OR
                 transaction_type = 'reversal'),
            CONSTRAINT ck_loyalty_transactions_operation_id CHECK
                (length(trim(operation_id)) > 0)
        )"""
    )
    for name, (columns, unique, where) in INDEXES.items():
        unique_sql = "UNIQUE " if unique else ""
        where_sql = f" WHERE {where}" if where else ""
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f'CREATE {unique_sql}INDEX "{name}" '
            f'ON "{TABLE_NAME}" ({quoted_columns}){where_sql}'
        )
    for trigger_sql in TRIGGERS.values():
        connection.execute(trigger_sql)


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int | str]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    source = _connect(database_path, read_only=True)
    try:
        tables = _table_names(source)
        _validate_parent_tables(source)
        if TABLE_NAME in tables:
            _validate_loyalty_schema(source)
            return {
                "database": str(database_path),
                "created_table": 0,
                "backup": "not created; compatible loyalty schema already exists",
            }
        reserved_names = set(INDEXES) | set(TRIGGERS)
        collisions = [name for name in reserved_names if _schema(source, name)]
        if collisions:
            raise MigrationError(
                f"Reserved loyalty schema object(s) already exist: {', '.join(sorted(collisions))}"
            )
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        violations = source.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise MigrationError(f"Source integrity_check failed: {integrity}")
        if violations:
            raise MigrationError(
                f"Source foreign_key_check found violations: "
                f"{[tuple(row) for row in violations]}"
            )
        schemas = {table: _schema(source, table) for table in tables}
        rows = {table: _snapshot(source, table) for table in tables}
    finally:
        source.close()

    backup_path = _make_backup(database_path, tables, schemas, rows)
    connection = _connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise MigrationError("Could not enable SQLite foreign-key checks.")
        connection.execute("BEGIN IMMEDIATE")
        current_tables = _table_names(connection)
        if TABLE_NAME in current_tables:
            _validate_loyalty_schema(connection)
            connection.commit()
            return {
                "database": str(database_path),
                "created_table": 0,
                "backup": str(backup_path),
            }
        if current_tables != tables:
            raise MigrationError("Existing table list changed during migration.")
        _create_loyalty_schema(connection)
        _validate_loyalty_schema(connection)
        if _table_names(connection) != sorted([*tables, TABLE_NAME]):
            raise MigrationError("Migration unexpectedly changed the table list.")
        for table in tables:
            if _schema(connection, table) != schemas[table]:
                raise MigrationError(f"Existing schema changed in {table}.")
            if _snapshot(connection, table) != rows[table]:
                raise MigrationError(f"Existing data changed in {table}.")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"Post-migration foreign_key_check found violations: "
                f"{[tuple(row) for row in violations]}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "database": str(database_path),
        "created_table": 1,
        "backup": str(backup_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    arguments = parser.parse_args()
    print(migrate(arguments.database))


if __name__ == "__main__":
    main()