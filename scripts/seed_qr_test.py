"""Seed the isolated QR-loyalty development database.

This script never creates tables, customers, sales, or loyalty transactions.
It only creates or refreshes one marked test branch, manager, category, two
test products, and their branch-scoped inventory rows.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATABASE = (
    PROJECT_ROOT / "instance" / "bms-qr-test.db"
).resolve()
TEST_BRANCH_NAME = "QR Loyalty Test Branch"
TEST_BRANCH_ADDRESS = "Development database only"
TEST_CATEGORY_NAME = "QR Loyalty Test Category"
TEST_STAFF_USERNAME = "qr-test-manager"
TEST_STAFF_EMAIL = "qr-test-manager@bms.invalid"
TEST_STOCK_QUANTITY = 100
REQUIRED_TABLES = {
    "branches",
    "branch_inventory",
    "categories",
    "customers",
    "guest_loyalty_claims",
    "loyalty_transactions",
    "products",
    "sales",
    "users",
}
PRODUCTS = (
    {
        "name": "QR Loyalty Test Product A",
        "sku": "QR-LOYALTY-TEST-001",
        "purchase_price": Decimal("50.00"),
        "selling_price": Decimal("1000.00"),
    },
    {
        "name": "QR Loyalty Test Product B",
        "sku": "QR-LOYALTY-TEST-002",
        "purchase_price": Decimal("25.00"),
        "selling_price": Decimal("250.00"),
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Must be C:\\project1-BMS\\instance\\bms-qr-test.db",
    )
    return parser.parse_args()


def request_password(existing_user: bool) -> str | None:
    prompt = (
        "Temporary password for qr-test-manager "
        "(input is hidden; blank keeps the current password): "
    )
    password = getpass.getpass(prompt)
    if not password:
        if existing_user:
            return None
        raise ValueError(
            "A password is required for the new test account; no seed changes were made."
        )
    if not 8 <= len(password) <= 128:
        raise ValueError("Password must contain between 8 and 128 characters.")
    confirmation = getpass.getpass("Confirm temporary password: ")
    if not secrets.compare_digest(password, confirmation):
        raise ValueError("Passwords did not match; no seed changes were made.")
    return password


def verify_existing_database(app) -> None:
    from app import db

    with app.app_context():
        configured_database = Path(db.engine.url.database).resolve()
    if configured_database != EXPECTED_DATABASE:
        raise ValueError(
            "BMS_DATABASE_URI did not resolve to the isolated QR test database."
        )

    with app.app_context():
        table_names = set(inspect(db.engine).get_table_names())
        missing_tables = REQUIRED_TABLES - table_names
        if missing_tables:
            raise ValueError(
                "The QR test database is missing initialized tables: "
                + ", ".join(sorted(missing_tables))
            )


def seed_database(password: str | None) -> None:
    from app import create_app, db
    from app.models import Branch, BranchInventory, Category, Product, User

    database_uri = f"sqlite:///{EXPECTED_DATABASE}"
    os.environ["BMS_DATABASE_URI"] = database_uri
    app = create_app({"TESTING": True})
    verify_existing_database(app)

    with app.app_context():
        existing_staff = User.query.filter_by(username=TEST_STAFF_USERNAME).first()
        if existing_staff is not None and (
            existing_staff.email != TEST_STAFF_EMAIL
            or existing_staff.role != "manager"
        ):
            raise ValueError(
                "The reserved test username already belongs to an incompatible account."
            )
        email_owner = User.query.filter_by(email=TEST_STAFF_EMAIL).first()
        if email_owner is not None and email_owner.username != TEST_STAFF_USERNAME:
            raise ValueError(
                "The reserved test email already belongs to another account."
            )
        db.session.rollback()

        if password is None and existing_staff is None:
            raise ValueError(
                "A password is required for the new test account; no seed changes were made."
            )

        try:
            with db.session.begin():
                if db.engine.dialect.name == "sqlite":
                    db.session.execute(text("BEGIN IMMEDIATE"))

                branch = Branch.query.filter_by(name=TEST_BRANCH_NAME).first()
                if branch is None:
                    branch = Branch(
                        name=TEST_BRANCH_NAME,
                        address=TEST_BRANCH_ADDRESS,
                        status="active",
                    )
                    db.session.add(branch)
                    db.session.flush()
                elif (
                    branch.address != TEST_BRANCH_ADDRESS
                    or branch.status != "active"
                ):
                    raise ValueError(
                        "The reserved test branch exists with unexpected settings."
                    )

                category = Category.query.filter_by(
                    name=TEST_CATEGORY_NAME
                ).first()
                if category is None:
                    category = Category(name=TEST_CATEGORY_NAME)
                    db.session.add(category)
                    db.session.flush()

                staff = User.query.filter_by(username=TEST_STAFF_USERNAME).first()
                if staff is None:
                    staff = User(
                        username=TEST_STAFF_USERNAME,
                        email=TEST_STAFF_EMAIL,
                        password_hash=generate_password_hash(password),
                        role="manager",
                        is_active=True,
                    )
                    db.session.add(staff)
                else:
                    if (
                        staff.email != TEST_STAFF_EMAIL
                        or staff.role != "manager"
                        or staff.employee_id is not None
                    ):
                        raise ValueError(
                            "The reserved test account exists with unexpected settings."
                        )
                    staff.is_active = True
                    if password is not None:
                        staff.password_hash = generate_password_hash(password)

                for product_data in PRODUCTS:
                    product = Product.query.filter_by(
                        sku=product_data["sku"]
                    ).first()
                    if product is None:
                        product = Product(
                            name=product_data["name"],
                            sku=product_data["sku"],
                            category_id=category.id,
                            branch_id=branch.id,
                            purchase_price=product_data["purchase_price"],
                            selling_price=product_data["selling_price"],
                            stock_quantity=TEST_STOCK_QUANTITY,
                            minimum_stock=0,
                        )
                        db.session.add(product)
                        db.session.flush()
                    elif (
                        product.name != product_data["name"]
                        or product.category_id != category.id
                        or product.branch_id != branch.id
                        or product.purchase_price != product_data["purchase_price"]
                        or product.selling_price != product_data["selling_price"]
                    ):
                        raise ValueError(
                            f"Reserved test SKU {product_data['sku']} exists "
                            "with unexpected settings."
                        )

                    product.stock_quantity = TEST_STOCK_QUANTITY
                    product.minimum_stock = 0
                    inventory = BranchInventory.query.filter_by(
                        branch_id=branch.id,
                        product_id=product.id,
                    ).first()
                    if inventory is None:
                        inventory = BranchInventory(
                            branch_id=branch.id,
                            product_id=product.id,
                            stock_quantity=TEST_STOCK_QUANTITY,
                            minimum_stock=0,
                        )
                        db.session.add(inventory)
                    else:
                        inventory.stock_quantity = TEST_STOCK_QUANTITY
                        inventory.minimum_stock = 0

                db.session.flush()
        except IntegrityError as error:
            db.session.rollback()
            raise RuntimeError(
                "Seed was rolled back because a database uniqueness constraint "
                "conflicted with the reserved QR test records."
            ) from error
        except Exception:
            db.session.rollback()
            raise

        print(f"Test staff username: {TEST_STAFF_USERNAME}")
        print(f"Database: {EXPECTED_DATABASE}")
        print(f"Test branch: {TEST_BRANCH_NAME}")
        print("Test products: QR-LOYALTY-TEST-001, QR-LOYALTY-TEST-002")
        print(
            "No customer, sale, or loyalty transaction records were created."
        )


def main() -> int:
    arguments = parse_args()
    database_path = arguments.database.expanduser().resolve()
    if database_path != EXPECTED_DATABASE:
        print(
            "Refusing to seed: --database must resolve exactly to "
            f"{EXPECTED_DATABASE}.",
            file=sys.stderr,
        )
        return 2
    if not database_path.is_file():
        print(
            f"Refusing to seed: database file does not exist: {database_path}",
            file=sys.stderr,
        )
        return 2

    try:
        from app import create_app, db
        from app.models import User

        database_uri = f"sqlite:///{database_path}"
        os.environ["BMS_DATABASE_URI"] = database_uri
        app = create_app({"TESTING": True})
        verify_existing_database(app)
        with app.app_context():
            existing_user = User.query.filter_by(
                username=TEST_STAFF_USERNAME
            ).first()
            db.session.rollback()
        password = request_password(existing_user is not None)
        seed_database(password)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"QR test seed failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
