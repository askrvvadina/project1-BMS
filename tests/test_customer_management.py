import re
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
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
    User,
)
from migrations.phase3_customer_phone_unique import (
    MigrationError,
    migrate as migrate_customer_phone_index,
)


class CustomerManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "customer-tests.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-customer-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
            }
        )

        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Customer Branch A", address="A")
            branch_b = Branch(name="Customer Branch B", address="B")
            category = Category(name="Customer test category")
            admin = User(
                username="customer-admin",
                email="customer-admin@test.invalid",
                password_hash="test",
                role="admin",
            )
            manager = User(
                username="customer-manager",
                email="customer-manager@test.invalid",
                password_hash="test",
                role="manager",
            )
            employee = Employee(
                full_name="Customer employee",
                position="Seller",
                hire_date=date(2025, 1, 1),
                branch_id=1,
                status="active",
            )
            employee_user = User(
                username="customer-employee",
                email="customer-employee@test.invalid",
                password_hash="test",
                role="employee",
            )
            db.session.add_all(
                [
                    branch_a,
                    branch_b,
                    category,
                    admin,
                    manager,
                    employee,
                    employee_user,
                ]
            )
            db.session.flush()
            employee.branch_id = branch_a.id
            employee_user.employee_id = employee.id
            product = Product(
                name="Customer test product",
                sku="CUSTOMER-TEST-1",
                category_id=category.id,
                branch_id=branch_a.id,
                purchase_price=Decimal("2.00"),
                selling_price=Decimal("7.50"),
                stock_quantity=10,
                minimum_stock=0,
            )
            db.session.add(product)
            db.session.flush()
            db.session.add_all(
                [
                    BranchInventory(
                        branch_id=branch_a.id,
                        product_id=product.id,
                        stock_quantity=10,
                        minimum_stock=0,
                    ),
                    BranchInventory(
                        branch_id=branch_b.id,
                        product_id=product.id,
                        stock_quantity=10,
                        minimum_stock=0,
                    ),
                ]
            )
            db.session.commit()
            self.ids = {
                "branch_a": branch_a.id,
                "branch_b": branch_b.id,
                "admin": admin.id,
                "manager": manager.id,
                "employee": employee_user.id,
                "product": product.id,
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

    def create_customer(self, client, phone="5551234567", **fields):
        values = {
            "name": "Test customer",
            "phone": phone,
            "email": "",
        }
        values.update(fields)
        return client.post("/customers/new", data=values, follow_redirects=False)

    def add_customer(self, phone, name="Saved customer", email=None):
        with self.app.app_context():
            customer = Customer(
                name=name,
                phone=phone,
                email=email,
                registration_date=date(2025, 1, 1),
            )
            db.session.add(customer)
            db.session.commit()
            return customer.id

    def add_sale(
        self,
        customer_id,
        branch_id,
        created_at,
        total_amount,
        payment_method="cash",
        discount="0.00",
    ):
        with self.app.app_context():
            sale = Sale(
                customer_id=customer_id,
                branch_id=branch_id,
                total_amount=Decimal(total_amount),
                discount=Decimal(discount),
                payment_method=payment_method,
                created_at=created_at,
                created_by_user_id=self.ids["manager"],
            )
            db.session.add(sale)
            db.session.commit()
            return sale.id

    def test_customer_creation_validation_edit_and_registration_date(self):
        client = self.client_for("admin")
        response = self.create_customer(
            client,
            phone="+1 (555) 123-4567",
            name="New customer",
            email="new.customer@example.test",
        )
        self.assertEqual(response.status_code, 302)
        customer_id = int(re.search(r"/customers/(\d+)$", response.location).group(1))

        with self.app.app_context():
            customer = db.session.get(Customer, customer_id)
            self.assertEqual(customer.name, "New customer")
            self.assertEqual(customer.phone, "+15551234567")
            self.assertEqual(customer.email, "new.customer@example.test")
            self.assertEqual(customer.registration_date, date.today())
            original_registration_date = customer.registration_date

        edit = client.post(
            f"/customers/{customer_id}/edit",
            data={
                "name": "Updated customer",
                "phone": "+1 (555) 123-4567",
                "email": "",
            },
        )
        self.assertEqual(edit.status_code, 302)
        with self.app.app_context():
            customer = db.session.get(Customer, customer_id)
            self.assertEqual(customer.name, "Updated customer")
            self.assertEqual(customer.phone, "+15551234567")
            self.assertIsNone(customer.email)
            self.assertEqual(customer.registration_date, original_registration_date)

    def test_required_phone_name_email_and_phone_validation(self):
        client = self.client_for("manager")
        invalid_forms = [
            {"name": "", "phone": "5551234567", "email": ""},
            {"name": "Name only", "phone": "", "email": ""},
            {"name": "Name", "phone": "letters-only", "email": ""},
            {"name": "Name", "phone": "12345", "email": ""},
            {"name": "Name", "phone": "5551234567", "email": "bad-email"},
            {
                "name": "N" * 121,
                "phone": "5551234567",
                "email": "",
            },
        ]
        for values in invalid_forms:
            with self.subTest(values=values):
                response = client.post("/customers/new", data=values)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"error", response.data)
        with self.app.app_context():
            self.assertEqual(Customer.query.count(), 0)

    def test_phone_duplicate_normalization_and_unchanged_legacy_values(self):
        client = self.client_for("admin")
        self.assertEqual(
            self.create_customer(client, phone="+1 (555) 123-4567").status_code,
            302,
        )
        duplicate = self.create_customer(
            client,
            phone="1-555-123-4567",
            name="Duplicate",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(
            "таким номером телефона уже существует".encode(),
            duplicate.data,
        )
        with self.app.app_context():
            self.assertEqual(Customer.query.count(), 1)

        legacy_id = self.add_customer("(555) 987-6543")
        edit = client.post(
            f"/customers/{legacy_id}/edit",
            data={
                "name": "Renamed legacy",
                "phone": "(555) 987-6543",
                "email": "legacy@example.test",
            },
        )
        self.assertEqual(edit.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                db.session.get(Customer, legacy_id).phone,
                "(555) 987-6543",
            )

    def test_customer_search_by_name_phone_email_and_empty_state(self):
        first_id = self.add_customer(
            "+15550001111",
            name="Alice Example",
            email="alice@example.test",
        )
        self.add_customer(
            "+15550002222",
            name="Bob Example",
            email="bob@example.test",
        )
        client = self.client_for("employee")

        for query, expected_id in (
            ("Alice", first_id),
            ("50001111", first_id),
            ("alice@example", first_id),
            ("no-match", None),
        ):
            response = client.get("/customers", query_string={"q": query})
            self.assertEqual(response.status_code, 200)
            if expected_id is None:
                self.assertIn("не найдены".encode(), response.data)
                self.assertNotIn(b"Alice Example", response.data)
            else:
                self.assertIn(f"/customers/{expected_id}".encode(), response.data)
                self.assertNotIn(b"Bob Example", response.data)

    def test_customer_list_pagination(self):
        for index in range(27):
            self.add_customer(f"+1555{index:07d}", name=f"Customer {index:02d}")
        client = self.client_for("manager")
        first = client.get("/customers")
        self.assertEqual(first.status_code, 200)
        self.assertIn("Страница 1 из 2".encode(), first.data)
        self.assertEqual(first.data.count("Просмотр".encode()), 25)
        second = client.get("/customers?page=2")
        self.assertIn("Страница 2 из 2".encode(), second.data)
        self.assertEqual(second.data.count("Просмотр".encode()), 2)

    def test_customer_purchase_history_saved_totals_order_and_employee_privacy(self):
        customer_id = self.add_customer("+15551234567")
        older = self.add_sale(
            customer_id,
            self.ids["branch_a"],
            datetime(2026, 1, 1, 10, 0),
            "13.45",
        )
        visible = self.add_sale(
            customer_id,
            self.ids["branch_a"],
            datetime(2026, 1, 2, 10, 0),
            "27.89",
            "card",
            "5.00",
        )
        hidden = self.add_sale(
            customer_id,
            self.ids["branch_b"],
            datetime(2026, 1, 3, 10, 0),
            "999.99",
        )

        admin_page = self.client_for("admin").get(
            f"/customers/{customer_id}"
        )
        self.assertEqual(admin_page.status_code, 200)
        admin_html = admin_page.get_data(as_text=True)
        self.assertIn("27.89", admin_html)
        self.assertIn("5.00", admin_html)
        self.assertIn("22.89", admin_html)
        self.assertIn("13.45", admin_html)
        self.assertIn("999.99", admin_html)
        self.assertLess(admin_html.index(f"#{hidden}"), admin_html.index(f"#{visible}"))
        self.assertIn(f"/sales/{visible}/receipt", admin_html)

        employee_page = self.client_for("employee").get(
            f"/customers/{customer_id}"
        )
        employee_html = employee_page.get_data(as_text=True)
        self.assertIn(f"#{visible}".encode(), employee_page.data)
        self.assertIn(f"#{older}".encode(), employee_page.data)
        self.assertNotIn(f"#{hidden}".encode(), employee_page.data)
        self.assertNotIn(b"999.99", employee_page.data)
        self.assertNotIn(f"/sales/{hidden}/receipt".encode(), employee_page.data)

    def test_safe_delete_blocks_linked_sales_and_deletes_unlinked_customer(self):
        client = self.client_for("manager")
        linked_customer_id = self.add_customer("+15551230000")
        sale_id = self.add_sale(
            linked_customer_id,
            self.ids["branch_a"],
            datetime(2026, 2, 1),
            "42.50",
        )
        blocked = client.post(f"/customers/{linked_customer_id}/delete")
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(
            client.get(blocked.location).status_code,
            200,
        )
        with self.app.app_context():
            sale = db.session.get(Sale, sale_id)
            self.assertIsNotNone(db.session.get(Customer, linked_customer_id))
            self.assertEqual(sale.customer_id, linked_customer_id)
            self.assertEqual(sale.total_amount, Decimal("42.50"))

        removable_id = self.add_customer("+15551230001")
        deleted = client.post(f"/customers/{removable_id}/delete")
        self.assertEqual(deleted.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Customer, removable_id))

    def test_employee_can_view_customers_but_cannot_manage_them(self):
        customer_id = self.add_customer("+15551230002")
        employee = self.client_for("employee")
        self.assertEqual(employee.get("/customers").status_code, 200)
        self.assertEqual(
            employee.get(f"/customers/{customer_id}").status_code,
            200,
        )
        restricted = [
            employee.get("/customers/new"),
            employee.post("/customers/new", data={"name": "Denied"}),
            employee.get(f"/customers/{customer_id}/edit"),
            employee.post(f"/customers/{customer_id}/edit"),
            employee.post(f"/customers/{customer_id}/delete"),
        ]
        self.assertTrue(all(response.status_code == 403 for response in restricted))
        with self.app.app_context():
            self.assertEqual(Customer.query.count(), 1)

    def test_manager_can_create_and_edit_customers(self):
        manager = self.client_for("manager")
        created = self.create_customer(
            manager,
            phone="+1 (555) 123-0004",
            name="Manager-created customer",
        )
        self.assertEqual(created.status_code, 302)
        customer_id = int(
            re.search(r"/customers/(\d+)$", created.location).group(1)
        )
        edited = manager.post(
            f"/customers/{customer_id}/edit",
            data={
                "name": "Manager-edited customer",
                "phone": "+1 (555) 123-0004",
                "email": "manager.customer@example.test",
            },
        )
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context():
            customer = db.session.get(Customer, customer_id)
            self.assertEqual(customer.name, "Manager-edited customer")
            self.assertEqual(customer.email, "manager.customer@example.test")

    def test_unauthenticated_customer_routes_are_protected(self):
        client = self.app.test_client()
        for path in (
            "/customers",
            "/customers/new",
            "/customers/1",
            "/customers/1/edit",
        ):
            self.assertEqual(client.get(path).status_code, 302)
        self.assertEqual(
            client.post("/customers/1/delete").status_code,
            302,
        )

    def test_pos_customer_selection_checkout_receipt_and_inventory_are_unchanged(self):
        customer_id = self.add_customer("+15551230003", name="POS customer")
        employee = self.client_for("employee")
        pos_page = employee.get("/sales")
        self.assertEqual(pos_page.status_code, 200)
        self.assertIn(f'value="{customer_id}">POS customer'.encode(), pos_page.data)

        checkout = employee.post(
            "/sales/checkout",
            json={
                "items": [{"product_id": self.ids["product"], "quantity": 2}],
                "payment_method": "card",
                "customer_id": customer_id,
                "branch_id": self.ids["branch_b"],
            },
        )
        self.assertEqual(checkout.status_code, 201)
        sale_id = checkout.json["sale_id"]
        receipt = employee.get(checkout.json["receipt_url"])
        self.assertEqual(receipt.status_code, 200)
        self.assertIn(b"POS customer", receipt.data)
        history = employee.get("/sales/history")
        self.assertEqual(history.status_code, 200)
        self.assertIn(f"#{sale_id}".encode(), history.data)
        customer_history = employee.get(f"/customers/{customer_id}")
        self.assertIn(f"#{sale_id}".encode(), customer_history.data)

        with self.app.app_context():
            sale = db.session.get(Sale, sale_id)
            inventory = BranchInventory.query.filter_by(
                branch_id=self.ids["branch_a"],
                product_id=self.ids["product"],
            ).one()
            transaction = InventoryTransaction.query.filter_by(
                sale_id=sale_id
            ).one()
            self.assertEqual(sale.customer_id, customer_id)
            self.assertEqual(sale.total_amount, Decimal("15.00"))
            self.assertEqual(inventory.stock_quantity, 8)
            self.assertEqual(transaction.transaction_type, "sale_out")
            self.assertEqual(transaction.quantity, 2)

    def test_phone_index_migration_is_additive_and_refuses_duplicates(self):
        safe_db = Path(self.temp_dir.name) / "safe-migration.sqlite"
        connection = sqlite3.connect(safe_db)
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, phone TEXT)")
        connection.executemany(
            "INSERT INTO customers (phone) VALUES (?)",
            [("+15551110000",), (None,)],
        )
        connection.commit()
        connection.close()

        result = migrate_customer_phone_index(safe_db)
        self.assertEqual(result["created_index"], 1)
        connection = sqlite3.connect(safe_db)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM customers"
            ).fetchone()[0],
            2,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO customers (phone) VALUES (?)",
                ("+15551110000",),
            )
        connection.close()

        duplicate_db = Path(self.temp_dir.name) / "duplicate-migration.sqlite"
        connection = sqlite3.connect(duplicate_db)
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, phone TEXT)")
        connection.executemany(
            "INSERT INTO customers (phone) VALUES (?)",
            [("+1 (555) 111-0000",), ("15551110000",)],
        )
        connection.commit()
        connection.close()
        with self.assertRaises(MigrationError):
            migrate_customer_phone_index(duplicate_db)
        connection = sqlite3.connect(duplicate_db)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM customers"
            ).fetchone()[0],
            2,
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
