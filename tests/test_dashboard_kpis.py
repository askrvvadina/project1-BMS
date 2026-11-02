import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.financial import (
    calculate_category_sales,
    calculate_daily_sales_revenue,
    calculate_low_stock,
    calculate_monthly_sales_revenue,
    calculate_top_products,
)
from app.models import (
    BranchInventory, SaleItem,
    Branch, Category, Employee, Expense, ExpenseCategory, Product, Sale, User,
)


class DashboardKpiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "dashboard-kpis.sqlite"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "dashboard-kpi-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="KPI Branch A", address="A")
            branch_b = Branch(name="KPI Branch B", address="B")
            category = Category(name="KPI category")
            expense_category = ExpenseCategory(name="KPI expense category")
            admin = User(username="kpi-admin", email="kpi-admin@test.invalid", password_hash="test", role="admin")
            manager = User(username="kpi-manager", email="kpi-manager@test.invalid", password_hash="test", role="manager")
            employee_a = Employee(full_name="Employee A", position="Seller", hire_date=date(2025, 1, 1), branch_id=1, status="active")
            employee_b = Employee(full_name="Employee B", position="Seller", hire_date=date(2025, 1, 1), branch_id=2, status="active")
            db.session.add_all([branch_a, branch_b, category, expense_category, admin, manager, employee_a, employee_b])
            db.session.flush()
            employee_a.branch_id = branch_a.id
            employee_b.branch_id = branch_b.id
            employee_user_a = User(username="kpi-employee-a", email="kpi-employee-a@test.invalid", password_hash="test", role="employee", employee_id=employee_a.id)
            employee_user_b = User(username="kpi-employee-b", email="kpi-employee-b@test.invalid", password_hash="test", role="employee", employee_id=employee_b.id)
            employee_no_branch = User(username="kpi-unassigned", email="kpi-unassigned@test.invalid", password_hash="test", role="employee")
            unauthorized = User(username="kpi-outsider", email="kpi-outsider@test.invalid", password_hash="test", role="outsider")
            db.session.add_all([employee_user_a, employee_user_b, employee_no_branch, unauthorized])
            db.session.flush()
            product_a = Product(name="Product A", sku="KPI-A", category_id=category.id, branch_id=branch_a.id, purchase_price=Decimal("1.00"), selling_price=Decimal("10.00"))
            product_b = Product(name="Product B", sku="KPI-B", category_id=category.id, branch_id=branch_b.id, purchase_price=Decimal("1.00"), selling_price=Decimal("10.00"))
            db.session.add_all([product_a, product_b])
            db.session.flush()
            db.session.add_all([
                BranchInventory(branch_id=branch_a.id, product_id=product_a.id, stock_quantity=2, minimum_stock=5),
                BranchInventory(branch_id=branch_b.id, product_id=product_b.id, stock_quantity=10, minimum_stock=5),
            ])
            db.session.commit()
            self.ids = {
                "admin": admin.id,
                "manager": manager.id,
                "employee_a": employee_user_a.id,
                "employee_b": employee_user_b.id,
                "unassigned": employee_no_branch.id,
                "unauthorized": unauthorized.id,
                "branch_a": branch_a.id,
                "branch_b": branch_b.id,
                "category": expense_category.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def client_for(self, key, role=None):
        client = self.app.test_client()
        if role is None:
            role = "employee" if key.startswith("employee_") else key
        with client.session_transaction() as browser_session:
            browser_session["user_id"] = self.ids[key]
            browser_session["role"] = role
        return client

    def add_records(self):
        with self.app.app_context():
            db.session.add_all([
                Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("100.00"), discount=Decimal("20.00"), payment_method="cash", created_by_user_id=self.ids["admin"]),
                Sale(branch_id=self.ids["branch_b"], total_amount=Decimal("300.00"), discount=Decimal("30.00"), payment_method="card", created_by_user_id=self.ids["admin"]),
                Expense(date=date(2026, 6, 1), amount=Decimal("25.00"), description="A expense", category_id=self.ids["category"], category="KPI expense category", branch_id=self.ids["branch_a"]),
                Expense(date=date(2026, 6, 2), amount=Decimal("40.00"), description="B expense", category_id=self.ids["category"], category="KPI expense category", branch_id=self.ids["branch_b"]),
            ])
            db.session.commit()

    def test_admin_dashboard_kpis_and_loyalty_redemption(self):
        self.add_records()
        response = self.client_for("admin").get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for expected in ("350.00 сом", "2 продаж", "2", "65.00 сом", "285.00 сом"):
            self.assertIn(expected, html)
        self.assertIn("Фактически оплаченная выручка", html)
        self.assertIn("Операционная разница", html)

    def test_empty_dashboard(self):
        response = self.client_for("admin").get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("0.00 сом", html)
        self.assertIn("0 продаж", html)

    def test_employee_dashboard_is_branch_isolated(self):
        self.add_records()
        response = self.client_for("employee_a").get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("80.00 сом", html)
        self.assertIn("25.00 сом", html)
        self.assertIn("55.00 сом", html)
        self.assertIn("1 продаж", html)
        self.assertNotIn("270.00 сом", html)

    def test_unauthorized_and_missing_employee_branch_are_denied(self):
        self.assertEqual(self.client_for("unauthorized", "outsider").get("/").status_code, 403)
        self.assertEqual(self.client_for("unassigned", "employee").get("/").status_code, 403)

    def test_daily_and_monthly_sales_charts_zero_fill_and_loyalty(self):
        end_date = date(2026, 11, 2)
        with self.app.app_context():
            db.session.add_all([
                Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("100.00"), discount=Decimal("20.00"), payment_method="cash", created_at=datetime(2026, 11, 2, 10, 0)),
                Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("50.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 10, 15, 10, 0)),
            ])
            db.session.commit()
            daily = calculate_daily_sales_revenue(end_date=end_date, branch_id=self.ids["branch_a"])
            monthly = calculate_monthly_sales_revenue(end_date=end_date, branch_id=self.ids["branch_a"])
            self.assertEqual(len(daily["labels"]), 30)
            self.assertEqual(len(daily["values"]), 30)
            self.assertEqual(daily["values"][-1], 80.0)
            self.assertGreater(sum(value == 0 for value in daily["values"]), 1)
            self.assertEqual(len(monthly["labels"]), 12)
            october = monthly["labels"].index("октябрь 2026")
            november = monthly["labels"].index("ноябрь 2026")
            self.assertEqual(monthly["values"][october], 50.0)
            self.assertEqual(monthly["values"][november], 80.0)

    def test_sales_charts_branch_isolation_and_empty_history(self):
        end_date = date(2026, 11, 2)
        with self.app.app_context():
            daily = calculate_daily_sales_revenue(end_date=end_date, branch_id=self.ids["branch_a"])
            self.assertTrue(all(value == 0 for value in daily["values"]))
            monthly = calculate_monthly_sales_revenue(end_date=end_date, branch_id=self.ids["branch_a"])
            self.assertTrue(all(value == 0 for value in monthly["values"]))

            db.session.add(Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("75.00"), discount=Decimal("5.00"), payment_method="cash", created_at=datetime(2026, 11, 2, 10, 0)))
            db.session.add(Sale(branch_id=self.ids["branch_b"], total_amount=Decimal("200.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 11, 2, 11, 0)))
            db.session.commit()
            self.assertEqual(calculate_daily_sales_revenue(end_date=end_date, branch_id=self.ids["branch_a"])["values"][-1], 70.0)
            self.assertEqual(calculate_daily_sales_revenue(end_date=end_date, branch_id=self.ids["branch_b"])["values"][-1], 200.0)

    def test_top_products_category_sales_and_low_stock(self):
        with self.app.app_context():
            sale_a = Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("120.00"), discount=Decimal("20.00"), payment_method="cash")
            sale_b = Sale(branch_id=self.ids["branch_b"], total_amount=Decimal("30.00"), discount=Decimal("0.00"), payment_method="cash")
            db.session.add_all([sale_a, sale_b])
            db.session.flush()
            product_a = Product.query.filter_by(sku="KPI-A").one()
            product_b = Product.query.filter_by(sku="KPI-B").one()
            db.session.add_all([
                SaleItem(sale_id=sale_a.id, product_id=product_a.id, quantity=3, unit_price=Decimal("10.00")),
                SaleItem(sale_id=sale_a.id, product_id=product_b.id, quantity=1, unit_price=Decimal("30.00")),
                SaleItem(sale_id=sale_b.id, product_id=product_b.id, quantity=2, unit_price=Decimal("15.00")),
            ])
            db.session.commit()

            top = calculate_top_products(branch_id=self.ids["branch_a"])
            self.assertEqual(top[0]["product_id"], product_a.id)
            self.assertEqual(top[0]["units_sold"], 3)
            self.assertEqual(top[0]["gross_sales"], Decimal("30.00"))

            categories = calculate_category_sales(branch_id=self.ids["branch_a"])
            self.assertEqual(categories[0]["category_name"], "KPI category")
            self.assertEqual(categories[0]["gross_sales"], Decimal("60.00"))

            low_stock = calculate_low_stock(branch_id=self.ids["branch_a"])
            self.assertEqual(len(low_stock), 1)
            self.assertEqual(low_stock[0]["stock_quantity"], 2)
            self.assertEqual(low_stock[0]["minimum_stock"], 5)

    def test_product_analytics_empty_and_branch_isolation(self):
        with self.app.app_context():
            self.assertEqual(calculate_top_products(branch_id=self.ids["branch_a"]), [])
            self.assertEqual(calculate_category_sales(branch_id=self.ids["branch_a"]), [])
            low_stock = calculate_low_stock(branch_id=self.ids["branch_a"])
            self.assertEqual(len(low_stock), 1)
            other_stock = calculate_low_stock(branch_id=self.ids["branch_b"])
            self.assertEqual(other_stock, [])

    def test_dashboard_date_and_branch_filters(self):
        self.add_records()
        with self.app.app_context():
            db.session.add(Sale(
                branch_id=self.ids["branch_a"], total_amount=Decimal("50.00"),
                discount=Decimal("10.00"), payment_method="cash", created_at=datetime(2026, 6, 15, 10, 0)
            ))
            db.session.commit()

        response = self.client_for("admin").get(
            "/?start_date=2026-06-01&end_date=2026-06-30&branch_id={}".format(self.ids["branch_a"])
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("40.00 сом", html)
        self.assertIn("25.00 сом", html)
        self.assertIn("15.00 сом", html)
        self.assertIn('value="2026-06-01"', html)
        self.assertIn('value="2026-06-30"', html)

    def test_dashboard_combined_filter_and_chart_consistency(self):
        self.add_records()
        with self.app.app_context():
            db.session.add(Sale(
                branch_id=self.ids["branch_a"], total_amount=Decimal("50.00"), discount=Decimal("10.00"),
                payment_method="cash", created_at=datetime(2026, 6, 15, 10, 0)
            ))
            db.session.commit()
        response = self.client_for("admin").get(
            "/?start_date=2026-06-01&end_date=2026-06-30&branch_id={}".format(self.ids["branch_a"])
        )
        html = response.get_data(as_text=True)
        self.assertIn("40.00 сом", html)
        self.assertIn('id="dailySalesData"', html)
        self.assertIn('id="monthlySalesData"', html)

    def test_dashboard_employee_filter_cannot_override_branch(self):
        self.add_records()
        with self.app.app_context():
            db.session.add(Sale(
                branch_id=self.ids["branch_a"], total_amount=Decimal("50.00"), discount=Decimal("10.00"),
                payment_method="cash", created_at=datetime(2026, 6, 15, 10, 0)
            ))
            db.session.commit()
        response = self.client_for("employee_a").get(
            "/?start_date=2026-06-01&end_date=2026-06-30&branch_id={}".format(self.ids["branch_b"])
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("40.00 сом", html)
        self.assertNotIn("270.00 сом", html)

    def test_dashboard_invalid_dates_and_branch_are_safe(self):
        self.add_records()
        response = self.client_for("admin").get("/?start_date=bad&end_date=bad&branch_id=999999")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Некорректный диапазон дат", html)
        self.assertIn('value="bad"', html)

    def test_dashboard_filtered_charts_use_exact_selected_period(self):
        with self.app.app_context():
            db.session.add_all([
                Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("100.00"), discount=Decimal("20.00"), payment_method="cash", created_at=datetime(2026, 1, 10, 10, 0)),
                Sale(branch_id=self.ids["branch_a"], total_amount=Decimal("50.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 3, 10, 10, 0)),
            ])
            db.session.commit()
            daily = calculate_daily_sales_revenue(start_date=date(2026, 3, 1), end_date=date(2026, 3, 31), branch_id=self.ids["branch_a"])
            monthly = calculate_monthly_sales_revenue(start_date=date(2026, 1, 1), end_date=date(2026, 3, 31), branch_id=self.ids["branch_a"])
            self.assertEqual(len(daily["labels"]), 31)
            self.assertEqual(monthly["values"][0], 80.0)
            self.assertEqual(monthly["values"][-1], 50.0)
            self.assertEqual(len(monthly["labels"]), 3)

    def test_unauthenticated_dashboard_is_redirected(self):
        self.assertEqual(self.app.test_client().get("/").status_code, 302)


if __name__ == "__main__":
    unittest.main()
