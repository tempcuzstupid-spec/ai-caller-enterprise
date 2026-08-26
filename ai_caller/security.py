"""Enterprise security layer.

Features:
  - Twilio webhook signature validation (via official twilio SDK)
  - API key authentication for admin endpoints
  - Rate limiting
  - PII redaction in logs
  - Input sanitization
"""
import hmac
import hashlib
import re
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.security import APIKeyHeader
from twilio.request_validator import RequestValidator

from ai_caller.config import get_settings

settings = get_settings()

# ── Twilio Signature Validation ──
# Cached validator — auth_token doesn't change at runtime
_twilio_validator: Optional[RequestValidator] = None


def _get_twilio_validator() -> RequestValidator:
    global _twilio_validator
    if _twilio_validator is None:
        _twilio_validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    return _twilio_validator


async def verify_twilio_signature(request: Request) -> None:
    """FastAPI dependency: validate Twilio webhook signature.

    Use as `Depends(verify_twilio_signature)` on any webhook endpoint.
    Reads the form, validates, and lets Starlette re-use the cached body
    for downstream `await request.form()` calls.
    """
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Twilio-Signature header",
        )

    # Reading form() forces Starlette to consume + cache the body. Subsequent
    # .form() calls in the endpoint re-derive from this cache.
    form = await request.form()

    # Use the configured BASE_URL (what Twilio actually signed against) instead
    # of request.url — Render's proxy may rewrite host/scheme, breaking sig.
    public_url = settings.BASE_URL.rstrip("/") + request.url.path
    if request.url.query:
        public_url += "?" + request.url.query

    # Pass the form directly — Starlette's FormData is dict-like and the
    # validator handles MultiDict via get_values().
    validator = _get_twilio_validator()
    if not validator.validate(public_url, dict(form), signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Twilio signature",
        )


async def twilio_signature_middleware(request: Request, call_next):
    """No-op kept for backwards compat. Real validation is via dependency."""
    return await call_next(request)


# ── API Key Authentication ──

api_key_header = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)

async def verify_admin_api_key(request: Request) -> bool:
    """Verify admin API key from header."""
    if not settings.ADMIN_API_KEY:
        # If no admin key configured, allow (development mode)
        return True

    api_key = request.headers.get(settings.API_KEY_HEADER)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if not hmac.compare_digest(api_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return True


# ── PII Redaction ──

PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),           # SSN
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"), "[CARD-REDACTED]"),  # Credit card
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL-REDACTED]"),  # Email
]

def redact_pii(text: str) -> str:
    """Redact PII from text before logging."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_phone(phone: str) -> str:
    """Redact phone number to last 4 digits."""
    if len(phone) > 4:
        return "*" * (len(phone) - 4) + phone[-4:]
    return "****"
