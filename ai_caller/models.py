"""Pydantic models for request/response validation."""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal


class OutboundCallRequest(BaseModel):
    """Validated request body for triggering outbound calls."""
    to: str = Field(..., pattern=r"^\+\d{10,15}$", description="E.164 phone number")
    purpose: Literal["general", "sales_demo", "support", "reminder", "personal_assistant"] = "general"
    context: str = Field(default="", max_length=2000, description="Additional context for the AI")

    @field_validator("to")
    @classmethod
    def validate_e164(cls, v: str) -> str:
        if not v.startswith("+"):
            raise ValueError("Phone number must be E.164 format (e.g., +1234567890)")
        return v


class CallResponse(BaseModel):
    """Response model for call operations."""
    success: bool
    call_sid: str
    to: str
    purpose: str
    status: str
    message: Optional[str] = None


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
