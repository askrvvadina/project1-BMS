"""Add the additive guest-loyalty QR claim table to a SQLite database.

Run with:
    python migrations/phase5_guest_loyalty_claims.py --database PATH

The migration creates a verified backup before changing the database and never
rebuilds or modifies existing tables.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"
TABLE_NAME = "guest_loyalty_claims"
INDEXES = {
    "uq_guest_loyalty_claims_sale": (("sale_id",), True),
    "uq_guest_loyalty_claims_token_hash": (("token_hash",), True),
    "ix_guest_loyalty_claims_branch_id": (("branch_id",), False),
    "ix_guest_loyalty_claims_expires_at": (("expires_at",), False),
}
TRIGGERS = {
    "guest_loyalty_claims_validate_insert": """CREATE TRIGGER guest_loyalty_claims_validate_insert
        BEFORE INSERT ON guest_loyalty_claims BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM sales WHERE id = NEW.sale_id
            AND customer_id IS NULL AND branch_id = NEW.branch_id)
            THEN RAISE(ABORT, 'guest loyalty claim sale mismatch') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM loyalty_transactions WHERE sale_id = NEW.sale_id)
            THEN RAISE(ABORT, 'sale already has loyalty points') END;
        END""",
    "guest_loyalty_claims_guard_update": """CREATE TRIGGER guest_loyalty_claims_guard_update
        BEFORE UPDATE ON guest_loyalty_claims
        WHEN OLD.sale_id != NEW.sale_id
        OR OLD.branch_id != NEW.branch_id
        OR OLD.token_hash != NEW.token_hash
        OR OLD.points != NEW.points
        OR OLD.expires_at != NEW.expires_at
        OR OLD.created_at != NEW.created_at
        OR (OLD.claimed_at IS NOT NULL AND NEW.claimed_at IS NOT OLD.claimed_at)
        BEGIN SELECT RAISE(ABORT, 'guest loyalty claim is immutable'); END""",
    "loyalty_transactions_require_claim_consumed": """CREATE TRIGGER loyalty_transactions_require_claim_consumed
        BEFORE INSERT ON loyalty_transactions WHEN EXISTS (
            SELECT 1 FROM guest_loyalty_claims
            WHERE sale_id = NEW.sale_id AND claimed_at IS NULL)
        BEGIN SELECT RAISE(ABORT, 'guest loyalty claim is not consumed'); END""",
}


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
    for table in ("sales", "branches", "loyalty_transactions"):
        columns = {
            row["name"]: row
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if not columns:
            raise MigrationError(f"Required table is missing: {table}.")
        if "id" not in columns or columns["id"]["pk"] != 1:
            raise MigrationError(f"Required primary key is incompatible: {table}.id")
    sale_columns = {
        row["name"] for row in connection.execute('PRAGMA table_info("sales")')
    }
    if not {"customer_id", "branch_id"}.issubset(sale_columns):
        raise MigrationError("sales must contain customer_id and branch_id.")
    ledger_columns = {
        row["name"]
        for row in connection.execute(
            'PRAGMA table_info("loyalty_transactions")'
        )
    }
    if "sale_id" not in ledger_columns:
        raise MigrationError("loyalty_transactions must contain sale_id.")


def _validate_claim_schema(connection: sqlite3.Connection) -> None:
    table_sql = _schema(connection, TABLE_NAME)
    if table_sql is None:
        raise MigrationError("Guest loyalty claim table is missing.")
    expected_columns = {
        "id": ("INTEGER", 1, 1),
        "sale_id": ("INTEGER", 1, 0),
        "branch_id": ("INTEGER", 1, 0),
        "token_hash": ("VARCHAR(64)", 1, 0),
        "points": ("INTEGER", 1, 0),
        "expires_at": ("DATETIME", 1, 0),
        "claimed_at": ("DATETIME", 0, 0),
        "created_at": ("DATETIME", 1, 0),
    }
    actual_columns = {
        row["name"]: (row["type"].upper(), row["notnull"], row["pk"])
        for row in connection.execute(f'PRAGMA table_info("{TABLE_NAME}")')
    }
    if actual_columns != expected_columns:
        raise MigrationError("guest_loyalty_claims has incompatible columns.")

    foreign_keys = {
        row["from"]: (row["table"], row["to"], row["on_delete"].upper())
        for row in connection.execute(f'PRAGMA foreign_key_list("{TABLE_NAME}")')
    }
    if foreign_keys != {
        "sale_id": ("sales", "id", "RESTRICT"),
        "branch_id": ("branches", "id", "RESTRICT"),
    }:
        raise MigrationError("guest_loyalty_claims has incompatible foreign keys.")

    normalized_table = _normalize_sql(table_sql)
    required_checks = (
        "length(token_hash)=64",
        "typeof(points)='integer'andpoints>=0",
    )
    if any(check not in normalized_table for check in required_checks):
        raise MigrationError("guest_loyalty_claims is missing required checks.")

    index_rows = {
        row["name"]: row
        for row in connection.execute(f'PRAGMA index_list("{TABLE_NAME}")')
    }
    for name, (expected_columns, unique) in INDEXES.items():
        index = index_rows.get(name)
        actual_columns = tuple(
            row["name"]
            for row in connection.execute(f'PRAGMA index_info("{name}")')
        )
        if (
            index is None
            or bool(index["unique"]) != unique
            or actual_columns != expected_columns
        ):
            raise MigrationError(f"Missing or incompatible index: {name}")

    for name, expected_sql in TRIGGERS.items():
        actual_sql = _schema(connection, name)
        if (
            actual_sql is None
            or _normalize_sql(actual_sql) != _normalize_sql(expected_sql)
        ):
            raise MigrationError(f"Missing or incompatible trigger: {name}")


def _create_claim_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE guest_loyalty_claims (
            id INTEGER NOT NULL,
            sale_id INTEGER NOT NULL REFERENCES sales (id) ON DELETE RESTRICT,
            branch_id INTEGER NOT NULL REFERENCES branches (id) ON DELETE RESTRICT,
            token_hash VARCHAR(64) NOT NULL,
            points INTEGER NOT NULL,
            expires_at DATETIME NOT NULL,
            claimed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_guest_loyalty_claims_token_hash_length
                CHECK (length(token_hash) = 64),
            CONSTRAINT ck_guest_loyalty_claims_points
                CHECK (typeof(points) = 'integer' AND points >= 0)
        )"""
    )
    for name, (columns, unique) in INDEXES.items():
        unique_sql = "UNIQUE " if unique else ""
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f'CREATE {unique_sql}INDEX "{name}" '
            f'ON "{TABLE_NAME}" ({quoted_columns})'
        )
    for trigger_sql in TRIGGERS.values():
        connection.execute(trigger_sql)


def _make_backup(
    database_path: Path,
    tables: list[str],
    schemas: dict[str, str],
    rows: dict[str, list[tuple]],
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = database_path.with_name(
        f"{database_path.stem}-pre-guest-loyalty-claims-{timestamp}"
        f"{database_path.suffix}"
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


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int | str]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    source = _connect(database_path, read_only=True)
    try:
        tables = _table_names(source)
        _validate_parent_tables(source)
        if TABLE_NAME in tables:
            _validate_claim_schema(source)
            return {
                "database": str(database_path),
                "created_table": 0,
                "backup": "not created; compatible guest claim schema already exists",
            }
        reserved_names = set(INDEXES) | set(TRIGGERS)
        collisions = [name for name in reserved_names if _schema(source, name)]
        if collisions:
            raise MigrationError(
                "Reserved guest claim schema object(s) already exist: "
                + ", ".join(sorted(collisions))
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
        if _table_names(connection) != tables:
            raise MigrationError("Existing table list changed during migration.")
        _create_claim_schema(connection)
        _validate_claim_schema(connection)
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
