import os
import secrets
from pathlib import Path

from flask import Flask, Response


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
    app_secret = os.environ.get("APP_SECRET_KEY", "").strip()
    app.secret_key = app_secret or secrets.token_urlsafe(48)

    # Jika di belakang reverse proxy (nginx/Cloudflare), set TRUST_PROXY_HOPS=1 agar
    # IP klien asli terbaca (dipakai rate limiter). Jangan aktifkan bila tanpa proxy.
    proxy_hops = int(os.environ.get("TRUST_PROXY_HOPS", "0") or 0)
    if proxy_hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_hops, x_proto=proxy_hops, x_host=proxy_hops)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
        PREFERRED_URL_SCHEME="https",
    )

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        # Gunakan assignment langsung agar Flask menjadi single source of truth
        # dan tidak menghasilkan header terduplikasi/konflik.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        # Cegah pembocoran banner versi web server dari aplikasi
        response.headers.pop("Server", None)
        return response

    from .routes import main
    app.register_blueprint(main)

    return app
