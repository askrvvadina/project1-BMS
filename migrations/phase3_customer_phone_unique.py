"""Add a unique index for customer phone numbers without rewriting records.

Run with:
    python migrations/phase3_customer_phone_unique.py

An alternate database can be supplied with --database. The migration refuses
to proceed if existing phone values conflict after punctuation is ignored.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "instance" / "bms.db"


class MigrationError(RuntimeError):
    """Raised when the existing database is not safe to migrate."""


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int | str]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'customers'"
        ).fetchone()
        if table_exists is None:
            raise MigrationError("Required table is missing: customers")

        columns = {
            row["name"]
            for row in connection.execute('PRAGMA table_info("customers")')
        }
        if "phone" not in columns:
            raise MigrationError("Required column is missing: customers.phone")

        phone_values = [
            row["phone"]
            for row in connection.execute(
                "SELECT phone FROM customers WHERE phone IS NOT NULL"
            )
        ]
        normalized_counts = Counter(
            digits
            for phone in phone_values
            if (digits := re.sub(r"\D", "", phone))
        )
        normalized_duplicates = sum(
            count > 1 for count in normalized_counts.values()
        )
        if normalized_duplicates:
            raise MigrationError(
                f"{normalized_duplicates} duplicate phone group(s) exist after "
                "normalization; resolve them explicitly before migrating."
            )

        exact_duplicates = connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT phone
                FROM customers
                WHERE phone IS NOT NULL
                GROUP BY phone
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        if exact_duplicates:
            raise MigrationError(
                f"{exact_duplicates} duplicate stored phone value(s) exist."
            )

        existing_index = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'uq_customers_phone'
            """
        ).fetchone()
        if existing_index is None:
            connection.execute(
                "CREATE UNIQUE INDEX uq_customers_phone ON customers(phone)"
            )
            created = 1
        else:
            index_rows = connection.execute(
                'PRAGMA index_info("uq_customers_phone")'
            ).fetchall()
            index_list = {
                row["name"]: row
                for row in connection.execute(
                    'PRAGMA index_list("customers")'
                )
            }
            if (
                not index_list.get("uq_customers_phone")
                or not index_list["uq_customers_phone"]["unique"]
                or [row["name"] for row in index_rows] != ["phone"]
            ):
                raise MigrationError(
                    "uq_customers_phone exists but has an unexpected definition."
                )
            created = 0

        connection.commit()
        return {
            "database": str(database_path),
            "created_index": created,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    arguments = parser.parse_args()
    print(migrate(arguments.database))


if __name__ == "__main__":
    main()
