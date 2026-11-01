import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.models import (
    Branch,
    Employee,
    Expense,
    ExpenseCategory,
    Sale,
    User,
)
from migrations.phase8_expense_management import migrate as migrate_expenses
from app.financial import calculate_financial_summary


class ExpenseManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "expense-tests.sqlite"
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "isolated-expense-tests",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
        })
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Expense Branch A", address="A")
            branch_b = Branch(name="Expense Branch B", address="B")
            db.session.add_all([branch_a, branch_b])
            db.session.flush()
            admin = User(username="expense-admin", email="expense-admin@test.invalid", password_hash="test", role="admin")
            manager = User(username="expense-manager", email="expense-manager@test.invalid", password_hash="test", role="manager")
            employee = Employee(full_name="Expense Employee", position="Seller", hire_date=date(2026, 1, 1), branch_id=branch_a.id, status="active")
            db.session.add_all([admin, manager, employee])
            db.session.flush()
            employee_user = User(username="expense-employee", email="expense-employee@test.invalid", password_hash="test", role="employee", employee_id=employee.id)
            db.session.add(employee_user)
            db.session.commit()
            self.ids = {"admin": admin.id, "manager": manager.id, "employee": employee_user.id, "branch_a": branch_a.id, "branch_b": branch_b.id}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def client_for(self, role):
        client = self.app.test_client()
        with client.session_transaction() as browser_session:
            browser_session["user_id"] = self.ids[role]
            browser_session["role"] = role
        return client

    def category(self, name="Office"):
        with self.app.app_context():
            category = ExpenseCategory(name=name)
            db.session.add(category)
            db.session.commit()
            return category.id

    def expense(self, category_id=None, branch_id=None):
        with self.app.app_context():
            if category_id is None:
                category = ExpenseCategory.query.first()
                if category is None:
                    category = ExpenseCategory(name="Default expense category")
                    db.session.add(category)
                    db.session.flush()
                category_id = category.id
            else:
                category = db.session.get(ExpenseCategory, category_id)
            expense = Expense(
                date=date(2026, 3, 4),
                amount=Decimal("125.50"),
                description="Existing expense",
                category_id=category.id,
                category=category.name,
                branch_id=branch_id or self.ids["branch_a"],
            )
            db.session.add(expense)
            db.session.commit()
            return expense.id

    def test_expense_crud(self):
        client = self.client_for("admin")
        category_id = self.category("Utilities")
        created = client.post("/expenses/new", data={
            "date": "2026-03-10",
            "amount": "245.75",
            "description": "Electricity bill",
            "category_id": str(category_id),
            "branch_id": str(self.ids["branch_a"]),
        })
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            expense = Expense.query.filter_by(description="Electricity bill").one()
            expense_id = expense.id
            self.assertEqual(expense.amount, Decimal("245.75"))
            self.assertEqual(expense.category_id, category_id)
            self.assertEqual(expense.branch_id, self.ids["branch_a"])
        detail = client.get(f"/expenses/{expense_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Electricity bill", detail.data)
        updated = client.post(f"/expenses/{expense_id}/edit", data={
            "date": "2026-03-11",
            "amount": "300.00",
            "description": "Updated electricity bill",
            "category_id": str(category_id),
            "branch_id": str(self.ids["branch_b"]),
        })
        self.assertEqual(updated.status_code, 302)
        with self.app.app_context():
            expense = db.session.get(Expense, expense_id)
            self.assertEqual(expense.description, "Updated electricity bill")
            self.assertEqual(expense.branch_id, self.ids["branch_b"])
            self.assertEqual(expense.amount, Decimal("300.00"))
        deleted = client.post(f"/expenses/{expense_id}/delete")
        self.assertEqual(deleted.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Expense, expense_id))

    def test_expense_validation_rejects_invalid_fields(self):
        client = self.client_for("manager")
        category_id = self.category()
        invalid_values = ["0", "-1", "not-a-number", ""]
        for amount in invalid_values:
            with self.subTest(amount=amount):
                response = client.post("/expenses/new", data={
                    "date": "2026-03-10",
                    "amount": amount,
                    "description": "Invalid amount",
                    "category_id": str(category_id),
                    "branch_id": str(self.ids["branch_a"]),
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn("больше нуля".encode(), response.data)
        response = client.post("/expenses/new", data={
            "date": "2026-02-30",
            "amount": "10",
            "description": "",
            "category_id": str(category_id),
            "branch_id": "99999",
        })
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(Expense.query.count(), 0)

    def create_filter_expenses(self):
        with self.app.app_context():
            category_a = ExpenseCategory(name="Filter Office")
            category_b = ExpenseCategory(name="Filter Travel")
            db.session.add_all([category_a, category_b])
            db.session.flush()
            records = [
                Expense(date=date(2026, 4, 1), amount=Decimal("100.25"), description="Branch A office 1", category_id=category_a.id, category=category_a.name, branch_id=self.ids["branch_a"]),
                Expense(date=date(2026, 4, 15), amount=Decimal("200.50"), description="Branch A travel", category_id=category_b.id, category=category_b.name, branch_id=self.ids["branch_a"]),
                Expense(date=date(2026, 5, 1), amount=Decimal("300.75"), description="Branch B office 2", category_id=category_a.id, category=category_a.name, branch_id=self.ids["branch_b"]),
                Expense(date=date(2026, 5, 15), amount=Decimal("400.00"), description="Branch B travel", category_id=category_b.id, category=category_b.name, branch_id=self.ids["branch_b"]),
            ]
            db.session.add_all(records)
            db.session.commit()
            return category_a.id, category_b.id

    def create_financial_records(self):
        with self.app.app_context():
            category = ExpenseCategory(name="Financial Office")
            db.session.add(category)
            db.session.flush()
            sales = [
                Sale(branch_id=self.ids["branch_a"], customer_id=None, total_amount=Decimal("100.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 6, 1, 10, 0)),
                Sale(branch_id=self.ids["branch_a"], customer_id=None, total_amount=Decimal("200.00"), discount=Decimal("40.00"), payment_method="card", created_at=datetime(2026, 6, 15, 10, 0)),
                Sale(branch_id=self.ids["branch_b"], customer_id=None, total_amount=Decimal("300.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 6, 20, 10, 0)),
                Sale(branch_id=self.ids["branch_a"], customer_id=None, total_amount=Decimal("999.00"), discount=Decimal("0.00"), payment_method="cash", created_at=datetime(2026, 7, 1, 10, 0)),
            ]
            db.session.add_all(sales)
            db.session.add_all([
                Expense(date=date(2026, 6, 1), amount=Decimal("50.25"), description="June A expense", category_id=category.id, category=category.name, branch_id=self.ids["branch_a"]),
                Expense(date=date(2026, 6, 10), amount=Decimal("75.75"), description="June A expense 2", category_id=category.id, category=category.name, branch_id=self.ids["branch_a"]),
                Expense(date=date(2026, 6, 20), amount=Decimal("100.00"), description="June B expense", category_id=category.id, category=category.name, branch_id=self.ids["branch_b"]),
                Expense(date=date(2026, 7, 1), amount=Decimal("900.00"), description="July expense", category_id=category.id, category=category.name, branch_id=self.ids["branch_a"]),
            ])
            db.session.commit()

    def test_financial_calculations_loyalty_and_branches(self):
        self.create_financial_records()
        with self.app.app_context():
            june = calculate_financial_summary(date(2026, 6, 1), date(2026, 6, 30), None)
            self.assertEqual(june["gross_sales"], Decimal("600.00"))
            self.assertEqual(june["loyalty_discounts"], Decimal("40.00"))
            self.assertEqual(june["actual_paid_sales"], Decimal("560.00"))
            self.assertEqual(june["total_expenses"], Decimal("226.00"))
            self.assertEqual(june["operational_difference"], Decimal("334.00"))

            branch_a = calculate_financial_summary(date(2026, 6, 1), date(2026, 6, 30), self.ids["branch_a"])
            self.assertEqual(branch_a["gross_sales"], Decimal("300.00"))
            self.assertEqual(branch_a["actual_paid_sales"], Decimal("260.00"))
            self.assertEqual(branch_a["total_expenses"], Decimal("126.00"))

    def test_financial_empty_period_and_expenses_page_branch_permissions(self):
        self.create_financial_records()
        with self.app.app_context():
            empty = calculate_financial_summary(date(2030, 1, 1), date(2030, 1, 31), None)
            self.assertEqual(empty["gross_sales"], Decimal("0.00"))
            self.assertEqual(empty["operational_difference"], Decimal("0.00"))

        employee = self.client_for("employee")
        page = employee.get("/expenses?branch_id=999999&date_from=2026-06-01&date_to=2026-06-30")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"260.00", page.data)
        self.assertIn(b"126.00", page.data)
        self.assertIn(b"134.00", page.data)
        self.assertNotIn(b"300.00", page.data.split(b"Branch B")[-1] if b"Branch B" in page.data else b"")

    def test_financial_page_invalid_range(self):
        self.create_financial_records()
        page = self.client_for("admin").get("/expenses?date_from=2026-06-30&date_to=2026-06-01")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Начальная дата не может быть позже конечной.".encode(), page.data)
        self.assertIn(b"1599.00", page.data)

    def test_expense_individual_filters_and_totals(self):
        category_a, category_b = self.create_filter_expenses()
        client = self.client_for("admin")

        by_date = client.get("/expenses?date_from=2026-05-01&date_to=2026-05-31")
        self.assertEqual(by_date.status_code, 200)
        self.assertIn(b"700.75", by_date.data)
        self.assertIn("2 записи".encode(), by_date.data)

        by_branch = client.get(f"/expenses?branch_id={self.ids['branch_a']}")
        self.assertIn(b"300.75", by_branch.data)

        by_category = client.get(f"/expenses?category_id={category_a}")
        self.assertIn(b"401.00", by_category.data)

        response = client.get(f"/expenses?category_id={category_b}")
        self.assertIn(b"600.50", response.data)

    def test_expense_combined_filters_empty_results_and_invalid_range(self):
        category_a, category_b = self.create_filter_expenses()
        client = self.client_for("admin")

        combined = client.get(
            f"/expenses?date_from=2026-04-01&date_to=2026-04-30"
            f"&branch_id={self.ids['branch_a']}&category_id={category_b}"
        )
        self.assertEqual(combined.status_code, 200)
        self.assertIn(b"200.50", combined.data)
        self.assertIn(b"Branch A travel", combined.data)
        self.assertNotIn(b"Branch A office 1", combined.data)

        empty = client.get("/expenses?date_from=2027-01-01&date_to=2027-01-31")
        self.assertIn("Расходы по выбранным условиям не найдены.".encode(), empty.data)

        invalid = client.get("/expenses?date_from=2026-06-30&date_to=2026-06-01")
        self.assertIn("Начальная дата не может быть позже конечной.".encode(), invalid.data)

    def test_expense_filter_branch_permissions_cannot_be_bypassed(self):
        category_a, _ = self.create_filter_expenses()
        employee = self.client_for("employee")
        list_page = employee.get(f"/expenses?branch_id={self.ids['branch_b']}&category_id={category_a}")
        self.assertEqual(list_page.status_code, 200)
        self.assertNotIn(b"Branch B office 2", list_page.data)
        self.assertIn(b"Branch A office 1", list_page.data)
        self.assertIn(b"100.25", list_page.data)

        own_detail = employee.get(f"/expenses?branch_id=all")
        self.assertEqual(own_detail.status_code, 200)

    def test_expense_filter_reset_and_invalid_values(self):
        self.create_filter_expenses()
        client = self.client_for("manager")
        reset_page = client.get("/expenses")
        self.assertIn(b"1001.50", reset_page.data)
        self.assertIn("4 записи".encode(), reset_page.data)
        malformed_date = client.get("/expenses?date_from=not-a-date")
        self.assertIn("Укажите корректную начальную дату.".encode(), malformed_date.data)
        unknown_branch = client.get("/expenses?branch_id=999999")
        self.assertIn("Выбранный филиал не найден".encode(), unknown_branch.data)

    def test_category_management_and_in_use_protection(self):
        client = self.client_for("admin")
        created = client.post("/expense-categories/create", data={"name": "Transport"})
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            category = ExpenseCategory.query.filter_by(name="Transport").one()
            category_id = category.id
            category_used = Expense(category_id=category.id, category=category.name, branch_id=self.ids["branch_a"], date=date(2026, 3, 1), amount=Decimal("10.00"), description="Taxi")
            db.session.add(category_used)
            db.session.commit()
        protected = client.post(f"/expense-categories/{category_id}/delete")
        self.assertEqual(protected.status_code, 302)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(ExpenseCategory, category_id))
        edited = client.post(f"/expense-categories/{category_id}/edit", data={"name": "Travel"})
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context():
            category = db.session.get(ExpenseCategory, category_id)
            self.assertEqual(category.name, "Travel")
            self.assertEqual(db.session.get(Expense, category.expenses[0].id).category, "Travel")

    def test_employee_is_limited_to_assigned_branch_and_cannot_manage(self):
        own_expense = self.expense(branch_id=self.ids["branch_a"])
        other_expense = self.expense(branch_id=self.ids["branch_b"])
        employee = self.client_for("employee")
        own_page = employee.get(f"/expenses/{own_expense}")
        other_page = employee.get(f"/expenses/{other_expense}")
        self.assertEqual(own_page.status_code, 200)
        self.assertEqual(other_page.status_code, 403)
        self.assertEqual(employee.get("/expenses/new").status_code, 403)
        self.assertEqual(employee.post(f"/expenses/{own_expense}/delete").status_code, 403)
        list_page = employee.get("/expenses")
        self.assertIn(f"#{own_expense}".encode(), list_page.data)
        self.assertNotIn(f"#{other_expense}".encode(), list_page.data)
        self.assertEqual(employee.get("/expense-categories").status_code, 403)

    def test_expense_migration_preserves_legacy_records(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-expenses.sqlite"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT, address TEXT);
                INSERT INTO branches (id, name, address) VALUES (1, 'Legacy Branch', 'Address');
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY,
                    branch_id INTEGER NOT NULL,
                    category VARCHAR(100) NOT NULL,
                    amount NUMERIC(10,2) NOT NULL,
                    description VARCHAR(255),
                    date DATE NOT NULL
                );
                INSERT INTO expenses VALUES (1, 1, 'Office', 25.50, NULL, '2026-01-01');
                INSERT INTO expenses VALUES (2, 1, 'Office', 30.00, 'Paper', '2026-01-02');
                """
            )
            connection.commit()
            connection.close()

            result = migrate_expenses(database_path)
            connection = sqlite3.connect(database_path)
            rows = connection.execute(
                "SELECT id, category_id, description FROM expenses ORDER BY id"
            ).fetchall()
            category_count = connection.execute(
                "SELECT COUNT(*) FROM expense_categories WHERE name = 'Office'"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(result["expenses_preserved"], 2)
            self.assertEqual(category_count, 1)
            self.assertEqual(rows[1][2], "Paper")
            self.assertEqual(rows[0][2], "")
            self.assertEqual(rows[0][1], rows[1][1])

    def test_manager_has_expense_management_but_not_admin_only_pages(self):
        manager = self.client_for("manager")
        category_id = self.category("Advertising")
        response = manager.post("/expenses/new", data={
            "date": "2026-03-12",
            "amount": "50",
            "description": "Poster printing",
            "category_id": str(category_id),
            "branch_id": str(self.ids["branch_b"]),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(manager.get("/expense-categories").status_code, 200)


if __name__ == "__main__":
    unittest.main()
