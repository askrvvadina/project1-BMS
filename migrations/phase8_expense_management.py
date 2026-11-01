"""Add managed expense categories while preserving existing expenses.

Run with:
    python migrations/phase8_expense_management.py

An alternate database can be supplied with --database.
This migration does not modify the real database by default until explicitly run.
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
        raise MigrationError("Database file does not exist.")

    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if not table_exists(connection, "expenses"):
            raise MigrationError("expenses table does not exist.")

        expense_columns = {
            row["name"]: row
            for row in connection.execute('PRAGMA table_info("expenses")')
        }
        required = {"id", "branch_id", "category", "amount", "description", "date"}
        if not required.issubset(expense_columns):
            raise MigrationError("expenses has an unexpected schema.")

        created_categories = not table_exists(connection, "expense_categories")
        if created_categories:
            connection.execute(
                """
                CREATE TABLE expense_categories (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE
                )
                """
            )

        category_names = connection.execute(
            "SELECT DISTINCT category FROM expenses WHERE category IS NOT NULL AND trim(category) != '' ORDER BY category"
        ).fetchall()
        category_rows_created = 0
        for row in category_names:
            cursor = connection.execute(
                "SELECT id FROM expense_categories WHERE lower(name) = lower(?)",
                (row["category"],),
            ).fetchone()
            if cursor is None:
                cursor = connection.execute(
                    "INSERT INTO expense_categories (name) VALUES (?)",
                    (row["category"],),
                )
                category_rows_created += 1

        added_category_id = "category_id" not in expense_columns
        if added_category_id:
            connection.execute(
                "ALTER TABLE expenses ADD COLUMN category_id INTEGER REFERENCES expense_categories (id)"
            )

        connection.execute(
            """
            UPDATE expenses
            SET category_id = (
                SELECT ec.id FROM expense_categories ec
                WHERE lower(ec.name) = lower(expenses.category)
                LIMIT 1
            )
            WHERE category_id IS NULL
            """
        )
        connection.execute(
            "UPDATE expenses SET description = '' WHERE description IS NULL"
        )
        connection.commit()
        return {
            "categories_created": category_rows_created,
            "expenses_preserved": connection.execute("SELECT COUNT(*) FROM expenses").fetchone()[0],
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    arguments = parser.parse_args()
    result = migrate(arguments.database if arguments.database else DEFAULT_DATABASE)
    print(result)


if __name__ == "__main__":
    main()
