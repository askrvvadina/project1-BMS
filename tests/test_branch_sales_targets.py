import tempfile
import unittest
import sqlite3
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.models import Branch, BranchSalesTarget, User
from app.sales_targets import (
    DEFAULT_DAILY_TARGET,
    DEFAULT_MONTHLY_TARGET,
    MAX_TARGET,
    get_branch_sales_targets,
    update_branch_sales_targets,
)


class BranchSalesTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "branch-sales-targets.sqlite"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "branch-sales-target-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Target Branch A", address="A")
            branch_b = Branch(name="Target Branch B", address="B")
            admin = User(username="target-admin", email="target-admin@test.invalid", password_hash="test", role="admin")
            manager = User(username="target-manager", email="target-manager@test.invalid", password_hash="test", role="manager")
            db.session.add_all([branch_a, branch_b, admin, manager])
            db.session.commit()
            self.branch_a_id = branch_a.id
            self.branch_b_id = branch_b.id
            self.admin_id = admin.id
            self.manager_id = manager.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def actor(self, key):
        with self.app.app_context():
            return db.session.get(User, self.admin_id if key == "admin" else self.manager_id)

    def test_default_targets_are_created(self):
        with self.app.app_context():
            targets = get_branch_sales_targets(self.branch_a_id, create_defaults=True)
            self.assertEqual(targets.daily_target, DEFAULT_DAILY_TARGET)
            self.assertEqual(targets.monthly_target, DEFAULT_MONTHLY_TARGET)
            self.assertEqual(BranchSalesTarget.query.count(), 1)

    def test_branches_have_separate_configurations(self):
        with self.app.app_context():
            first = get_branch_sales_targets(self.branch_a_id, create_defaults=True)
            second = get_branch_sales_targets(self.branch_b_id, create_defaults=True)
            second.daily_target = Decimal("12345.67")
            second.monthly_target = Decimal("234567.89")
            db.session.commit()

            self.assertEqual(first.daily_target, Decimal("15000.00"))
            self.assertEqual(second.daily_target, Decimal("12345.67"))
            self.assertEqual(second.monthly_target, Decimal("234567.89"))

    def test_update_targets_as_admin(self):
        with self.app.app_context():
            actor = db.session.get(User, self.admin_id)
            targets = update_branch_sales_targets(
                self.branch_a_id,
                Decimal("20000.12"),
                Decimal("600000.34"),
                actor,
            )
            self.assertEqual(targets.daily_target, Decimal("20000.12"))
            self.assertEqual(targets.monthly_target, Decimal("600000.34"))

    def test_manager_cannot_update_targets(self):
        with self.app.app_context():
            actor = db.session.get(User, self.manager_id)
            with self.assertRaises(PermissionError):
                update_branch_sales_targets(self.branch_a_id, 1, 1, actor)
            self.assertIsNone(BranchSalesTarget.query.filter_by(branch_id=self.branch_a_id).one_or_none())

    def test_invalid_values_are_rejected(self):
        invalid_values = (-1, Decimal("NaN"), Decimal("Infinity"), "not-a-number", "1.001", MAX_TARGET + Decimal("0.01"))
        with self.app.app_context():
            actor = db.session.get(User, self.admin_id)
            for value in invalid_values:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        update_branch_sales_targets(self.branch_a_id, value, 450000, actor)

    def test_decimal_precision_and_maximum(self):
        with self.app.app_context():
            actor = db.session.get(User, self.admin_id)
            targets = update_branch_sales_targets(
                self.branch_a_id,
                Decimal("9999999999.99"),
                Decimal("1234567890.12"),
                actor,
            )
            self.assertEqual(targets.daily_target, Decimal("9999999999.99"))
            self.assertEqual(targets.monthly_target, Decimal("1234567890.12"))

    def test_duplicate_configuration_is_rejected(self):
        with self.app.app_context():
            db.session.add(BranchSalesTarget(
                branch_id=self.branch_a_id,
                daily_target=DEFAULT_DAILY_TARGET,
                monthly_target=DEFAULT_MONTHLY_TARGET,
            ))
            db.session.commit()
            db.session.add(BranchSalesTarget(
                branch_id=self.branch_a_id,
                daily_target=DEFAULT_DAILY_TARGET,
                monthly_target=DEFAULT_MONTHLY_TARGET,
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_existing_branches_are_not_modified(self):
        with self.app.app_context():
            branch_count = Branch.query.count()
            get_branch_sales_targets(self.branch_a_id, create_defaults=True)
            self.assertEqual(Branch.query.count(), branch_count)
            self.assertEqual(db.session.get(Branch, self.branch_a_id).name, "Target Branch A")

    def test_prepared_migration_isolated_and_repeatable(self):
        from migrations.phase9_branch_sales_targets import migrate

        migration_db = Path(self.temp_dir.name) / "migration-copy.sqlite"
        connection = sqlite3.connect(migration_db)
        connection.execute(
            "CREATE TABLE branches (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(120) NOT NULL, address VARCHAR(255) NOT NULL, phone VARCHAR(30), status VARCHAR(20) NOT NULL)"
        )
        connection.execute("INSERT INTO branches (id, name, address, status) VALUES (1, 'Existing Branch', 'Address', 'active')")
        connection.commit()
        connection.close()

        first = migrate(migration_db)
        second = migrate(migration_db)
        self.assertEqual(first["created_targets"], 1)
        self.assertEqual(second["created_targets"], 0)

        connection = sqlite3.connect(migration_db)
        target = connection.execute(
            "SELECT branch_id, daily_target, monthly_target FROM branch_sales_targets"
        ).fetchone()
        branch_name = connection.execute("SELECT name FROM branches WHERE id = 1").fetchone()[0]
        connection.close()
        self.assertEqual(target, (1, 15000.0, 450000.0))
        self.assertEqual(branch_name, "Existing Branch")


if __name__ == "__main__":
    unittest.main()
