"""Add one current sales-target configuration per branch.

Prepare/execute only against an explicitly supplied database copy:
    python migrations/phase9_branch_sales_targets.py --database C:\\path\\copy.sqlite

The real instance/bms.db is never selected by this script unless explicitly supplied.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"


class MigrationError(RuntimeError):
    pass


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if not table_exists(connection, "branches"):
            raise MigrationError("branches table does not exist.")

        if not table_exists(connection, "branch_sales_targets"):
            connection.execute(
                """
                CREATE TABLE branch_sales_targets (
                    id INTEGER NOT NULL PRIMARY KEY,
                    branch_id INTEGER NOT NULL
                        REFERENCES branches (id) ON DELETE CASCADE,
                    daily_target NUMERIC(12, 2) NOT NULL DEFAULT 15000.00,
                    monthly_target NUMERIC(12, 2) NOT NULL DEFAULT 450000.00,
                    CONSTRAINT uq_branch_sales_targets_branch UNIQUE (branch_id),
                    CONSTRAINT ck_branch_sales_targets_daily_nonnegative
                        CHECK (daily_target >= 0),
                    CONSTRAINT ck_branch_sales_targets_monthly_nonnegative
                        CHECK (monthly_target >= 0)
                )
                """
            )

        inserted = 0
        for row in connection.execute("SELECT id FROM branches ORDER BY id"):
            cursor = connection.execute(
                "SELECT 1 FROM branch_sales_targets WHERE branch_id = ?",
                (row["id"],),
            )
            if cursor.fetchone() is None:
                connection.execute(
                    """
                    INSERT INTO branch_sales_targets (
                        branch_id, daily_target, monthly_target
                    ) VALUES (?, 15000.00, 450000.00)
                    """,
                    (row["id"],),
                )
                inserted += 1

        connection.commit()
        return {
            "created_targets": inserted,
            "existing_branches": connection.execute("SELECT COUNT(*) FROM branches").fetchone()[0],
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    arguments = parser.parse_args()
    print(migrate(arguments.database))


if __name__ == "__main__":
    main()
