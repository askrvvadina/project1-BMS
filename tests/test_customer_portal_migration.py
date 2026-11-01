import sqlite3
import tempfile
import unittest
from pathlib import Path

from migrations.phase6_customer_portal import MigrationError, migrate


class CustomerPortalMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "customer-portal.sqlite"
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(30) UNIQUE,
                email VARCHAR(120),
                registration_date DATE NOT NULL
            );
            CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO customers VALUES
                (1, 'Existing Customer', '+996555111222', NULL, '2025-01-01');
            INSERT INTO branches VALUES (1, 'Migration Branch');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def connect(self):
        return sqlite3.connect(self.database_path)

    def test_migration_is_additive_backed_up_and_idempotent(self):
        connection = self.connect()
        original_customers = connection.execute(
            "SELECT * FROM customers"
        ).fetchall()
        original_branch = connection.execute("SELECT * FROM branches").fetchall()
        original_branch_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='branches'"
        ).fetchone()[0]
        connection.close()

        first = migrate(self.database_path)
        self.assertEqual(first["added_column"], 1)
        backup_path = Path(first["backup"])
        self.assertTrue(backup_path.is_file())
        second = migrate(self.database_path)
        self.assertEqual(second["added_column"], 0)
        self.assertEqual(second["backup"], "")

        connection = self.connect()
        self.assertEqual(
            connection.execute("SELECT * FROM customers").fetchall(),
            [(*original_customers[0], None)],
        )
        self.assertEqual(connection.execute("SELECT * FROM branches").fetchall(), original_branch)
        self.assertEqual(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='branches'"
            ).fetchone()[0],
            original_branch_schema,
        )
        self.assertEqual(
            [
                row[1]
                for row in connection.execute('PRAGMA table_info("customers")')
            ][-1],
            "password_hash",
        )
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

        backup = sqlite3.connect(backup_path)
        self.assertEqual(backup.execute("SELECT * FROM customers").fetchall(), original_customers)
        self.assertEqual(
            [row[1] for row in backup.execute('PRAGMA table_info("customers")')],
            ["id", "name", "phone", "email", "registration_date"],
        )
        backup.close()

    def test_migration_refuses_incompatible_password_hash_column(self):
        connection = self.connect()
        connection.execute(
            "ALTER TABLE customers ADD COLUMN password_hash INTEGER NOT NULL DEFAULT 1"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(MigrationError):
            migrate(self.database_path)


if __name__ == "__main__":
    unittest.main()
