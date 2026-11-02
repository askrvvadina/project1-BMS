import hashlib
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import create_app, db
from app.models import (
    Branch,
    BranchInventory,
    Category,
    Customer,
    Employee,
    GuestLoyaltyClaim,
    InventoryTransaction,
    LoyaltyTransaction,
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
                "PUBLIC_BASE_URL": "https://bms.example.test",
                "SESSION_COOKIE_SECURE": False,
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
            self.assertEqual(
                LoyaltyTransaction.query.filter_by(sale_id=sale.id).count(),
                0,
            )
            self.assertEqual(
                GuestLoyaltyClaim.query.filter_by(sale_id=sale.id).count(),
                0,
            )

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
                LoyaltyTransaction.query.filter_by(sale_id=sale.id).count(),
                0,
            )
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["case_a_inventory"],
                ).stock_quantity,
                0,
            )

    def test_customer_sale_earns_shared_branch_points_and_receipt_balance(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            other_customer = Customer(
                name="Another POS customer",
                registration_date=date(2025, 1, 1),
            )
            db.session.add(other_customer)
            db.session.flush()
            db.session.add_all(
                [
                    LoyaltyTransaction(
                        customer_id=self.ids["customer"],
                        branch_id=self.ids["branch_a"],
                        transaction_type="earn",
                        points=3,
                        operation_id="existing-customer-points",
                    ),
                    LoyaltyTransaction(
                        customer_id=other_customer.id,
                        branch_id=self.ids["branch_a"],
                        transaction_type="earn",
                        points=900,
                        operation_id="other-customer-points",
                    ),
                ]
            )
            db.session.commit()

        response = self.submit(
            self.client_for("employee"),
            [{"product_id": self.ids["phone"], "quantity": 2}],
            branch_id=self.ids["branch_a"],
            customer_id=self.ids["customer"],
            payment_method="card",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["total_amount"], "200.00")
        self.assertEqual(response.json["amount_paid"], "200.00")
        sale_id = response.json["sale_id"]

        with self.app.app_context():
            sale = db.session.get(Sale, sale_id)
            earned = LoyaltyTransaction.query.filter_by(
                sale_id=sale_id,
                transaction_type="earn",
            ).one()
            self.assertEqual(sale.branch_id, self.ids["branch_b"])
            self.assertEqual(earned.customer_id, self.ids["customer"])
            self.assertEqual(earned.branch_id, self.ids["branch_b"])
            self.assertEqual(earned.points, 2)
            self.assertEqual(earned.operation_id, f"sale-{sale_id}-earn")
            self.assertEqual(
                db.session.query(db.func.sum(LoyaltyTransaction.points))
                .filter_by(customer_id=self.ids["customer"])
                .scalar(),
                5,
            )

        receipt_response = self.client_for("employee").get(
            response.json["receipt_url"]
        )
        self.assertEqual(receipt_response.status_code, 200)
        receipt = receipt_response.get_data(as_text=True)
        self.assertIn("Начислено баллов</dt><dd>2", receipt)
        self.assertIn("Баланс баллов</dt><dd>5", receipt)
        self.assertNotIn("data:image/svg+xml;base64,", receipt)
        self.assertNotIn("900", receipt)
        self.assertNotIn("904", receipt)
        with self.app.app_context():
            self.assertEqual(
                GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).count(),
                0,
            )

    def test_redemption_quote_checkout_and_receipt_use_net_paid_amount(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=200,
                    operation_id="redemption-test-opening-balance",
                )
            )
            db.session.commit()

        client = self.client_for("employee")
        quote = client.post(
            "/sales/loyalty-quote",
            json={
                "customer_id": self.ids["customer"],
                "items": [{"product_id": self.ids["phone"], "quantity": 2}],
            },
        )
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.json["available_points"], 200)
        self.assertEqual(quote.json["maximum_redemption"], 40)

        response = self.submit(
            client,
            [{"product_id": self.ids["phone"], "quantity": 2}],
            customer_id=self.ids["customer"],
            redeem_points=40,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["total_amount"], "200.00")
        self.assertEqual(response.json["amount_paid"], "160.00")
        self.assertEqual(response.json["redeemed_points"], 40)
        self.assertEqual(response.json["earned_points"], 1)
        self.assertEqual(response.json["customer_balance"], 161)
        self.assertNotIn("guest_claim_qr_data_uri", response.json)
        self.assertNotIn("guest_claim_points", response.json)

        with self.app.app_context():
            sale = db.session.get(Sale, response.json["sale_id"])
            self.assertEqual(sale.total_amount, Decimal("200.00"))
            self.assertEqual(sale.discount, Decimal("40.00"))
            redemption = LoyaltyTransaction.query.filter_by(
                sale_id=sale.id,
                transaction_type="redeem",
            ).one()
            earning = LoyaltyTransaction.query.filter_by(
                sale_id=sale.id,
                transaction_type="earn",
            ).one()
            self.assertEqual(redemption.points, -40)
            self.assertEqual(earning.points, 1)
            self.assertEqual(redemption.branch_id, self.ids["branch_b"])
            self.assertEqual(earning.branch_id, self.ids["branch_b"])

        receipt_response = client.get(response.json["receipt_url"])
        receipt = receipt_response.get_data(as_text=True)
        self.assertIn("Сумма покупки</dt><dd>200.00", receipt)
        self.assertIn("Списано бонусов</dt><dd>40 баллов", receipt)
        self.assertIn("Фактически оплачено</dt><dd>160.00", receipt)
        self.assertIn("Начислено баллов</dt><dd>1", receipt)
        self.assertIn("Баланс баллов</dt><dd>161", receipt)

    def test_redemption_above_cap_or_balance_is_rejected_without_mutations(self):
        with self.app.app_context():
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=500,
                    operation_id="redemption-cap-opening-balance",
                )
            )
            db.session.commit()

        client = self.client_for("employee")
        over_cap = self.submit(
            client,
            [{"product_id": self.ids["phone"], "quantity": 2}],
            customer_id=self.ids["customer"],
            redeem_points=5,
        )
        self.assertEqual(over_cap.status_code, 400)
        self.assertIn("лимит 20%", over_cap.json["error"])

        with self.app.app_context():
            low_balance_customer = Customer(
                name="Low balance customer",
                registration_date=date(2025, 1, 1),
            )
            db.session.add(low_balance_customer)
            db.session.flush()
            db.session.add(
                LoyaltyTransaction(
                    customer_id=low_balance_customer.id,
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=2,
                    operation_id="redemption-balance-opening",
                )
            )
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            db.session.commit()
            low_customer_id = low_balance_customer.id

        over_balance = self.submit(
            client,
            [{"product_id": self.ids["phone"], "quantity": 2}],
            customer_id=low_customer_id,
            redeem_points=3,
        )
        self.assertEqual(over_balance.status_code, 400)
        self.assertIn("доступный баланс", over_balance.json["error"])
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 0)
            self.assertEqual(
                db.session.get(BranchInventory, self.ids["phone_inventory"]).stock_quantity,
                2,
            )
            self.assertEqual(
                db.session.query(db.func.sum(LoyaltyTransaction.points))
                .filter_by(customer_id=low_customer_id)
                .scalar(),
                2,
            )

    def test_guest_cannot_redeem_and_redemption_failure_rolls_back_checkout(self):
        guest_response = self.submit(
            self.client_for("employee"),
            [{"product_id": self.ids["phone"], "quantity": 1}],
            redeem_points=1,
        )
        self.assertEqual(guest_response.status_code, 400)

        with self.app.app_context():
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=10,
                    operation_id="rollback-opening-balance",
                )
            )
            db.session.commit()
            db.session.execute(
                db.text(
                    "CREATE TRIGGER fail_test_redemption BEFORE INSERT ON loyalty_transactions "
                    "BEGIN SELECT RAISE(ABORT, 'forced ledger failure'); END"
                )
            )
            db.session.commit()

        failed = self.submit(
            self.client_for("employee"),
            [{"product_id": self.ids["phone"], "quantity": 1}],
            customer_id=self.ids["customer"],
            redeem_points=2,
        )
        self.assertEqual(failed.status_code, 500)
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 0)
            self.assertEqual(SaleItem.query.count(), 0)
            self.assertEqual(
                db.session.get(BranchInventory, self.ids["phone_inventory"]).stock_quantity,
                2,
            )
            self.assertEqual(
                InventoryTransaction.query.filter_by(
                    branch_inventory_id=self.ids["phone_inventory"]
                ).count(),
                0,
            )
            self.assertEqual(
                LoyaltyTransaction.query.filter_by(
                    customer_id=self.ids["customer"]
                ).count(),
                1,
            )

    def test_concurrent_redemptions_cannot_spend_the_same_balance(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=20,
                    operation_id="concurrent-redemption-opening",
                )
            )
            db.session.commit()
        clients = [self.client_for("employee"), self.client_for("employee")]
        barrier = Barrier(2)
        results = []

        def checkout(client):
            barrier.wait()
            results.append(
                self.submit(
                    client,
                    [{"product_id": self.ids["phone"], "quantity": 1}],
                    customer_id=self.ids["customer"],
                    redeem_points=20,
                )
            )

        threads = [
            Thread(target=checkout, args=(client,))
            for client in clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(result.status_code for result in results), [201, 400])
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 1)
            self.assertEqual(
                db.session.query(db.func.sum(LoyaltyTransaction.points))
                .filter_by(customer_id=self.ids["customer"])
                .scalar(),
                0,
            )

    def test_earn_entry_cannot_be_duplicated_for_a_sale(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            db.session.commit()
        response = self.submit(
            self.client_for("manager"),
            [{"product_id": self.ids["case"], "quantity": 1}],
            branch_id=self.ids["branch_a"],
            customer_id=self.ids["customer"],
        )
        self.assertEqual(response.status_code, 201)
        sale_id = response.json["sale_id"]

        with self.app.app_context():
            self.assertEqual(
                LoyaltyTransaction.query.filter_by(
                    sale_id=sale_id,
                    transaction_type="earn",
                ).count(),
                0,
            )

        response = self.submit(
            self.client_for("employee"),
            [{"product_id": self.ids["phone"], "quantity": 2}],
            customer_id=self.ids["customer"],
        )
        self.assertEqual(response.status_code, 201)
        sale_id = response.json["sale_id"]

        with self.app.app_context():
            earned = LoyaltyTransaction.query.filter_by(
                sale_id=sale_id,
                transaction_type="earn",
            ).one()
            linked_sale = db.session.get(Sale, sale_id)
            linked_claim = GuestLoyaltyClaim(
                sale_id=sale_id,
                branch_id=linked_sale.branch_id,
                token_hash="a" * 64,
                points=1,
                expires_at=(
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    + timedelta(days=7)
                ),
            )
            db.session.add(linked_claim)
            with self.assertRaises(IntegrityError):
                db.session.flush()
            db.session.rollback()

        with self.app.app_context():
            earned = LoyaltyTransaction.query.filter_by(
                sale_id=sale_id,
                transaction_type="earn",
            ).one()
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    sale_id=sale_id,
                    branch_id=self.ids["branch_b"],
                    transaction_type="earn",
                    points=earned.points,
                    operation_id=f"duplicate-sale-{sale_id}-earn",
                )
            )
            with self.assertRaises(IntegrityError):
                db.session.flush()
            db.session.rollback()
            self.assertEqual(
                LoyaltyTransaction.query.filter_by(
                    sale_id=sale_id,
                    transaction_type="earn",
                ).count(),
                1,
            )

    def test_guest_sale_receipt_has_repeatable_private_qr_and_unique_claim(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("50.00")
            db.session.commit()
        client = self.client_for("employee")
        checkout = self.submit(
            client,
            [{"product_id": self.ids["phone"], "quantity": 2}],
        )
        self.assertEqual(checkout.status_code, 201)
        sale_id = checkout.json["sale_id"]
        self.assertIn("guest_claim_qr_data_uri", checkout.json)
        self.assertTrue(checkout.json["guest_claim_qr_data_uri"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(checkout.json["guest_claim_points"], 1)
        self.assertNotIn("token", checkout.json)
        self.assertNotIn("claim_token", checkout.json)
        set_cookie = checkout.headers["Set-Cookie"]
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        cookie_name = f"bms_guest_claim_{sale_id}"
        token = client.get_cookie(
            cookie_name,
            path=checkout.json["receipt_url"],
        )
        self.assertIsNotNone(token)
        token = token.value

        with self.app.app_context():
            sale = db.session.get(Sale, sale_id)
            claim = GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).one()
            self.assertIsNone(sale.customer_id)
            self.assertEqual(claim.branch_id, sale.branch_id)
            self.assertEqual(claim.branch_id, self.ids["branch_b"])
            self.assertEqual(claim.points, 1)
            self.assertEqual(
                claim.token_hash,
                hashlib.sha256(token.encode("ascii")).hexdigest(),
            )
            self.assertNotEqual(claim.token_hash, token)
            self.assertLess(
                abs(
                    (claim.expires_at - claim.created_at)
                    - timedelta(days=7)
                ).total_seconds(),
                2,
            )
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)

        first_receipt = client.get(
            checkout.json["receipt_url"],
            base_url="https://localhost",
        )
        second_receipt = client.get(
            checkout.json["receipt_url"],
            base_url="https://localhost",
        )
        self.assertEqual(first_receipt.status_code, 200)
        self.assertEqual(second_receipt.status_code, 200)
        for response in (first_receipt, second_receipt):
            html = response.get_data(as_text=True)
            self.assertIn("data:image/svg+xml;base64,", html)
            self.assertIn("можно получить 1 бонусных баллов", html)
            self.assertNotIn(token, html)
            self.assertEqual(response.headers["Cache-Control"], "no-store")

        with self.app.app_context():
            self.assertEqual(GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).count(), 1)
            duplicate = GuestLoyaltyClaim(
                sale_id=sale_id,
                branch_id=self.ids["branch_b"],
                token_hash="f" * 64,
                points=1,
                expires_at=(
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    + timedelta(days=7)
                ),
            )
            db.session.add(duplicate)
            with self.assertRaises(IntegrityError):
                db.session.flush()
            db.session.rollback()

        self.app.config["LOYALTY_CLAIM_TTL_DAYS"] = 2
        second_checkout = self.submit(
            self.client_for("manager"),
            [{"product_id": self.ids["case"], "quantity": 1}],
            branch_id=self.ids["branch_a"],
        )
        self.assertEqual(second_checkout.status_code, 201)
        with self.app.app_context():
            second_claim = GuestLoyaltyClaim.query.filter_by(
                sale_id=second_checkout.json["sale_id"]
            ).one()
            first_claim = GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).one()
            self.assertNotEqual(first_claim.token_hash, second_claim.token_hash)
            self.assertEqual(second_claim.branch_id, self.ids["branch_a"])
            self.assertLess(
                abs(
                    (second_claim.expires_at - second_claim.created_at)
                    - timedelta(days=2)
                ).total_seconds(),
                2,
            )

    def test_public_claim_validation_is_read_only_and_reports_safe_states(self):
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("50.00")
            db.session.commit()
        client = self.client_for("employee")
        checkout = self.submit(
            client,
            [{"product_id": self.ids["phone"], "quantity": 2}],
        )
        self.assertEqual(checkout.status_code, 201)
        sale_id = checkout.json["sale_id"]
        token = client.get_cookie(
            f"bms_guest_claim_{sale_id}",
            path=checkout.json["receipt_url"],
        ).value

        landing = self.app.test_client().get("/loyalty/claim")
        self.assertEqual(landing.status_code, 200)
        self.assertIn("войти или зарегистрироваться".encode(), landing.data)
        self.assertNotIn(token.encode(), landing.data)
        self.assertEqual(landing.headers["Referrer-Policy"], "no-referrer")

        public_client = self.app.test_client()
        first = public_client.post(
            "/loyalty/claim/validate",
            json={"token": token},
        )
        second = public_client.post(
            "/loyalty/claim/validate",
            json={"token": token},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["status"], "available")
        self.assertEqual(first.json["points"], 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json["points"], 1)
        self.assertNotIn("sale_id", first.json)
        self.assertNotIn("customer", first.json)
        self.assertEqual(first.headers["Cache-Control"], "no-store")

        invalid = public_client.post(
            "/loyalty/claim/validate",
            json={"token": "A" * 43},
        )
        malformed = public_client.post(
            "/loyalty/claim/validate",
            json={"token": "not-a-token"},
        )
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(invalid.json["status"], "invalid")
        self.assertEqual(malformed.status_code, 404)

        with self.app.app_context():
            expired_sale = Sale(
                branch_id=self.ids["branch_b"],
                customer_id=None,
                total_amount=Decimal("40.00"),
                discount=Decimal("0.00"),
                payment_method="cash",
            )
            db.session.add(expired_sale)
            db.session.flush()
            expired_token = "E" * 43
            db.session.add(
                GuestLoyaltyClaim(
                    sale_id=expired_sale.id,
                    branch_id=self.ids["branch_b"],
                    token_hash=hashlib.sha256(
                        expired_token.encode("ascii")
                    ).hexdigest(),
                    points=2,
                    expires_at=(
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        - timedelta(seconds=1)
                    ),
                )
            )
            db.session.commit()
        expired = public_client.post(
            "/loyalty/claim/validate",
            json={"token": expired_token},
        )
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(expired.json["status"], "expired")

        with self.app.app_context():
            claimed_sale = Sale(
                branch_id=self.ids["branch_b"],
                customer_id=None,
                total_amount=Decimal("40.00"),
                discount=Decimal("0.00"),
                payment_method="cash",
            )
            db.session.add(claimed_sale)
            db.session.flush()
            claimed_token = "C" * 43
            db.session.add(
                GuestLoyaltyClaim(
                    sale_id=claimed_sale.id,
                    branch_id=self.ids["branch_b"],
                    token_hash=hashlib.sha256(
                        claimed_token.encode("ascii")
                    ).hexdigest(),
                    points=2,
                    expires_at=(
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        + timedelta(days=7)
                    ),
                    claimed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            db.session.commit()
        claimed = public_client.post(
            "/loyalty/claim/validate",
            json={"token": claimed_token},
        )
        self.assertEqual(claimed.status_code, 409)
        self.assertEqual(claimed.json["status"], "claimed")
        with self.app.app_context():
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)

    def test_claim_validation_is_rate_limited(self):
        self.app.config["LOYALTY_CLAIM_RATE_LIMIT"] = 1
        client = self.app.test_client()
        first = client.post("/loyalty/claim/validate", json={"token": "bad"})
        second = client.post("/loyalty/claim/validate", json={"token": "bad"})
        self.assertEqual(first.status_code, 404)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["Retry-After"], "60")

    def test_localhost_guest_checkout_uses_discovered_lan_address(self):
        self.app.config["PUBLIC_BASE_URL"] = None
        client = self.client_for("employee")
        with patch("app.discover_local_ipv4", return_value="192.168.1.25"):
            checkout = self.submit(client, [{"product_id": self.ids["phone"], "quantity": 1}])
        self.assertEqual(checkout.status_code, 201)
        self.assertIn("guest_claim_qr_data_uri", checkout.json)
        self.assertNotIn("guest_claim_configuration_error", checkout.json)

    def test_guest_checkout_without_lan_address_has_configuration_warning(self):
        self.app.config["PUBLIC_BASE_URL"] = None
        client = self.client_for("employee")
        with patch("app.discover_local_ipv4", return_value=None):
            checkout = self.submit(client, [{"product_id": self.ids["phone"], "quantity": 1}])
        self.assertEqual(checkout.status_code, 201)
        self.assertIn("guest_claim_configuration_error", checkout.json)
        self.assertNotIn("guest_claim_qr_data_uri", checkout.json)

    def test_lan_address_access_is_used_without_discovery(self):
        self.app.config["PUBLIC_BASE_URL"] = None
        self.app.config["SESSION_COOKIE_SECURE"] = False
        client = self.app.test_client()
        with client.session_transaction(base_url="http://192.168.1.25:5000") as browser_session:
            browser_session["user_id"] = self.ids["employee"]
            browser_session["role"] = "employee"
        with patch("app.discover_local_ipv4") as discover:
            checkout = client.post(
                "/sales/checkout",
                json={"items": [{"product_id": self.ids["phone"], "quantity": 1}], "payment_method": "cash"},
                base_url="http://192.168.1.25:5000",
            )
        discover.assert_not_called()
        self.assertEqual(checkout.status_code, 201)
        self.assertIn("guest_claim_qr_data_uri", checkout.json)

    def test_production_requires_explicit_public_base_url(self):
        with self.assertRaises(ValueError):
            create_app({
                "TESTING": True,
                "ENVIRONMENT": "production",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "PUBLIC_BASE_URL": None,
            })

    def test_public_base_url_must_be_reachable_https(self):
        for invalid_url in (
            "http://bms.example.test",
            "https://localhost",
            "https://127.0.0.1",
            "https://[::1]",
        ):
            with self.subTest(public_base_url=invalid_url):
                with self.assertRaises(ValueError):
                    create_app(
                        {
                            "TESTING": True,
                            "SQLALCHEMY_DATABASE_URI": "sqlite://",
                            "PUBLIC_BASE_URL": invalid_url,
                        }
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
        with self.app.app_context():
            db.session.get(Product, self.ids["phone"]).selling_price = Decimal("100.00")
            db.session.commit()

        def fail_sale_out_flush(session, flush_context, instances):
            del flush_context, instances
            if any(
                isinstance(item, LoyaltyTransaction)
                and item.transaction_type == "earn"
                for item in session.new
            ):
                raise RuntimeError("injected loyalty flush failure")

        event.listen(Session, "before_flush", fail_sale_out_flush)
        try:
            response = self.submit(
                self.client_for("employee"),
                [{"product_id": self.ids["phone"], "quantity": 2}],
                branch_id=self.ids["branch_a"],
                customer_id=self.ids["customer"],
            )
        finally:
            event.remove(Session, "before_flush", fail_sale_out_flush)

        self.assertEqual(response.status_code, 500)
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 0)
            self.assertEqual(SaleItem.query.count(), 0)
            self.assertEqual(InventoryTransaction.query.count(), 0)
            self.assertEqual(LoyaltyTransaction.query.count(), 0)
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["phone_inventory"],
                ).stock_quantity,
                2,
            )

    def test_guest_claim_failure_rolls_back_sale_inventory_and_cookie(self):
        def fail_claim_flush(session, flush_context, instances):
            del flush_context, instances
            if any(isinstance(item, GuestLoyaltyClaim) for item in session.new):
                raise RuntimeError("injected guest-claim flush failure")

        client = self.client_for("employee")
        event.listen(Session, "before_flush", fail_claim_flush)
        try:
            response = self.submit(
                client,
                [{"product_id": self.ids["phone"], "quantity": 2}],
            )
        finally:
            event.remove(Session, "before_flush", fail_claim_flush)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("bms_guest_claim_", response.headers.get("Set-Cookie", ""))
        with self.app.app_context():
            self.assertEqual(Sale.query.count(), 0)
            self.assertEqual(SaleItem.query.count(), 0)
            self.assertEqual(InventoryTransaction.query.count(), 0)
            self.assertEqual(GuestLoyaltyClaim.query.count(), 0)
            self.assertEqual(
                db.session.get(
                    BranchInventory,
                    self.ids["phone_inventory"],
                ).stock_quantity,
                2,
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
