"""Add a nullable password hash to existing customer identities.

Run with:
    python migrations/phase6_customer_portal.py --database PATH

This migration is additive, creates a verified SQLite backup before changes,
and preserves every existing customer row.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"


class MigrationError(RuntimeError):
    """Raised when the existing database is not safe to migrate."""


def _connect(path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _backup(
    database_path: Path,
    tables: list[str],
    schemas: dict[str, str],
    snapshots: dict[str, list[tuple]],
):
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-customer-portal"
    )
    if backup_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = database_path.with_name(
            f"{database_path.name}.pre-customer-portal.{stamp}"
        )
    source = _connect(database_path, read_only=True)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()

    backup = _connect(backup_path, read_only=True)
    try:
        backup_tables = [
            row["name"]
            for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if backup_tables != tables:
            raise MigrationError("The pre-migration backup has a different table list.")
        for table in tables:
            row = backup.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row is None or row["sql"] != schemas[table]:
                raise MigrationError(
                    f"The pre-migration backup did not preserve {table}."
                )
            backup_rows = [
                tuple(row) for row in backup.execute(f'SELECT * FROM "{table}"')
            ]
            if sorted(backup_rows, key=repr) != sorted(snapshots[table], key=repr):
                raise MigrationError(
                    f"The pre-migration backup did not preserve data in {table}."
                )
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("The pre-migration backup failed integrity_check.")
    finally:
        backup.close()
    return backup_path


def migrate(database: Path = DEFAULT_DATABASE) -> dict[str, str | int]:
    database_path = Path(database).expanduser().resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database does not exist: {database_path}")

    connection = _connect(database_path, read_only=True)
    try:
        customer_table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='customers'"
        ).fetchone()
        if customer_table is None:
            raise MigrationError("Required table is missing: customers.")
        columns = {
            row["name"]: row
            for row in connection.execute('PRAGMA table_info("customers")')
        }
        if (
            "id" not in columns
            or columns["id"]["pk"] != 1
            or "name" not in columns
            or "phone" not in columns
        ):
            raise MigrationError("The customers table is not phase-3 compatible.")
        password_column = columns.get("password_hash")
        if password_column is not None:
            column_type = (password_column["type"] or "").upper()
            if column_type not in {"VARCHAR(255)", "TEXT"} or password_column["notnull"]:
                raise MigrationError(
                    "Existing customers.password_hash has an incompatible definition."
                )
            return {
                "database": str(database_path),
                "added_column": 0,
                "backup": "",
            }
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schemas = {}
        snapshots = {}
        for table in tables:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            schemas[table] = row["sql"]
            snapshots[table] = [
                tuple(item)
                for item in connection.execute(f'SELECT * FROM "{table}"')
            ]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or violations:
            raise MigrationError("The source database failed SQLite integrity checks.")
    finally:
        connection.close()

    backup_path = _backup(database_path, tables, schemas, snapshots)
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current_customer_columns = {
            row["name"]: row
            for row in connection.execute('PRAGMA table_info("customers")')
        }
        if "password_hash" not in current_customer_columns:
            connection.execute(
                "ALTER TABLE customers ADD COLUMN password_hash VARCHAR(255)"
            )
        elif (
            (current_customer_columns["password_hash"]["type"] or "").upper()
            not in {"VARCHAR(255)", "TEXT"}
            or current_customer_columns["password_hash"]["notnull"]
        ):
            raise MigrationError(
                "Existing customers.password_hash has an incompatible definition."
            )
        for table in tables:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if schema is None or schema["sql"] != schemas[table]:
                if table != "customers":
                    raise MigrationError(f"Migration unexpectedly changed {table}.")
            if table != "customers":
                current_rows = [
                    tuple(row)
                    for row in connection.execute(f'SELECT * FROM "{table}"')
                ]
                if current_rows != snapshots[table]:
                    raise MigrationError(f"Migration unexpectedly changed data in {table}.")
        customer_rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT id, name, phone FROM customers ORDER BY id"
            )
        ]
        if customer_rows != sorted(
            (row[0], row[1], row[2]) for row in snapshots["customers"]
        ):
            raise MigrationError("Migration unexpectedly changed customer identities.")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Post-migration integrity_check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("Post-migration foreign_key_check failed.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "database": str(database_path),
        "added_column": 1,
        "backup": str(backup_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    print(migrate(parser.parse_args().database))


if __name__ == "__main__":
    main()
