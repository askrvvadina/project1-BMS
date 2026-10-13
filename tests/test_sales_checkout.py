import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread

from sqlalchemy import event
from sqlalchemy.orm import Session

from app import create_app, db
from app.models import (
    Branch,
    BranchInventory,
    Category,
    Customer,
    Employee,
    InventoryTransaction,
    Notification,
    Product,
    Sale,
    SaleItem,
    User,
)


class SalesCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "checkout-tests.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-checkout-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
            }
        )

        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Branch A", address="A")
            branch_b = Branch(name="Branch B", address="B")
            db.session.add_all([branch_a, branch_b])
            db.session.flush()

            category = Category(name="Checkout category")
            admin = User(
                username="admin",
                email="admin@test.invalid",
                password_hash="test",
                role="admin",
            )
            manager = User(
                username="manager",
                email="manager@test.invalid",
                password_hash="test",
                role="manager",
            )
            employee = Employee(
                full_name="POS employee",
                position="Seller",
                hire_date=date(2025, 1, 1),
                branch_id=branch_b.id,
                status="active",
            )
            employee_user = User(
                username="employee",
                email="employee@test.invalid",
                password_hash="test",
                role="employee",
            )
            unlinked_user = User(
                username="unlinked",
                email="unlinked@test.invalid",
                password_hash="test",
                role="employee",
            )
            customer = Customer(
                name="POS customer",
                registration_date=date(2025, 1, 1),
            )
            db.session.add_all(
                [
                    category,
                    admin,
                    manager,
                    employee,
                    employee_user,
                    unlinked_user,
                    customer,
                ]
            )
            db.session.flush()
            employee_user.employee_id = employee.id

            phone = Product(
                name="Phone",
                sku="PHONE-1",
                category_id=category.id,
                branch_id=branch_b.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("12.34"),
                stock_quantity=900,
                minimum_stock=900,
            )
            case = Product(
                name="Case",
                sku="CASE-2",
                category_id=category.id,
                branch_id=branch_a.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("2.50"),
                stock_quantity=0,
                minimum_stock=999,
            )
            adapter = Product(
                name="Adapter",
                sku="ADAPTER-3",
                category_id=category.id,
                branch_id=branch_b.id,
                purchase_price=Decimal("1.00"),
                selling_price=Decimal("1.23"),
                stock_quantity=0,
                minimum_stock=999,
            )
            db.session.add_all([phone, case, adapter])
            db.session.flush()

            phone_inventory = BranchInventory(
                branch_id=branch_b.id,
                product_id=phone.id,
                stock_quantity=2,
                minimum_stock=0,
            )
            case_a_inventory = BranchInventory(
                branch_id=branch_a.id,
                product_id=case.id,
                stock_quantity=4,
                minimum_stock=4,
            )
            case_b_inventory = BranchInventory(
                branch_id=branch_b.id,
                product_id=case.id,
                stock_quantity=10,
                minimum_stock=0,
            )
            adapter_a_inventory = BranchInventory(
                branch_id=branch_a.id,
                product_id=adapter.id,
                stock_quantity=3,
                minimum_stock=0,
            )
            db.session.add_all(
                [
                    phone_inventory,
                    case_a_inventory,
                    case_b_inventory,
                    adapter_a_inventory,
                ]
            )
            db.session.commit()
            self.ids = {
                "branch_a": branch_a.id,
                "branch_b": branch_b.id,
                "admin": admin.id,
                "manager": manager.id,
                "employee": employee_user.id,
                "unlinked": unlinked_user.id,
                "customer": customer.id,
                "phone": phone.id,
                "case": case.id,
                "adapter": adapter.id,
                "phone_inventory": phone_inventory.id,
                "case_a_inventory": case_a_inventory.id,
                "case_b_inventory": case_b_inventory.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def client_for(self, username):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.ids[username]
            session["role"] = "employee" if username in {"employee", "unlinked"} else username
        return client

    def submit(self, client, items, **extra):
        payload = {"items": items, "payment_method": "cash"}
        payload.update(extra)
        return client.post("/sales/checkout", json=payload)

    def test_employee_branch_and_admin_manager_sales(self):
        employee_client = self.client_for("employee")
        employee_sale = self.submit(
            employee_client,
            [{"product_id": self.ids["phone"], "quantity": 1}],
            branch_id=self.ids["branch_a"],
        )
        self.assertEqual(employee_sale.status_code, 201)
        self.assertEqual(employee_sale.json["branch_id"], self.ids["branch_b"])

        manager_client = self.client_for("manager")
        manager_sale = self.submit(
            manager_client,
            [
                {
                    "product_id": self.ids["case"],
                    "quantity": 2,
                    "unit_price": "0.01",
                    "subtotal": 0,
                },
                {"product_id": self.ids["adapter"], "quantity": 1},
            ],
            branch_id=self.ids["branch_a"],
            customer_id=self.ids["customer"],
            payment_method="card",
            total=0,
        )
        self.assertEqual(manager_sale.status_code, 201)
        self.assertEqual(manager_sale.json["total_amount"], "6.23")

        with self.app.app_context():
            sale = db.session.get(Sale, manager_sale.json["sale_id"])
            self.assertEqual(sale.created_by_user_id, self.ids["manager"])
            self.assertEqual(sale.customer_id, self.ids["customer"])
            self.assertEqual(sale.discount, Decimal("0.00"))
            self.assertEqual(sale.payment_method, "card")

            items = SaleItem.query.filter_by(sale_id=sale.id).all()
            self.assertEqual(
                {
                    (item.product_id, item.quantity, item.unit_price)
                    for item in items
                },
                {
                    (self.ids["case"], 2, Decimal("2.50")),
                    (self.ids["adapter"], 1, Decimal("1.23")),
                },
            )
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
                2,
            )
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_b_inventory"],
                ).stock_quantity,
                10,
            )
            movements = InventoryTransaction.query.filter_by(
                sale_id=sale.id
            ).all()
            self.assertEqual(len(movements), 2)
            self.assertTrue(
                all(
                    movement.transaction_type == "sale_out"
                    and movement.quantity > 0
                    for movement in movements
                )
            )

        admin_client = self.client_for("admin")
        no_customer_sale = self.submit(
            admin_client,
            [{"product_id": self.ids["case"], "quantity": 2}],
            branch_id=self.ids["branch_a"],
        )
        self.assertEqual(no_customer_sale.status_code, 201)
        with self.app.app_context():
            sale = db.session.get(Sale, no_customer_sale.json["sale_id"])
            self.assertIsNone(sale.customer_id)
            self.assertEqual(sale.payment_method, "cash")
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
                0,
            )

    def test_invalid_cart_payment_customer_and_branch_do_not_mutate(self):
        client = self.client_for("manager")
        invalid_payloads = [
            {"items": [], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": "not a list", "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [None], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": True, "quantity": 1}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 0}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": -1}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 1.5}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 1}, {"product_id": self.ids["case"], "quantity": 1}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": 999999, "quantity": 1}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["phone"], "quantity": 1}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 99}], "payment_method": "cash", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 1}], "payment_method": "bitcoin", "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 1}], "payment_method": "cash", "customer_id": 999999, "branch_id": self.ids["branch_a"]},
            {"items": [{"product_id": self.ids["case"], "quantity": 1}], "payment_method": "cash", "branch_id": 999999},
        ]

        with self.app.app_context():
            counts_before = (
                Sale.query.count(),
                SaleItem.query.count(),
                InventoryTransaction.query.count(),
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
            )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = client.post("/sales/checkout", json=payload)
                self.assertEqual(response.status_code, 400)

        with self.app.app_context():
            self.assertEqual(
                (
                    Sale.query.count(),
                    SaleItem.query.count(),
                    InventoryTransaction.query.count(),
                    db.session.get(
                        BranchInventory,
                        self.ids["case_a_inventory"],
                    ).stock_quantity,
                ),
                counts_before,
            )

        unlinked_response = self.submit(
            self.client_for("unlinked"),
            [{"product_id": self.ids["phone"], "quantity": 1}],
        )
        self.assertEqual(unlinked_response.status_code, 400)

    def test_branch_specific_low_stock_alert_is_deduplicated(self):
        client = self.client_for("manager")
        payload = {
            "items": [{"product_id": self.ids["case"], "quantity": 1}],
            "payment_method": "cash",
            "branch_id": self.ids["branch_a"],
        }
        self.assertEqual(client.post("/sales/checkout", json=payload).status_code, 201)
        self.assertEqual(client.post("/sales/checkout", json=payload).status_code, 201)

        with self.app.app_context():
            notifications = Notification.query.filter(
                Notification.message.startswith(
                    f"Low stock alert [BranchInventory #{self.ids['case_a_inventory']}]:"
                ),
                Notification.is_read.is_(False),
            ).all()
            other_branch_notifications = Notification.query.filter(
                Notification.message.startswith(
                    f"Low stock alert [BranchInventory #{self.ids['case_b_inventory']}]:"
                ),
                Notification.is_read.is_(False),
            ).all()
            self.assertEqual(len(notifications), 2)
            self.assertEqual(
                len({notification.user_id for notification in notifications}),
                2,
            )
            self.assertTrue(
                all("Current stock: 2" in notification.message for notification in notifications)
            )
            self.assertEqual(other_branch_notifications, [])

    def test_failure_after_stock_update_rolls_back_everything(self):
        def fail_sale_out_flush(session, flush_context, instances):
            del flush_context, instances
            if any(
                isinstance(item, InventoryTransaction)
                and item.transaction_type == "sale_out"
                for item in session.new
            ):
                raise RuntimeError("injected checkout failure")

        event.listen(Session, "before_flush", fail_sale_out_flush)
        try:
            response = self.submit(
                self.client_for("admin"),
                [{"product_id": self.ids["case"], "quantity": 2}],
                branch_id=self.ids["branch_a"],
            )
        finally:
            event.remove(Session, "before_flush", fail_sale_out_flush)

        self.assertEqual(response.status_code, 500)
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 0)
            self.assertEqual(SaleItem.query.count(), 0)
            self.assertEqual(InventoryTransaction.query.count(), 0)
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
                4,
            )

    def test_simultaneous_checkouts_cannot_oversell_final_unit(self):
        with self.app.app_context():
            inventory = db.session.get(
                BranchInventory,
                self.ids["case_a_inventory"],
            )
            inventory.stock_quantity = 1
            db.session.commit()

        start = Barrier(3)
        results = []

        def checkout_as(username):
            client = self.client_for(username)
            start.wait(timeout=10)
            response = self.submit(
                client,
                [{"product_id": self.ids["case"], "quantity": 1}],
                branch_id=self.ids["branch_a"],
            )
            results.append(response.status_code)

        threads = [
            Thread(target=checkout_as, args=("admin",)),
            Thread(target=checkout_as, args=("manager",)),
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(201), 1)
        self.assertEqual(results.count(400), 1)
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 1)
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
                0,
            )

    def test_manual_inventory_movements_and_history_labels_still_work(self):
        manager_client = self.client_for("manager")
        self.assertEqual(
            self.submit(
                manager_client,
                [{"product_id": self.ids["case"], "quantity": 1}],
                branch_id=self.ids["branch_a"],
            ).status_code,
            201,
        )

        for transaction_type, quantity in (("stock_in", "2"), ("write_off", "1")):
            response = manager_client.post(
                f"/inventory?branch_id={self.ids['branch_a']}",
                data={
                    "branch_id": str(self.ids["branch_a"]),
                    "action": "movement",
                    "product_id": str(self.ids["case"]),
                    "transaction_type": transaction_type,
                    "quantity": quantity,
                },
            )
            self.assertEqual(response.status_code, 302)

        history = manager_client.get(
            f"/inventory?branch_id={self.ids['branch_a']}"
        ).get_data(as_text=True)
        self.assertIn("Поступление", history)
        self.assertIn("Списание", history)
        self.assertIn("Продажа", history)
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 1)
