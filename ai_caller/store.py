"""PostgreSQL-backed call state and transcript storage with metrics."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from ai_caller.database import get_conn


@dataclass
class CallState:
    call_sid: str
    phone_number: str
    direction: str
    purpose: str = "general"
    context: str = ""
    status: str = "initiated"
    stream_sid: Optional[str] = None
    duration: Optional[int] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None


class PostgresStore:
    """Durable call store backed by PostgreSQL."""

    async def create(
        self,
        call_sid: str,
        phone_number: str,
        direction: str,
        purpose: str = "general",
        context: str = "",
    ) -> CallState:
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO calls (call_sid, phone_number, direction, purpose, context)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (call_sid) DO NOTHING
                """,
                call_sid, phone_number, direction, purpose, context,
            )
        return CallState(
            call_sid=call_sid, phone_number=phone_number,
            direction=direction, purpose=purpose, context=context,
        )

    async def get(self, call_sid: str) -> Optional[CallState]:
        async with get_conn() as conn:
            row = await conn.fetchrow("SELECT * FROM calls WHERE call_sid = $1", call_sid)
        if not row:
            return None
        return CallState(
            call_sid=row["call_sid"],
            phone_number=row["phone_number"],
            direction=row["direction"],
            purpose=row["purpose"],
            context=row["context"],
            status=row["status"],
            stream_sid=row["stream_sid"],
            duration=row["duration"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    async def update(self, call_sid: str, **kwargs) -> Optional[CallState]:
        allowed = {"status", "stream_sid", "duration", "ended_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return await self.get(call_sid)

        set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
        async with get_conn() as conn:
            await conn.execute(
                f"UPDATE calls SET {set_clause}, updated_at = NOW() WHERE call_sid = $1",
                call_sid, *updates.values(),
            )
        return await self.get(call_sid)

    async def add_transcript(
        self,
        call_sid: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict] = None,
    ):
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO transcripts (call_sid, role, content, tool_name, tool_args)
                VALUES ($1, $2, $3, $4, $5)
                """,
                call_sid, role, content, tool_name, tool_args,
            )

    async def add_metrics(
        self,
        call_sid: str,
        stt_latency_ms: Optional[int] = None,
        llm_latency_ms: Optional[int] = None,
        tts_latency_ms: Optional[int] = None,
        total_latency_ms: Optional[int] = None,
        interruption_count: int = 0,
        tool_call_count: int = 0,
    ):
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO call_metrics
                (call_sid, stt_latency_ms, llm_latency_ms, tts_latency_ms,
                 total_latency_ms, interruption_count, tool_call_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                call_sid, stt_latency_ms, llm_latency_ms, tts_latency_ms,
                total_latency_ms, interruption_count, tool_call_count,
            )

    async def get_transcript(self, call_sid: str) -> List[Dict[str, Any]]:
        async with get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content, tool_name, tool_args, created_at
                FROM transcripts WHERE call_sid = $1 ORDER BY created_at ASC
                """,
                call_sid,
            )
        return [dict(r) for r in rows]

    async def get_metrics(self, call_sid: str) -> Optional[Dict[str, Any]]:
        async with get_conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM call_metrics WHERE call_sid = $1", call_sid
            )
        return dict(row) if row else None

    async def list_active(self) -> Dict[str, CallState]:
        async with get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM calls
                WHERE status NOT IN ('completed', 'failed', 'canceled', 'no-answer')
                ORDER BY started_at DESC
                """
            )
        return {
            r["call_sid"]: CallState(
                call_sid=r["call_sid"], phone_number=r["phone_number"],
                direction=r["direction"], purpose=r["purpose"],
                context=r["context"], status=r["status"],
                stream_sid=r["stream_sid"], duration=r["duration"],
                started_at=r["started_at"], ended_at=r["ended_at"],
            )
            for r in rows
        }

    async def get_call_stats(self) -> Dict[str, Any]:
        async with get_conn() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM calls")
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM calls WHERE status = 'in-progress'"
            )
            today = await conn.fetchval(
                "SELECT COUNT(*) FROM calls WHERE started_at > NOW() - INTERVAL '24 hours'"
            )
            avg_duration = await conn.fetchval(
                "SELECT AVG(duration) FROM calls WHERE duration IS NOT NULL"
            )
        return {
            "total_calls": total,
            "active_calls": active,
            "calls_today": today,
            "avg_duration_seconds": round(avg_duration, 2) if avg_duration else 0,
        }


call_store: PostgresStore = PostgresStore()
