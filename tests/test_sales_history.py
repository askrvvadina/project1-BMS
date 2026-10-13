import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.models import (
    Branch,
    BranchInventory,
    Category,
    Customer,
    Employee,
    InventoryTransaction,
    Product,
    Sale,
    SaleItem,
    User,
)


class SalesHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "sales-history.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-sales-history-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
            }
        )

        with self.app.app_context():
            db.create_all()
            self.branch_a = Branch(name="Branch A", address="A")
            self.branch_b = Branch(name="Branch B", address="B")
            category = Category(name="Sales history category")
            admin = User(
                username="history-admin",
                email="history-admin@test.invalid",
                password_hash="test",
                role="admin",
            )
            manager = User(
                username="history-manager",
                email="history-manager@test.invalid",
                password_hash="test",
                role="manager",
            )
            employee = Employee(
                full_name="History employee",
                position="Seller",
                hire_date=date(2025, 1, 1),
                branch_id=1,
                status="active",
            )
            employee_user = User(
                username="history-employee",
                email="history-employee@test.invalid",
                password_hash="test",
                role="employee",
            )
            customer = Customer(
                name="History customer",
                registration_date=date(2025, 1, 1),
            )
            db.session.add_all(
                [
                    self.branch_a,
                    self.branch_b,
                    category,
                    admin,
                    manager,
                    employee,
                    employee_user,
                    customer,
                ]
            )
            db.session.flush()
            employee.branch_id = self.branch_b.id
            employee_user.employee_id = employee.id

            self.product_a = Product(
                name="Historical product A",
                sku="HISTORY-A",
                category_id=category.id,
                branch_id=self.branch_b.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("999.99"),
                stock_quantity=10,
                minimum_stock=0,
            )
            self.product_b = Product(
                name="Historical product B",
                sku="HISTORY-B",
                category_id=category.id,
                branch_id=self.branch_b.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("888.88"),
                stock_quantity=10,
                minimum_stock=0,
            )
            db.session.add_all([self.product_a, self.product_b])
            db.session.flush()
            db.session.add_all(
                [
                    BranchInventory(
                        branch_id=self.branch_b.id,
                        product_id=self.product_a.id,
                        stock_quantity=10,
                        minimum_stock=0,
                    ),
                    BranchInventory(
                        branch_id=self.branch_b.id,
                        product_id=self.product_b.id,
                        stock_quantity=10,
                        minimum_stock=0,
                    ),
                ]
            )
            db.session.commit()
            self.ids = {
                "branch_a": self.branch_a.id,
                "branch_b": self.branch_b.id,
                "admin": admin.id,
                "manager": manager.id,
                "employee": employee_user.id,
                "customer": customer.id,
                "product_a": self.product_a.id,
                "product_b": self.product_b.id,
            }

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

    def make_sale(
        self,
        branch_id,
        created_at,
        payment_method="cash",
        total="10.00",
        customer_id=None,
        items=None,
    ):
        with self.app.app_context():
            sale = Sale(
                branch_id=branch_id,
                customer_id=customer_id,
                total_amount=Decimal(total),
                discount=Decimal("0.00"),
                payment_method=payment_method,
                created_at=created_at,
                created_by_user_id=self.ids["manager"],
            )
            db.session.add(sale)
            db.session.flush()
            for product_id, quantity, unit_price in items or [
                (self.ids["product_a"], 1, "10.00")
            ]:
                db.session.add(
                    SaleItem(
                        sale_id=sale.id,
                        product_id=product_id,
                        quantity=quantity,
                        unit_price=Decimal(unit_price),
                    )
                )
            db.session.commit()
            return sale.id

    def test_receipt_uses_historical_prices_and_saved_totals(self):
        sale_id = self.make_sale(
            self.ids["branch_b"],
            datetime(2026, 5, 4, 15, 30, 12),
            payment_method="card",
            total="32.50",
            customer_id=self.ids["customer"],
            items=[
                (self.ids["product_a"], 2, "10.25"),
                (self.ids["product_b"], 2, "7.25"),
            ],
        )
        with self.app.app_context():
            db.session.get(Sale, sale_id).discount = Decimal("2.50")
            db.session.commit()

        response = self.client_for("admin").get(f"/sales/{sale_id}/receipt")
        self.assertEqual(response.status_code, 200)
        receipt = response.get_data(as_text=True)
        for expected in (
            f"Продажа №{sale_id}",
            "04.05.2026 15:30:12",
            "Branch B",
            "history-manager",
            "History customer",
            "Карта",
            "Historical product A",
            "HISTORY-A",
            "Historical product B",
            "HISTORY-B",
            "20.50",
            "14.50",
            "32.50",
            "2.50",
            "window.print()",
        ):
            self.assertIn(expected, receipt)
        self.assertNotIn("999.99", receipt)
        self.assertNotIn("888.88", receipt)

    def test_receipt_without_customer_and_cash_payment(self):
        sale_id = self.make_sale(
            self.ids["branch_a"],
            datetime(2026, 5, 5, 9, 0),
        )
        receipt = self.client_for("admin").get(
            f"/sales/{sale_id}/receipt"
        ).get_data(as_text=True)
        self.assertIn("Без клиента", receipt)
        self.assertIn("Наличные", receipt)

    def test_history_is_newest_first_and_paginated(self):
        base_time = datetime(2026, 6, 1, 10, 0)
        sale_ids = [
            self.make_sale(
                self.ids["branch_a"] if index % 2 else self.ids["branch_b"],
                base_time + timedelta(minutes=index),
            )
            for index in range(23)
        ]

        client = self.client_for("admin")
        first_page = client.get("/sales/history?branch_id=all")
        self.assertEqual(first_page.status_code, 200)
        first_html = first_page.get_data(as_text=True)
        first_page_ids = [
            int(value)
            for value in re.findall(r"<td>#(\d+)</td>", first_html)
        ]
        self.assertEqual(len(first_page_ids), 20)
        self.assertEqual(first_page_ids[0], sale_ids[-1])
        self.assertIn("Страница 1 из 2", first_html)

        second_page = client.get("/sales/history?branch_id=all&page=2")
        second_ids = [
            int(value)
            for value in re.findall(
                r"<td>#(\d+)</td>", second_page.get_data(as_text=True)
            )
        ]
        self.assertEqual(second_ids, sale_ids[:3][::-1])

    def test_branch_filter_and_manager_preference(self):
        sale_a = self.make_sale(
            self.ids["branch_a"], datetime(2026, 6, 1, 10, 0)
        )
        sale_b = self.make_sale(
            self.ids["branch_b"], datetime(2026, 6, 1, 11, 0)
        )
        client = self.client_for("admin")
        branch_a_history = client.get(
            f"/sales/history?branch_id={self.ids['branch_a']}"
        ).get_data(as_text=True)
        self.assertIn(f"#{sale_a}", branch_a_history)
        self.assertNotIn(f"#{sale_b}", branch_a_history)

        all_history = client.get("/sales/history?branch_id=all").get_data(
            as_text=True
        )
        self.assertIn(f"#{sale_a}", all_history)
        self.assertIn(f"#{sale_b}", all_history)

        manager = self.client_for("manager")
        manager.get(f"/sales/history?branch_id={self.ids['branch_a']}")
        with manager.session_transaction() as browser_session:
            self.assertEqual(
                browser_session["management_branch_id"],
                self.ids["branch_a"],
            )
        remembered = manager.get("/sales/history").get_data(as_text=True)
        self.assertIn(f"#{sale_a}", remembered)
        self.assertNotIn(f"#{sale_b}", remembered)
        self.assertEqual(
            manager.get(f"/sales/{sale_b}/receipt").status_code,
            200,
        )

    def test_payment_and_inclusive_date_range_filters(self):
        matching_sale = self.make_sale(
            self.ids["branch_a"],
            datetime(2026, 5, 11, 23, 59, 59),
            payment_method="card",
        )
        self.make_sale(
            self.ids["branch_a"],
            datetime(2026, 5, 10, 12, 0),
            payment_method="card",
        )
        self.make_sale(
            self.ids["branch_a"],
            datetime(2026, 5, 11, 12, 0),
            payment_method="cash",
        )
        self.make_sale(
            self.ids["branch_b"],
            datetime(2026, 5, 11, 12, 0),
            payment_method="card",
        )

        response = self.client_for("admin").get(
            "/sales/history"
            f"?branch_id={self.ids['branch_a']}"
            "&payment_method=card&date_from=2026-05-11&date_to=2026-05-11"
        )
        history = response.get_data(as_text=True)
        self.assertIn(f"#{matching_sale}", history)
        self.assertEqual(
            re.findall(r"<td>#(\d+)</td>", history),
            [str(matching_sale)],
        )

    def test_employee_history_and_receipt_are_limited_to_assigned_branch(self):
        allowed_sale = self.make_sale(
            self.ids["branch_b"], datetime(2026, 6, 1, 10, 0)
        )
        forbidden_sale = self.make_sale(
            self.ids["branch_a"], datetime(2026, 6, 1, 11, 0)
        )
        employee = self.client_for("employee")
        history = employee.get(
            f"/sales/history?branch_id={self.ids['branch_a']}"
        )
        self.assertEqual(history.status_code, 200)
        self.assertIn(f"#{allowed_sale}".encode(), history.data)
        self.assertNotIn(f"#{forbidden_sale}".encode(), history.data)
        self.assertEqual(
            employee.get(f"/sales/{allowed_sale}/receipt").status_code,
            200,
        )
        self.assertEqual(
            employee.get(f"/sales/{forbidden_sale}/receipt").status_code,
            403,
        )

    def test_unauthenticated_sales_pages_are_protected(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/sales/history").status_code, 302)
        self.assertEqual(client.get("/sales/1/receipt").status_code, 302)

    def test_checkout_returns_receipt_link_and_keeps_inventory_deduction(self):
        employee = self.client_for("employee")
        response = employee.post(
            "/sales/checkout",
            json={
                "items": [{"product_id": self.ids["product_a"], "quantity": 2}],
                "payment_method": "cash",
                "branch_id": self.ids["branch_a"],
            },
        )
        self.assertEqual(response.status_code, 201)
        sale_id = response.json["sale_id"]
        self.assertEqual(
            response.json["receipt_url"],
            f"/sales/{sale_id}/receipt",
        )
        self.assertEqual(employee.get(response.json["receipt_url"]).status_code, 200)
        with self.app.app_context():
            inventory = BranchInventory.query.filter_by(
                branch_id=self.ids["branch_b"],
                product_id=self.ids["product_a"],
            ).one()
            self.assertEqual(inventory.stock_quantity, 8)
            transaction = InventoryTransaction.query.filter_by(
                sale_id=sale_id
            ).one()
            self.assertEqual(transaction.transaction_type, "sale_out")
            self.assertEqual(transaction.quantity, 2)


if __name__ == "__main__":
    unittest.main()
