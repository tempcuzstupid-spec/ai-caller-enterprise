"""Unit tests for security module."""
import pytest
from ai_caller.security import redact_pii, redact_phone


def test_redact_phone():
    assert redact_phone("+1234567890") == "******7890"
    assert redact_phone("+1") == "****"


def test_redact_pii():
    text = "My email is john@example.com and SSN is 123-45-6789"
    result = redact_pii(text)
    assert "[EMAIL-REDACTED]" in result
    assert "[SSN-REDACTED]" in result
    assert "john@example.com" not in result
