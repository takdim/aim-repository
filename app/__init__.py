import os
from pathlib import Path

from flask import Flask


def _load_dotenv_file() -> None:
    """Load KEY=VALUE pairs from project .env into os.environ if not already set."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def create_app() -> Flask:
    _load_dotenv_file()

    app = Flask(__name__)
    app.secret_key = os.environ.get("APP_SECRET_KEY", "unhas-repo-viewer-dev-secret-2026")

    from .routes import main
    app.register_blueprint(main)

    return app
