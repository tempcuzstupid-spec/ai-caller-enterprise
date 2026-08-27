"""Pydantic models for request/response validation."""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


class OutboundCallRequest(BaseModel):
    """Validated request body for triggering outbound calls."""
    to: str = Field(..., pattern=r"^\+\d{10,15}$", description="E.164 phone number")
    purpose: Literal["general", "sales_demo", "support", "reminder", "personal_assistant", "lead_qualification", "sales_close", "appointment"] = "general"
    context: str = Field(default="", max_length=2000, description="Additional context for the AI")
    tz: str = Field(default="America/New_York", description="IANA timezone for the lead (used for calling-hours compliance)")
    lead_name: Optional[str] = Field(default=None, description="Lead's name (for personalized greeting)")
    lead_context: Optional[str] = Field(default=None, description="Specific context about this lead (e.g. 'showed interest in retatrutide')")

    @field_validator("to")
    @classmethod
    def validate_e164(cls, v: str) -> str:
        if not v.startswith("+"):
            raise ValueError("Phone number must be E.164 format (e.g., +1234567890)")
        return v


class CallResponse(BaseModel):
    """Response model for call operations."""
    success: bool
    call_sid: Optional[str] = None
    to: str
    purpose: str
    status: str
    message: Optional[str] = None
    caller_id: Optional[str] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    env: str
    version: str = "1.0.0"
    active_calls: int
    total_calls: int
    calls_today: int
    dependencies: dict


class TranscriptEntry(BaseModel):
    """Single transcript entry."""
    role: str
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    created_at: Optional[str] = None
