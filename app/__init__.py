from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "unhas-repo-viewer-dev-secret-2026"

    from .routes import main
    app.register_blueprint(main)

    return app
