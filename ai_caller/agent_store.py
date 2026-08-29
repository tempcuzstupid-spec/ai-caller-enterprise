"""Agent store + template catalog.

Phase 1: single-tenant. All agents are global. Phase 2 will add user_id.
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any

from ai_caller.database import get_conn

logger = logging.getLogger("ai_caller")


@dataclass
class Agent:
    id: int
    name: str
    slug: str
    category: str
    direction: str
    system_prompt: str
    opening_line: Optional[str]
    voice_id: str
    model: str
    handoff_number: Optional[str]
    handoff_action_url: Optional[str]
    from_numbers: str  # CSV
    knowledge_base: Optional[str]
    active: bool
    is_template: bool
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return d


class AgentStore:
    """Postgres-backed CRUD for AI agent personas."""

    async def create(
        self,
        slug: str,
        name: str,
        category: str,
        direction: str,
        system_prompt: str,
        voice_id: str = "TxGEqnHWrfWFTfGW9XjX",
        model: str = "gpt-4o-mini",
        opening_line: Optional[str] = None,
        handoff_number: Optional[str] = None,
        handoff_action_url: Optional[str] = None,
        from_numbers: str = "",
        knowledge_base: Optional[str] = None,
        active: bool = True,
        is_template: bool = False,
    ) -> Agent:
        async with get_conn() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO agents
                    (slug, name, category, direction, system_prompt, opening_line,
                     voice_id, model, handoff_number, handoff_action_url,
                     from_numbers, knowledge_base, active, is_template)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                RETURNING *
                """,
                slug, name, category, direction, system_prompt, opening_line,
                voice_id, model, handoff_number, handoff_action_url,
                from_numbers, knowledge_base, active, is_template,
            )
        return self._row_to_agent(row)

    async def get(self, agent_id: int) -> Optional[Agent]:
        async with get_conn() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id)
        if not row:
            return None
        return self._row_to_agent(row)

    async def get_by_slug(self, slug: str) -> Optional[Agent]:
        async with get_conn() as conn:
            row = await conn.fetchrow("SELECT * FROM agents WHERE slug = $1", slug)
        if not row:
            return None
        return self._row_to_agent(row)

    async def list(self, only_active: bool = False) -> List[Agent]:
        sql = "SELECT * FROM agents"
        if only_active:
            sql += " WHERE active = TRUE"
        sql += " ORDER BY is_template DESC, id ASC"
        async with get_conn() as conn:
            rows = await conn.fetch(sql)
        return [self._row_to_agent(r) for r in rows]

    async def update(self, agent_id: int, **fields) -> Optional[Agent]:
        if not fields:
            return await self.get(agent_id)
        # Whitelist of updatable columns
        allowed = {
            "name", "category", "direction", "system_prompt", "opening_line",
            "voice_id", "model", "handoff_number", "handoff_action_url",
            "from_numbers", "knowledge_base", "active",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return await self.get(agent_id)
        set_clause = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
        async with get_conn() as conn:
            await conn.execute(
                f"UPDATE agents SET {set_clause}, updated_at = NOW() WHERE id = $1",
                agent_id, *updates.values(),
            )
        return await self.get(agent_id)

    async def delete(self, agent_id: int) -> bool:
        async with get_conn() as conn:
            row = await conn.fetchrow(
                "DELETE FROM agents WHERE id = $1 AND is_template = FALSE RETURNING id",
                agent_id,
            )
        return row is not None

    async def count(self) -> int:
        async with get_conn() as conn:
            n = await conn.fetchval("SELECT COUNT(*) FROM agents")
        return int(n)

    def _row_to_agent(self, row) -> Agent:
        return Agent(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            category=row["category"],
            direction=row["direction"],
            system_prompt=row["system_prompt"],
            opening_line=row["opening_line"],
            voice_id=row["voice_id"],
            model=row["model"],
            handoff_number=row["handoff_number"],
            handoff_action_url=row["handoff_action_url"],
            from_numbers=row["from_numbers"] or "",
            knowledge_base=row["knowledge_base"],
            active=row["active"],
            is_template=row["is_template"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Singleton
agent_store = AgentStore()


# ── Agent templates (lifted from VoiceReach AI, generalized) ─────────
# These are the 5 "starter packs" that new users can clone + customize.
# They get inserted as is_template=TRUE on first boot so they're
# available out of the box but can't be deleted.

AGENT_TEMPLATES = [
    {
        "slug": "template-inbound-support",
        "name": "Inbound Support",
        "category": "inbound_support",
        "direction": "inbound",
        "system_prompt": (
            "You are {name}, a friendly and professional customer support agent.\n\n"
            "Rules:\n"
            "- Greet warmly, identify yourself as an AI assistant.\n"
            "- Answer questions using only the business information you are given.\n"
            "- Never invent prices, policies, or availability.\n"
            "- If the caller asks for a human, say you will connect them right away.\n"
            "- If the caller asks to stop being contacted, apologize once and end the call politely.\n"
            "Keep every reply under 3 sentences. Speak naturally, no lists."
        ),
        "opening_line": "Thanks for calling. This is {name}, how can I help you today?",
    },
    {
        "slug": "template-outbound-sales",
        "name": "Outbound Sales",
        "category": "outbound_sales",
        "direction": "outbound",
        "system_prompt": (
            "You are {name}, an outbound sales qualifier calling on behalf of the business.\n\n"
            "Rules:\n"
            "- You QUALIFY and RECOMMEND. You never close the sale or take payment.\n"
            "- Ask one question at a time. Listen more than you talk.\n"
            "- Recommend exactly ONE option based on what the caller needs.\n"
            "- Offer to text them a link with details (say \"let me text you the details\").\n"
            "- If they want to buy or talk to a human, offer to connect them to a specialist.\n"
            "- If they say they are not interested, thank them and end the call politely.\n"
            "Keep every reply under 3 sentences. Never pressure. Never repeat yourself."
        ),
        "opening_line": "Hi, this is {name} calling from the team — do you have a quick minute?",
    },
    {
        "slug": "template-appointment-reminder",
        "name": "Appointment Reminder",
        "category": "appointment_reminder",
        "direction": "outbound",
        "system_prompt": (
            "You are {name}, an appointment reminder assistant.\n\n"
            "Rules:\n"
            "- State the appointment date, time, and location clearly.\n"
            "- Ask the caller to confirm, reschedule, or cancel.\n"
            "- If they want to reschedule, take the preferred day/time and say the office will confirm.\n"
            "- If they cancel, confirm the cancellation politely.\n"
            "- One attempt at rescheduling, then wrap up.\n"
            "Keep every reply under 3 sentences."
        ),
        "opening_line": "Hi, this is {name} with a quick reminder about your upcoming appointment.",
    },
    {
        "slug": "template-personal-assistant",
        "name": "Personal Assistant",
        "category": "personal_assistant",
        "direction": "both",
        "system_prompt": (
            "You are {name}, a personal assistant making this call on behalf of your client.\n\n"
            "Rules:\n"
            "- Immediately disclose you are an AI assistant calling on behalf of your client.\n"
            "- State the purpose of the call clearly and politely.\n"
            "- Confirm any details you book (date, time, name, party size) back to the other person.\n"
            "- Never make commitments beyond the task you were given.\n"
            "Keep every reply under 3 sentences."
        ),
        "opening_line": "Hi, this is {name} calling on behalf of a client. Do you have a moment?",
    },
    {
        "slug": "template-custom",
        "name": "Custom Agent",
        "category": "custom",
        "direction": "both",
        "system_prompt": (
            "You are {name}, a helpful AI voice agent. "
            "Greet the caller, understand what they need, and either help them or "
            "transfer to a human. Keep replies under 3 sentences."
        ),
        "opening_line": "Hi, this is {name}. How can I help?",
    },
]


async def seed_templates_if_empty(voice_id_default: str = "TxGEqnHWrfWFTfGW9XjX") -> int:
    """Insert the 5 default templates if the agents table is empty.
    Returns the number of templates inserted."""
    n = await agent_store.count()
    if n > 0:
        return 0
    inserted = 0
    for tmpl in AGENT_TEMPLATES:
        # Substitute {name} into the prompt for the first instance
        system_prompt = tmpl["system_prompt"].replace("{name}", tmpl["name"])
        opening_line = (tmpl["opening_line"] or "").replace("{name}", tmpl["name"])
        await agent_store.create(
            slug=tmpl["slug"],
            name=tmpl["name"],
            category=tmpl["category"],
            direction=tmpl["direction"],
            system_prompt=system_prompt,
            opening_line=opening_line,
            voice_id=voice_id_default,
            model="gpt-4o-mini",
            is_template=True,
            active=True,
        )
        inserted += 1
    logger.info(f"Seeded {inserted} agent templates")
    return inserted
