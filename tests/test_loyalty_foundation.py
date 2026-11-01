import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.loyalty import calculate_earned_points, calculate_redemption_limit
from migrations.phase4_loyalty_ledger import MigrationError, migrate


class LoyaltyCalculationTests(unittest.TestCase):
    def test_earning_uses_paid_amount_and_rounds_down(self):
        self.assertEqual(calculate_earned_points(Decimal("19.99")), 0)
        self.assertEqual(calculate_earned_points(Decimal("99.99")), 0)
        self.assertEqual(calculate_earned_points(Decimal("100.00")), 1)
        self.assertEqual(calculate_earned_points(Decimal("299.99")), 2)

    def test_redemption_is_limited_by_purchase_and_balance(self):
        self.assertEqual(
            calculate_redemption_limit(Decimal("99.99"), 500),
            19,
        )
        self.assertEqual(
            calculate_redemption_limit(Decimal("100.00"), 7),
            7,
        )
        self.assertEqual(
            calculate_redemption_limit(Decimal("0.99"), 500),
            0,
        )

    def test_calculations_reject_invalid_amounts_and_point_balances(self):
        for amount in (Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    calculate_earned_points(amount)
        with self.assertRaises(TypeError):
            calculate_earned_points(1)
        for points in (-1, True, 1.0):
            with self.subTest(points=points):
                with self.assertRaises(ValueError):
                    calculate_redemption_limit(Decimal("10.00"), points)


class LoyaltyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "loyalty.sqlite"
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT
            );
            CREATE TABLE branches (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id),
                branch_id INTEGER NOT NULL REFERENCES branches(id),
                total_amount NUMERIC NOT NULL
            );
            INSERT INTO customers VALUES (1, 'Canonical customer', '5550001');
            INSERT INTO branches VALUES (1, 'Main branch');
            INSERT INTO sales VALUES (1, 1, 1, 40.00);
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def test_migration_is_additive_and_idempotent(self):
        connection = self.connect()
        original_tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        original_schemas = {
            table: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            for table in original_tables
        }
        original_rows = {
            table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            for table in original_tables
        }
        connection.close()

        first = migrate(self.database_path)
        self.assertEqual(first["created_table"], 1)
        self.assertTrue(Path(first["backup"]).is_file())
        second = migrate(self.database_path)
        self.assertEqual(second["created_table"], 0)

        connection = self.connect()
        self.assertEqual(
            [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ],
            sorted([*original_tables, "loyalty_transactions"]),
        )
        for table in original_tables:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            rows = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            self.assertEqual(schema, original_schemas[table])
            self.assertEqual(rows, original_rows[table])
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

    def test_ledger_constraints_balance_and_immutability(self):
        migrate(self.database_path)
        connection = self.connect()

        def insert(transaction_type, points, operation_id, sale_id=None, customer_id=1, branch_id=None):
            connection.execute(
                """INSERT INTO loyalty_transactions
                (customer_id, sale_id, branch_id, transaction_type, points, operation_id)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (customer_id, sale_id, branch_id, transaction_type, points, operation_id),
            )
            connection.commit()

        insert("earn", 10, "purchase-1-earn", sale_id=1, branch_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            insert("redeem", -1, "purchase-1-redeem", sale_id=1, branch_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            insert("redeem", -11, "overspend", sale_id=None)
        insert("redeem", -4, "redemption-1")
        insert("reversal", 2, "reversal-1")

        balance = connection.execute(
            "SELECT SUM(points) FROM loyalty_transactions WHERE customer_id = 1"
        ).fetchone()[0]
        self.assertEqual(balance, 8)
        for transaction_type, points, operation_id in (
            ("award", 1, "bad-type"),
            ("earn", 0, "zero"),
            ("earn", 1.5, "fractional"),
            ("earn", -1, "wrong-sign"),
            ("redeem", 1, "wrong-redeem-sign"),
            ("reversal", 0, "zero-reversal"),
        ):
            with self.subTest(transaction_type=transaction_type, points=points):
                with self.assertRaises(sqlite3.IntegrityError):
                    insert(transaction_type, points, operation_id)

        with self.assertRaises(sqlite3.IntegrityError):
            insert("earn", 1, "purchase-1-earn")
        with self.assertRaises(sqlite3.IntegrityError):
            insert("earn", 1, "duplicate-sale-earn", sale_id=1, branch_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            insert("earn", 1, "unknown-customer", customer_id=999)
        with self.assertRaises(sqlite3.IntegrityError):
            insert("earn", 1, "wrong-sale-customer", sale_id=1, customer_id=2)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE loyalty_transactions SET points = 11 WHERE operation_id = ?",
                ("purchase-1-earn",),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM loyalty_transactions WHERE operation_id = ?",
                ("purchase-1-earn",),
            )
        connection.close()

    def test_migration_rejects_incompatible_existing_table(self):
        connection = sqlite3.connect(self.database_path)
        connection.execute(
            "CREATE TABLE loyalty_transactions (id INTEGER PRIMARY KEY, customer_id INTEGER)"
        )
        connection.commit()
        connection.close()

        with self.assertRaises(MigrationError):
            migrate(self.database_path)
        connection = sqlite3.connect(self.database_path)
        columns = [
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("loyalty_transactions")'
            )
        ]
        self.assertEqual(columns, ["id", "customer_id"])
        connection.close()

    def test_migration_accepts_model_created_schema_without_rewriting(self):
        model_path = Path(self.temp_dir.name) / "model-created.sqlite"
        app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{model_path.as_posix()}",
            }
        )
        with app.app_context():
            db.create_all()
            customer = db.session.execute(
                db.text(
                    "INSERT INTO customers (name, registration_date) "
                    "VALUES ('Model customer', '2026-01-01') RETURNING id"
                )
            ).scalar_one()
            db.session.commit()
            self.assertGreater(customer, 0)
            db.session.remove()
        result = migrate(model_path)
        self.assertEqual(result["created_table"], 0)
        with app.app_context():
            db.engine.dispose()


if __name__ == "__main__":
    unittest.main()