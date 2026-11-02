from app import db
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import DDL, event


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id"),
        nullable=True,
        unique=True,
    )
    employee = db.relationship(
        "Employee",
        back_populates="user",
        uselist=False,
        foreign_keys=[employee_id],
    )
    created_sales = db.relationship(
        "Sale",
        back_populates="created_by_user",
        foreign_keys="Sale.created_by_user_id",
    )


class Branch(db.Model):
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(30))
    status = db.Column(db.String(20), nullable=False, default="active")
    inventory_items = db.relationship("BranchInventory", back_populates="branch")
    sales_target = db.relationship(
        "BranchSalesTarget",
        back_populates="branch",
        uselist=False,
        cascade="all, delete-orphan",
    )


class BranchSalesTarget(db.Model):
    __tablename__ = "branch_sales_targets"
    __table_args__ = (
        db.UniqueConstraint("branch_id", name="uq_branch_sales_targets_branch"),
        db.CheckConstraint("daily_target >= 0", name="ck_branch_sales_targets_daily_nonnegative"),
        db.CheckConstraint("monthly_target >= 0", name="ck_branch_sales_targets_monthly_nonnegative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    daily_target = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("15000.00"))
    monthly_target = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("450000.00"))

    branch = db.relationship("Branch", back_populates="sales_target")


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    position = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120), unique=True)
    hire_date = db.Column(db.Date, nullable=False)
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False
    )
    status = db.Column(db.String(20), nullable=False, default="active")
    user = db.relationship(
        "User",
        back_populates="employee",
        uselist=False,
        foreign_keys="User.employee_id",
    )


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    sku = db.Column(db.String(100), unique=True, nullable=False)

    category_id = db.Column(
        db.Integer,
        db.ForeignKey("categories.id"),
        nullable=False
    )

    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False
    )

    purchase_price = db.Column(db.Numeric(10, 2), nullable=False)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False)
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    minimum_stock = db.Column(db.Integer, nullable=False, default=0)
    branch_inventories = db.relationship(
        "BranchInventory",
        back_populates="product",
    )


class BranchInventory(db.Model):
    __tablename__ = "branch_inventory"
    __table_args__ = (
        db.UniqueConstraint(
            "branch_id",
            "product_id",
            name="uq_branch_inventory_branch_product",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False,
    )
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
    )
    stock_quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    minimum_stock = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    branch = db.relationship("Branch", back_populates="inventory_items")
    product = db.relationship("Product", back_populates="branch_inventories")
    transactions = db.relationship(
        "InventoryTransaction",
        back_populates="branch_inventory",
    )


class InventoryTransaction(db.Model):
    __tablename__ = "inventory_transactions"

    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )

    branch_inventory_id = db.Column(
        db.Integer,
        db.ForeignKey("branch_inventory.id"),
        nullable=True,
    )
    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id"),
        nullable=True,
    )
    transaction_type = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    branch_inventory = db.relationship(
        "BranchInventory",
        back_populates="transactions",
    )
    sale = db.relationship(
        "Sale",
        back_populates="inventory_transactions",
        foreign_keys=[sale_id],
    )


class Customer(db.Model):
    __tablename__ = "customers"
    __table_args__ = (
        db.UniqueConstraint("phone", name="uq_customers_phone"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(255))
    registration_date = db.Column(db.Date, nullable=False)
    sales = db.relationship(
        "Sale",
        back_populates="customer",
        passive_deletes="all",
    )
    loyalty_transactions = db.relationship(
        "LoyaltyTransaction",
        back_populates="customer",
        passive_deletes="all",
    )


class LoyaltyTransaction(db.Model):
    __tablename__ = "loyalty_transactions"
    __table_args__ = (
        db.CheckConstraint(
            "transaction_type IN ('earn', 'redeem', 'reversal')",
            name="ck_loyalty_transactions_type",
        ),
        db.CheckConstraint(
            "typeof(points) = 'integer' AND points != 0",
            name="ck_loyalty_transactions_nonzero",
        ),
        db.CheckConstraint(
            "(transaction_type = 'earn' AND points > 0) "
            "OR (transaction_type = 'redeem' AND points < 0) "
            "OR transaction_type = 'reversal'",
            name="ck_loyalty_transactions_sign",
        ),
        db.CheckConstraint(
            "length(trim(operation_id)) > 0",
            name="ck_loyalty_transactions_operation_id",
        ),
        db.Index(
            "uq_loyalty_transactions_sale_operation",
            "customer_id",
            "sale_id",
            "transaction_type",
            unique=True,
            sqlite_where=db.text(
                "sale_id IS NOT NULL AND transaction_type IN ('earn', 'redeem')"
            ),
        ),
        db.Index(
            "ix_loyalty_transactions_customer_created_at",
            "customer_id",
            "created_at",
        ),
        db.Index("ix_loyalty_transactions_sale_id", "sale_id"),
        db.Index("ix_loyalty_transactions_branch_id", "branch_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id", ondelete="RESTRICT"),
        nullable=True,
    )
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    transaction_type = db.Column(db.String(20), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    operation_id = db.Column(db.String(100), nullable=False, unique=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.func.current_timestamp(),
    )

    customer = db.relationship("Customer", back_populates="loyalty_transactions")
    sale = db.relationship("Sale")
    branch = db.relationship("Branch")


event.listen(
    LoyaltyTransaction.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER loyalty_transactions_validate_insert "
        "BEFORE INSERT ON loyalty_transactions BEGIN "
        "SELECT CASE WHEN NEW.sale_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM sales WHERE id = NEW.sale_id "
        "AND customer_id = NEW.customer_id "
        "AND (NEW.branch_id IS NULL OR branch_id = NEW.branch_id)) "
        "THEN RAISE(ABORT, 'loyalty sale reference mismatch') END; "
        "SELECT CASE WHEN (SELECT COALESCE(SUM(points), 0) "
        "FROM loyalty_transactions WHERE customer_id = NEW.customer_id) "
        "+ NEW.points < 0 "
        "THEN RAISE(ABORT, 'loyalty balance cannot be negative') END; "
        "END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    LoyaltyTransaction.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER loyalty_transactions_prevent_update "
        "BEFORE UPDATE ON loyalty_transactions BEGIN "
        "SELECT RAISE(ABORT, 'loyalty transactions are immutable'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    LoyaltyTransaction.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER loyalty_transactions_prevent_delete "
        "BEFORE DELETE ON loyalty_transactions BEGIN "
        "SELECT RAISE(ABORT, 'loyalty transactions are immutable'); END"
    ).execute_if(dialect="sqlite"),
)


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.id"),
        nullable=True
    )
    customer = db.relationship("Customer", back_populates="sales")

    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False
    )

    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    discount = db.Column(db.Numeric(10, 2), nullable=True)
    payment_method = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )
    created_by_user = db.relationship(
        "User",
        back_populates="created_sales",
        foreign_keys=[created_by_user_id],
    )
    inventory_transactions = db.relationship(
        "InventoryTransaction",
        back_populates="sale",
        foreign_keys="InventoryTransaction.sale_id",
    )
    guest_loyalty_claim = db.relationship(
        "GuestLoyaltyClaim",
        back_populates="sale",
        uselist=False,
        passive_deletes="all",
    )


class SaleItem(db.Model):
    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)

    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False
    )

    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)


class GuestLoyaltyClaim(db.Model):
    __tablename__ = "guest_loyalty_claims"
    __table_args__ = (
        db.CheckConstraint(
            "length(token_hash) = 64",
            name="ck_guest_loyalty_claims_token_hash_length",
        ),
        db.CheckConstraint(
            "typeof(points) = 'integer' AND points >= 0",
            name="ck_guest_loyalty_claims_points",
        ),
        db.Index("uq_guest_loyalty_claims_sale", "sale_id", unique=True),
        db.Index(
            "uq_guest_loyalty_claims_token_hash",
            "token_hash",
            unique=True,
        ),
        db.Index("ix_guest_loyalty_claims_branch_id", "branch_id"),
        db.Index("ix_guest_loyalty_claims_expires_at", "expires_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash = db.Column(db.String(64), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    claimed_at = db.Column(db.DateTime)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=db.func.current_timestamp(),
    )

    sale = db.relationship("Sale", back_populates="guest_loyalty_claim")
    branch = db.relationship("Branch")


event.listen(
    GuestLoyaltyClaim.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER guest_loyalty_claims_validate_insert "
        "BEFORE INSERT ON guest_loyalty_claims BEGIN "
        "SELECT CASE WHEN NOT EXISTS ("
        "SELECT 1 FROM sales WHERE id = NEW.sale_id "
        "AND customer_id IS NULL AND branch_id = NEW.branch_id) "
        "THEN RAISE(ABORT, 'guest loyalty claim sale mismatch') END; "
        "SELECT CASE WHEN EXISTS ("
        "SELECT 1 FROM loyalty_transactions WHERE sale_id = NEW.sale_id) "
        "THEN RAISE(ABORT, 'sale already has loyalty points') END; "
        "END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    GuestLoyaltyClaim.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER guest_loyalty_claims_guard_update "
        "BEFORE UPDATE ON guest_loyalty_claims "
        "WHEN OLD.sale_id != NEW.sale_id "
        "OR OLD.branch_id != NEW.branch_id "
        "OR OLD.token_hash != NEW.token_hash "
        "OR OLD.points != NEW.points "
        "OR OLD.expires_at != NEW.expires_at "
        "OR OLD.created_at != NEW.created_at "
        "OR (OLD.claimed_at IS NOT NULL AND NEW.claimed_at IS NOT OLD.claimed_at) "
        "BEGIN SELECT RAISE(ABORT, 'guest loyalty claim is immutable'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    GuestLoyaltyClaim.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER loyalty_transactions_require_claim_consumed "
        "BEFORE INSERT ON loyalty_transactions WHEN EXISTS ("
        "SELECT 1 FROM guest_loyalty_claims "
        "WHERE sale_id = NEW.sale_id AND claimed_at IS NULL) "
        "BEGIN SELECT RAISE(ABORT, 'guest loyalty claim is not consumed'); END"
    ).execute_if(dialect="sqlite"),
)


def resolve_employee_branch_id(user):
    """Return an employee user's valid assigned branch ID, or None."""
    if user is None or (user.role or "").lower() != "employee":
        return None

    if user.employee_id is None:
        return None

    employee = db.session.get(Employee, user.employee_id)
    if employee is None:
        return None

    branch = db.session.get(Branch, employee.branch_id)
    if branch is None:
        return None

    return branch.id


class Shift(db.Model):
    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)

    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id"),
        nullable=False
    )

    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="scheduled")


class ExpenseCategory(db.Model):
    __tablename__ = "expense_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    expenses = db.relationship("Expense", back_populates="expense_category")


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)

    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False
    )

    category_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_categories.id"),
        nullable=True,
    )
    # Retained as a display-compatible snapshot for records created before
    # expense categories were introduced.
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    date = db.Column(db.Date, nullable=False)

    expense_category = db.relationship(
        "ExpenseCategory",
        back_populates="expenses",
    )


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False
    )

    action = db.Column(db.String(255), nullable=False)
    object_type = db.Column(db.String(100), nullable=False)
    object_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)