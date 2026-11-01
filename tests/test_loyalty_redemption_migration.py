import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.models import Branch, Customer, LoyaltyTransaction, Sale, User
from migrations.phase4_loyalty_ledger import TRIGGERS
from migrations.phase7_loyalty_redemption import migrate


class LoyaltyRedemptionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "redemption.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-migration-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.database_path.as_posix()}",
            }
        )
        with self.app.app_context():
            db.create_all()
            branch = Branch(name="Migration branch", address="Test")
            customer = Customer(
                name="Migration customer",
                registration_date=date(2025, 1, 1),
            )
            user = User(
                username="migration-user",
                email="migration@test.invalid",
                password_hash="test",
                role="admin",
            )
            db.session.add_all([branch, customer, user])
            db.session.flush()
            sale = Sale(
                branch_id=branch.id,
                customer_id=customer.id,
                total_amount=Decimal("100.00"),
                discount=Decimal("0.00"),
                payment_method="cash",
                created_by_user_id=user.id,
            )
            db.session.add(sale)
            db.session.flush()
            db.session.add(
                LoyaltyTransaction(
                    customer_id=customer.id,
                    sale_id=sale.id,
                    branch_id=branch.id,
                    transaction_type="earn",
                    points=10,
                    operation_id="migration-existing-earning",
                )
            )
            db.session.commit()
            self.sale_id = sale.id
            self.customer_id = customer.id
            db.session.execute(
                db.text(
                    "DROP TRIGGER loyalty_transactions_validate_insert"
                )
            )
            db.session.execute(db.text(TRIGGERS["loyalty_transactions_validate_insert"]))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def test_migration_backs_up_preserves_ledger_and_is_idempotent(self):
        first = migrate(self.database_path)
        self.assertEqual(first["updated_trigger"], 1)
        self.assertTrue(Path(first["backup"]).is_file())
        second = migrate(self.database_path)
        self.assertEqual(second["updated_trigger"], 0)

        with self.app.app_context():
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.customer_id,
                    sale_id=self.sale_id,
                    branch_id=1,
                    transaction_type="redeem",
                    points=-2,
                    operation_id="migration-sale-redemption",
                )
            )
            db.session.commit()
            self.assertEqual(
                LoyaltyTransaction.query.filter_by(sale_id=self.sale_id).count(),
                2,
            )
            self.assertEqual(
                db.session.query(db.func.sum(LoyaltyTransaction.points))
                .filter_by(customer_id=self.customer_id)
                .scalar(),
                8,
            )
