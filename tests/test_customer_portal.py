import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Thread

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app, db
from app.guest_loyalty import create_claim_token
from app.models import (
    Branch,
    Customer,
    GuestLoyaltyClaim,
    LoyaltyTransaction,
    Sale,
)


class CustomerPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "portal.sqlite"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "isolated-customer-portal-tests",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
                "SESSION_COOKIE_SECURE": False,
                "PUBLIC_BASE_URL": "https://bms.example.test",
                "CUSTOMER_AUTH_RATE_LIMIT": 2,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            branch_a = Branch(name="Portal Branch A", address="A")
            branch_b = Branch(name="Portal Branch B", address="B")
            db.session.add_all([branch_a, branch_b])
            db.session.flush()
            customer = Customer(
                name="Portal Customer",
                phone="+996555111222",
                registration_date=date.today(),
                password_hash=generate_password_hash("portal-password"),
            )
            legacy_customer = Customer(
                name="Existing POS Customer",
                phone="+996555333444",
                registration_date=date.today(),
            )
            db.session.add_all([customer, legacy_customer])
            db.session.commit()
            self.ids = {
                "branch_a": branch_a.id,
                "branch_b": branch_b.id,
                "customer": customer.id,
                "legacy_customer": legacy_customer.id,
            }

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        with client.session_transaction(base_url="https://bms.example.test") as browser_session:
            token = browser_session.get("portal_csrf_token")
        if token:
            return token
        response = client.get("/portal/login", base_url="https://bms.example.test")
        self.assertEqual(response.status_code, 200)
        with client.session_transaction(base_url="https://bms.example.test") as browser_session:
            return browser_session["portal_csrf_token"]

    def login_customer(self, client=None):
        client = client or self.client
        csrf_token = self.csrf(client)
        response = client.post(
            "/portal/login",
            base_url="https://bms.example.test",
            data={
                "csrf_token": csrf_token,
                "phone": "996 555 111 222",
                "password": "portal-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        return response

    def create_guest_claim(self, branch_id=None, amount=Decimal("100.00"), ttl_days=7):
        with self.app.app_context():
            token, token_hash = create_claim_token()
            sale = Sale(
                branch_id=branch_id or self.ids["branch_b"],
                customer_id=None,
                total_amount=amount,
                discount=Decimal("0.00"),
                payment_method="cash",
            )
            db.session.add(sale)
            db.session.flush()
            claim = GuestLoyaltyClaim(
                sale_id=sale.id,
                branch_id=sale.branch_id,
                token_hash=token_hash,
                points=int(amount * Decimal("0.05")),
                expires_at=(
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    + timedelta(days=ttl_days)
                ),
            )
            db.session.add(claim)
            db.session.commit()
            return sale.id, token

    def validate_claim(self, token, client=None):
        client = client or self.client
        return client.post(
            "/loyalty/claim/validate",
            base_url="https://bms.example.test",
            json={"token": token},
        )

    def test_registration_hashes_password_and_creates_one_canonical_customer(self):
        csrf_token = self.csrf()
        response = self.client.post(
            "/portal/register",
            base_url="https://bms.example.test",
            data={
                "csrf_token": csrf_token,
                "name": "New Portal Customer",
                "phone": "(996) 700-123-456",
                "password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            customer = Customer.query.filter_by(phone="+996700123456").one()
            self.assertNotEqual(customer.password_hash, "new-password-123")
            self.assertTrue(check_password_hash(customer.password_hash, "new-password-123"))
            self.assertEqual(Customer.query.filter_by(phone="+996700123456").count(), 1)

    def test_existing_customer_phone_cannot_be_re_registered(self):
        csrf_token = self.csrf()
        response = self.client.post(
            "/portal/register",
            base_url="https://bms.example.test",
            data={
                "csrf_token": csrf_token,
                "name": "Impersonator",
                "phone": "996 (555) 333-444",
                "password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/portal/login")
        with self.app.app_context():
            self.assertEqual(Customer.query.count(), 2)
            self.assertIsNone(
                db.session.get(Customer, self.ids["legacy_customer"]).password_hash
            )

    def test_login_rotates_session_and_customer_is_not_a_staff_user(self):
        with self.client.session_transaction(base_url="https://bms.example.test") as browser_session:
            browser_session["staff_pre_auth_marker"] = "must be discarded"
        self.login_customer()
        with self.client.session_transaction(base_url="https://bms.example.test") as browser_session:
            self.assertEqual(browser_session["customer_id"], self.ids["customer"])
            self.assertNotIn("user_id", browser_session)
            self.assertNotIn("staff_pre_auth_marker", browser_session)
        dashboard = self.client.get("/portal", base_url="https://bms.example.test")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Portal Customer".encode(), dashboard.data)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_dashboard_is_scoped_to_authenticated_customer(self):
        with self.app.app_context():
            other = Customer(
                name="Private Other Customer",
                phone="+996555999000",
                registration_date=date.today(),
                password_hash=generate_password_hash("other-password"),
            )
            db.session.add(other)
            db.session.commit()
        self.login_customer()
        response = self.client.get("/portal", base_url="https://bms.example.test")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Portal Customer", response.data)
        self.assertNotIn(b"Private Other Customer", response.data)

    def test_portal_forms_reject_missing_csrf_token(self):
        self.assertEqual(
            self.client.post(
                "/portal/login",
                base_url="https://bms.example.test",
                data={"phone": "996555111222", "password": "wrong"},
            ).status_code,
            400,
        )

    def test_login_attempts_are_rate_limited(self):
        for _ in range(2):
            csrf_token = self.csrf()
            response = self.client.post(
                "/portal/login",
                base_url="https://bms.example.test",
                data={
                    "csrf_token": csrf_token,
                    "phone": "996555111222",
                    "password": "wrong-password",
                },
            )
            self.assertEqual(response.status_code, 200)
        csrf_token = self.csrf()
        response = self.client.post(
            "/portal/login",
            base_url="https://bms.example.test",
            data={
                "csrf_token": csrf_token,
                "phone": "996555111222",
                "password": "wrong-password",
            },
        )
        self.assertEqual(response.status_code, 429)

    def test_claim_requires_authentication_and_explicit_confirmation(self):
        sale_id, token = self.create_guest_claim()
        validation = self.validate_claim(token)
        self.assertEqual(validation.status_code, 200)
        self.assertEqual(validation.json["status"], "available")
        with self.app.app_context():
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)
            self.assertIsNone(db.session.get(Sale, sale_id).customer_id)

        cookie = self.client.get_cookie(
            "bms_pending_loyalty_claim",
            domain="bms.example.test",
            path="/portal",
        )
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie.http_only)
        self.assertTrue(cookie.secure)
        self.assertEqual(cookie.path, "/portal")
        self.login_customer()
        preview = self.client.get("/portal/claim", base_url="https://bms.example.test")
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"5", preview.data)
        with self.app.app_context():
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)

    def test_claim_credits_once_and_keeps_cross_branch_balance(self):
        sale_id, token = self.create_guest_claim(branch_id=self.ids["branch_b"])
        with self.app.app_context():
            prior_sale = Sale(
                branch_id=self.ids["branch_a"],
                customer_id=self.ids["customer"],
                total_amount=Decimal("200.00"),
                discount=Decimal("0.00"),
                payment_method="card",
            )
            db.session.add(prior_sale)
            db.session.flush()
            db.session.add(
                LoyaltyTransaction(
                    customer_id=self.ids["customer"],
                    sale_id=prior_sale.id,
                    branch_id=self.ids["branch_a"],
                    transaction_type="earn",
                    points=10,
                    operation_id=f"portal-test-existing-{prior_sale.id}",
                )
            )
            db.session.commit()

        self.validate_claim(token)
        self.login_customer()
        csrf_token = self.csrf()
        response = self.client.post(
            "/portal/claim",
            base_url="https://bms.example.test",
            data={"csrf_token": csrf_token},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            earned = LoyaltyTransaction.query.filter_by(sale_id=sale_id).one()
            self.assertEqual(earned.points, 5)
            self.assertEqual(earned.branch_id, self.ids["branch_b"])
            self.assertEqual(earned.customer_id, self.ids["customer"])
            self.assertEqual(db.session.get(Sale, sale_id).customer_id, self.ids["customer"])
            self.assertEqual(
                db.session.query(db.func.sum(LoyaltyTransaction.points))
                .filter_by(customer_id=self.ids["customer"])
                .scalar(),
                15,
            )
            claim = GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).one()
            self.assertIsNotNone(claim.claimed_at)
        replay = self.validate_claim(token)
        self.assertEqual(replay.status_code, 409)

    def test_concurrent_claim_requests_create_only_one_earning(self):
        sale_id, token = self.create_guest_claim()
        clients = []
        for _ in range(2):
            client = self.app.test_client()
            validation = self.validate_claim(token, client=client)
            self.assertEqual(validation.status_code, 200)
            self.login_customer(client)
            clients.append(client)

        barrier = Barrier(2)
        statuses = []

        def submit_claim(client):
            with client.session_transaction(base_url="https://bms.example.test") as browser_session:
                csrf_token = browser_session["portal_csrf_token"]
            barrier.wait()
            response = client.post(
                "/portal/claim",
                base_url="https://bms.example.test",
                data={"csrf_token": csrf_token},
            )
            statuses.append(response.status_code)

        threads = [
            Thread(target=submit_claim, args=(client,))
            for client in clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(statuses), [302, 409])
        with self.app.app_context():
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 1)

    def test_invalid_and_expired_tokens_are_safe(self):
        self.assertEqual(self.validate_claim("x" * 43).status_code, 404)
        sale_id, token = self.create_guest_claim(ttl_days=-1)
        response = self.validate_claim(token)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json["status"], "expired")
        with self.app.app_context():
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)

    def test_claim_failure_rolls_back_sale_association_and_claim(self):
        sale_id, token = self.create_guest_claim()
        self.validate_claim(token)
        self.login_customer()

        def reject_ledger_insert(mapper, connection, target):
            raise IntegrityError("forced rollback", {}, RuntimeError("test"))

        event.listen(LoyaltyTransaction, "before_insert", reject_ledger_insert)
        try:
            csrf_token = self.csrf()
            response = self.client.post(
                "/portal/claim",
                base_url="https://bms.example.test",
                data={"csrf_token": csrf_token},
            )
        finally:
            event.remove(LoyaltyTransaction, "before_insert", reject_ledger_insert)
        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Sale, sale_id).customer_id)
            self.assertIsNone(
                GuestLoyaltyClaim.query.filter_by(sale_id=sale_id).one().claimed_at
            )
            self.assertEqual(LoyaltyTransaction.query.filter_by(sale_id=sale_id).count(), 0)

    def test_logout_requires_csrf_and_clears_customer_session(self):
        self.login_customer()
        self.assertEqual(
            self.client.post(
                "/portal/logout",
                base_url="https://bms.example.test",
            ).status_code,
            400,
        )
        csrf_token = self.csrf()
        response = self.client.post(
            "/portal/logout",
            base_url="https://bms.example.test",
            data={"csrf_token": csrf_token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get("/portal", base_url="https://bms.example.test").status_code,
            302,
        )


if __name__ == "__main__":
    unittest.main()
