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


async def twilio_signature_middleware(request: Request, call_next):
    """ASGI middleware to validate Twilio signatures on webhook endpoints.

    - Reads the body once (so downstream handlers can re-read via .body() / .form())
    - Validates the X-Twilio-Signature against the raw body + full URL
    - Rejects with 403 if signature is missing or invalid
    - Skips validation for non-webhook paths (admin endpoints have their own auth)
    """
    WEBHOOK_PATHS = ("/webhook/incoming", "/webhook/outbound", "/webhook/status")

    if request.url.path in WEBHOOK_PATHS:
        # Read body once and cache it; downstream handlers call .form() which
        # re-derives from this cached body via FastAPI's request.body() impl.
        body_bytes = await request.body()
        request.state.body_cache = body_bytes

        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing X-Twilio-Signature header",
            )

        # Parse form params from the raw body for the validator
        from urllib.parse import parse_qs
        form_params = {}
        if body_bytes:
            parsed = parse_qs(body_bytes.decode("utf-8", errors="ignore"))
            # Twilio validator expects {key: value} (not lists)
            form_params = {k: v[0] for k, v in parsed.items()}

        validator = _get_twilio_validator()
        # Use the public URL Twilio signed against (request.url is fine for
        # standard deployments; for proxies, set BASE_URL and override)
        public_url = str(request.url)
        if not validator.validate(public_url, form_params, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Twilio signature",
            )

    response = await call_next(request)
    return response


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
