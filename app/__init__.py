from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bms.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from app import models

    @app.route("/")
    def home():
        return render_template("dashboard.html")

    @app.route("/login")
    def login():
        return render_template("login.html")

    @app.route("/register")
    def register():
        return render_template("register.html")

    @app.route("/profile")
    def profile():
        return render_template(
            "placeholder.html",
            page_title="Profile",
            message="User profile management will be implemented in a later development stage."
        )

    @app.route("/logout")
    def logout():
        return render_template(
            "placeholder.html",
            page_title="Logout",
            message="Logout flow will be implemented in a later development stage."
        )

    @app.route("/employees")
    def employees():
        return render_template(
            "placeholder.html",
            page_title="Employees",
            message="Employee management will be implemented in a later development stage."
        )

    @app.route("/shifts")
    def shifts():
        return render_template(
            "placeholder.html",
            page_title="Shifts",
            message="Shift management will be implemented in a later development stage."
        )

    @app.route("/products")
    def products():
        return render_template(
            "placeholder.html",
            page_title="Products",
            message="Product management will be implemented in a later development stage."
        )

    @app.route("/categories")
    def categories():
        return render_template(
            "placeholder.html",
            page_title="Categories",
            message="Category management will be implemented in a later development stage."
        )

    @app.route("/inventory")
    def inventory():
        return render_template(
            "placeholder.html",
            page_title="Inventory",
            message="Inventory tracking will be implemented in a later development stage."
        )

    @app.route("/sales")
    def sales():
        return render_template(
            "placeholder.html",
            page_title="Sales",
            message="Sales management will be implemented in a later development stage."
        )

    @app.route("/customers")
    def customers():
        return render_template(
            "placeholder.html",
            page_title="Customers",
            message="Customer management will be implemented in a later development stage."
        )

    @app.route("/analytics")
    def analytics():
        return render_template(
            "placeholder.html",
            page_title="Analytics",
            message="Analytics and reporting will be implemented in a later development stage."
        )

    return app
