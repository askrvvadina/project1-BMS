import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app, db
from migrations.phase5_guest_loyalty_claims import MigrationError, migrate


class GuestLoyaltyClaimMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "claims.sqlite"
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE branches (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id),
                branch_id INTEGER NOT NULL REFERENCES branches(id)
            );
            CREATE TABLE loyalty_transactions (
                id INTEGER PRIMARY KEY,
                sale_id INTEGER REFERENCES sales(id),
                points INTEGER NOT NULL
            );
            INSERT INTO customers VALUES (1, 'Customer');
            INSERT INTO branches VALUES (1, 'Branch');
            INSERT INTO sales VALUES (1, 1, 1);
            INSERT INTO sales VALUES (2, NULL, 1);
            INSERT INTO sales VALUES (3, NULL, 1);
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

    def test_migration_is_additive_backed_up_and_idempotent(self):
        connection = self.connect()
        tables_before = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schemas_before = {
            table: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            for table in tables_before
        }
        rows_before = {
            table: [
                tuple(row)
                for row in connection.execute(f'SELECT * FROM "{table}"')
            ]
            for table in tables_before
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
            sorted([*tables_before, "guest_loyalty_claims"]),
        )
        for table in tables_before:
            self.assertEqual(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()[0],
                schemas_before[table],
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in connection.execute(f'SELECT * FROM "{table}"')
                ],
                rows_before[table],
            )

        connection.execute(
            """INSERT INTO guest_loyalty_claims
            (sale_id, branch_id, token_hash, points, expires_at)
            VALUES (2, 1, ?, 2, '2099-01-01 00:00:00')""",
            ("a" * 64,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO guest_loyalty_claims
                (sale_id, branch_id, token_hash, points, expires_at)
                VALUES (2, 1, ?, 2, '2099-01-01 00:00:00')""",
                ("b" * 64,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO guest_loyalty_claims
                (sale_id, branch_id, token_hash, points, expires_at)
                VALUES (1, 1, ?, 2, '2099-01-01 00:00:00')""",
                ("c" * 64,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO guest_loyalty_claims
                (sale_id, branch_id, token_hash, points, expires_at)
                VALUES (2, 99, ?, 2, '2099-01-01 00:00:00')""",
                ("d" * 64,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO guest_loyalty_claims
                (sale_id, branch_id, token_hash, points, expires_at)
                VALUES (3, 1, ?, 2, '2099-01-01 00:00:00')""",
                ("a" * 64,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE guest_loyalty_claims SET token_hash = ? WHERE sale_id = 2",
                ("e" * 64,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO loyalty_transactions (sale_id, points) VALUES (2, 2)"
            )
        connection.execute(
            """UPDATE guest_loyalty_claims SET claimed_at = '2026-10-20 00:00:00'
            WHERE sale_id = 2"""
        )
        connection.execute(
            "INSERT INTO loyalty_transactions (sale_id, points) VALUES (2, 2)"
        )
        connection.commit()
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

    def test_migration_refuses_incompatible_claim_table(self):
        connection = sqlite3.connect(self.database_path)
        connection.execute(
            "CREATE TABLE guest_loyalty_claims (id INTEGER PRIMARY KEY, token TEXT)"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(MigrationError):
            migrate(self.database_path)

    def test_model_created_claim_schema_is_migration_compatible(self):
        model_database = Path(self.temp_dir.name) / "model.sqlite"
        app = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": (
                    f"sqlite:///{model_database.as_posix()}"
                ),
            }
        )
        with app.app_context():
            db.create_all()
            db.session.remove()
            db.engine.dispose()

        result = migrate(model_database)
        self.assertEqual(result["created_table"], 0)
        self.assertIn("compatible", result["backup"])


if __name__ == "__main__":
    unittest.main()
