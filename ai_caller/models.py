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
    agent_id: Optional[int] = Field(default=None, description="Agent persona to use. If omitted, picks the first active outbound_sales agent.")

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


# ── Agent models (Phase 1: single-tenant; Phase 2 will add user_id) ──

class AgentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: Literal["inbound_support", "outbound_sales", "appointment_reminder", "personal_assistant", "custom"] = "custom"
    direction: Literal["inbound", "outbound", "both"] = "both"
    system_prompt: str = Field(..., min_length=1, max_length=20000)
    opening_line: Optional[str] = Field(default=None, max_length=2000)
    voice_id: str = Field(default="TxGEqnHWrfWFTfGW9XjX", max_length=64)
    model: str = Field(default="gpt-4o-mini", max_length=64)
    handoff_number: Optional[str] = Field(default=None, description="E.164 number to dial for live transfer")
    handoff_action_url: Optional[str] = Field(default=None, description="Optional action URL on the <Connect> verb (default: /webhook/transfer/{agent_id})")
    from_numbers: str = Field(default="", description="CSV of E.164 Twilio numbers this agent can call from (first = primary)")
    knowledge_base: Optional[str] = Field(default=None, description="Optional inline knowledge base to inject into the system prompt")
    active: bool = True


class AgentCreate(AgentBase):
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


class AgentUpdate(BaseModel):
    """All fields optional — partial update."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[Literal["inbound_support", "outbound_sales", "appointment_reminder", "personal_assistant", "custom"]] = None
    direction: Optional[Literal["inbound", "outbound", "both"]] = None
    system_prompt: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    opening_line: Optional[str] = Field(default=None, max_length=2000)
    voice_id: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=64)
    handoff_number: Optional[str] = None
    handoff_action_url: Optional[str] = None
    from_numbers: Optional[str] = None
    knowledge_base: Optional[str] = None
    active: Optional[bool] = None


class Agent(AgentBase):
    id: int
    slug: str
    is_template: bool
    created_at: str
    updated_at: str


class AgentTemplateInfo(BaseModel):
    """Metadata for a built-in agent template."""
    id: str
    label: str
    description: str
    direction: str
    default_prompt: str
    default_opening: str


class AgentListResponse(BaseModel):
    total: int
    agents: list[Agent]
    created_at: Optional[str] = None
