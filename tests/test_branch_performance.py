import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.financial import (
    calculate_branch_performance,
    calculate_financial_summary,
)
from app.models import Branch, Sale


class BranchPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "branch-performance.sqlite"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "branch-performance-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Alpha", address="A")
            branch_b = Branch(name="Beta", address="B")
            branch_c = Branch(name="Charlie", address="C")
            db.session.add_all([branch_a, branch_b, branch_c])
            db.session.flush()
            db.session.add_all([
                Sale(
                    branch_id=branch_a.id,
                    total_amount=Decimal("100.00"),
                    discount=Decimal("10.00"),
                    payment_method="cash",
                    created_at=datetime(2026, 6, 10, 9, 0),
                ),
                Sale(
                    branch_id=branch_a.id,
                    total_amount=Decimal("80.00"),
                    discount=Decimal("0.00"),
                    payment_method="card",
                    created_at=datetime(2026, 6, 11, 9, 0),
                ),
                Sale(
                    branch_id=branch_b.id,
                    total_amount=Decimal("55.00"),
                    discount=None,
                    payment_method="cash",
                    created_at=datetime(2026, 6, 10, 9, 0),
                ),
                Sale(
                    branch_id=branch_a.id,
                    total_amount=Decimal("999.00"),
                    discount=Decimal("0.00"),
                    payment_method="cash",
                    created_at=datetime(2026, 5, 31, 9, 0),
                ),
            ])
            db.session.commit()
            self.branch_ids = {
                "a": branch_a.id,
                "b": branch_b.id,
                "c": branch_c.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def test_multiple_branches_and_zero_sales(self):
        with self.app.app_context():
            rows = calculate_branch_performance(
                authorized_branch_ids=[self.branch_ids["a"], self.branch_ids["b"], self.branch_ids["c"]]
            )

        by_id = {row["branch_id"]: row for row in rows}
        self.assertEqual(by_id[self.branch_ids["a"]]["actual_paid_revenue"], Decimal("1169.00"))
        self.assertEqual(by_id[self.branch_ids["a"]]["sales_count"], 3)
        self.assertEqual(by_id[self.branch_ids["a"]]["average_transaction_value"], Decimal("389.67"))
        self.assertEqual(by_id[self.branch_ids["b"]]["actual_paid_revenue"], Decimal("55.00"))
        self.assertEqual(by_id[self.branch_ids["c"]]["actual_paid_revenue"], Decimal("0.00"))
        self.assertEqual(by_id[self.branch_ids["c"]]["sales_count"], 0)
        self.assertEqual(by_id[self.branch_ids["c"]]["average_transaction_value"], Decimal("0.00"))

    def test_loyalty_discount_is_deducted_once(self):
        with self.app.app_context():
            rows = calculate_branch_performance(
                authorized_branch_ids=[self.branch_ids["a"]],
                date_from=date(2026, 6, 10),
                date_to=date(2026, 6, 11),
            )

        self.assertEqual(rows[0]["actual_paid_revenue"], Decimal("170.00"))
        self.assertEqual(rows[0]["sales_count"], 2)
        self.assertEqual(rows[0]["average_transaction_value"], Decimal("85.00"))

    def test_date_range_uses_inclusive_end_date(self):
        with self.app.app_context():
            rows = calculate_branch_performance(
                authorized_branch_ids=[self.branch_ids["a"]],
                date_from=date(2026, 6, 11),
                date_to=date(2026, 6, 11),
            )

        self.assertEqual(rows[0]["actual_paid_revenue"], Decimal("80.00"))
        self.assertEqual(rows[0]["sales_count"], 1)

    def test_branch_scope_cannot_expand(self):
        with self.app.app_context():
            rows = calculate_branch_performance(
                authorized_branch_ids=[self.branch_ids["b"]],
            )

        self.assertEqual([row["branch_id"] for row in rows], [self.branch_ids["b"]])

    def test_empty_scope_does_not_return_branches(self):
        with self.app.app_context():
            self.assertEqual(calculate_branch_performance(authorized_branch_ids=[]), [])
            self.assertEqual(calculate_branch_performance(), [])

    def test_consistent_with_existing_financial_summary(self):
        with self.app.app_context():
            rows = calculate_branch_performance(
                authorized_branch_ids=[self.branch_ids["a"]],
                date_from=date(2026, 6, 10),
                date_to=date(2026, 6, 11),
            )
            financial = calculate_financial_summary(
                date_from=date(2026, 6, 10),
                date_to=date(2026, 6, 11),
                branch_id=self.branch_ids["a"],
            )

        self.assertEqual(rows[0]["actual_paid_revenue"], financial["actual_paid_sales"])


if __name__ == "__main__":
    unittest.main()
