import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional
from flask import current_app

# ASVS 13.3.1: Cryptographic signing for temporary resource access
# ASVS 3.5.1 / CWE-306: Signed, short-lived tokens to protect document assets from scraping

TOKEN_TTL_SECONDS = int(os.environ.get("VIEWER_TOKEN_TTL_SECONDS", "3600"))  # 1 hour


def _get_signing_key() -> bytes:
    try:
        key = current_app.secret_key
        if key:
            return key.encode("utf-8") if isinstance(key, str) else key
    except RuntimeError:
        pass
    env_secret = os.environ.get("APP_SECRET_KEY", "sidora-default-secret-key-32chars!!")
    return env_secret.encode("utf-8")


def _ip_fingerprint(client_ip: str, key: bytes) -> str:
    """Create a privacy-preserving network-level hash to bind the token to the client."""
    clean_ip = (client_ip or "unknown").strip()
    # Normalize localhost
    if clean_ip in {"::1", "127.0.0.1", "localhost"}:
        clean_ip = "127.0.0.1"
    elif "." in clean_ip:
        # Match class C subnet (/24) to tolerate minor mobile carrier/NAT IP changes
        parts = clean_ip.split(".")
        if len(parts) == 4:
            clean_ip = ".".join(parts[:3])

    return hmac.new(key, clean_ip.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def generate_viewer_token(doc_id: str, doc_type: str, client_ip: str) -> str:
    """Generate a signed, time-limited token bound to document, type, and client IP."""
    key = _get_signing_key()
    now = int(time.time())
    payload = {
        "id": str(doc_id),
        "type": str(doc_type),
        "exp": now + TOKEN_TTL_SECONDS,
        "ip": _ip_fingerprint(client_ip, key),
    }

    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")

    signature = hmac.new(key, b64_payload.encode("ascii"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")

    return f"{b64_payload}.{b64_sig}"


def verify_viewer_token(token: Optional[str], doc_id: str, doc_type: str, client_ip: str) -> bool:
    """Verify cryptographic signature, expiration, document target, and IP binding."""
    if not token or "." not in token:
        return False

    b64_payload, _, b64_sig = token.partition(".")
    if not b64_payload or not b64_sig:
        return False

    key = _get_signing_key()

    # 1. Verify HMAC signature (constant-time comparison)
    expected_sig = hmac.new(key, b64_payload.encode("ascii"), hashlib.sha256).digest()
    expected_b64_sig = base64.urlsafe_b64encode(expected_sig).decode("ascii").rstrip("=")

    if not hmac.compare_digest(b64_sig, expected_b64_sig):
        return False

    # 2. Decode payload
    try:
        padded_b64 = b64_payload + "=" * ((4 - len(b64_payload) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded_b64.encode("ascii"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    # 3. Check expiration
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() > exp:
        return False

    # 4. Check document ID and document type
    if str(payload.get("id")) != str(doc_id) or str(payload.get("type")) != str(doc_type):
        return False

    # 5. Check client IP binding
    token_ip_hash = payload.get("ip")
    current_ip_hash = _ip_fingerprint(client_ip, key)
    # Allow localhost dev mismatch or exact hash match
    if token_ip_hash and token_ip_hash != current_ip_hash:
        clean_ip = (client_ip or "").strip()
        if clean_ip not in {"::1", "127.0.0.1", "localhost", "unknown"}:
            return False

    return True
