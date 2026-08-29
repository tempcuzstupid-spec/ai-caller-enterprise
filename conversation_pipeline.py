"""AI Caller — Twilio ConversationRelay pipeline (EXCEPTIONAL version).

Architecture: Twilio terminates STT (Deepgram) and TTS (ElevenLabs).
We exchange JSON text messages over WebSocket. Barge-in is native.

Message types (Twilio -> us):
  setup:     {type: "setup", callSid, streamSid, ...}
  prompt:    {type: "prompt", voicePrompt, last, ...}
  interrupt: {type: "interrupt"}      (caller spoke during our turn)
  error:     {type: "error", description}
  end:       {type: "end", reason}

Message types (us -> Twilio):
  text:      {type: "text", token, last}
  end:       {type: "end"}

Personas: support, tollfree, sales — defined in PERSONA_DNA.json
"""
import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_caller_exceptional")

# ── Config (from env) ──
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FLY_API_URL = os.getenv("FLY_API_URL", "https://ai-caller-enterprise.fly.dev")
FLY_ADMIN_KEY = os.getenv("FLY_ADMIN_KEY", "")
HUMAN_REP_NUMBER = os.getenv("HUMAN_REP_NUMBER", "+17543529826")
SELF_CLOSE_THRESHOLD_USD = int(os.getenv("SELF_CLOSE_THRESHOLD_USD", "400"))
STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/coastalvanguard")
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "10"))

# ── Load catalog + persona DNA ──
CATALOG = None
PERSONAS = None
PERSONA_DNA = None

def _load_assets():
    global CATALOG, PERSONAS, PERSONA_DNA
    try:
        with open("/opt/ai-caller-ws/catalog.json") as f:
            CATALOG = json.load(f)
        logger.info(f"Catalog: {len(CATALOG.get('products', []))} products, {len(CATALOG.get('bundles', []))} bundles")
    except Exception as e:
        logger.warning(f"Catalog load failed: {e}")
        CATALOG = {"products": [], "bundles": []}
    try:
        with open("/opt/ai-caller-ws/persona_dna.json") as f:
            PERSONA_DNA = json.load(f)
    except Exception as e:
        logger.warning(f"Persona DNA load failed: {e}")
        PERSONA_DNA = {}

_load_assets()


def build_product_knowledge() -> str:
    """Build a compact, LLM-friendly product knowledge block from the catalog."""
    if not CATALOG.get("products"):
        return "Catalog not loaded."

    lines = []
    lines.append(f"COMPANY: {CATALOG.get('company')} — {CATALOG.get('tagline')}")
    lines.append(f"CONTACT: {CATALOG['contact']['phone']} / {CATALOG['contact']['email']}")
    lines.append(f"SHIPPING: {CATALOG['shipping']['processing']}. {CATALOG['shipping']['standard_2day']} standard 2-day. Free on orders ${CATALOG['shipping']['free_threshold']}+.")
    lines.append(f"PAYMENT: {', '.join(CATALOG['payment_methods'])}")
    lines.append(f"DISCLAIMER: {CATALOG['disclaimer']}")
    lines.append("")

    by_category = {}
    for p in CATALOG["products"]:
        cat = p.get("category", "other")
        by_category.setdefault(cat, []).append(p)

    cat_labels = {
        "weight_management": "WEIGHT MANAGEMENT",
        "recovery": "RECOVERY & HEALING",
        "growth": "GROWTH & PERFORMANCE",
        "longevity": "LONGEVITY & CELLULAR HEALTH",
        "cognitive": "COGNITIVE ENHANCEMENT",
        "beauty": "SKIN, BEAUTY & TANNING",
        "immune": "IMMUNE",
        "specialty": "SPECIALTY",
        "accessory": "ACCESSORIES",
    }

    for cat, items in by_category.items():
        lines.append(f"=== {cat_labels.get(cat, cat.upper())} ===")
        for p in items:
            price = f"${p['price_usd']}" if p.get("price_usd") else "Premium (inquire)"
            lines.append(
                f"  {p['name']} ({p['sku']}): {p.get('aka', '')} | {p.get('format', 'N/A')} | "
                f"{p.get('dose_mg', 'N/A')}{'mg' if p.get('dose_mg') else ''} | {price} | "
                f"Best for: {p.get('best_for', '')}"
            )
        lines.append("")

    lines.append("=== BUNDLES (preset combinations at a discount) ===")
    for b in CATALOG.get("bundles", []):
        price = f"${b['price_usd']}"
        names = [c.get("sku", "?") for c in b.get("contents", [])]
        lines.append(
            f"  Bundle {b['sku']} \"{b['name']}\" {price}: {b.get('outcome', '')} | "
            f"Who: {b.get('who', '')} | Contains: {', '.join(names)}"
        )

    return "\n".join(lines)


PRODUCT_KNOWLEDGE = build_product_knowledge()


# ── Use the production prompt builder (Coastal Vanguard official prompts + knowledge base) ──
# Imports the official prompts the user just provided, plus the full
# knowledge base (21 packages, 13 products, contraindications, package selector).
# Builds a single, complete system prompt per persona.
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from prompt_builder import build_tollfree_prompt, build_sales_prompt, build_support_prompt





PERSONAS = {
    "support": {
        "system_prompt": build_support_prompt(),
        "model": "gpt-4o-mini",
        "max_history_turns": 8,
    },
    "tollfree": {
        "system_prompt": build_tollfree_prompt(),
        "model": "gpt-4o-mini",
        "max_history_turns": 8,
    },
    "sales": {
        "system_prompt_builder": build_sales_prompt,
        "model": "gpt-4o-mini",
        "fallback_model": "gpt-4o-mini",
        "max_history_turns": 12,
    },
}


def detect_opt_out(text: str) -> bool:
    text_lower = text.lower()
    patterns = [r"\bremove\b", r"\bstop calling\b", r"\bdo not call\b", r"\bdon'?t call\b", r"\bunsubscribe\b", r"\bopt[-\s]?out\b", r"\bnot interested\b", r"\bleave me alone\b"]
    return any(re.search(p, text_lower) for p in patterns)


def detect_handoff_request(text: str) -> bool:
    text_lower = text.lower()
    patterns = [r"\btalk to (a |someone|human|person|rep|real)\b", r"\byour manager\b", r"\byour boss\b", r"\bnot a bot\b", r"\bconnect me\b", r"\btransfer me\b"]
    return any(re.search(p, text_lower) for p in patterns)


def detect_payment_link_request(text: str) -> bool:
    text_lower = text.lower()
    patterns = [r"\bpayment link\b", r"\btext me\b", r"\bsend me (the|a) link\b", r"\bsend payment\b"]
    return any(re.search(p, text_lower) for p in patterns)


def extract_email(text: str) -> Optional[str]:
    m = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
    return m.group(0) if m else None


def extract_zip(text: str) -> Optional[str]:
    # US 5-digit or ZIP+4
    m = re.search(r'\b\d{5}(?:-\d{4})?\b', text)
    return m.group(0) if m else None


async def openai_llm_stream(
    transcript: str,
    history: list,
    system_prompt: str,
    on_token: callable,
    model: str = "gpt-4o-mini",
) -> str:
    history.append({"role": "user", "content": transcript})
    # New OpenAI API requires content as structured object: {"type": "text", "text": "..."}
    # The plain string form gives a 400 "expected an object, but got a string instead"
    messages = [{"role": "system", "content": [{"type": "text", "text": system_prompt}]}]
    for m in history:
        messages.append({"role": m["role"], "content": [{"type": "text", "text": m["content"]}]})
    # Retry with backoff for 429 (rate limit) and 5xx errors
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 250,
                        "temperature": 0.7,
                        "stream": True,
                    },
                )
                if r.status_code == 429 and attempt < max_retries - 1:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"OpenAI 429 (attempt {attempt+1}), waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                full = ""
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                        if delta:
                            full += delta
                            await on_token(delta)
                    except Exception:
                        pass
                history.append({"role": "assistant", "content": full})
                return full
        except Exception as e:
            error_str = str(e)
            logger.error(f"OpenAI error (attempt {attempt+1}): {error_str[:200]}")
            if "429" in error_str and attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            if "429" in error_str or "insufficient_quota" in error_str or "credit" in error_str.lower():
                err = "I'm sorry, my language service is currently unavailable. Please call back later."
            elif "timeout" in error_str.lower():
                err = "I'm sorry, that took a bit long. Could you say that again?"
            else:
                err = "I apologize, I'm having a moment. Let me try again."
            await on_token(err)
            history.append({"role": "assistant", "content": err})
            return err
    return "I apologize, I'm having a moment. Let me try again."


def trim_history(history: list, max_turns: int) -> list:
    """Keep the last max_turns exchanges. Drop older to keep prompt lean."""
    # Each "turn" is 2 messages (user + assistant)
    max_messages = max_turns * 2
    if len(history) > max_messages:
        return history[-max_messages:]
    return history


async def fetch_agent_config(agent_id: int) -> dict | None:
    """Fetch agent config from the Fly app's /agents/{id} endpoint.
    Cached in-process for 60s to avoid hammering Fly on every WebSocket connection.
    Returns the agent dict, or None on failure.
    """
    if not FLY_API_URL or not FLY_ADMIN_KEY:
        return None
    import time as _time
    cache_key = f"agent:{agent_id}"
    now = _time.monotonic()
    if cache_key in _AGENT_CACHE:
        cached_at, cached_value = _AGENT_CACHE[cache_key]
        if (now - cached_at) < 60:
            return cached_value
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{FLY_API_URL}/agents/{agent_id}",
                headers={"X-API-Key": FLY_ADMIN_KEY},
            )
            if r.status_code == 200:
                agent = r.json()
                _AGENT_CACHE[cache_key] = (now, agent)
                return agent
            logger.warning(f"[WS] Fly /agents/{agent_id} returned {r.status_code}")
            return None
    except Exception as e:
        logger.warning(f"[WS] Failed to fetch agent {agent_id} from Fly: {e}")
        return None


_AGENT_CACHE: dict = {}


async def handle_conversation_stream(websocket: WebSocket, persona: str = "support", agent_id: int | None = None):
    await websocket.accept()

    # If an agent_id is provided, fetch the agent's config from Fly.
    # This overrides the persona-derived prompt/opening line/voice/model.
    agent_config: dict | None = None
    if agent_id:
        agent_config = await fetch_agent_config(agent_id)
        if agent_config:
            logger.info(f"WS accepted | agent_id={agent_id} slug={agent_config.get('slug')!r}")
        else:
            logger.warning(f"WS accepted | agent_id={agent_id} (NOT FOUND on Fly, falling back to persona={persona!r})")

    # Resolve the persona config (fallback path)
    persona_cfg = PERSONAS.get(persona, PERSONAS["support"])
    if not agent_config:
        logger.info(f"WS accepted | persona={persona}")

    call_sid = None
    stream_sid = None
    lead_name = ""
    lead_context = ""
    lead_email = None
    lead_zip = None
    history: list = []
    opt_out = False
    handoff_requested = False
    payment_link_sent = False
    catalog_sms_sent = False
    lead_phone = None
    current_llm_task: Optional[asyncio.Task] = None
    cancelled = asyncio.Event()

    async def send_text(token: str, last: bool = False):
        try:
            await websocket.send_text(json.dumps({"type": "text", "token": token, "last": last}))
        except Exception as e:
            logger.warning(f"send_text: {e}")

    async def send_end(handoff_data: dict = None):
        """End the ConversationRelay session. If handoff_data is provided,
        Twilio will POST it to the action URL on the <Connect> verb. That
        action handler can then <Dial> a human agent."""
        msg = {"type": "end"}
        if handoff_data:
            msg["handoffData"] = json.dumps(handoff_data)
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception:
            pass

    async def generate_and_send(transcript: str, system_prompt: str, model: str):
        nonlocal payment_link_sent, catalog_sms_sent, opt_out, handoff_requested, lead_email, lead_zip, lead_phone
        cancelled.clear()
        # Capture lead info from their speech
        if not lead_email:
            lead_email = extract_email(transcript)
        if not lead_zip:
            lead_zip = extract_zip(transcript)

        full = ""
        async def on_token(delta: str):
            nonlocal full
            if cancelled.is_set():
                return
            full += delta
            await send_text(delta, last=False)

        try:
            trimmed = trim_history(list(history), persona_cfg["max_history_turns"])
            full = await openai_llm_stream(transcript, trimmed, system_prompt, on_token, model)
            if not cancelled.is_set():
                await send_text("", last=True)
        except asyncio.CancelledError:
            logger.info("LLM cancelled (barge-in)")
            return
        except Exception as e:
            logger.error(f"generate_and_send: {e}")
            if not cancelled.is_set():
                await send_text("I apologize, I'm having a moment. Let me try again.", last=True)

        # Triggers
        if detect_payment_link_request(full) and not payment_link_sent:
            payment_link_sent = True
            if STRIPE_PAYMENT_LINK and call_sid and FLY_ADMIN_KEY:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            f"{FLY_API_URL}/calls/{call_sid}/send-payment-link",
                            headers={"X-API-Key": FLY_ADMIN_KEY},
                            json={"url": STRIPE_PAYMENT_LINK, "amount": SELF_CLOSE_THRESHOLD_USD, "lead_email": lead_email, "lead_zip": lead_zip},
                        )
                    logger.info(f"Payment link sent for {call_sid}")
                except Exception as e:
                    logger.warning(f"send-payment-link: {e}")

        # Send catalog link via SMS when Marcus says he's sending it.
        # Try to extract the package Marcus recommended (if any) so the SMS
        # is personalized — "Like we discussed, here's more on A1..."
        if not catalog_sms_sent and ("text you" in full.lower() or "send you the link" in full.lower() or "sending it" in full.lower() or "text that" in full.lower()):
            if call_sid and FLY_ADMIN_KEY:
                # Try to pull the recommended package out of the LLM output
                recommended = ""
                for pkg_name in [
                    "A1", "A2", "A3", "A4", "A5",
                    "B1", "B2", "B3",
                    "C1", "C2",
                    "D1", "D2",
                    "E1", "E2",
                    "F1", "G1",
                    "H1", "H2", "H3", "H4", "H5",
                ]:
                    if pkg_name in full:
                        recommended = pkg_name
                        break
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            f"{FLY_API_URL}/calls/{call_sid}/send-catalog-sms",
                            headers={"X-API-Key": FLY_ADMIN_KEY},
                            json={
                                "url": "https://coastalvanguard.org",
                                "lead_phone": lead_phone,
                                "package": recommended,
                                "lead_name": lead_name,
                            },
                        )
                    catalog_sms_sent = True
                    logger.info(f"Catalog SMS sent for {call_sid} package={recommended!r}")
                except Exception as e:
                    logger.warning(f"send-catalog-sms: {e}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "setup":
                call_sid = data.get("callSid")
                stream_sid = data.get("streamSid")
                lead_phone = data.get("from") or data.get("caller")  # Twilio's caller number
                custom = data.get("customParameters", {}) or {}
                lead_name = custom.get("lead_name", "")
                lead_context = custom.get("lead_context", "")
                logger.info(
                    f"Setup: call={call_sid} lead={lead_name} from={lead_phone} "
                    f"persona={persona} agent_id={agent_id}"
                )

                # Resolve the system prompt and opening line.
                # If we have agent_config from Fly, use it. Otherwise fall
                # back to the legacy persona path.
                sales_opening_line = None
                if agent_config:
                    system_prompt = agent_config.get("system_prompt") or persona_cfg["system_prompt"]
                    # If the agent has an opening_line, use it. Otherwise let
                    # the LLM greet from the system prompt.
                    agent_opening = agent_config.get("opening_line") or ""
                    if agent_opening:
                        # Substitute {name} if present
                        agent_name = agent_config.get("name", "")
                        sales_opening_line = agent_opening.replace("{name}", agent_name)
                elif persona == "sales":
                    system_prompt, sales_opening_line = build_sales_prompt(lead_name, lead_context)
                else:
                    system_prompt = persona_cfg["system_prompt"]

                # If the agent is outbound-direction, send the opening line.
                # If inbound, stay silent and wait for the caller to speak.
                is_outbound = (agent_config and agent_config.get("direction") == "outbound") or (not agent_config and persona == "sales")

                if is_outbound and sales_opening_line:
                    await send_text(sales_opening_line, last=True)
                    history.append({"role": "assistant", "content": sales_opening_line})
                # Inbound: stay silent, wait for the caller's first prompt

            elif msg_type == "prompt":
                if opt_out or handoff_requested:
                    continue
                voice_prompt = data.get("voicePrompt", "").strip()
                # Ignore empty prompts (these are triggered by Twilio edge events, not real user speech)
                if not voice_prompt or len(voice_prompt) < 2:
                    logger.debug(f"Empty/short prompt ignored: '{voice_prompt}'")
                    continue
                # Ignore empty acknowledgments OR very short first replies — let the LLM handle the
                # next turn naturally (it has the system prompt's "open question" instruction)
                last = data.get("last", False)
                logger.info(f"Prompt ({persona}): {voice_prompt[:80]}")

                # Compliance: opt-out (highest priority)
                if detect_opt_out(voice_prompt):
                    opt_out = True
                    logger.info(f"OPT-OUT: {voice_prompt[:80]}")
                    await send_text("I completely understand, I apologize for the interruption. I'll remove your number from our list right now. Have a great day.", last=True)
                    if call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.post(f"{FLY_API_URL}/calls/{call_sid}/opt-out", headers={"X-API-Key": FLY_ADMIN_KEY}, json={"reason": voice_prompt[:200]})
                        except Exception as e:
                            logger.warning(f"opt-out record: {e}")
                    await send_end()
                    continue

                # Compliance: handoff
                if detect_handoff_request(voice_prompt):
                    handoff_requested = True
                    logger.info(f"HANDOFF: {voice_prompt[:80]}")
                    if persona == "sales":
                        await send_text("Of course, let me grab my colleague David for you — he can take it from here. One moment please.", last=True)
                    else:
                        await send_text("Of course, let me connect you with a specialist who can help. One moment please.", last=True)
                    if call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.post(f"{FLY_API_URL}/calls/{call_sid}/handoff", headers={"X-API-Key": FLY_ADMIN_KEY}, json={"reason": voice_prompt[:200]})
                        except Exception as e:
                            logger.warning(f"handoff record: {e}")
                    # Build handoffData with caller context. The Fly action
                    # handler at /webhook/transfer will receive this and <Dial>
                    # the human rep (David). It also gets a "whisper" so David
                    # knows who he's about to talk to.
                    handoff_data = {
                        "reasonCode": "live-agent-handoff",
                        "reason": voice_prompt[:200],
                        "lead_name": lead_name or "the caller",
                        "lead_phone": lead_phone or "",
                        "persona": persona,
                        "agent_id": agent_id,
                        "agent_slug": (agent_config or {}).get("slug"),
                        "caller_id": HUMAN_REP_NUMBER,  # The number to dial (set in Fly handler)
                    }
                    await send_end(handoff_data=handoff_data)
                    continue

                # Cancel in-flight LLM if user is interrupting
                if current_llm_task and not current_llm_task.done():
                    cancelled.set()
                    current_llm_task.cancel()
                    try:
                        await current_llm_task
                    except (asyncio.CancelledError, Exception):
                        pass

                # Build persona prompt (rebuild in case lead info changed)
                if agent_config:
                    system_prompt = agent_config.get("system_prompt") or persona_cfg["system_prompt"]
                    model = agent_config.get("model") or persona_cfg["model"]
                elif persona == "sales":
                    system_prompt, _ = build_sales_prompt(lead_name, lead_context)
                    model = persona_cfg["model"]
                else:
                    system_prompt = persona_cfg["system_prompt"]
                    model = persona_cfg["model"]

                current_llm_task = asyncio.create_task(generate_and_send(voice_prompt, system_prompt, model))

            elif msg_type == "interrupt":
                logger.info("Barge-in interrupt")
                if current_llm_task and not current_llm_task.done():
                    cancelled.set()
                    current_llm_task.cancel()
                    try:
                        await current_llm_task
                    except (asyncio.CancelledError, Exception):
                        pass
                await send_text("", last=False)

            elif msg_type == "error":
                logger.error(f"Twilio: {data}")

            elif msg_type == "end":
                logger.info(f"End: {data.get('reason', '?')}")
                break

    except WebSocketDisconnect:
        logger.info("WS disconnected")
    except Exception as e:
        logger.error(f"handler: {e}")
    finally:
        if current_llm_task and not current_llm_task.done():
            current_llm_task.cancel()
            try:
                await current_llm_task
            except (asyncio.CancelledError, Exception):
                pass
        if call_sid and FLY_ADMIN_KEY:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{FLY_API_URL}/calls/{call_sid}/transcript",
                        headers={"X-API-Key": FLY_ADMIN_KEY},
                        json={
                            "messages": history,
                            "outcome": "opted_out" if opt_out else ("handoff" if handoff_requested else "completed"),
                            "lead_email": lead_email,
                            "lead_zip": lead_zip,
                        },
                    )
            except Exception as e:
                logger.warning(f"save transcript: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set")
    logger.info(f"Personas: {list(PERSONAS.keys())}")
    logger.info(f"Catalog: {len(CATALOG.get('products', []))} products, {len(CATALOG.get('bundles', []))} bundles")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "ai-caller-conversation-exceptional",
        "personas": list(PERSONAS.keys()),
        "models": {k: v["model"] for k, v in PERSONAS.items()},
        "deps": {"openai": bool(OPENAI_API_KEY), "catalog": len(CATALOG.get("products", []))},
        "config": {"max_history_turns": MAX_HISTORY_TURNS, "self_close_threshold": SELF_CLOSE_THRESHOLD_USD}
    })


@app.websocket("/ws/conversation")
async def ws_conversation_endpoint(websocket: WebSocket):
    """WebSocket endpoint for Twilio ConversationRelay.

    Query params:
      agent_id: ID of the agent to use (preferred). If absent, falls back
                to the legacy `persona` param.
      persona:  Legacy param — one of support/tollfree/sales. Only used
                if agent_id is absent. Backwards-compatible.
    """
    agent_id_str = websocket.query_params.get("agent_id")
    persona = websocket.query_params.get("persona", "support")
    await handle_conversation_stream(websocket, persona=persona, agent_id=int(agent_id_str) if agent_id_str else None)
