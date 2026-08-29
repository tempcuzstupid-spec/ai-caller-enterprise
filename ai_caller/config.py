"""Enterprise-grade application configuration with validation."""
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import field_validator, HttpUrl


class Settings(BaseSettings):
    """All configuration loaded from environment variables with validation."""

    # Database
    DATABASE_URL: str
    DATABASE_POOL_MIN: int = 2
    DATABASE_POOL_MAX: int = 10

    # Twilio
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    TWILIO_WEBHOOK_SECRET: str = ""  # Optional: for enhanced signature validation

    # AI Services
    DEEPGRAM_API_KEY: str
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o"
    ELEVENLABS_API_KEY: str
    ELEVENLABS_VOICE_ID: str = "EXAVITQu4vr4xnSDxMaL"

    # Security
    API_KEY_HEADER: str = "X-API-Key"
    ADMIN_API_KEY: str = ""  # Required for admin endpoints
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 10
    CORS_ORIGINS: str = "*"
    TRUSTED_PROXIES: str = ""

    # Redis (optional, falls back to in-memory)
    REDIS_URL: str = ""

    # App
    BASE_URL: str
    ENV: str = "development"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    REQUEST_TIMEOUT: int = 30
    MAX_CALL_DURATION_MINUTES: int = 30

    # ── Brand configuration (multi-tenant ready) ──────────────
    # The peptide business is branded as "Premium Meridian".
    # The AI Caller swaps the brand name + website domain in
    # Marcus's voice, TwiML greetings, SMS bodies, and knowledge
    # base based on these env vars. To rebrand in the future,
    # change these two env vars and redeploy — no code changes.
    BRAND_NAME: str = "Coastal Vanguard"           # What Marcus says
    BRAND_DOMAIN: str = "coastalvanguard.org"      # Catalog link
    BRAND_LEGAL_NAME: str = "Coastal Vanguard LLC"  # Legal disclaimers
    BRAND_PHONE: str = ""                          # Customer support number (empty = don't include)
    BRAND_EMAIL: str = ""                          # Support email
    BRAND_WS_DOMAIN: str = "ws.coastalvanguard.org"  # WebSocket gateway domain

    # Circuit Breaker
    CB_FAILURE_THRESHOLD: int = 5
    CB_RECOVERY_TIMEOUT: int = 30
    CB_EXPECTED_EXCEPTIONS: str = "TimeoutError,ConnectionError"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @field_validator("TWILIO_PHONE_NUMBER")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not v.startswith("+"):
            raise ValueError("Phone number must include country code (e.g., +1234567890)")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def parse_cors(cls, v: str) -> str:
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
