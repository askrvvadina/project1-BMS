import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.models import Branch, BranchSalesTarget, Employee, Sale, User
from app.sales_targets import calculate_target_progress


class BranchAnalyticsPageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "branch-analytics.sqlite"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "branch-analytics-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Alpha Analytics", address="A")
            branch_b = Branch(name="Beta Analytics", address="B")
            branch_empty = Branch(name="Empty Analytics", address="C")
            db.session.add_all([branch_a, branch_b, branch_empty])
            db.session.flush()

            employee_a = Employee(
                full_name="Employee A",
                position="Seller",
                hire_date=date(2025, 1, 1),
                branch_id=branch_a.id,
            )
            db.session.add(employee_a)
            db.session.flush()
            users = [
                User(username="analytics-admin", email="analytics-admin@test.invalid", password_hash="test", role="admin"),
                User(username="analytics-manager", email="analytics-manager@test.invalid", password_hash="test", role="manager"),
                User(username="analytics-employee", email="analytics-employee@test.invalid", password_hash="test", role="employee", employee_id=employee_a.id),
                User(username="analytics-unassigned", email="analytics-unassigned@test.invalid", password_hash="test", role="employee"),
            ]
            db.session.add_all(users)
            db.session.add_all([
                Sale(branch_id=branch_a.id, total_amount=Decimal("100.00"), discount=Decimal("10.00"), payment_method="cash", created_at=datetime(2026, 6, 10, 9, 0)),
                Sale(branch_id=branch_a.id, total_amount=Decimal("80.00"), discount=Decimal("0.00"), payment_method="card", created_at=datetime(2026, 6, 11, 9, 0)),
                Sale(branch_id=branch_b.id, total_amount=Decimal("55.00"), discount=None, payment_method="cash", created_at=datetime(2026, 6, 10, 9, 0)),
                Sale(branch_id=branch_a.id, total_amount=Decimal("500.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 5, 10, 9, 0)),
            ])
            db.session.commit()
            self.ids = {
                "admin": users[0].id,
                "manager": users[1].id,
                "employee": users[2].id,
                "unassigned": users[3].id,
                "branch_a": branch_a.id,
                "branch_b": branch_b.id,
                "branch_empty": branch_empty.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def client_for(self, key):
        client = self.app.test_client()
        with client.session_transaction() as test_session:
            test_session["user_id"] = self.ids[key]
            test_session["role"] = key
            if key == "employee":
                test_session["role"] = "employee"
            elif key == "unassigned":
                test_session["role"] = "employee"
            else:
                test_session["role"] = key
        return client

    def test_admin_and_manager_can_see_all_branch_rows(self):
        for role in ("admin", "manager"):
            with self.subTest(role=role):
                response = self.client_for(role).get("/branch-analytics")
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Alpha Analytics", html)
                self.assertIn("Beta Analytics", html)
                self.assertIn("Empty Analytics", html)

    def test_employee_is_isolated_to_assigned_branch(self):
        response = self.client_for("employee").get("/branch-analytics")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alpha Analytics", html)
        self.assertNotIn("Beta Analytics", html)
        self.assertNotIn("Empty Analytics", html)

    def test_employee_without_branch_fails_closed(self):
        response = self.client_for("unassigned").get("/branch-analytics")
        self.assertEqual(response.status_code, 403)

    def test_date_filter_and_financial_values(self):
        response = self.client_for("admin").get(
            "/branch-analytics?start_date=2026-06-10&end_date=2026-06-11"
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("170.00 сом", html)
        self.assertIn("85.00 сом", html)
        self.assertIn("55.00 сом", html)
        self.assertIn("0.00 сом", html)
        self.assertIn('value="2026-06-10"', html)
        self.assertIn('value="2026-06-11"', html)

    def test_invalid_dates_are_reported_and_preserved(self):
        response = self.client_for("admin").get(
            "/branch-analytics?start_date=bad&end_date=also-bad"
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Некорректный диапазон дат", html)
        self.assertIn('value="bad"', html)
        self.assertIn('value="also-bad"', html)

    def test_zero_sales_branch_is_rendered(self):
        response = self.client_for("admin").get("/branch-analytics")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Empty Analytics", html)
        table_start = html.index('<tbody>')
        empty_position = html.index("Empty Analytics", table_start)
        row = html[empty_position:html.index("</tr>", empty_position)]
        self.assertIn("0.00 сом", row)
        self.assertRegex(row, r"\n\s*<td>0</td>")

    def test_target_progress_calculations(self):
        normal = calculate_target_progress(Decimal("100.00"), Decimal("125.00"))
        self.assertEqual(normal["achievement_percentage"], Decimal("125.00"))
        self.assertEqual(normal["remaining_amount"], Decimal("0.00"))

        zero = calculate_target_progress(Decimal("0.00"), Decimal("100.00"))
        self.assertTrue(zero["is_unset"])
        self.assertIsNone(zero["achievement_percentage"])
        self.assertEqual(zero["remaining_amount"], Decimal("0.00"))

    def test_admin_controls_only_visible_and_postable_by_admin(self):
        admin_html = self.client_for("admin").get("/branch-analytics").get_data(as_text=True)
        manager_html = self.client_for("manager").get("/branch-analytics").get_data(as_text=True)
        employee_html = self.client_for("employee").get("/branch-analytics").get_data(as_text=True)
        self.assertIn("branch-target-form", admin_html)
        self.assertNotIn("branch-target-form", manager_html)
        self.assertNotIn("branch-target-form", employee_html)

        self.assertEqual(
            self.client_for("manager").post(
                f"/branch-analytics/{self.ids['branch_a']}/targets",
                data={"daily_target": "1", "monthly_target": "2"},
            ).status_code,
            403,
        )
        response = self.client_for("admin").post(
            f"/branch-analytics/{self.ids['branch_a']}/targets",
            data={"daily_target": "20000.25", "monthly_target": "600000.50"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            targets = BranchSalesTarget.query.filter_by(branch_id=self.ids["branch_a"]).one()
            self.assertEqual(targets.daily_target, Decimal("20000.25"))
            self.assertEqual(targets.monthly_target, Decimal("600000.50"))

    def test_current_progress_uses_actual_paid_revenue(self):
        with self.app.app_context():
            branch = db.session.get(Branch, self.ids["branch_a"])
            db.session.add(Sale(
                branch_id=branch.id,
                total_amount=Decimal("100.00"),
                discount=Decimal("15.00"),
                payment_method="cash",
                created_at=datetime.now(),
            ))
            db.session.commit()

        html = self.client_for("admin").get("/branch-analytics").get_data(as_text=True)
        alpha_card = html[html.index("Alpha Analytics"):html.index("Beta Analytics")]
        self.assertIn("85.00 / 15000.00 сом", alpha_card)

    def test_navigation_contains_russian_entry(self):
        response = self.client_for("admin").get("/branch-analytics")
        self.assertIn("Аналитика филиалов", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
