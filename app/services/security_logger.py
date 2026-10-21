import logging
import sys
from typing import Optional

# ASVS 16.1.1: Documented security logging architecture
# ASVS 16.2.1: Structured format containing timestamp, event, IP, message
# ASVS 16.3.3: Logging security events (anti-automation, access violations)
# ASVS 16.3.4: Logging unexpected errors and security control failures

logger = logging.getLogger("sidora.security")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [SECURITY] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def log_rate_limit(client_ip: str, endpoint: str, limit: int, window: int) -> None:
    """ASVS 16.3.3: Log rate-limit / anti-automation control triggering."""
    logger.warning(
        "Rate limit exceeded | ip=%s | endpoint=%s | limit=%d/%ds",
        client_ip,
        endpoint,
        limit,
        window,
    )


def log_auth_event(action: str, success: bool, details: Optional[str] = None) -> None:
    """ASVS 16.3.1: Log backend/upstream authentication operations."""
    status = "SUCCESS" if success else "FAILED"
    level = logging.INFO if success else logging.WARNING
    extra = f" | details={details}" if details else ""
    logger.log(level, "Auth event | action=%s | status=%s%s", action, status, extra)


def log_security_violation(event_type: str, client_ip: str, details: Optional[str] = None) -> None:
    """ASVS 16.3.3: Log attempts to bypass security controls or access forbidden resources."""
    extra = f" | details={details}" if details else ""
    logger.warning("Security violation | type=%s | ip=%s%s", event_type, client_ip, extra)


def log_upstream_error(service: str, error_message: str) -> None:
    """ASVS 16.3.4: Log unexpected security or connection failures with upstream providers."""
    logger.error("Upstream error | service=%s | error=%s", service, error_message)
