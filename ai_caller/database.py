"""PostgreSQL database layer with connection pooling and retry logic."""
import asyncpg
import logging
from contextlib import asynccontextmanager
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_caller.config import get_settings

logger = logging.getLogger("ai_caller")
_pool: Optional[asyncpg.Pool] = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calls (
    id              SERIAL PRIMARY KEY,
    call_sid        VARCHAR(64) UNIQUE NOT NULL,
    phone_number    VARCHAR(32) NOT NULL,
    direction       VARCHAR(16) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    purpose         VARCHAR(32) NOT NULL DEFAULT 'general',
    context         TEXT NOT NULL DEFAULT '',
    status          VARCHAR(32) NOT NULL DEFAULT 'initiated',
    stream_sid      VARCHAR(64),
    duration        INTEGER,
    line            VARCHAR(32) NOT NULL DEFAULT 'unknown',
    caller_id       VARCHAR(32),
    lead_name       VARCHAR(128),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Migration: add columns if they don't exist (for existing tables)
ALTER TABLE calls ADD COLUMN IF NOT EXISTS line VARCHAR(32) NOT NULL DEFAULT 'unknown';
ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_id VARCHAR(32);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS lead_name VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_calls_sid ON calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_phone ON calls(phone_number);
CREATE INDEX IF NOT EXISTS idx_calls_line ON calls(line);

CREATE TABLE IF NOT EXISTS transcripts (
    id          SERIAL PRIMARY KEY,
    call_sid    VARCHAR(64) NOT NULL REFERENCES calls(call_sid) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content     TEXT NOT NULL,
    tool_name   VARCHAR(64),
    tool_args   JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transcripts_call ON transcripts(call_sid, created_at);

CREATE TABLE IF NOT EXISTS call_metrics (
    id              SERIAL PRIMARY KEY,
    call_sid        VARCHAR(64) NOT NULL REFERENCES calls(call_sid) ON DELETE CASCADE,
    stt_latency_ms  INTEGER,
    llm_latency_ms  INTEGER,
    tts_latency_ms  INTEGER,
    total_latency_ms INTEGER,
    interruption_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metrics_call ON call_metrics(call_sid);

-- ── Agents (per-persona config) ─────────────────────────────────────────
-- An agent is a complete voice AI persona: system prompt, opening line,
-- voice ID, model, handoff number, and which Twilio number to call from.
-- Phase 1 is single-tenant (no user_id yet) — all agents belong to the
-- owner. Phase 2 will add user_id + per-tenant credentials.
CREATE TABLE IF NOT EXISTS agents (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(120) NOT NULL,
    slug                VARCHAR(64) UNIQUE NOT NULL,
    category            VARCHAR(32) NOT NULL CHECK (category IN (
                            'inbound_support', 'outbound_sales',
                            'appointment_reminder', 'personal_assistant', 'custom'
                        )),
    direction           VARCHAR(16) NOT NULL CHECK (direction IN ('inbound', 'outbound', 'both')),
    system_prompt       TEXT NOT NULL,
    opening_line        TEXT,
    voice_id            VARCHAR(64) NOT NULL DEFAULT 'TxGEqnHWrfWFTfGW9XjX',
    model               VARCHAR(64) NOT NULL DEFAULT 'gpt-4o-mini',
    handoff_number      VARCHAR(32),
    handoff_action_url  VARCHAR(512),
    from_numbers        TEXT NOT NULL DEFAULT '',  -- CSV of E.164 numbers; first = primary
    knowledge_base      TEXT,                       -- optional inline catalog/system info
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    is_template         BOOLEAN NOT NULL DEFAULT FALSE,  -- templates can't be deleted
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_slug ON agents(slug);
CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(active);
CREATE INDEX IF NOT EXISTS idx_agents_category ON agents(category);

-- Link calls to the agent that handled them
ALTER TABLE calls ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_calls_agent ON calls(agent_id);
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def init_pool():
    """Initialize database connection pool with retry logic.

    SSL is handled via the URL's ?sslmode=require param. We do NOT pass
    a separate ssl= kwarg because that conflicts with channel_binding
    when both are set.
    """
    global _pool
    settings = get_settings()

    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=settings.DATABASE_POOL_MIN,
        max_size=settings.DATABASE_POOL_MAX,
        command_timeout=settings.REQUEST_TIMEOUT,
    )

    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)

    logger.info(
        "Database pool initialized",
        extra={"min": settings.DATABASE_POOL_MIN, "max": settings.DATABASE_POOL_MAX},
    )


async def close_pool():
    """Gracefully close all database connections."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


@asynccontextmanager
async def get_conn():
    """Acquire a connection from the pool."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
