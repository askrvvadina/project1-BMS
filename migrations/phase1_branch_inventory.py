"""Add branch-level inventory and map current product stock to it.

Run with:
    python migrations/phase1_branch_inventory.py

An alternate database can be supplied with --database. The migration is
additive and repeat-safe: it never deletes records or overwrites existing
branch inventory values or transaction links.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEFAULT_DATABASE = (
    Path(__file__).resolve().parents[1] / "instance" / "bms.db"
)


class MigrationError(RuntimeError):
    """Raised when the existing database is not safe to migrate."""


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _column_info(
    connection: sqlite3.Connection,
    table_name: str,
) -> dict[str, sqlite3.Row]:
    return {
        row["name"]: row
        for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    }


def _verify_branch_inventory_schema(connection: sqlite3.Connection) -> None:
    columns = _column_info(connection, "branch_inventory")
    required_columns = {
        "id",
        "branch_id",
        "product_id",
        "stock_quantity",
        "minimum_stock",
    }
    if not required_columns.issubset(columns):
        raise MigrationError("branch_inventory exists with an unexpected schema.")
    for name in ("branch_id", "product_id", "stock_quantity", "minimum_stock"):
        if not columns[name]["notnull"]:
            raise MigrationError(f"branch_inventory.{name} must be NOT NULL.")

    foreign_keys = {
        row["from"]: row["table"]
        for row in connection.execute('PRAGMA foreign_key_list("branch_inventory")')
    }
    if foreign_keys.get("branch_id") != "branches":
        raise MigrationError("branch_inventory.branch_id has no expected FK.")
    if foreign_keys.get("product_id") != "products":
        raise MigrationError("branch_inventory.product_id has no expected FK.")

    unique_pairs = set()
    for index in connection.execute('PRAGMA index_list("branch_inventory")'):
        if index["unique"]:
            names = tuple(
                row["name"]
                for row in connection.execute(
                    f'PRAGMA index_info("{index["name"]}")'
                )
            )
            unique_pairs.add(names)
    if ("branch_id", "product_id") not in unique_pairs:
        raise MigrationError(
            "branch_inventory is missing UNIQUE(branch_id, product_id)."
        )


def _verify_transaction_schema(connection: sqlite3.Connection) -> None:
    columns = _column_info(connection, "inventory_transactions")
    branch_inventory_column = columns.get("branch_inventory_id")
    if branch_inventory_column is None or branch_inventory_column["notnull"]:
        raise MigrationError(
            "inventory_transactions.branch_inventory_id must exist and be nullable."
        )

    foreign_keys = {
        row["from"]: row["table"]
        for row in connection.execute(
            'PRAGMA foreign_key_list("inventory_transactions")'
        )
    }
    if foreign_keys.get("branch_inventory_id") != "branch_inventory":
        raise MigrationError(
            "inventory_transactions.branch_inventory_id has no expected FK."
        )


def migrate(database_path: Path = DEFAULT_DATABASE) -> dict[str, int | str]:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise MigrationError(f"Database file does not exist: {database_path}")

    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise MigrationError("Could not enable SQLite foreign-key checks.")
        connection.execute("BEGIN IMMEDIATE")

        for table in ("branches", "products", "inventory_transactions"):
            if not _table_exists(connection, table):
                raise MigrationError(f"Required table is missing: {table}")

        invalid_products = connection.execute(
            """
            SELECT COUNT(*)
            FROM products AS p
            LEFT JOIN branches AS b ON b.id = p.branch_id
            WHERE p.branch_id IS NULL OR b.id IS NULL
            """
        ).fetchone()[0]
        if invalid_products:
            raise MigrationError(
                f"{invalid_products} product(s) have an invalid branch_id."
            )

        orphan_transactions = connection.execute(
            """
            SELECT COUNT(*)
            FROM inventory_transactions AS t
            LEFT JOIN products AS p ON p.id = t.product_id
            WHERE p.id IS NULL
            """
        ).fetchone()[0]
        if orphan_transactions:
            raise MigrationError(
                f"{orphan_transactions} transaction(s) reference missing products."
            )

        product_snapshot = connection.execute(
            """
            SELECT id, branch_id, stock_quantity, minimum_stock
            FROM products ORDER BY id
            """
        ).fetchall()
        transaction_snapshot = connection.execute(
            """
            SELECT id, product_id, transaction_type, quantity, created_at
            FROM inventory_transactions ORDER BY id
            """
        ).fetchall()

        created_table = not _table_exists(connection, "branch_inventory")
        transaction_column_existed = (
            "branch_inventory_id"
            in _column_info(connection, "inventory_transactions")
        )
        migration_was_already_applied = (
            not created_table and transaction_column_existed
        )
        if created_table:
            connection.execute(
                """
                CREATE TABLE branch_inventory (
                    id INTEGER NOT NULL PRIMARY KEY,
                    branch_id INTEGER NOT NULL
                        REFERENCES branches (id),
                    product_id INTEGER NOT NULL
                        REFERENCES products (id),
                    stock_quantity INTEGER NOT NULL DEFAULT 0,
                    minimum_stock INTEGER NOT NULL DEFAULT 0,
                    CONSTRAINT uq_branch_inventory_branch_product
                        UNIQUE (branch_id, product_id)
                )
                """
            )
        else:
            _verify_branch_inventory_schema(connection)

        transaction_columns = _column_info(
            connection,
            "inventory_transactions",
        )
        added_transaction_column = "branch_inventory_id" not in transaction_columns
        if added_transaction_column:
            connection.execute(
                """
                ALTER TABLE inventory_transactions
                ADD COLUMN branch_inventory_id INTEGER
                    REFERENCES branch_inventory (id)
                """
            )

        _verify_branch_inventory_schema(connection)
        _verify_transaction_schema(connection)

        created_rows = 0
        for product in product_snapshot:
            existing = connection.execute(
                """
                SELECT id FROM branch_inventory
                WHERE branch_id = ? AND product_id = ?
                """,
                (product["branch_id"], product["id"]),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO branch_inventory (
                        branch_id, product_id, stock_quantity, minimum_stock
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        product["branch_id"],
                        product["id"],
                        product["stock_quantity"],
                        product["minimum_stock"],
                    ),
                )
                created_rows += 1

        connection.execute(
            """
            UPDATE inventory_transactions
            SET branch_inventory_id = (
                SELECT bi.id
                FROM branch_inventory AS bi
                JOIN products AS p ON p.id = bi.product_id
                WHERE p.id = inventory_transactions.product_id
                  AND bi.branch_id = p.branch_id
            )
            WHERE branch_inventory_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM branch_inventory AS bi
                  JOIN products AS p ON p.id = bi.product_id
                  WHERE p.id = inventory_transactions.product_id
                    AND bi.branch_id = p.branch_id
              )
            """
        )

        if not migration_was_already_applied:
            mismatched_stock = connection.execute(
                """
                SELECT COUNT(*)
                FROM products AS p
                LEFT JOIN branch_inventory AS bi
                  ON bi.branch_id = p.branch_id
                 AND bi.product_id = p.id
                WHERE bi.id IS NULL
                   OR bi.stock_quantity != p.stock_quantity
                   OR bi.minimum_stock != p.minimum_stock
                """
            ).fetchone()[0]
            if mismatched_stock:
                raise MigrationError(
                    "Branch inventory values do not match legacy Product values."
                )

        unlinked_transactions = connection.execute(
            """
            SELECT COUNT(*)
            FROM inventory_transactions
            WHERE branch_inventory_id IS NULL
            """
        ).fetchone()[0]
        if unlinked_transactions:
            raise MigrationError(
                f"{unlinked_transactions} transaction(s) could not be linked."
            )

        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise MigrationError(
                f"SQLite foreign_key_check found violations: "
                f"{[tuple(row) for row in foreign_key_violations]}"
            )

        product_after = connection.execute(
            """
            SELECT id, branch_id, stock_quantity, minimum_stock
            FROM products ORDER BY id
            """
        ).fetchall()
        transaction_after = connection.execute(
            """
            SELECT id, product_id, transaction_type, quantity, created_at
            FROM inventory_transactions ORDER BY id
            """
        ).fetchall()
        if [tuple(row) for row in product_snapshot] != [
            tuple(row) for row in product_after
        ]:
            raise MigrationError("Product data changed during migration.")
        if [tuple(row) for row in transaction_snapshot] != [
            tuple(row) for row in transaction_after
        ]:
            raise MigrationError(
                "Existing inventory transaction data changed during migration."
            )

        transaction_count = len(transaction_snapshot)
        connection.commit()
        return {
            "database": str(database_path),
            "products": len(product_snapshot),
            "branch_inventory_rows_created": created_rows,
            "branch_inventory_rows_total": connection.execute(
                "SELECT COUNT(*) FROM branch_inventory"
            ).fetchone()[0],
            "inventory_transactions": transaction_count,
            "inventory_transactions_linked": connection.execute(
                """
                SELECT COUNT(*)
                FROM inventory_transactions
                WHERE branch_inventory_id IS NOT NULL
                """
            ).fetchone()[0],
            "created_branch_inventory_table": int(created_table),
            "added_transaction_column": int(added_transaction_column),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite database to migrate (default: {DEFAULT_DATABASE})",
    )
    args = parser.parse_args()
    result = migrate(args.database)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
