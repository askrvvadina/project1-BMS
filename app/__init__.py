import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

import click
from flask import Flask, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_, or_, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()

ALLOWED_ROLES = {"employee", "manager"}
MIN_PASSWORD_LENGTH = 8


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            flash("Войдите, чтобы продолжить.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def role_required(*allowed_roles):
    allowed = {role.lower() for role in allowed_roles}

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                flash("Войдите, чтобы продолжить.", "error")
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

    def resolve_management_branch(explicit_branch_id=None):
        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        has_explicit_branch = (
            explicit_branch_id is not None
            and str(explicit_branch_id).strip() != ""
        )
        invalid_explicit_branch = False

        if has_explicit_branch:
            try:
                parsed_branch_id = int(str(explicit_branch_id).strip())
            except ValueError:
                parsed_branch_id = -1

            selected_branch = (
                models.Branch.query.filter_by(id=parsed_branch_id).first()
                if parsed_branch_id > 0
                else None
            )
            if selected_branch is not None:
                session["management_branch_id"] = selected_branch.id
                return selected_branch, False
            invalid_explicit_branch = True

        stored_branch_id = session.get("management_branch_id")
        if stored_branch_id is not None:
            try:
                parsed_stored_branch_id = int(stored_branch_id)
            except (TypeError, ValueError):
                parsed_stored_branch_id = -1

            stored_branch = (
                models.Branch.query.filter_by(id=parsed_stored_branch_id).first()
                if parsed_stored_branch_id > 0
                else None
            )
            if stored_branch is not None:
                return stored_branch, invalid_explicit_branch
            session.pop("management_branch_id", None)

        return (branches[0] if branches else None), invalid_explicit_branch

    def sync_low_stock_notifications(branch_inventory):
        product = branch_inventory.product
        notification_prefix = (
            f"Low stock alert [BranchInventory #{branch_inventory.id}]:"
        )
        notifications = models.Notification.query.filter(
            models.Notification.message.startswith(notification_prefix),
        ).all()

        legacy_prefix = f"Low stock alert [Product #{product.id}]:"
        legacy_notifications = models.Notification.query.filter(
            models.Notification.message.startswith(legacy_prefix),
        ).all()
        for notification in legacy_notifications:
            notification.is_read = True
            if not notification.message.startswith("Resolved: "):
                notification.message = f"Resolved: {notification.message}"

        if branch_inventory.stock_quantity > branch_inventory.minimum_stock:
            for notification in notifications:
                notification.is_read = True
                if not notification.message.startswith("Resolved: "):
                    notification.message = f"Resolved: {notification.message}"
            return

        branch_name = branch_inventory.branch.name
        message = (
            f"{notification_prefix} Product: {product.name[:55]}; "
            f"SKU: {product.sku[:40]}; Branch: {branch_name[:40]}; "
            f"Current stock: {branch_inventory.stock_quantity}."
        )
        managers = models.User.query.filter(
            models.User.role.in_(("admin", "manager")),
            models.User.is_active.is_(True),
        ).all()
        active_by_user = {}
        for notification in notifications:
            if notification.user_id in active_by_user:
                notification.is_read = True
            else:
                active_by_user[notification.user_id] = notification

        for user in managers:
            notification = active_by_user.get(user.id)
            if notification is None:
                db.session.add(
                    models.Notification(
                        user_id=user.id,
                        message=message,
                        is_read=False,
                    )
                )
            elif notification.message != message:
                notification.message = message

    @app.context_processor
    def notification_context():
        user_id = session.get("user_id")
        unread_notification_count = 0
        if user_id:
            unread_notification_count = models.Notification.query.filter_by(
                user_id=user_id,
                is_read=False,
            ).count()
        return {"unread_notification_count": unread_notification_count}

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
            errors.append("Укажите название товара.")
        elif len(name) > 120:
            errors.append("Название товара не должно превышать 120 символов.")

        if not sku:
            errors.append("Укажите артикул.")
        elif len(sku) > 100:
            errors.append("Артикул не должен превышать 100 символов.")
        else:
            duplicate_query = models.Product.query.filter(
                db.func.lower(models.Product.sku) == sku.lower()
            )
            if current_product_id is not None:
                duplicate_query = duplicate_query.filter(
                    models.Product.id != current_product_id
                )
            if duplicate_query.first() is not None:
                errors.append("Товар с таким артикулом уже существует.")

        category = None
        try:
            category_id = int(form_data["category_id"])
            if category_id < 1:
                raise ValueError
            category = models.Category.query.filter_by(id=category_id).first()
        except ValueError:
            category = None
        if category is None:
            errors.append("Выберите существующую категорию.")

        branch = None
        try:
            branch_id = int(form_data["branch_id"])
            if branch_id < 1:
                raise ValueError
            branch = models.Branch.query.filter_by(id=branch_id).first()
        except ValueError:
            branch = None
        if branch is None:
            errors.append("Выберите существующий филиал.")

        prices = {}
        for field, label in (
            ("purchase_price", "Закупочная цена"),
            ("selling_price", "Цена продажи"),
        ):
            try:
                value = Decimal(form_data[field])
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
                prices[field] = value
            except InvalidOperation:
                errors.append(f"Поле «{label}» должно содержать корректное неотрицательное число.")

        quantities = {}
        quantity_fields = [("minimum_stock", "Минимальный остаток")]
        if current_product_id is None:
            quantity_fields.insert(0, ("stock_quantity", "Количество на складе"))

        for field, label in quantity_fields:
            try:
                value = int(form_data[field])
                if value < 0:
                    raise ValueError
                quantities[field] = value
            except ValueError:
                errors.append(f"Поле «{label}» должно содержать неотрицательное целое число.")

        for error in errors:
            flash(error, "error")

        if errors:
            return None

        product_data = {
            "name": name,
            "sku": sku,
            "category_id": category.id,
            "branch_id": branch.id,
            "purchase_price": prices["purchase_price"],
            "selling_price": prices["selling_price"],
            "minimum_stock": quantities["minimum_stock"],
        }
        if current_product_id is None:
            product_data["stock_quantity"] = quantities["stock_quantity"]

        return product_data

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
                flash("Укажите электронную почту или имя пользователя и пароль.", "error")
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
                flash("Неверные данные для входа.", "error")
                return render_template("login.html")

            if not user.is_active:
                flash("Ваша учётная запись неактивна.", "error")
                return render_template("login.html")

            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = (user.role or "").lower()
            flash("Вход выполнен.", "success")
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
                flash("Заполните все поля регистрации.", "error")
                return render_template("register.html")

            if role not in ALLOWED_ROLES:
                flash("Самостоятельная регистрация доступна только для ролей «Сотрудник» и «Менеджер».", "error")
                return render_template("register.html")

            if len(password) < MIN_PASSWORD_LENGTH:
                flash(f"Пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов.", "error")
                return render_template("register.html")

            if password != confirm_password:
                flash("Пароли не совпадают.", "error")
                return render_template("register.html")

            if models.User.query.filter(db.func.lower(models.User.username) == username.lower()).first():
                flash("Это имя пользователя уже занято.", "error")
                return render_template("register.html")

            if models.User.query.filter(db.func.lower(models.User.email) == email.lower()).first():
                flash("Эта электронная почта уже зарегистрирована.", "error")
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

            flash("Регистрация завершена. Войдите в систему.", "success")
            return redirect(url_for("login"))

        return render_template("register.html")

    @app.route("/logout")
    @login_required
    def logout():
        session.clear()
        flash("Вы вышли из системы.", "success")
        return redirect(url_for("login"))

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager", "employee")
    def profile():
        user = models.User.query.get(session.get("user_id"))

        if user is None:
            session.clear()
            flash("Срок действия сеанса истёк. Войдите в систему снова.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_new_password = request.form.get("confirm_new_password", "")

            if not current_password or not new_password or not confirm_new_password:
                flash("Заполните все поля пароля.", "error")
                return render_template("profile.html", user=user)

            if not check_password_hash(user.password_hash, current_password):
                flash("Текущий пароль указан неверно.", "error")
                return render_template("profile.html", user=user)

            if len(new_password) < MIN_PASSWORD_LENGTH:
                flash(f"Новый пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов.", "error")
                return render_template("profile.html", user=user)

            if new_password != confirm_new_password:
                flash("Новые пароли не совпадают.", "error")
                return render_template("profile.html", user=user)

            user.password_hash = generate_password_hash(new_password)
            db.session.commit()

            session.clear()
            flash("Пароль изменён. Войдите в систему с новым паролем.", "success")
            return redirect(url_for("login"))

        return render_template("profile.html", user=user)

    @app.route("/users")
    @login_required
    @role_required("admin")
    def users():
        user_list = models.User.query.order_by(models.User.created_at.desc()).all()
        employee_options = (
            db.session.query(models.Employee, models.Branch.name)
            .outerjoin(models.Branch, models.Employee.branch_id == models.Branch.id)
            .order_by(models.Employee.full_name.asc())
            .all()
        )
        employee_user_ids = {
            user.employee_id: user.id
            for user in user_list
            if user.employee_id is not None
        }
        employee_branch_names = {
            employee.id: branch_name
            for employee, branch_name in employee_options
        }
        employees_by_id = {
            row[0].id: row[0]
            for row in employee_options
        }
        return render_template(
            "users.html",
            users=user_list,
            current_user_id=session.get("user_id"),
            employee_options=employee_options,
            employee_user_ids=employee_user_ids,
            employee_branch_names=employee_branch_names,
            employees_by_id=employees_by_id,
        )

    @app.route("/users/<int:user_id>/employee", methods=["POST"])
    @login_required
    @role_required("admin")
    def update_user_employee(user_id):
        target_user = models.User.query.get(user_id)
        if target_user is None:
            flash("Пользователь не найден.", "error")
            return redirect(url_for("users"))

        if (target_user.role or "").lower() != "employee":
            flash("Связать сотрудника можно только с учётной записью сотрудника.", "error")
            return redirect(url_for("users"))

        employee_id_value = request.form.get("employee_id", "").strip()
        employee = None
        if employee_id_value:
            try:
                employee_id = int(employee_id_value)
                if employee_id < 1:
                    raise ValueError
            except ValueError:
                flash("Выберите существующего сотрудника.", "error")
                return redirect(url_for("users"))

            employee = models.Employee.query.filter_by(id=employee_id).first()
            if employee is None:
                flash("Выберите существующего сотрудника.", "error")
                return redirect(url_for("users"))

            already_linked = models.User.query.filter(
                models.User.employee_id == employee.id,
                models.User.id != target_user.id,
            ).first()
            if already_linked is not None:
                flash("Этот сотрудник уже связан с другой учётной записью.", "error")
                return redirect(url_for("users"))

        target_user.employee_id = employee.id if employee is not None else None
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Не удалось сохранить связь с сотрудником. Обновите страницу и повторите попытку.", "error")
            return redirect(url_for("users"))

        if employee is None:
            flash("Связь с сотрудником снята.", "success")
        else:
            flash("Учётная запись связана с сотрудником.", "success")
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>/toggle-active", methods=["POST"])
    @login_required
    @role_required("admin")
    def toggle_user_active(user_id):
        target_user = models.User.query.get(user_id)
        if target_user is None:
            flash("Пользователь не найден.", "error")
            return redirect(url_for("users"))

        if target_user.id == session.get("user_id"):
            flash("Нельзя деактивировать собственную учётную запись администратора.", "error")
            return redirect(url_for("users"))

        target_user.is_active = not target_user.is_active
        db.session.commit()

        status = "активирована" if target_user.is_active else "деактивирована"
        flash(f"Учётная запись пользователя «{target_user.username}» {status}.", "success")
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
            flash("Филиал не найден.", "error")
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
                flash("Укажите название филиала.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            if not address:
                flash("Укажите адрес филиала.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            if status not in {"active", "inactive"}:
                flash("Выберите корректный статус филиала.", "error")
                return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

            branch = models.Branch(name=name, address=address, phone=phone or None, status=status)
            db.session.add(branch)
            db.session.commit()
            flash("Филиал создан.", "success")
            return redirect(url_for("branches"))

        return render_template("branch_form.html", branch=None, mode="create", form_action=url_for("branch_create"))

    @app.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin")
    def branch_edit(branch_id):
        branch = models.Branch.query.get(branch_id)
        if branch is None:
            flash("Филиал не найден.", "error")
            return redirect(url_for("branches"))

        if request.method == "POST":
            branch.name = request.form.get("name", "").strip()
            branch.address = request.form.get("address", "").strip()
            branch.phone = request.form.get("phone", "").strip() or None
            branch.status = request.form.get("status", "").strip().lower()

            if not branch.name:
                flash("Укажите название филиала.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            if not branch.address:
                flash("Укажите адрес филиала.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            if branch.status not in {"active", "inactive"}:
                flash("Выберите корректный статус филиала.", "error")
                return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

            db.session.commit()
            flash("Данные филиала обновлены.", "success")
            return redirect(url_for("branches"))

        return render_template("branch_form.html", branch=branch, mode="edit", form_action=url_for("branch_edit", branch_id=branch.id))

    @app.route("/branches/<int:branch_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin")
    def branch_delete(branch_id):
        branch = models.Branch.query.get(branch_id)
        if branch is None:
            flash("Филиал не найден.", "error")
            return redirect(url_for("branches"))

        dependent_checks = [
            (models.Employee.query.filter_by(branch_id=branch.id).first(), "employees"),
            (models.Product.query.filter_by(branch_id=branch.id).first(), "products"),
            (models.Sale.query.filter_by(branch_id=branch.id).first(), "sales"),
            (models.Expense.query.filter_by(branch_id=branch.id).first(), "expenses"),
        ]

        if any(record is not None for record, _ in dependent_checks):
            flash("Нельзя удалить филиал: с ним связаны рабочие данные системы.", "error")
            return redirect(url_for("branches"))

        db.session.delete(branch)
        db.session.commit()
        flash("Филиал удалён.", "success")
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
                "branch_name": branch.name if branch else "Неизвестный филиал",
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
            flash("Перед добавлением сотрудников необходимо создать филиал.", "error")
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
                flash("Укажите ФИО сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not position:
                flash("Укажите должность сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not hire_date_raw:
                flash("Укажите дату приёма на работу.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            try:
                parsed_hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Укажите корректную дату приёма в формате ГГГГ-ММ-ДД.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if not branch_id_raw:
                flash("Выберите филиал.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            try:
                branch_id = int(branch_id_raw)
            except ValueError:
                flash("Выберите корректный филиал.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            branch = models.Branch.query.get(branch_id)
            if branch is None:
                flash("Выбранный филиал не существует.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if status not in {"active", "inactive"}:
                flash("Выберите корректный статус сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="create")

            if email:
                existing_email = models.Employee.query.filter(
                    db.func.lower(models.Employee.email) == email.lower()
                ).first()
                if existing_email is not None:
                    flash("Сотрудник с такой электронной почтой уже существует.", "error")
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
            flash("Сотрудник добавлен.", "success")
            return redirect(url_for("employees"))

        return render_template("employee_form.html", employee=None, branches=branches, mode="create")

    @app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def employee_edit(employee_id):
        employee = models.Employee.query.get(employee_id)
        if employee is None:
            flash("Сотрудник не найден.", "error")
            return redirect(url_for("employees"))

        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        if not branches:
            flash("Перед редактированием сотрудников необходимо создать филиал.", "error")
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
                flash("Укажите ФИО сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not position:
                flash("Укажите должность сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not hire_date_raw:
                flash("Укажите дату приёма на работу.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            try:
                parsed_hire_date = datetime.strptime(hire_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Укажите корректную дату приёма в формате ГГГГ-ММ-ДД.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if not branch_id_raw:
                flash("Выберите филиал.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            try:
                branch_id = int(branch_id_raw)
            except ValueError:
                flash("Выберите корректный филиал.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            branch = models.Branch.query.get(branch_id)
            if branch is None:
                flash("Выбранный филиал не существует.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if status not in {"active", "inactive"}:
                flash("Выберите корректный статус сотрудника.", "error")
                return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            if email:
                duplicate_email = models.Employee.query.filter(
                    db.func.lower(models.Employee.email) == email.lower(),
                    models.Employee.id != employee.id,
                ).first()
                if duplicate_email is not None:
                    flash("Сотрудник с такой электронной почтой уже существует.", "error")
                    return render_template("employee_form.html", employee=form_data, branches=branches, mode="edit", employee_id=employee.id)

            employee.full_name = full_name
            employee.position = position
            employee.phone = phone or None
            employee.email = email or None
            employee.hire_date = parsed_hire_date
            employee.branch_id = branch.id
            employee.status = status
            db.session.commit()
            flash("Данные сотрудника обновлены.", "success")
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
            flash("Сотрудник не найден.", "error")
            return redirect(url_for("employees"))

        branch = models.Branch.query.get(employee.branch_id)
        return render_template("employee_detail.html", employee=employee, branch=branch)

    @app.route("/employees/<int:employee_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin", "manager")
    def employee_delete(employee_id):
        employee = models.Employee.query.get(employee_id)
        if employee is None:
            flash("Сотрудник не найден.", "error")
            return redirect(url_for("employees"))

        if models.Shift.query.filter_by(employee_id=employee.id).first() is not None:
            flash("Нельзя удалить сотрудника: с ним связаны записи о сменах.", "error")
            return redirect(url_for("employees"))

        db.session.delete(employee)
        db.session.commit()
        flash("Сотрудник удалён.", "success")
        return redirect(url_for("employees"))

    @app.route("/shifts")
    @login_required
    @role_required("admin", "manager")
    def shifts():
        return render_template(
            "placeholder.html",
            page_title="Смены",
            message="Управление сменами будет добавлено на следующем этапе разработки."
        )

    @app.route("/products")
    @login_required
    @role_required("admin", "manager", "employee")
    def products():
        search_query = request.args.get("q", "").strip()
        category_filter = request.args.get("category_id", "").strip()
        active_sort = request.args.get("sort", "name_asc")
        role = (session.get("role") or "").lower()
        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        branch_error = False

        if role in {"admin", "manager"}:
            selected_branch, branch_error = resolve_management_branch(
                request.args.get("branch_id")
            )
        else:
            user = db.session.get(models.User, session.get("user_id"))
            employee_branch_id = (
                models.resolve_employee_branch_id(user) if user is not None else None
            )
            selected_branch = (
                db.session.get(models.Branch, employee_branch_id)
                if employee_branch_id is not None
                else None
            )
            branches = [selected_branch] if selected_branch is not None else []

        if branch_error:
            flash("Выбранный филиал не найден. Показан безопасный филиал.", "error")

        branch_filter = (
            str(selected_branch.id) if selected_branch is not None else ""
        )
        query = db.session.query(
            models.Product,
            models.Category.name,
            models.BranchInventory,
        ).join(
            models.Category, models.Product.category_id == models.Category.id
        ).outerjoin(
            models.BranchInventory,
            and_(
                models.BranchInventory.product_id == models.Product.id,
                models.BranchInventory.branch_id == (
                    selected_branch.id if selected_branch is not None else -1
                ),
            ),
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

        sort_columns = {
            "name_asc": models.Product.name.asc(),
            "name_desc": models.Product.name.desc(),
            "price_low": models.Product.selling_price.asc(),
            "price_high": models.Product.selling_price.desc(),
            "stock_low": db.func.coalesce(
                models.BranchInventory.stock_quantity, -1
            ).asc(),
            "stock_high": db.func.coalesce(
                models.BranchInventory.stock_quantity, -1
            ).desc(),
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
            selected_branch=selected_branch,
            branches=branches,
            active_sort=active_sort,
        )

    @app.route("/products/<int:product_id>")
    @login_required
    @role_required("admin", "manager", "employee")
    def product_detail(product_id):
        product = models.Product.query.filter_by(id=product_id).first()
        if product is None:
            flash("Товар не найден.", "error")
            return redirect(url_for("products"))

        category = models.Category.query.filter_by(id=product.category_id).first()
        role = (session.get("role") or "").lower()
        if role in {"admin", "manager"}:
            branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        else:
            user = db.session.get(models.User, session.get("user_id"))
            employee_branch_id = (
                models.resolve_employee_branch_id(user) if user is not None else None
            )
            employee_branch = (
                db.session.get(models.Branch, employee_branch_id)
                if employee_branch_id is not None
                else None
            )
            branches = [employee_branch] if employee_branch is not None else []

        inventory_by_branch = {
            inventory.branch_id: inventory
            for inventory in models.BranchInventory.query.filter(
                models.BranchInventory.product_id == product.id,
                models.BranchInventory.branch_id.in_(
                    [branch.id for branch in branches]
                ),
            ).all()
        } if branches else {}
        branch_inventory_rows = [
            (branch, inventory_by_branch.get(branch.id)) for branch in branches
        ]
        return render_template(
            "product_detail.html",
            product=product,
            category=category,
            branch_inventory_rows=branch_inventory_rows,
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
        default_branch, _ = resolve_management_branch()
        if default_branch is not None:
            form_data["branch_id"] = str(default_branch.id)

        if request.method == "POST":
            for field in (
                "name",
                "sku",
                "category_id",
                "branch_id",
                "purchase_price",
                "selling_price",
            ):
                form_data[field] = request.form.get(field, "").strip()

            product_data = validate_product_form(form_data)
            if product_data is not None:
                product = models.Product(**product_data)
                try:
                    db.session.add(product)
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    current_app.logger.exception(
                        "Failed to create product %s.",
                        product.sku,
                    )
                    flash(
                        "Не удалось создать товар. Попробуйте ещё раз.",
                        "error",
                    )
                    return render_template(
                        "product_form.html",
                        form_data=form_data,
                        mode="create",
                        **product_form_options(),
                    )
                flash("Товар создан.", "success")
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
            flash("Товар не найден.", "error")
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
                if field in {"stock_quantity", "minimum_stock"}:
                    continue
                form_data[field] = request.form.get(field, "").strip()

            product_data = validate_product_form(form_data, product.id)
            if product_data is not None:
                for field, value in product_data.items():
                    if field not in {"stock_quantity", "minimum_stock"}:
                        setattr(product, field, value)
                try:
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    current_app.logger.exception(
                        "Failed to update product %s.",
                        product.id,
                    )
                    flash(
                        "Не удалось обновить товар. Попробуйте ещё раз.",
                        "error",
                    )
                    return render_template(
                        "product_form.html",
                        form_data=form_data,
                        product_id=product.id,
                        mode="edit",
                        **product_form_options(),
                    )
                flash("Данные товара обновлены.", "success")
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
            flash("Товар не найден.", "error")
            return redirect(url_for("products"))

        if models.SaleItem.query.filter_by(product_id=product.id).first() is not None:
            flash("Нельзя удалить товар: он указан в истории продаж.", "error")
            return redirect(url_for("product_detail", product_id=product.id))

        if models.InventoryTransaction.query.filter_by(product_id=product.id).first() is not None:
            flash("Нельзя удалить товар: для него сохранена история складских операций.", "error")
            return redirect(url_for("product_detail", product_id=product.id))

        db.session.delete(product)
        db.session.commit()
        flash("Товар удалён.", "success")
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
                flash("Укажите название категории.", "error")
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
                flash("Категория с таким названием уже существует.", "error")
                return render_template(
                    "category_form.html",
                    category=None,
                    category_name=name,
                    mode="create",
                )

            category = models.Category(name=name)
            db.session.add(category)
            db.session.commit()
            flash("Категория создана.", "success")
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
            flash("Категория не найдена.", "error")
            return redirect(url_for("categories"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if not name:
                flash("Укажите название категории.", "error")
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
                flash("Категория с таким названием уже существует.", "error")
                return render_template(
                    "category_form.html",
                    category=category,
                    category_name=name,
                    mode="edit",
                )

            category.name = name
            db.session.commit()
            flash("Данные категории обновлены.", "success")
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
            flash("Категория не найдена.", "error")
            return redirect(url_for("categories"))

        if models.Product.query.filter_by(category_id=category.id).first() is not None:
            flash("Нельзя удалить категорию: она используется в карточках товаров.", "error")
            return redirect(url_for("categories"))

        db.session.delete(category)
        db.session.commit()
        flash("Категория удалена.", "success")
        return redirect(url_for("categories"))

    @app.route("/inventory", methods=["GET", "POST"])
    @login_required
    @role_required("admin", "manager")
    def inventory():
        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        if not branches:
            flash("Перед управлением складом необходимо создать филиал.", "error")
            return render_template(
                "inventory.html",
                branches=[],
                selected_branch=None,
                branch_inventory_rows=[],
                available_products=[],
                transaction_rows=[],
            )

        branch_id_value = (
            request.form.get("branch_id")
            if request.method == "POST"
            else request.args.get("branch_id")
        )
        selected_branch, invalid_branch_request = resolve_management_branch(
            branch_id_value
        )
        if invalid_branch_request:
            flash("Выберите существующий филиал.", "error")
            if request.method == "POST":
                if selected_branch is not None:
                    return redirect(
                        url_for("inventory", branch_id=selected_branch.id)
                    )
                return redirect(url_for("inventory"))
        if selected_branch is None:
            return render_template(
                "inventory.html",
                branches=[],
                selected_branch=None,
                branch_inventory_rows=[],
                available_products=[],
                transaction_rows=[],
            )
        selected_branch_id = selected_branch.id

        def inventory_redirect():
            return redirect(
                url_for("inventory", branch_id=selected_branch_id)
            )

        def positive_or_zero_integer(field_name, label):
            value = request.form.get(field_name, "").strip()
            try:
                parsed = int(value)
                if parsed < 0:
                    raise ValueError
                return parsed
            except ValueError:
                flash(f"Поле «{label}» должно содержать неотрицательное целое число.", "error")
                return None

        if request.method == "POST":
            action = request.form.get("action", "").strip()

            product_id_value = request.form.get("product_id", "").strip()
            try:
                product_id = int(product_id_value)
                if product_id < 1:
                    raise ValueError
            except ValueError:
                flash("Выберите существующий товар.", "error")
                return inventory_redirect()

            product = models.Product.query.filter_by(id=product_id).first()
            if product is None:
                flash("Выбранный товар не существует.", "error")
                return inventory_redirect()

            if action == "assign":
                initial_stock = positive_or_zero_integer(
                    "initial_stock",
                    "Начальный остаток",
                )
                minimum_stock = positive_or_zero_integer(
                    "minimum_stock",
                    "Минимальный остаток",
                )
                if initial_stock is None or minimum_stock is None:
                    return inventory_redirect()

                existing_assignment = models.BranchInventory.query.filter_by(
                    branch_id=selected_branch_id,
                    product_id=product.id,
                ).first()
                if existing_assignment is not None:
                    flash(
                        "Этот товар уже добавлен в выбранный филиал.",
                        "error",
                    )
                    return inventory_redirect()

                branch_inventory = models.BranchInventory(
                    branch_id=selected_branch_id,
                    product_id=product.id,
                    stock_quantity=initial_stock,
                    minimum_stock=minimum_stock,
                )
                try:
                    db.session.add(branch_inventory)
                    db.session.flush()
                    if initial_stock > 0:
                        db.session.add(
                            models.InventoryTransaction(
                                product_id=product.id,
                                branch_inventory_id=branch_inventory.id,
                                transaction_type="stock_in",
                                quantity=initial_stock,
                            )
                        )
                    sync_low_stock_notifications(branch_inventory)
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    current_app.logger.exception(
                        "Failed to assign product %s to branch %s.",
                        product.id,
                        selected_branch_id,
                    )
                    flash(
                        "Не удалось добавить товар. Возможно, он уже есть в этом филиале.",
                        "error",
                    )
                    return inventory_redirect()

                flash("Товар успешно добавлен в филиал.", "success")
                return inventory_redirect()

            branch_inventory = models.BranchInventory.query.filter_by(
                branch_id=selected_branch_id,
                product_id=product.id,
            ).first()
            if branch_inventory is None:
                flash(
                    "Выбранный товар не добавлен в этот филиал.",
                    "error",
                )
                return inventory_redirect()

            if action == "threshold":
                minimum_stock = positive_or_zero_integer(
                    "minimum_stock",
                    "Минимальный остаток",
                )
                if minimum_stock is None:
                    return inventory_redirect()
                try:
                    branch_inventory.minimum_stock = minimum_stock
                    sync_low_stock_notifications(branch_inventory)
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    current_app.logger.exception(
                        "Failed to update minimum stock for branch inventory %s.",
                        branch_inventory.id,
                    )
                    flash(
                        "Не удалось обновить минимальный остаток.",
                        "error",
                    )
                    return inventory_redirect()
                flash("Минимальный остаток обновлён.", "success")
                return inventory_redirect()

            transaction_type = request.form.get(
                "transaction_type",
                "",
            ).strip()
            if action != "movement" or transaction_type not in {
                "stock_in",
                "write_off",
            }:
                flash("Выберите корректную складскую операцию.", "error")
                return inventory_redirect()

            quantity_value = request.form.get("quantity", "").strip()
            try:
                quantity = int(quantity_value)
                if quantity < 1:
                    raise ValueError
            except ValueError:
                flash("Количество должно быть положительным целым числом.", "error")
                return inventory_redirect()

            stock_update = update(models.BranchInventory).where(
                models.BranchInventory.id == branch_inventory.id,
                models.BranchInventory.branch_id == selected_branch_id,
                models.BranchInventory.product_id == product.id,
            )
            if transaction_type == "stock_in":
                stock_update = stock_update.values(
                    stock_quantity=(
                        models.BranchInventory.stock_quantity + quantity
                    )
                )
            else:
                stock_update = stock_update.where(
                    models.BranchInventory.stock_quantity >= quantity
                ).values(
                    stock_quantity=(
                        models.BranchInventory.stock_quantity - quantity
                    )
                )

            try:
                result = db.session.execute(stock_update)
                if result.rowcount != 1:
                    db.session.rollback()
                    flash(
                        "Недостаточно товара на складе для списания. "
                        "Обновите страницу и попробуйте ещё раз.",
                        "error",
                    )
                    return inventory_redirect()

                db.session.refresh(branch_inventory)
                db.session.add(
                    models.InventoryTransaction(
                        product_id=product.id,
                        branch_inventory_id=branch_inventory.id,
                        transaction_type=transaction_type,
                        quantity=quantity,
                    )
                )
                sync_low_stock_notifications(branch_inventory)
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception(
                    "Failed to record inventory movement for branch inventory %s.",
                    branch_inventory.id,
                )
                flash(
                    "Не удалось сохранить складскую операцию. "
                    "Остаток не изменён.",
                    "error",
                )
                return inventory_redirect()

            flash("Операция с остатком успешно выполнена.", "success")
            return inventory_redirect()

        branch_inventory_rows = models.BranchInventory.query.join(
            models.Product,
            models.BranchInventory.product_id == models.Product.id,
        ).filter(
            models.BranchInventory.branch_id == selected_branch_id
        ).order_by(
            models.Product.name.asc(),
            models.Product.id.asc(),
        ).all()
        assigned_product_ids = db.session.query(
            models.BranchInventory.product_id
        ).filter(
            models.BranchInventory.branch_id == selected_branch_id
        )
        available_products = models.Product.query.filter(
            ~models.Product.id.in_(assigned_product_ids)
        ).order_by(
            models.Product.name.asc(),
            models.Product.id.asc(),
        ).all()

        try:
            for branch_inventory in models.BranchInventory.query.all():
                sync_low_stock_notifications(branch_inventory)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to synchronize branch low-stock notifications."
            )
            flash(
                "Не удалось обновить уведомления о низком остатке. "
                "Попробуйте ещё раз.",
                "error",
            )

        transaction_rows = db.session.query(
            models.InventoryTransaction,
            models.Product.name,
            models.Product.sku,
            db.func.coalesce(
                models.Branch.name,
                selected_branch.name,
            ),
        ).outerjoin(
            models.BranchInventory,
            models.InventoryTransaction.branch_inventory_id
            == models.BranchInventory.id,
        ).join(
            models.Product,
            models.Product.id
            == db.func.coalesce(
                models.BranchInventory.product_id,
                models.InventoryTransaction.product_id,
            ),
        ).outerjoin(
            models.Branch,
            models.BranchInventory.branch_id == models.Branch.id,
        ).filter(
            or_(
                models.BranchInventory.branch_id == selected_branch_id,
                (
                    models.InventoryTransaction.branch_inventory_id.is_(None)
                    & (models.Product.branch_id == selected_branch_id)
                ),
            )
        ).order_by(
            models.InventoryTransaction.created_at.desc(),
            models.InventoryTransaction.id.desc(),
        ).limit(25).all()

        return render_template(
            "inventory.html",
            branches=branches,
            selected_branch=selected_branch,
            branch_inventory_rows=branch_inventory_rows,
            available_products=available_products,
            transaction_rows=transaction_rows,
        )

    @app.route("/notifications")
    @login_required
    @role_required("admin", "manager", "employee")
    def notifications():
        user_notifications = models.Notification.query.filter_by(
            user_id=session.get("user_id")
        ).order_by(
            models.Notification.created_at.desc(),
            models.Notification.id.desc(),
        ).all()
        return render_template(
            "notifications.html",
            notifications=user_notifications,
        )

    @app.route("/notifications/<int:notification_id>/read", methods=["POST"])
    @login_required
    @role_required("admin", "manager", "employee")
    def notification_mark_read(notification_id):
        notification = models.Notification.query.filter_by(
            id=notification_id,
            user_id=session.get("user_id"),
        ).first()
        if notification is None:
            flash("Уведомление не найдено.", "error")
            return redirect(url_for("notifications"))

        notification.is_read = True
        db.session.commit()
        flash("Уведомление отмечено как прочитанное.", "success")
        return redirect(url_for("notifications"))

    @app.route("/notifications/read-all", methods=["POST"])
    @login_required
    @role_required("admin", "manager", "employee")
    def notifications_mark_all_read():
        models.Notification.query.filter_by(
            user_id=session.get("user_id"),
            is_read=False,
        ).update({"is_read": True}, synchronize_session=False)
        db.session.commit()
        flash("Все уведомления отмечены как прочитанные.", "success")
        return redirect(url_for("notifications"))

    @app.route("/sales")
    @login_required
    @role_required("admin", "manager", "employee")
    def sales():
        user = db.session.get(models.User, session.get("user_id"))
        if user is None:
            session.clear()
            flash("Сеанс завершён. Войдите в систему снова.", "error")
            return redirect(url_for("login"))

        role = (user.role or "").lower()
        if role not in {"admin", "manager", "employee"}:
            return render_template("403.html", requested_path=request.path), 403

        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        selected_branch = None
        branch_error = None

        if role == "employee":
            branch_id = models.resolve_employee_branch_id(user)
            if branch_id is None:
                branch_error = (
                    "Для работы с кассой администратор должен связать вашу "
                    "учётную запись с сотрудником, у которого указан действующий филиал."
                )
            else:
                selected_branch = db.session.get(models.Branch, branch_id)
                if selected_branch is None:
                    branch_error = (
                        "Не удалось определить филиал для вашей учётной записи. "
                        "Обратитесь к администратору."
                    )
        elif not branches:
            branch_error = "Для работы с кассой сначала необходимо создать филиал."
        else:
            selected_branch, invalid_branch_request = resolve_management_branch(
                request.args.get("branch_id")
            )
            if invalid_branch_request:
                flash(
                    "Выбранный филиал не найден. Показан безопасный филиал.",
                    "error",
                )

        branch_inventory_rows = []
        customers = []
        if selected_branch is not None:
            branch_inventory_rows = (
                db.session.query(models.BranchInventory, models.Product)
                .join(
                    models.Product,
                    models.Product.id == models.BranchInventory.product_id,
                )
                .filter(models.BranchInventory.branch_id == selected_branch.id)
                .order_by(models.Product.name.asc(), models.Product.sku.asc())
                .all()
            )
            customers = models.Customer.query.order_by(
                models.Customer.name.asc()
            ).all()

        return render_template(
            "sales.html",
            branches=branches if role in {"admin", "manager"} else [],
            selected_branch=selected_branch,
            branch_inventory_rows=branch_inventory_rows,
            customers=customers,
            branch_error=branch_error,
            can_select_branch=role in {"admin", "manager"},
        )

    @app.route("/sales/history")
    @login_required
    @role_required("admin", "manager", "employee")
    def sales_history():
        user = db.session.get(models.User, session.get("user_id"))
        if user is None:
            session.clear()
            flash("Сеанс завершён. Войдите в систему снова.", "error")
            return redirect(url_for("login"))

        role = (user.role or "").lower()
        if role not in {"admin", "manager", "employee"}:
            return render_template("403.html", requested_path=request.path), 403

        branches = models.Branch.query.order_by(models.Branch.name.asc()).all()
        selected_branch = None
        if role in {"admin", "manager"}:
            requested_branch = request.args.get("branch_id")
            if requested_branch == "all":
                branch_error = False
            else:
                selected_branch, branch_error = resolve_management_branch(
                    requested_branch
                )
            if branch_error:
                flash(
                    "Выбранный филиал не найден. Показан безопасный филиал.",
                    "error",
                )
        else:
            employee_branch_id = models.resolve_employee_branch_id(user)
            if employee_branch_id is None:
                return render_template("403.html", requested_path=request.path), 403
            selected_branch = db.session.get(models.Branch, employee_branch_id)
            if selected_branch is None:
                return render_template("403.html", requested_path=request.path), 403
            branches = [selected_branch]

        payment_method = request.args.get("payment_method", "").strip()
        if payment_method not in {"", "cash", "card"}:
            flash("Выбран недопустимый способ оплаты.", "error")
            payment_method = ""

        date_from_value = request.args.get("date_from", "").strip()
        date_to_value = request.args.get("date_to", "").strip()
        date_from = None
        date_to = None
        try:
            if date_from_value:
                date_from = date.fromisoformat(date_from_value)
        except ValueError:
            flash("Укажите корректную начальную дату.", "error")
            date_from_value = ""
        try:
            if date_to_value:
                date_to = date.fromisoformat(date_to_value)
        except ValueError:
            flash("Укажите корректную конечную дату.", "error")
            date_to_value = ""
        if date_from is not None and date_to is not None and date_from > date_to:
            flash("Начальная дата не может быть позже конечной.", "error")
            date_from = None
            date_to = None
            date_from_value = ""
            date_to_value = ""

        sales_query = (
            db.session.query(
                models.Sale,
                models.Branch,
                models.User,
                models.Customer,
            )
            .join(models.Branch, models.Sale.branch_id == models.Branch.id)
            .outerjoin(
                models.User,
                models.Sale.created_by_user_id == models.User.id,
            )
            .outerjoin(
                models.Customer,
                models.Sale.customer_id == models.Customer.id,
            )
        )
        if selected_branch is not None:
            sales_query = sales_query.filter(
                models.Sale.branch_id == selected_branch.id
            )
        if payment_method:
            sales_query = sales_query.filter(
                models.Sale.payment_method == payment_method
            )
        if date_from is not None:
            sales_query = sales_query.filter(
                models.Sale.created_at >= datetime.combine(
                    date_from, datetime.min.time()
                )
            )
        if date_to is not None:
            sales_query = sales_query.filter(
                models.Sale.created_at < datetime.combine(
                    date_to + timedelta(days=1), datetime.min.time()
                )
            )

        total_sales = sales_query.count()
        per_page = 20
        pages = max(1, (total_sales + per_page - 1) // per_page)
        requested_page = request.args.get("page", default=1, type=int) or 1
        page = min(max(requested_page, 1), pages)
        sale_rows = (
            sales_query.order_by(
                models.Sale.created_at.desc(),
                models.Sale.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        filter_args = {}
        if role in {"admin", "manager"}:
            filter_args["branch_id"] = (
                str(selected_branch.id) if selected_branch is not None else "all"
            )
        if payment_method:
            filter_args["payment_method"] = payment_method
        if date_from_value:
            filter_args["date_from"] = date_from_value
        if date_to_value:
            filter_args["date_to"] = date_to_value

        return render_template(
            "sales_history.html",
            sales=sale_rows,
            branches=branches if role in {"admin", "manager"} else [],
            selected_branch=selected_branch,
            can_select_branch=role in {"admin", "manager"},
            payment_method=payment_method,
            date_from_value=date_from_value,
            date_to_value=date_to_value,
            filter_args=filter_args,
            page=page,
            pages=pages,
            total_sales=total_sales,
        )

    @app.route("/sales/<int:sale_id>/receipt")
    @login_required
    @role_required("admin", "manager", "employee")
    def sales_receipt(sale_id):
        user = db.session.get(models.User, session.get("user_id"))
        if user is None:
            session.clear()
            flash("Сеанс завершён. Войдите в систему снова.", "error")
            return redirect(url_for("login"))

        role = (user.role or "").lower()
        if role not in {"admin", "manager", "employee"}:
            return render_template("403.html", requested_path=request.path), 403

        sale = db.session.get(models.Sale, sale_id)
        if sale is None:
            abort(404)
        if role == "employee":
            employee_branch_id = models.resolve_employee_branch_id(user)
            if employee_branch_id is None or sale.branch_id != employee_branch_id:
                return render_template("403.html", requested_path=request.path), 403

        branch = db.session.get(models.Branch, sale.branch_id)
        seller = db.session.get(models.User, sale.created_by_user_id)
        customer = (
            db.session.get(models.Customer, sale.customer_id)
            if sale.customer_id is not None
            else None
        )
        sale_items = (
            db.session.query(models.SaleItem, models.Product)
            .join(
                models.Product,
                models.SaleItem.product_id == models.Product.id,
            )
            .filter(models.SaleItem.sale_id == sale.id)
            .order_by(models.SaleItem.id.asc())
            .all()
        )
        return render_template(
            "sales_receipt.html",
            sale=sale,
            branch=branch,
            seller=seller,
            customer=customer,
            sale_items=sale_items,
        )

    @app.route("/sales/checkout", methods=["POST"])
    @login_required
    @role_required("admin", "manager", "employee")
    def sales_checkout():
        class CheckoutError(ValueError):
            pass

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Некорректный запрос на оформление продажи."), 400

        items_payload = payload.get("items")
        if not isinstance(items_payload, list) or not items_payload:
            return jsonify(error="Корзина пуста."), 400

        product_quantities = []
        seen_product_ids = set()
        for item in items_payload:
            if not isinstance(item, dict):
                return jsonify(error="Некорректный состав корзины."), 400

            product_id = item.get("product_id")
            quantity = item.get("quantity")
            if (
                type(product_id) is not int
                or product_id < 1
                or product_id > 0x7FFFFFFFFFFFFFFF
            ):
                return jsonify(error="В корзине указан некорректный товар."), 400
            if type(quantity) is not int or quantity <= 0:
                return jsonify(error="Количество товара должно быть положительным целым числом."), 400
            if product_id in seen_product_ids:
                return jsonify(error="Один и тот же товар указан в корзине несколько раз."), 400

            seen_product_ids.add(product_id)
            product_quantities.append((product_id, quantity))

        payment_method = payload.get("payment_method")
        if not isinstance(payment_method, str) or payment_method not in {
            "cash",
            "card",
        }:
            return jsonify(error="Выберите допустимый способ оплаты."), 400

        customer_id = payload.get("customer_id")
        if customer_id == "":
            customer_id = None
        if customer_id is not None and (
            type(customer_id) is not int
            or customer_id < 1
            or customer_id > 0x7FFFFFFFFFFFFFFF
        ):
            return jsonify(error="Выбранный клиент не найден."), 400

        user_id = session.get("user_id")
        try:
            with db.session.begin():
                if db.engine.dialect.name == "sqlite":
                    # Reserve the write lock before reading stock, serializing competing checkouts.
                    db.session.execute(text("BEGIN IMMEDIATE"))

                user = db.session.get(models.User, user_id)
                if user is None or not user.is_active:
                    raise CheckoutError("Учётная запись недоступна. Войдите в систему снова.")

                role = (user.role or "").lower()
                if role == "employee":
                    branch_id = models.resolve_employee_branch_id(user)
                    if branch_id is None:
                        raise CheckoutError(
                            "Ваша учётная запись не связана с действующим сотрудником и филиалом. "
                            "Обратитесь к администратору."
                        )
                elif role in {"admin", "manager"}:
                    branch_id = payload.get("branch_id")
                    if (
                        type(branch_id) is not int
                        or branch_id < 1
                        or branch_id > 0x7FFFFFFFFFFFFFFF
                    ):
                        raise CheckoutError("Выберите существующий филиал.")
                else:
                    raise CheckoutError("У вас нет доступа к оформлению продаж.")

                branch = db.session.get(models.Branch, branch_id)
                if branch is None:
                    raise CheckoutError("Выбранный филиал не найден.")

                customer = None
                if customer_id is not None:
                    customer = db.session.get(models.Customer, customer_id)
                    if customer is None:
                        raise CheckoutError("Выбранный клиент не найден.")

                product_ids = [product_id for product_id, _ in product_quantities]
                products = {
                    product.id: product
                    for product in models.Product.query.filter(
                        models.Product.id.in_(product_ids)
                    ).all()
                }
                if len(products) != len(product_ids):
                    raise CheckoutError("Один из товаров больше не существует.")

                branch_inventory = {
                    item.product_id: item
                    for item in models.BranchInventory.query.filter(
                        models.BranchInventory.branch_id == branch.id,
                        models.BranchInventory.product_id.in_(product_ids),
                    ).all()
                }
                if len(branch_inventory) != len(product_ids):
                    raise CheckoutError(
                        "Один из товаров не назначен в выбранный филиал."
                    )

                priced_items = []
                sale_total = Decimal("0.00")
                for product_id, quantity in product_quantities:
                    product = products[product_id]
                    inventory = branch_inventory[product_id]
                    unit_price = product.selling_price
                    if (
                        not isinstance(unit_price, Decimal)
                        or not unit_price.is_finite()
                        or unit_price < 0
                    ):
                        raise CheckoutError(
                            f"Для товара «{product.name}» указана некорректная цена."
                        )
                    if inventory.stock_quantity < quantity:
                        raise CheckoutError(
                            f"Недостаточно товара «{product.name}» на складе филиала."
                        )

                    line_total = unit_price * quantity
                    sale_total += line_total
                    priced_items.append(
                        (product, inventory, quantity, unit_price)
                    )

                sale = models.Sale(
                    branch_id=branch.id,
                    customer_id=customer.id if customer is not None else None,
                    total_amount=sale_total,
                    discount=Decimal("0.00"),
                    payment_method=payment_method,
                    created_by_user_id=user.id,
                )
                db.session.add(sale)
                db.session.flush()

                stock_after_sale = []
                for product, inventory, quantity, unit_price in priced_items:
                    db.session.add(
                        models.SaleItem(
                            sale_id=sale.id,
                            product_id=product.id,
                            quantity=quantity,
                            unit_price=unit_price,
                        )
                    )

                    stock_result = db.session.execute(
                        update(models.BranchInventory)
                        .where(
                            models.BranchInventory.id == inventory.id,
                            models.BranchInventory.branch_id == branch.id,
                            models.BranchInventory.product_id == product.id,
                            models.BranchInventory.stock_quantity >= quantity,
                        )
                        .values(
                            stock_quantity=(
                                models.BranchInventory.stock_quantity - quantity
                            )
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if stock_result.rowcount != 1:
                        raise CheckoutError(
                            f"Недостаточно товара «{product.name}» на складе филиала."
                        )

                    db.session.expire(inventory, ["stock_quantity"])
                    db.session.add(
                        models.InventoryTransaction(
                            product_id=product.id,
                            branch_inventory_id=inventory.id,
                            transaction_type="sale_out",
                            quantity=quantity,
                            sale_id=sale.id,
                        )
                    )
                    sync_low_stock_notifications(inventory)
                    stock_after_sale.append(
                        {
                            "product_id": product.id,
                            "stock_quantity": inventory.stock_quantity,
                        }
                    )
                response_data = {
                    "sale_id": sale.id,
                    "receipt_url": url_for("sales_receipt", sale_id=sale.id),
                    "branch_id": branch.id,
                    "total_amount": format(sale_total, ".2f"),
                    "stock": stock_after_sale,
                }
        except CheckoutError as error:
            db.session.rollback()
            return jsonify(error=str(error)), 400
        except OperationalError:
            db.session.rollback()
            current_app.logger.info(
                "POS checkout conflicted with another inventory transaction for user %s.",
                user_id,
            )
            return jsonify(
                error="Остаток изменился во время оформления. Обновите данные и повторите попытку."
            ), 409
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "POS checkout failed for authenticated user %s.",
                user_id,
            )
            return jsonify(
                error="Не удалось оформить продажу. Изменения не сохранены. Повторите попытку."
            ), 500

        return jsonify(success=True, **response_data), 201

    @app.route("/customers")
    @login_required
    @role_required("admin", "manager")
    def customers():
        return render_template(
            "placeholder.html",
            page_title="Клиенты",
            message="Управление клиентами будет добавлено на следующем этапе разработки."
        )

    @app.route("/analytics")
    @login_required
    @role_required("admin", "manager")
    def analytics():
        return render_template(
            "placeholder.html",
            page_title="Аналитика",
            message="Аналитика и отчёты будут добавлены на следующем этапе разработки."
        )

    return app
