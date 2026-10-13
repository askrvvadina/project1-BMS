from app import db
from datetime import datetime


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

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    registration_date = db.Column(db.Date, nullable=False)


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.id"),
        nullable=True
    )

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


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)

    branch_id = db.Column(
        db.Integer,
        db.ForeignKey("branches.id"),
        nullable=False
    )

    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.String(255))
    date = db.Column(db.Date, nullable=False)


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