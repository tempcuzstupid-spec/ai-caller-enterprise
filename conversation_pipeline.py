"""AI Caller — Twilio ConversationRelay pipeline.

Twilio handles STT (Deepgram) and TTS (ElevenLabs) on their side.
Our backend exchanges JSON text messages over WebSocket.
Barge-in (interruption) is built-in: Twilio sends {type: "interrupt"}
when the caller speaks during our turn.

Message types (Twilio -> us):
  setup:     {type: "setup", callSid, streamSid, ...}
  prompt:    {type: "prompt", voicePrompt, last, ...}
  interrupt: {type: "interrupt"}      (caller spoke during our turn)
  error:     {type: "error", description}
  end:       {type: "end", reason}

Message types (us -> Twilio):
  setup:     {type: "setup", ...}    (initial handshake, optional)
  text:      {type: "text", token, last}   (we send LLM tokens)
  end:       {type: "end"}          (we hang up)

Personas (via query param ?persona=support|sales|tollfree):
  - support: Rachel voice, friendly, general help
  - sales:   Josh voice, BANT qualification, payment link, handoff
  - tollfree: Marcus voice, formal corporate
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

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_caller_conversation")

# ── Config (from env) ──
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FLY_API_URL = os.getenv("FLY_API_URL", "https://ai-caller-enterprise.fly.dev")
FLY_ADMIN_KEY = os.getenv("FLY_ADMIN_KEY", "")
HUMAN_REP_NUMBER = os.getenv("HUMAN_REP_NUMBER", "+17543529826")
SELF_CLOSE_THRESHOLD_USD = int(os.getenv("SELF_CLOSE_THRESHOLD_USD", "400"))
STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/coastalvanguard")

# Load catalog
CATALOG = None
def _load_catalog():
    global CATALOG
    try:
        with open("/opt/ai-caller-ws/catalog.json") as f:
            CATALOG = json.load(f)
        logger.info(f"Catalog loaded: {len(CATALOG.get('products', []))} products")
    except Exception as e:
        logger.warning(f"Catalog not loaded: {e}")
        CATALOG = {"products": [], "bundles": []}
_load_catalog()

# ── Per-persona system prompts and greeting ──
PERSONAS = {
    "support": {
        "system_prompt": (
            "You are a friendly, professional AI phone agent for Coastal Vanguard LLC, an authorized peptide supplier. "
            "Keep responses to 1-2 sentences. Speak naturally as if on a phone call. "
            "Spell out all numbers (e.g. 'twenty dollars' not '$20'). "
            "Never use markdown, bullet points, or emojis. If you don't know, say so briefly."
        ),
        "welcome": "Hi, this is the AI assistant. How can I help you today?",
    },
    "tollfree": {
        "system_prompt": (
            "You are Marcus, a customer service representative for Coastal Vanguard LLC. "
            "Speak formally and professionally. Keep responses to 1-2 sentences. "
            "Spell out all numbers. Never use markdown, bullet points, or emojis. "
            "If the customer asks about products, pricing, or orders, help them directly. "
            "If they need account changes or refunds, say you'll connect them to a specialist."
        ),
        "welcome": "Thank you for calling Coastal Vanguard. This is Marcus. How may I assist you today?",
    },
    "sales": {
        "system_prompt": (
            "You are Marcus, a sales representative for Coastal Vanguard LLC, an authorized peptide supplier. "
            "Mission: qualify the lead, recommend a product/bundle, close small orders (under $400) via SMS payment link, "
            "or warm-handoff to a human rep for larger orders. "
            "Keep responses to 2-3 sentences MAX. Spell out all numbers. "
            "Never use markdown, bullet points, or emojis. "
            "If lead says 'remove' or 'stop calling' — apologize, confirm removal, end call politely. "
            "If lead asks for a human — say you'll connect them with a specialist, then end the call. "
            "Products available: weight management (retatrutide, semaglutide, tirzepatide), "
            "recovery (BPC-157, TB-500, Wolverine Blend, KPV), "
            "growth (CJC-1295, Ipamorelin, Tesamorelin, Sermorelin, IGF-LR3), "
            "longevity (NAD+, MOTS-C, Epithalon), "
            "cognitive (Selank, Semax, Dihexa), "
            "beauty (GHK-Cu, GLOW, KLOW, Melanotan-2), "
            "immune (Thymosin Alpha-1), "
            "specialty (PT-141, SS-31, Glutathione, DSIP). "
            "Bundles range from $174 (D1 Daily Clarity) to $815 (H4 Triple Stack). "
            "Free shipping on orders $500+. Most orders ship within 24 hours."
        ),
        "welcome": "Hi, this is Marcus calling from Coastal Vanguard. {lead_name}? I'm reaching out because you expressed interest in our peptide catalog. Do you have a quick minute?",
    },
}

PERSONA_DEFAULT = "support"


def detect_opt_out(text: str) -> bool:
    text_lower = text.lower()
    patterns = [r"\bremove\b", r"\bstop calling\b", r"\bdo not call\b", r"\bdon'?t call\b", r"\bunsubscribe\b", r"\bopt[-\s]?out\b", r"\bnot interested\b"]
    return any(re.search(p, text_lower) for p in patterns)


def detect_handoff_request(text: str) -> bool:
    text_lower = text.lower()
    patterns = [r"\btalk to (a |someone|human|person|rep)\b", r"\byour manager\b", r"\byour boss\b", r"\breal (person|human)\b", r"\bnot a bot\b"]
    return any(re.search(p, text_lower) for p in patterns)


async def openai_llm_stream(
    transcript: str,
    history: list,
    system_prompt: str,
    on_token: callable = None,
) -> str:
    """Stream LLM response. Optional on_token callback for streaming."""
    history.append({"role": "user", "content": transcript})
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": messages,
                    "max_tokens": 200,
                    "temperature": 0.7,
                    "stream": True,
                },
            )
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
                        if on_token:
                            await on_token(delta)
                except Exception:
                    pass
            history.append({"role": "assistant", "content": full})
            return full
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        err = "I apologize, I'm having a moment. Let me try again."
        if on_token:
            await on_token(err)
        history.append({"role": "assistant", "content": err})
        return err


# ── Twilio ConversationRelay WebSocket handler ──
async def handle_conversation_stream(websocket: WebSocket, persona: str = PERSONA_DEFAULT):
    """ConversationRelay WebSocket handler. Barge-in native."""
    await websocket.accept()
    logger.info(f"ConversationRelay WS accepted | persona={persona}")

    persona_cfg = PERSONAS.get(persona, PERSONAS[PERSONA_DEFAULT])
    system_prompt = persona_cfg["system_prompt"]
    welcome = persona_cfg["welcome"]

    call_sid: Optional[str] = None
    stream_sid: Optional[str] = None
    lead_name: Optional[str] = None
    lead_context: Optional[str] = None
    history: list = []
    opt_out = False
    handoff_requested = False
    payment_link_sent = False
    current_llm_task: Optional[asyncio.Task] = None
    cancelled = asyncio.Event()

    async def send_text(token: str, last: bool = False):
        try:
            await websocket.send_text(json.dumps({
                "type": "text",
                "token": token,
                "last": last,
            }))
        except Exception as e:
            logger.warning(f"send_text error: {e}")

    async def send_end():
        try:
            await websocket.send_text(json.dumps({"type": "end"}))
        except Exception:
            pass

    async def generate_and_send(transcript: str):
        """Generate LLM response and stream tokens to Twilio. Honors cancellation."""
        nonlocal payment_link_sent, opt_out, handoff_requested, current_llm_task
        cancelled.clear()
        full = ""

        async def on_token(delta: str):
            nonlocal full
            if cancelled.is_set():
                return
            full += delta
            # Stream token-by-token to Twilio
            await send_text(delta, last=False)

        try:
            full = await openai_llm_stream(transcript, list(history), system_prompt, on_token)
            if not cancelled.is_set():
                await send_text("", last=True)
        except asyncio.CancelledError:
            logger.info("LLM stream cancelled (barge-in)")
            return
        except Exception as e:
            logger.error(f"generate_and_send error: {e}")
            if not cancelled.is_set():
                await send_text("I apologize, I'm having a moment. Let me try again.", last=True)

        # Self-close trigger: if AI just said "I'll text you a payment link"
        if not payment_link_sent and "payment link" in full.lower() and "text" in full.lower():
            payment_link_sent = True
            if STRIPE_PAYMENT_LINK and call_sid and FLY_ADMIN_KEY:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            f"{FLY_API_URL}/calls/{call_sid}/send-payment-link",
                            headers={"X-API-Key": FLY_ADMIN_KEY},
                            json={"url": STRIPE_PAYMENT_LINK, "amount": SELF_CLOSE_THRESHOLD_USD},
                        )
                    logger.info(f"Payment link triggered for {call_sid}")
                except Exception as e:
                    logger.warning(f"Failed to send payment link: {e}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "setup":
                call_sid = data.get("callSid")
                stream_sid = data.get("streamSid")
                custom = data.get("customParameters", {}) or {}
                lead_name = custom.get("lead_name", "")
                lead_context = custom.get("lead_context", "")
                logger.info(f"Setup: call_sid={call_sid} stream_sid={stream_sid} lead={lead_name}")
                if lead_name:
                    system_prompt = system_prompt + f"\n\nLEAD NAME: {lead_name}"
                if lead_context:
                    system_prompt = system_prompt + f"\n\nLEAD CONTEXT: {lead_context}"
                # Build personalized welcome
                if lead_name and "{lead_name}" in welcome:
                    welcome_msg = welcome.replace("{lead_name}", lead_name)
                else:
                    welcome_msg = welcome
                # Send welcome as a streamed "text" so Twilio speaks it
                await send_text(welcome_msg, last=True)
                history.append({"role": "assistant", "content": welcome_msg})

            elif msg_type == "prompt":
                if opt_out or handoff_requested:
                    continue
                voice_prompt = data.get("voicePrompt", "").strip()
                if not voice_prompt:
                    continue
                last = data.get("last", False)
                logger.info(f"Prompt: {voice_prompt[:80]}{'...' if len(voice_prompt) > 80 else ''}")

                # Compliance checks (sales persona only)
                if persona == "sales":
                    if detect_opt_out(voice_prompt):
                        opt_out = True
                        logger.info(f"Lead opted out: {voice_prompt[:80]}")
                        await send_text("I completely understand, I apologize for the interruption. I'll remove your number from our list right now. Have a great day.", last=True)
                        if call_sid and FLY_ADMIN_KEY:
                            try:
                                async with httpx.AsyncClient(timeout=5.0) as client:
                                    await client.post(f"{FLY_API_URL}/calls/{call_sid}/opt-out", headers={"X-API-Key": FLY_ADMIN_KEY}, json={"reason": voice_prompt[:200]})
                            except Exception as e:
                                logger.warning(f"Failed to record opt-out: {e}")
                        await send_end()
                        continue
                    if detect_handoff_request(voice_prompt):
                        handoff_requested = True
                        logger.info(f"Lead requested handoff: {voice_prompt[:80]}")
                        await send_text("Of course, let me connect you with a specialist who can help. One moment please.", last=True)
                        if call_sid and FLY_ADMIN_KEY:
                            try:
                                async with httpx.AsyncClient(timeout=5.0) as client:
                                    await client.post(f"{FLY_API_URL}/calls/{call_sid}/handoff", headers={"X-API-Key": FLY_ADMIN_KEY}, json={"reason": voice_prompt[:200]})
                            except Exception as e:
                                logger.warning(f"Failed to record handoff: {e}")
                        await send_end()
                        continue

                # Cancel any in-flight LLM stream
                if current_llm_task and not current_llm_task.done():
                    cancelled.set()
                    current_llm_task.cancel()
                    try:
                        await current_llm_task
                    except (asyncio.CancelledError, Exception):
                        pass

                # Start new LLM generation
                current_llm_task = asyncio.create_task(generate_and_send(voice_prompt))

            elif msg_type == "interrupt":
                # Barge-in: caller spoke during our turn. Stop current LLM.
                logger.info("Barge-in detected (interrupt event)")
                if current_llm_task and not current_llm_task.done():
                    cancelled.set()
                    current_llm_task.cancel()
                    try:
                        await current_llm_task
                    except (asyncio.CancelledError, Exception):
                        pass
                # Send empty text to clear the AI's current utterance
                await send_text("", last=False)

            elif msg_type == "error":
                logger.error(f"Twilio error: {data}")

            elif msg_type == "end":
                logger.info(f"Call ended: {data.get('reason', 'unknown')}")
                break

    except WebSocketDisconnect:
        logger.info("ConversationRelay WS disconnected")
    except Exception as e:
        logger.error(f"ConversationRelay handler error: {e}")
    finally:
        if current_llm_task and not current_llm_task.done():
            current_llm_task.cancel()
            try:
                await current_llm_task
            except (asyncio.CancelledError, Exception):
                pass
        # Save transcript
        if call_sid and FLY_ADMIN_KEY:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{FLY_API_URL}/calls/{call_sid}/transcript",
                        headers={"X-API-Key": FLY_ADMIN_KEY},
                        json={"messages": history, "outcome": "opted_out" if opt_out else ("handoff" if handoff_requested else "completed")},
                    )
            except Exception as e:
                logger.warning(f"Failed to save transcript: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "ai-caller-conversation-relay",
        "personas": list(PERSONAS.keys()),
        "deps": {
            "openai": bool(OPENAI_API_KEY),
            "catalog": len(CATALOG.get("products", [])) if CATALOG else 0,
        }
    })


@app.websocket("/ws/conversation")
async def ws_conversation_endpoint(websocket: WebSocket):
    """ConversationRelay WebSocket. Persona is selected via query param
    e.g. /ws/conversation?persona=support|tollfree|sales"""
    persona = websocket.query_params.get("persona", PERSONA_DEFAULT)
    await handle_conversation_stream(websocket, persona)
