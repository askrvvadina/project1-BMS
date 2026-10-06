import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

import click
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()

ALLOWED_ROLES = {"employee", "manager"}
MIN_PASSWORD_LENGTH = 8


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def role_required(*allowed_roles):
    allowed = {role.lower() for role in allowed_roles}

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))

            user_role = (session.get("role") or "").lower()
            if user_role not in allowed:
                return render_template("403.html", requested_path=request.path), 403
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def create_app(config=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app.config["SECRET_KEY"] = os.environ.get("BMS_SECRET_KEY", "dev-secret-key-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bms.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    if config is not None:
        app.config.update(config)

    db.init_app(app)

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("403.html", requested_path=request.path), 403

    from app import models

    def product_form_options():
        return {
            "categories": models.Category.query.order_by(models.Category.name.asc()).all(),
            "branches": models.Branch.query.order_by(models.Branch.name.asc()).all(),
        }

    def validate_product_form(form_data, current_product_id=None):
        errors = []
        name = form_data["name"]
        sku = form_data["sku"]

        if not name:
            errors.append("Product name is required.")
        elif len(name) > 120:
            errors.append("Product name must be 120 characters or fewer.")

        if not sku:
            errors.append("SKU is required.")
        elif len(sku) > 100:
            errors.append("SKU must be 100 characters or fewer.")
        else:
            duplicate_query = models.Product.query.filter(
                db.func.lower(models.Product.sku) == sku.lower()
            )
            if current_product_id is not None:
                duplicate_query = duplicate_query.filter(
                    models.Product.id != current_product_id
                )
            if duplicate_query.first() is not None:
                errors.append("A product with this SKU already exists.")

        category = None
        try:
            category_id = int(form_data["category_id"])
            if category_id < 1:
                raise ValueError
            category = models.Category.query.filter_by(id=category_id).first()
        except ValueError:
            category = None
        if category is None:
            errors.append("Select a valid category.")

        branch = None
        try:
            branch_id = int(form_data["branch_id"])
            if branch_id < 1:
                raise ValueError
            branch = models.Branch.query.filter_by(id=branch_id).first()
        except ValueError:
            branch = None
        if branch is None:
            errors.append("Select a valid branch.")

        prices = {}
        for field, label in (
            ("purchase_price", "Purchase price"),
            ("selling_price", "Selling price"),
        ):
            try:
                value = Decimal(form_data[field])
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
                prices[field] = value
            except InvalidOperation:
                errors.append(f"{label} must be a valid non-negative number.")

        quantities = {}
        for field, label in (
            ("stock_quantity", "Stock quantity"),
            ("minimum_stock", "Minimum stock"),
        ):
            try:
                value = int(form_data[field])
                if value < 0:
                    raise ValueError
                quantities[field] = value
            except ValueError:
                errors.append(f"{label} must be a non-negative whole number.")

        for error in errors:
            flash(error, "error")

        if errors:
            return None

        return {
            "name": name,
            "sku": sku,
            "category_id": category.id,
            "branch_id": branch.id,
            "purchase_price": prices["purchase_price"],
            "selling_price": prices["selling_price"],
            "stock_quantity": quantities["stock_quantity"],
            "minimum_stock": quantities["minimum_stock"],
        }

    @app.cli.command("create-admin")
    def create_admin():
        username = click.prompt("Username", default="", show_default=False)
        email = click.prompt("Email", default="", show_default=False)
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        if not username or not username.strip():
            raise click.ClickException("Username is required.")

        if not email or not email.strip():
            raise click.ClickException("Email is required.")

        if len(password) < MIN_PASSWORD_LENGTH:
            raise click.ClickException(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")

        if models.User.query.filter(db.func.lower(models.User.username) == username.strip().lower()).first():
            raise click.ClickException("Username already exists.")

        if models.User.query.filter(db.func.lower(models.User.email) == email.strip().lower()).first():
            raise click.ClickException("Email already exists.")

        admin_user = models.User(
            username=username.strip(),
            email=email.strip().lower(),
            password_hash=generate_password_hash(password),
            role="admin",
            is_active=True,
        )

        db.session.add(admin_user)
        db.session.commit()
        click.echo(f"Admin account '{admin_user.username}' created successfully.")

    @app.route("/")
    @login_required
    @role_required("admin", "manager", "employee")
    def home():
        return render_template("dashboard.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            identifier = request.form.get("identifier", "").strip()
            password = request.form.get("password", "")

            if not identifier or not password:
                flash("Email/username and password are required.", "error")
                return render_template("login.html")

            user = None
            if identifier:
                normalized_identifier = identifier.lower()
                user = models.User.query.filter(
                    db.func.lower(models.User.username) == normalized_identifier
                ).first()
                if user is None:
                    user = models.User.query.filter(
                        db.func.lower(models.User.email) == normalized_identifier
                    ).first()

            if user is None or not check_password_hash(user.password_hash, password):
                flash("Invalid credentials.", "error")
                return render_template("login.html")

            if not user.is_active:
                flash("Your account is inactive.", "error")
                return render_template("login.html")

            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = (user.role or "").lower()
            flash("Login successful.", "success")
            return redirect(url_for("home"))

        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            role = request.form.get("role", "").strip().lower()

            if not username or not email or not password or not confirm_password or not role:
                flash("All registration fields are required.", "error")
                return render_template("register.html")

            if role not in ALLOWED_ROLES:
                flash("Only employee or manager roles can register publicly.", "error")
                return render_template("register.html")

            if len(password) < MIN_PASSWORD_LENGTH:
                flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "error")
                return render_template("register.html")

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("register.html")

            if models.User.query.filter(db.func.lower(models.User.username) == username.lower()).first():
                flash("Username already exists.", "error")
                return render_template("register.html")

            if models.User.query.filter(db.func.lower(models.User.email) == email.lower()).first():
                flash("Email already exists.", "error")
                return render_template("register.html")

            new_user = models.User(
                username=username,
                email=email.lower(),
                password_hash=generate_password_hash(password),
                role=role,
                is_active=True,
            )
            db.session.add(new_user)
            db.session.commit()

            flash("Registration successful. Please sign in.", "success")
            return redirect(url_for("login"))

        return render_template("register.html")

    @app.route("/logout")
    @login_required
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("login"))

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager", "employee")
    def profile():
        user = models.User.query.get(session.get("user_id"))

        if user is None:
            session.clear()
            flash("Your session has expired. Please sign in again.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_new_password = request.form.get("confirm_new_password", "")

            if not current_password or not new_password or not confirm_new_password:
                flash("All password fields are required.", "error")
                return render_template("profile.html", user=user)

            if not check_password_hash(user.password_hash, current_password):
                flash("Current password is incorrect.", "error")
                return render_template("profile.html", user=user)

            if len(new_password) < MIN_PASSWORD_LENGTH:
                flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters long.", "error")
                return render_template("profile.html", user=user)

            if new_password != confirm_new_password:
                flash("New passwords do not match.", "error")
                return render_template("profile.html", user=user)

            user.password_hash = generate_password_hash(new_password)
            db.session.commit()

            session.clear()
            flash("Password changed successfully. Please sign in again with your new password.", "success")
            return redirect(url_for("login"))

        return render_template("profile.html", user=user)

    @app.route("/users")
    @login_required
    @role_required("admin")
    def users():
        user_list = models.User.query.order_by(models.User.created_at.desc()).all()
        return render_template("users.html", users=user_list, current_user_id=session.get("user_id"))

    @app.route("/users/<int:user_id>/toggle-active", methods=["POST"])
    @login_required
    @role_required("admin")
    def toggle_user_active(user_id):
        target_user = models.User.query.get(user_id)
        if target_user is None:
            flash("User not found.", "error")
            return redirect(url_for("users"))

        if target_user.id == session.get("user_id"):
            flash("Admin accounts cannot deactivate their own active session.", "error")
            return redirect(url_for("users"))

        target_user.is_active = not target_user.is_active
        db.session.commit()

        status = "activated" if target_user.is_active else "deactivated"
        flash(f"User '{target_user.username}' has been {status}.", "success")
        return redirect(url_for("users"))

    @app.route("/branches")
    @login_required
    @role_required("admin")
    def branches():
        branch_list = models.Branch.query.order_by(models.Branch.name.asc()).all()
        return render_template("branches.html", branches=branch_list)

    @app.route("/branches/<int:branch_id>")
    @login_required
    @role_required("admin")
    def branch_detail(branch_id):
        branch = models.Branch.query.get(branch_id)
        if branch is None:
            flash("Branch not found.", "error")
            return redirect(url_for("branches"))

        employee_count = models.Employee.query.filter_by(branch_id=branch.id).count()
        product_count = models.Product.query.filter_by(branch_id=branch.id).count()
        sales_count = models.Sale.query.filter_by(branch_id=branch.id).count()
        return render_template(
            "branch_detail.html",
            branch=branch,
            employee_count=employee_count,
            product_count=product_count,
            sales_count=sales_count,
        )

    @app.route("/branches/create", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def branch_create():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            address = request.form.get("address", "").strip()
            phone = request.form.get("phone", "").strip()
            status = request.form.get("status", "").strip().lower()

            if not name:
                flash("Branch name is required.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            if not address:
                flash("Branch address is required.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            if status not in {"active", "inactive"}:
                flash("Branch status must be either active or inactive.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            branch = models.Branch(name=name, address=address, phone=phone or None, status=status)
            db.session.add(branch)
            db.session.commit()
            flash("Branch created successfully.", "success")
            return redirect(url_for("branches"))

        return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

    @app.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def branch_edit(branch_id):
        branch = models.Branch.query.get(branch_id)
        if branch is None:
            flash("Branch not found.", "error")
            return redirect(url_for("branches"))

        if request.method == "POST":
            branch.name = request.form.get("name", "").strip()
            branch.address = request.form.get("address", "").strip()
            branch.phone = request.form.get("phone", "").strip() or None
            branch.status = request.form.get("status", "").strip().lower()

            if not branch.name:
                flash("Branch name is required.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            if not branch.address:
                flash("Branch address is required.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            if branch.status not in {"active", "inactive"}:
                flash("Branch status must be either active or inactive.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            db.session.commit()
            flash("Branch updated successfully.", "success")
            return redirect(url_for("branches"))

        return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

    @app.route("/branches/<int:branch_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin")
    def branch_delete(branch_id):
        branch = models.Branch.query.get(branch_id)
        if branch is None:
            flash("Branch not found.", "error")
            return redirect(url_for("branches"))

        dependent_checks = [
            (models.Employee.query.filter_by(branch_id=branch.id).first(), "employees"),
            (models.Product.query.filter_by(branch_id=branch.id).first(), "products"),
            (models.Sale.query.filter_by(branch_id=branch.id).first(), "sales"),
            (models.Expense.query.filter_by(branch_id=branch.id).first(), "expenses"),
        ]

        if any(record is not None for record, _ in dependent_checks):
            flash("This branch cannot be deleted because it has related business records in the system.", "error")
            return redirect(url_for("branches"))

        db.session.delete(branch)
        db.session.commit()
        flash("Branch deleted successfully.", "success")
        return redirect(url_for("branches"))

    @app.route("/employees")
    @login_required
    @role_required("admin", "manager")
    def employees():
        query = models.Employee.query

        search_term = request.args.get("q", "").strip()
        branch_filter = request.args.get("branch_id", "").strip()
        status_filter = request.args.get("status", "").strip().lower()
        sort_option = request.args.get("sort", "hire_newest").strip().lower()

        if search_term:
            pattern = f"%{search_term}%"
            query = query.filter(
                or_(
                    models.Employee.full_name.ilike(pattern),
                    models.Employee.position.ilike(pattern),
                    models.Employee.email.ilike(pattern),
                    models.Employee.phone.ilike(pattern),
                )
            )

        if branch_filter:
            try:
                branch_id = int(branch_filter)
            except ValueError:
                branch_id = None
            if branch_id is not None and models.Branch.query.get(branch_id) is not None:
                query = query.filter(models.Employee.branch_id == branch_id)

        if status_filter in {"active", "inactive"}:
            query = query.filter(models.Employee.status == status_filter)

        sort_map = {
            "name_asc": models.Employee.full_name.asc(),
            "name_desc": models.Employee.full_name.desc(),
            "hire_newest": models.Employee.hire_date.desc(),
            "hire_oldest": models.Employee.hire_date.asc(),
        }
        selected_sort = sort_map.get(sort_option, models.Employee.hire_date.desc())
        employee_rows = []
        for employee in query.order_by(selected_sort).all():
            branch = models.Branch.query.get(employee.branch_id)
            employee_rows.append({
                "employee": employee,
                "branch_name": branch.name if branch else "Unknown branch",
            })

        branch_options = models.Branch.query.order_by(models.Branch.name.asc()).all()
        return render_template(
            "employees.html",
            employees=employee_rows,
            branches=branch_options,
            search_query=search_term,
            active_branch=branch_filter,
            active_status=status_filter,
            active_sort=sort_option,
        )

    @app.route("/employees/create", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def employee_create():
        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        if not branches:
            flash("A branch must be created before adding employee records.", "error")
            return redirect(url_for("branches"))

        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            position = request.form.get("position", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            hire_date_raw = request.form.get("hire_date", "").strip()
            branch_id_raw = request.form.get("branch_id", "").strip()
            status = request.form.get("status", "").strip().lower()

            form_data = {
                "full_name": full_name,
                "position": position,
                "phone": phone,
                "email": email,
                "hire_date": hire_date_raw,
                "branch_id": branch_id_raw,
                "status": status,
            }

            if not full_name:
                flash("Full name is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not position:
                flash("Position is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not hire_date_raw:
                flash("Hire date is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            try:
                parsed_hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Hire date must be a valid date in YYYY-MM-DD format.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not branch_id_raw:
                flash("Branch is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            try:
                branch_id = int(branch_id_raw)
            except ValueError:
                flash("A valid branch selection is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            branch = models.Branch.query.get(branch_id)
            if branch is None:
                flash("The selected branch does not exist.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if status not in {"active", "inactive"}:
                flash("Employee status must be either active or inactive.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if email:
                existing_email = models.Employee.query.filter(
                    db.func.lower(models.Employee.email) == email.lower()
                ).first()
                if existing_email is not None:
                    flash("An employee with this email already exists.", "error")
                    return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            employee = models.Employee(
                full_name=full_name,
                position=position,
                phone=phone or None,
                email=email or None,
                hire_date=parsed_hire_date,
                branch_id=branch.id,
                status=status,
            )
            db.session.add(employee)
            db.session.commit()
            flash("Employee created successfully.", "success")
            return redirect(url_for("employees"))

        return render_template("employee_form.html", employee=None, branches=branches, mode="create")

    @app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def employee_edit(employee_id):
        employee = models.Employee.query.get(employee_id)
        if employee is None:
            flash("Employee not found.", "error")
            return redirect(url_for("employees"))

        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        if not branches:
            flash("A branch must be created before editing employee records.", "error")
            return redirect(url_for("branches"))

        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            position = request.form.get("position", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            hire_date_raw = request.form.get("hire_date", "").strip()
            branch_id_raw = request.form.get("branch_id", "").strip()
            status = request.form.get("status", "").strip().lower()

            form_data = {
                "full_name": full_name,
                "position": position,
                "phone": phone,
                "email": email,
                "hire_date": hire_date_raw,
                "branch_id": branch_id_raw,
                "status": status,
            }

            if not full_name:
                flash("Full name is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not position:
                flash("Position is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not hire_date_raw:
                flash("Hire date is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            try:
                parsed_hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Hire date must be a valid date in YYYY-MM-DD format.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not branch_id_raw:
                flash("Branch is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            try:
                branch_id = int(branch_id_raw)
            except ValueError:
                flash("A valid branch selection is required.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            branch = models.Branch.query.get(branch_id)
            if branch is None:
                flash("The selected branch does not exist.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if status not in {"active", "inactive"}:
                flash("Employee status must be either active or inactive.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if email:
                duplicate_email = models.Employee.query.filter(
                    db.func.lower(models.Employee.email) == email.lower(),
                    models.Employee.id != employee.id,
                ).first()
                if duplicate_email is not None:
                    flash("An employee with this email already exists.", "error")
                    return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            employee.full_name = full_name
            employee.position = position
            employee.phone = phone or None
            employee.email = email or None
            employee.hire_date = parsed_hire_date
            employee.branch_id = branch.id
            employee.status = status
            db.session.commit()
            flash("Employee updated successfully.", "success")
            return redirect(url_for("employees"))

        form_data = {
            "full_name": employee.full_name,
            "position": employee.position,
            "phone": employee.phone or "",
            "email": employee.email or "",
            "hire_date": employee.hire_date.isoformat() if employee.hire_date else "",
            "branch_id": str(employee.branch_id),
            "status": employee.status,
        }
        return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

    @app.route("/employees/<int:employee_id>")
    @login_required
    @role_required("admin", "manager")
    def employee_detail(employee_id):
        employee = models.Employee.query.get(employee_id)
        if employee is None:
            flash("Employee not found.", "error")
            return redirect(url_for("employees"))

        branch = models.Branch.query.get(employee.branch_id)
        return render_template("employee_detail.html", employee=employee, branch=branch)

    @app.route("/employees/<int:employee_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin", "manager")
    def employee_delete(employee_id):
        employee = models.Employee.query.get(employee_id)
        if employee is None:
            flash("Employee not found.", "error")
            return redirect(url_for("employees"))

        if models.Shift.query.filter_by(employee_id=employee.id).first() is not None:
            flash("This employee cannot be deleted because shift records are associated with them.", "error")
            return redirect(url_for("employees"))

        db.session.delete(employee)
        db.session.commit()
        flash("Employee deleted successfully.", "success")
        return redirect(url_for("employees"))

    @app.route("/shifts")
    @login_required
    @role_required("admin", "manager")
    def shifts():
        return render_template(
            "placeholder.html",
            page_title="Shifts",
            message="Shift management will be implemented in a later development stage."
        )

    @app.route("/products")
    @login_required
    @role_required("admin", "manager", "employee")
    def products():
        search_query = request.args.get("q", "").strip()
        category_filter = request.args.get("category_id", "").strip()
        branch_filter = request.args.get("branch_id", "").strip()
        active_sort = request.args.get("sort", "name_asc")

        query = db.session.query(
            models.Product,
            models.Category.name,
            models.Branch.name,
        ).join(
            models.Category, models.Product.category_id == models.Category.id
        ).join(
            models.Branch, models.Product.branch_id == models.Branch.id
        )

        if search_query:
            query = query.filter(
                or_(
                    models.Product.name.ilike(f"%{search_query}%"),
                    models.Product.sku.ilike(f"%{search_query}%"),
                )
            )

        if category_filter:
            try:
                category_filter_id = int(category_filter)
            except ValueError:
                category_filter_id = -1
            query = query.filter(models.Product.category_id == category_filter_id)

        if branch_filter:
            try:
                branch_filter_id = int(branch_filter)
            except ValueError:
                branch_filter_id = -1
            query = query.filter(models.Product.branch_id == branch_filter_id)

        sort_columns = {
            "name_asc": models.Product.name.asc(),
            "name_desc": models.Product.name.desc(),
            "price_low": models.Product.selling_price.asc(),
            "price_high": models.Product.selling_price.desc(),
            "stock_low": models.Product.stock_quantity.asc(),
            "stock_high": models.Product.stock_quantity.desc(),
        }
        if active_sort not in sort_columns:
            active_sort = "name_asc"
        product_rows = query.order_by(sort_columns[active_sort]).all()

        return render_template(
            "products.html",
            product_rows=product_rows,
            search_query=search_query,
            category_filter=category_filter,
            branch_filter=branch_filter,
            active_sort=active_sort,
            **product_form_options(),
        )

    @app.route("/products/<int:product_id>")
    @login_required
    @role_required("admin", "manager", "employee")
    def product_detail(product_id):
        product = models.Product.query.filter_by(id=product_id).first()
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("products"))

        category = models.Category.query.filter_by(id=product.category_id).first()
        branch = models.Branch.query.filter_by(id=product.branch_id).first()
        return render_template(
            "product_detail.html",
            product=product,
            category=category,
            branch=branch,
            low_stock=product.stock_quantity <= product.minimum_stock,
        )

    @app.route("/products/create", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def product_create():
        form_data = {
            "name": "",
            "sku": "",
            "category_id": "",
            "branch_id": "",
            "purchase_price": "",
            "selling_price": "",
            "stock_quantity": "0",
            "minimum_stock": "0",
        }

        if request.method == "POST":
            for field in form_data:
                form_data[field] = request.form.get(field, "").strip()

            product_data = validate_product_form(form_data)
            if product_data is not None:
                product = models.Product(**product_data)
                db.session.add(product)
                db.session.commit()
                flash("Product created successfully.", "success")
                return redirect(url_for("products"))

        return render_template(
            "product_form.html",
            form_data=form_data,
            mode="create",
            **product_form_options(),
        )

    @app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def product_edit(product_id):
        product = models.Product.query.filter_by(id=product_id).first()
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("products"))

        form_data = {
            "name": product.name,
            "sku": product.sku,
            "category_id": str(product.category_id),
            "branch_id": str(product.branch_id),
            "purchase_price": str(product.purchase_price),
            "selling_price": str(product.selling_price),
            "stock_quantity": str(product.stock_quantity),
            "minimum_stock": str(product.minimum_stock),
        }

        if request.method == "POST":
            for field in form_data:
                form_data[field] = request.form.get(field, "").strip()

            product_data = validate_product_form(form_data, product.id)
            if product_data is not None:
                for field, value in product_data.items():
                    setattr(product, field, value)
                db.session.commit()
                flash("Product updated successfully.", "success")
                return redirect(url_for("product_detail", product_id=product.id))

        return render_template(
            "product_form.html",
            form_data=form_data,
            product_id=product.id,
            mode="edit",
            **product_form_options(),
        )

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin", "manager")
    def product_delete(product_id):
        product = models.Product.query.filter_by(id=product_id).first()
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("products"))

        if models.SaleItem.query.filter_by(product_id=product.id).first() is not None:
            flash("This product cannot be deleted because it is referenced by sale history.", "error")
            return redirect(url_for("product_detail", product_id=product.id))

        if models.InventoryTransaction.query.filter_by(product_id=product.id).first() is not None:
            flash("This product cannot be deleted because it has inventory transaction history.", "error")
            return redirect(url_for("product_detail", product_id=product.id))

        db.session.delete(product)
        db.session.commit()
        flash("Product deleted successfully.", "success")
        return redirect(url_for("products"))

    @app.route("/categories")
    @login_required
    @role_required("admin", "manager")
    def categories():
        category_list = models.Category.query.order_by(models.Category.name.asc()).all()
        return render_template("categories.html", categories=category_list)

    @app.route("/categories/create", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def category_create():
        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if not name:
                flash("Category name is required.", "error")
                return render_template(
                    "category_form.html",
                    category=None,
                    category_name=name,
                    mode="create",
                )

            duplicate = models.Category.query.filter(
                db.func.lower(models.Category.name) == name.lower()
            ).first()
            if duplicate is not None:
                flash("A category with this name already exists.", "error")
                return render_template(
                    "category_form.html",
                    category=None,
                    category_name=name,
                    mode="create",
                )

            category = models.Category(name=name)
            db.session.add(category)
            db.session.commit()
            flash("Category created successfully.", "success")
            return redirect(url_for("categories"))

        return render_template(
            "category_form.html",
            category=None,
            category_name="",
            mode="create",
        )

    @app.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def category_edit(category_id):
        category = models.Category.query.get(category_id)
        if category is None:
            flash("Category not found.", "error")
            return redirect(url_for("categories"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if not name:
                flash("Category name is required.", "error")
                return render_template(
                    "category_form.html",
                    category=category,
                    category_name=name,
                    mode="edit",
                )

            duplicate = models.Category.query.filter(
                db.func.lower(models.Category.name) == name.lower(),
                models.Category.id != category.id,
            ).first()
            if duplicate is not None:
                flash("A category with this name already exists.", "error")
                return render_template(
                    "category_form.html",
                    category=category,
                    category_name=name,
                    mode="edit",
                )

            category.name = name
            db.session.commit()
            flash("Category updated successfully.", "success")
            return redirect(url_for("categories"))

        return render_template(
            "category_form.html",
            category=category,
            category_name=category.name,
            mode="edit",
        )

    @app.route("/categories/<int:category_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin", "manager")
    def category_delete(category_id):
        category = models.Category.query.get(category_id)
        if category is None:
            flash("Category not found.", "error")
            return redirect(url_for("categories"))

        if models.Product.query.filter_by(category_id=category.id).first() is not None:
            flash("This category cannot be deleted because it is used by one or more products.", "error")
            return redirect(url_for("categories"))

        db.session.delete(category)
        db.session.commit()
        flash("Category deleted successfully.", "success")
        return redirect(url_for("categories"))

    @app.route("/inventory")
    @login_required
    @role_required("admin", "manager")
    def inventory():
        return render_template(
            "placeholder.html",
            page_title="Inventory",
            message="Inventory tracking will be implemented in a later development stage."
        )

    @app.route("/sales")
    @login_required
    @role_required("admin", "manager", "employee")
    def sales():
        return render_template(
            "placeholder.html",
            page_title="Sales",
            message="Sales management will be implemented in a later development stage."
        )

    @app.route("/customers")
    @login_required
    @role_required("admin", "manager")
    def customers():
        return render_template(
            "placeholder.html",
            page_title="Customers",
            message="Customer management will be implemented in a later development stage."
        )

    @app.route("/analytics")
    @login_required
    @role_required("admin", "manager")
    def analytics():
        return render_template(
            "placeholder.html",
            page_title="Analytics",
            message="Analytics and reporting will be implemented in a later development stage."
        )

    return app
