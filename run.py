from app import create_app

app = create_app()


if __name__ == "__main__":
    is_development = app.config.get("ENVIRONMENT") != "production"
    app.run(host="0.0.0.0" if is_development else "127.0.0.1", port=5000, debug=False, use_reloader=False)