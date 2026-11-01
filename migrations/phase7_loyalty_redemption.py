"""Allow one redemption and one earning to share a sale in the loyalty ledger."""

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from migrations.phase4_loyalty_ledger import (
    DEFAULT_DATABASE,
    INDEXES,
    REDEMPTION_COMPATIBLE_VALIDATE_TRIGGER,
    TRIGGERS,
    MigrationError,
    _connect,
    _normalize_sql,
    _schema,
    _snapshot,
    _table_names,
    _validate_loyalty_schema,
)


TRIGGER_NAME = "loyalty_transactions_validate_insert"
EXPECTED_TRIGGER = REDEMPTION_COMPATIBLE_VALIDATE_TRIGGER
LEGACY_TRIGGER = TRIGGERS[TRIGGER_NAME]


def _make_backup(
    database_path: Path,
    tables: list[str],
    schemas: dict[str, str | None],
    rows: dict[str, list[tuple]],
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = database_path.with_name(
        f"{database_path.stem}-pre-loyalty-redemption-{timestamp}{database_path.suffix}"
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
        _validate_loyalty_schema(source)
        trigger_sql = _schema(source, TRIGGER_NAME)
        normalized_trigger = _normalize_sql(trigger_sql)
        if normalized_trigger == _normalize_sql(EXPECTED_TRIGGER):
            return {
                "database": str(database_path),
                "updated_trigger": 0,
                "backup": "not created; redemption-compatible trigger already exists",
            }
        if normalized_trigger != _normalize_sql(LEGACY_TRIGGER):
            raise MigrationError(
                "The loyalty insert trigger is not a supported phase 4 schema."
            )
        tables = _table_names(source)
        schemas = {table: _schema(source, table) for table in tables}
        rows = {table: _snapshot(source, table) for table in tables}
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        violations = source.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise MigrationError(f"Source integrity_check failed: {integrity}")
        if violations:
            raise MigrationError(
                f"Source foreign_key_check found violations: "
                f"{[tuple(row) for row in violations]}"
            )
    finally:
        source.close()

    backup_path = _make_backup(database_path, tables, schemas, rows)
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if _table_names(connection) != tables:
            raise MigrationError("Existing table list changed during migration.")
        for table in tables:
            if _schema(connection, table) != schemas[table]:
                raise MigrationError(f"Existing schema changed in {table}.")
            if _snapshot(connection, table) != rows[table]:
                raise MigrationError(f"Existing data changed in {table}.")
        if _normalize_sql(_schema(connection, TRIGGER_NAME)) != _normalize_sql(LEGACY_TRIGGER):
            raise MigrationError("Loyalty insert trigger changed during migration.")
        connection.execute(f'DROP TRIGGER "{TRIGGER_NAME}"')
        connection.execute(EXPECTED_TRIGGER)
        if _normalize_sql(_schema(connection, TRIGGER_NAME)) != _normalize_sql(EXPECTED_TRIGGER):
            raise MigrationError("Redemption-compatible trigger failed verification.")
        for index_name in INDEXES:
            if _schema(connection, index_name) is None:
                raise MigrationError(f"Required loyalty index is missing: {index_name}")
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
        "updated_trigger": 1,
        "backup": str(backup_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    arguments = parser.parse_args()
    print(migrate(arguments.database))


if __name__ == "__main__":
    main()
