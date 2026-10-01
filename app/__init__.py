import os
from functools import wraps

import click
from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
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


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app.config["SECRET_KEY"] = os.environ.get("BMS_SECRET_KEY", "dev-secret-key-change-me")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bms.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("403.html", requested_path=request.path), 403

    from app import models

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

    @app.route("/employees")
    @login_required
    @role_required("admin", "manager")
    def employees():
        return render_template(
            "placeholder.html",
            page_title="Employees",
            message="Employee management will be implemented in a later development stage."
        )

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
        return render_template(
            "placeholder.html",
            page_title="Products",
            message="Product management will be implemented in a later development stage."
        )

    @app.route("/categories")
    @login_required
    @role_required("admin", "manager")
    def categories():
        return render_template(
            "placeholder.html",
            page_title="Categories",
            message="Category management will be implemented in a later development stage."
        )

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
