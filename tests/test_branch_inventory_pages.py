import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app import create_app, db
from app.models import (
    Branch,
    BranchInventory,
    Category,
    Employee,
    Product,
    User,
)


class BranchInventoryPageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "branch-pages.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-branch-page-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
            }
        )

        with self.app.app_context():
            db.create_all()
            self.hq = Branch(name="HQ", address="HQ address")
            self.main = Branch(name="Main Branch", address="Main address")
            category = Category(name="Page test category")
            admin = User(
                username="page-admin",
                email="page-admin@test.invalid",
                password_hash="test",
                role="admin",
            )
            employee = Employee(
                full_name="Page test employee",
                position="Seller",
                hire_date=date(2025, 1, 1),
                branch_id=1,
                status="active",
            )
            employee_user = User(
                username="page-employee",
                email="page-employee@test.invalid",
                password_hash="test",
                role="employee",
            )
            db.session.add_all([self.hq, self.main, category, admin])
            db.session.flush()
            employee.branch_id = self.hq.id
            db.session.add_all([employee, employee_user])
            db.session.flush()
            employee_user.employee_id = employee.id

            self.product = Product(
                name="Office Mouse",
                sku="MOUSE-PAGE-1",
                category_id=category.id,
                branch_id=self.hq.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("5.00"),
                stock_quantity=999,
                minimum_stock=888,
            )
            db.session.add(self.product)
            db.session.flush()
            db.session.add_all(
                [
                    BranchInventory(
                        branch_id=self.hq.id,
                        product_id=self.product.id,
                        stock_quantity=8,
                        minimum_stock=3,
                    ),
                    BranchInventory(
                        branch_id=self.main.id,
                        product_id=self.product.id,
                        stock_quantity=4,
                        minimum_stock=2,
                    ),
                ]
            )
            db.session.commit()
            self.ids = {
                "hq": self.hq.id,
                "main": self.main.id,
                "admin": admin.id,
                "employee": employee_user.id,
                "product": self.product.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def client_for(self, role):
        client = self.app.test_client()
        user_id = self.ids["employee"] if role == "employee" else self.ids["admin"]
        with client.session_transaction() as browser_session:
            browser_session["user_id"] = user_id
            browser_session["role"] = role
        return client

    def test_branch_preference_is_shared_and_products_use_branch_inventory(self):
        client = self.client_for("admin")
        inventory = client.get(f"/inventory?branch_id={self.ids['main']}")
        self.assertIn(b"Main Branch", inventory.data)

        self.assertEqual(client.get("/notifications").status_code, 200)
        self.assertIn(b"Main Branch", client.get("/inventory").data)
        self.assertIn(
            f'data-branch-id="{self.ids["main"]}"'.encode(),
            client.get("/sales").data,
        )
        main_products = client.get("/products")
        self.assertIn(b"Main Branch", main_products.data)
        self.assertIn(b"4 \xd1\x88\xd1\x82.", main_products.data)
        self.assertNotIn(b"999", main_products.data)

        hq_sales = client.get(f"/sales?branch_id={self.ids['hq']}")
        self.assertIn(f'data-branch-id="{self.ids["hq"]}"'.encode(), hq_sales.data)
        hq_products = client.get("/products")
        self.assertIn(b"HQ", hq_products.data)
        self.assertIn(b"8 \xd1\x88\xd1\x82.", hq_products.data)
        self.assertNotIn(b"999", hq_products.data)

        client.get(f"/products?branch_id={self.ids['main']}")
        self.assertIn(b"Main Branch", client.get("/inventory").data)

    def test_invalid_branch_parameters_preserve_valid_preference(self):
        client = self.client_for("admin")
        client.get(f"/inventory?branch_id={self.ids['main']}")

        for invalid_branch in ("not-an-id", "999999"):
            with self.subTest(branch_id=invalid_branch):
                response = client.get(f"/sales?branch_id={invalid_branch}")
                self.assertIn(
                    f'data-branch-id="{self.ids["main"]}"'.encode(),
                    response.data,
                )
                self.assertIn(b"Main Branch", client.get("/products").data)
                with client.session_transaction() as browser_session:
                    self.assertEqual(
                        browser_session["management_branch_id"],
                        self.ids["main"],
                    )

    def test_deleted_stored_branch_is_removed_and_default_is_used(self):
        client = self.client_for("admin")
        client.get(f"/inventory?branch_id={self.ids['main']}")
        with self.app.app_context():
            BranchInventory.query.filter_by(
                branch_id=self.ids["main"]
            ).delete()
            db.session.delete(db.session.get(Branch, self.ids["main"]))
            db.session.commit()

        response = client.get("/inventory")
        self.assertIn(b"HQ", response.data)
        with client.session_transaction() as browser_session:
            self.assertNotIn("management_branch_id", browser_session)

    def test_employee_branch_is_server_derived_not_management_preference(self):
        client = self.client_for("employee")
        with client.session_transaction() as browser_session:
            browser_session["management_branch_id"] = self.ids["main"]

        sales = client.get(f"/sales?branch_id={self.ids['main']}")
        self.assertIn(b"HQ", sales.data)
        self.assertNotIn(b"Main Branch", sales.data)
        products = client.get(f"/products?branch_id={self.ids['main']}")
        self.assertIn(b"HQ", products.data)
        self.assertIn(b"8 \xd1\x88\xd1\x82.", products.data)
        self.assertNotIn(b"Main Branch", products.data)
        checkout = client.post(
            "/sales/checkout",
            json={
                "items": [{"product_id": self.ids["product"], "quantity": 1}],
                "payment_method": "cash",
                "branch_id": self.ids["main"],
            },
        )
        self.assertEqual(checkout.status_code, 201)
        self.assertEqual(checkout.json["branch_id"], self.ids["hq"])
        with client.session_transaction() as browser_session:
            self.assertEqual(
                browser_session["management_branch_id"],
                self.ids["main"],
            )

    def test_product_detail_uses_per_branch_inventory_and_hides_legacy_fields(self):
        client = self.client_for("admin")
        response = client.get(f"/products/{self.ids['product']}")

        self.assertIn(b"HQ", response.data)
        self.assertIn(b"Main Branch", response.data)
        self.assertIn(b"8 \xd1\x88\xd1\x82.", response.data)
        self.assertIn(b"4 \xd1\x88\xd1\x82.", response.data)
        self.assertIn(b"3 \xd1\x88\xd1\x82.", response.data)
        self.assertIn(b"2 \xd1\x88\xd1\x82.", response.data)
        self.assertNotIn(b"999", response.data)
        self.assertNotIn(b"888", response.data)

    def test_product_forms_do_not_offer_legacy_stock_or_branch_editing(self):
        client = self.client_for("admin")
        create_response = client.get("/products/create")
        edit_response = client.get(f"/products/{self.ids['product']}/edit")

        for response in (create_response, edit_response):
            self.assertNotIn(b'name="stock_quantity"', response.data)
            self.assertNotIn(b'name="minimum_stock"', response.data)
            self.assertNotIn(b'name="branch_id"', response.data)
            self.assertNotIn("Устаревший".encode("utf-8"), response.data)


if __name__ == "__main__":
    unittest.main()
