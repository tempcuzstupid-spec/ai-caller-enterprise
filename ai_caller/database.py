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
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_sid ON calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_phone ON calls(phone_number);

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
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def init_pool():
    """Initialize database connection pool with retry logic."""
    global _pool
    settings = get_settings()

    ssl_mode = "require" if "neon.tech" in settings.DATABASE_URL else None

    _pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=settings.DATABASE_POOL_MIN,
        max_size=settings.DATABASE_POOL_MAX,
        command_timeout=settings.REQUEST_TIMEOUT,
        ssl=ssl_mode,
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
