"""Sales AI Pipeline — different from support.

Persona: Marcus (confident, professional, conversational, BANT-qualified).
Voice:  ELEVENLABS_VOICE_ID_SALES env var (default: Marcus ID).
Goal:   qualify the lead, recommend a product/bundle, close (small orders)
        or hand off to a human rep (large orders).

Compliance:
  - Recording disclosure at call start
  - DNC opt-out (press 9)
  - Calling-hours (enforced on Fly side, not here)
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_caller_sales")

# ── Config (from env) ──
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID_SALES = os.getenv("ELEVENLABS_VOICE_ID_SALES", "TxGEqnHWrfWFTfGW9XjX")  # Josh default — confident male
FLY_API_URL = os.getenv("FLY_API_URL", "https://ai-caller-enterprise.fly.dev")
FLY_ADMIN_KEY = os.getenv("FLY_ADMIN_KEY", "")
HUMAN_REP_NUMBER = os.getenv("HUMAN_REP_NUMBER", "+17543529826")  # user cell for warm handoff
SELF_CLOSE_THRESHOLD_USD = int(os.getenv("SELF_CLOSE_THRESHOLD_USD", "400"))  # below this, AI self-closes
STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/coastalvanguard")

# Load catalog at startup
CATALOG = None
def _load_catalog():
    global CATALOG
    try:
        with open("/opt/ai-caller-ws/catalog.json") as f:
            CATALOG = json.load(f)
        logger.info(f"Catalog loaded: {len(CATALOG.get('products', []))} products, {len(CATALOG.get('bundles', []))} bundles")
    except Exception as e:
        logger.warning(f"Catalog not loaded: {e}")
        CATALOG = {"products": [], "bundles": []}

_load_catalog()

# ── Sales System Prompt ──
SALES_SYSTEM_PROMPT = """You are Marcus, a friendly and knowledgeable sales representative for Coastal Vanguard LLC, an authorized peptide supplier for research and wellness purposes.

YOUR MISSION:
1. Build rapport quickly (under 10 seconds)
2. Qualify the lead using BANT (Budget, Authority, Need, Timeline)
3. Recommend the right product or bundle based on their goals
4. For orders under $400: send them a payment link via SMS and close yourself
5. For orders over $400: warm-transfer to a human rep at +1-754-352-9826
6. If they ask to be removed from the list, immediately apologize and confirm
7. If they sound upset or want a human, transfer immediately

RULES:
- Keep responses to 2-3 sentences MAX. This is a phone call, not a lecture.
- Spell out all numbers ("three hundred dollars", not "$300")
- Never use markdown, bullet points, emojis, or special characters
- If you don't know, say "let me get my colleague who can help with that"
- Mention products by name (Retatrutide, Tirzepatide, BPC-157, etc.) — these are real products with real clinical backing
- All products are for research and wellness purposes, not for human consumption, not FDA-approved for therapeutic use. Disclose this if asked.
- Coastal Vanguard ships within 24 hours, M-F, with $35 standard shipping (free on orders $500+)
- Payment methods: CashApp, Zelle, Apple Pay, Visa, Mastercard, Amex, Bank Wire, Crypto (USDC)
- For institutional orders or accounts, transfer to a human

GREETING (use the lead's name if available):
"Hi, this is Marcus calling from Coastal Vanguard. {lead_name}? I'm reaching out because you expressed interest in our peptide catalog. Do you have a quick minute?"

If they say "not interested": "No problem at all, I appreciate your time. Have a great day." Then end the call politely.

If they say "remove me from your list": "I completely understand, I'll remove you right now. Sorry to bother you." Then signal end-of-call to the system.

WHEN TO RECOMMEND A BUNDLE vs SINGLE PRODUCT:
- If they want weight loss and have no GLP-1 experience: A1 "First Time, Done Right" ($463)
- If they have 15+ lbs to lose and want aggressive results: A4 "Triple Threat" ($648) — TRANSFER
- If they're cost-conscious: A5 "Vial-Max Value" ($530) — TRANSFER
- If they want recovery from injury: C2 "Wolverine" ($267) or C1 "Athlete" ($415)
- If they want longevity/anti-aging: B1 "Foundation" ($245) or B2 "Energy & Vitality" ($409)
- If they want cognitive boost: D1 "Daily Clarity" ($174) or D2 "Peak Cognitive" ($204)
- If they want beauty/skin: E1 "Glow" ($236) or E2 "Tanned & Toned" ($378)

HANDOFF PROTOCOL:
- For high-value orders: "Let me connect you with my colleague who specializes in this. One moment please."
- Then end the call normally — the system will transfer them.

OPT-OUT DETECTION:
- If they say "remove", "stop calling", "do not call", or "unsubscribe": apologize, confirm removal, end the call.
- The system handles the actual removal.
"""

# ── Deepgram live STT (same as support) ──
async def deepgram_stt(audio_queue: asyncio.Queue, transcript_queue: asyncio.Queue, lead_context: dict) -> None:
    if not DEEPGRAM_API_KEY:
        logger.error("DEEPGRAM_API_KEY not set")
        return

    url = "wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&channels=1&model=nova-2&interim_results=false&endpointing=300&utterance_end_ms=1000"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(url, additional_headers=headers, max_size=10_000_000) as dg:
            logger.info("Connected to Deepgram (sales)")

            async def sender():
                while True:
                    audio = await audio_queue.get()
                    if audio is None:
                        break
                    await dg.send(audio)

            async def receiver():
                async for msg in dg:
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "Results":
                            channel = data.get("channel", {})
                            alt = channel.get("alternatives", [{}])[0]
                            transcript = alt.get("transcript", "").strip()
                            if transcript and data.get("is_final"):
                                logger.info(f"STT (sales): {transcript}")
                                await transcript_queue.put(transcript)
                    except Exception as e:
                        logger.warning(f"Deepgram parse error: {e}")

            sender_task = asyncio.create_task(sender())
            receiver_task = asyncio.create_task(receiver())
            await asyncio.gather(sender_task, receiver_task)
    except Exception as e:
        logger.error(f"Deepgram error: {e}")


async def openai_llm_stream(transcript: str, history: list, system_prompt: str) -> str:
    if not OPENAI_API_KEY:
        return "I'm sorry, my language model is not configured."

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
                },
            )
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            history.append({"role": "assistant", "content": text})
            logger.info(f"LLM (sales): {text[:150]}")
            return text
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "I apologize, I'm having a moment. Let me try again."


async def elevenlabs_tts(text: str) -> bytes:
    if not ELEVENLABS_API_KEY:
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID_SALES}/stream"
    params = {"output_format": "ulaw_8000"}
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, params=params, headers=headers, json=payload)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.error(f"ElevenLabs error: {e}")
        return b""


def detect_opt_out(text: str) -> bool:
    """Detect if lead is asking to be removed from list."""
    text_lower = text.lower()
    patterns = [
        r"\bremove\b", r"\bstop calling\b", r"\bdo not call\b", r"\bdon'?t call\b",
        r"\bunsubscribe\b", r"\bopt[-\s]?out\b", r"\bnot interested\b",
    ]
    return any(re.search(p, text_lower) for p in patterns)


def detect_handoff_request(text: str) -> bool:
    """Detect if lead wants a human."""
    text_lower = text.lower()
    patterns = [
        r"\btalk to (a |someone|human|person|rep)\b",
        r"\byour manager\b", r"\byour boss\b",
        r"\breal (person|human)\b", r"\bnot a bot\b",
    ]
    return any(re.search(p, text_lower) for p in patterns)


def extract_order_value(text: str) -> int:
    """Try to extract a dollar amount from the lead's text (e.g. 'three hundred', '$500')."""
    # Map written numbers to digits
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
        "hundred": 100, "two hundred": 200, "three hundred": 300,
        "four hundred": 400, "five hundred": 500, "six hundred": 600,
        "seven hundred": 700, "eight hundred": 800, "nine hundred": 900,
        "thousand": 1000,
    }
    text_lower = text.lower()
    # Find explicit dollar signs first
    m = re.search(r'\$(\d+)', text)
    if m: return int(m.group(1))
    # Try to match "X hundred" or "X thousand"
    for phrase, val in sorted(word_to_num.items(), key=lambda x: -len(x[0])):
        if phrase in text_lower:
            return val
    return 0


# ── Twilio media stream handler (sales-specific) ──
async def handle_sales_stream(websocket: WebSocket) -> None:
    """Sales WebSocket handler."""
    await websocket.accept()
    logger.info("Sales WebSocket accepted")

    stream_sid: Optional[str] = None
    call_sid: Optional[str] = None
    lead_context = {}
    audio_queue: asyncio.Queue = asyncio.Queue()
    transcript_queue: asyncio.Queue = asyncio.Queue()
    history: list = []
    opt_out = False
    handoff_requested = False
    payment_link_sent = False

    async def stt_task():
        await deepgram_stt(audio_queue, transcript_queue, lead_context)

    stt = asyncio.create_task(stt_task())

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            event = data.get("event")

            if event == "start":
                start = data.get("start", {})
                stream_sid = start.get("streamSid")
                call_sid = start.get("callSid")
                # Twilio puts custom params in start.customParameters
                custom = start.get("customParameters", {}) or {}
                lead_name = custom.get("lead_name", "")
                lead_ctx_text = custom.get("lead_context", "")
                logger.info(f"Sales stream started: stream_sid={stream_sid} call_sid={call_sid} lead_name={lead_name}")

                lead_context = {"name": lead_name, "context": lead_ctx_text}

                # Build personalized system prompt
                sys_prompt = SALES_SYSTEM_PROMPT
                if lead_name:
                    sys_prompt += f"\n\nLEAD NAME: {lead_name}"
                if lead_ctx_text:
                    sys_prompt += f"\n\nLEAD CONTEXT: {lead_ctx_text}"

                # Replace the {lead_name} placeholder in the greeting
                if lead_name:
                    greeting = f"Hi, this is Marcus calling from Coastal Vanguard. {lead_name}? I'm reaching out because you expressed interest in our peptide catalog. Do you have a quick minute?"
                else:
                    greeting = "Hi, this is Marcus calling from Coastal Vanguard. I'm reaching out because you expressed interest in our peptide catalog. Do you have a quick minute?"

                # Recording disclosure + greeting
                disclosure = "This call may be recorded for quality and training purposes."
                full_opening = f"{disclosure} {greeting}"

                welcome_audio = await elevenlabs_tts(full_opening)
                if welcome_audio:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(welcome_audio).decode()},
                    }))
                history.append({"role": "system", "content": f"[outbound sales call to {lead_name or 'unknown'}, call_sid={call_sid}]"})

            elif event == "media":
                media = data.get("media", {})
                payload_b64 = media.get("payload", "")
                if payload_b64:
                    audio_bytes = base64.b64decode(payload_b64)
                    await audio_queue.put(audio_bytes)

            elif event == "stop":
                logger.info(f"Sales stream stopped: stream_sid={stream_sid}")
                await audio_queue.put(None)
                break

            # Process any pending transcripts
            while not transcript_queue.empty():
                transcript = transcript_queue.get_nowait()
                if not transcript or opt_out or handoff_requested:
                    continue

                # Compliance: opt-out detection
                if detect_opt_out(transcript):
                    logger.info(f"Lead opted out: {transcript[:100]}")
                    opt_out = True
                    goodbye = "I completely understand, I apologize for the interruption. I'll remove your number from our list right now. Have a great day."
                    audio = await elevenlabs_tts(goodbye)
                    if audio and stream_sid:
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(audio).decode()},
                        }))
                    # Notify Fly to add to DNC list
                    if call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.post(
                                    f"{FLY_API_URL}/calls/{call_sid}/opt-out",
                                    headers={"X-API-Key": FLY_ADMIN_KEY},
                                    json={"phone_number": "unknown", "reason": transcript[:200]},
                                )
                        except Exception as e:
                            logger.warning(f"Failed to record opt-out: {e}")
                    continue

                # Handoff detection
                if detect_handoff_request(transcript):
                    logger.info(f"Lead requested human handoff: {transcript[:100]}")
                    handoff_requested = True
                    handoff_msg = f"Of course, let me connect you with a specialist who can help. One moment please."
                    audio = await elevenlabs_tts(handoff_msg)
                    if audio and stream_sid:
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(audio).decode()},
                        }))
                    # Notify Fly to mark for warm transfer
                    if call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.post(
                                    f"{FLY_API_URL}/calls/{call_sid}/handoff",
                                    headers={"X-API-Key": FLY_ADMIN_KEY},
                                    json={"reason": transcript[:200]},
                                )
                        except Exception as e:
                            logger.warning(f"Failed to record handoff: {e}")
                    continue

                # Normal LLM response
                sys_prompt = SALES_SYSTEM_PROMPT
                if lead_context.get("name"):
                    sys_prompt += f"\n\nLEAD NAME: {lead_context['name']}"
                if lead_context.get("context"):
                    sys_prompt += f"\n\nLEAD CONTEXT: {lead_context['context']}"

                response = await openai_llm_stream(transcript, history, sys_prompt)
                audio = await elevenlabs_tts(response)
                if audio and stream_sid:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(audio).decode()},
                    }))

                # Self-close trigger: if AI just said "I'll text you a payment link"
                if not payment_link_sent and "payment link" in response.lower() and "text" in response.lower():
                    if STRIPE_PAYMENT_LINK and call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                await client.post(
                                    f"{FLY_API_URL}/calls/{call_sid}/send-payment-link",
                                    headers={"X-API-Key": FLY_ADMIN_KEY},
                                    json={"url": STRIPE_PAYMENT_LINK, "amount": SELF_CLOSE_THRESHOLD_USD},
                                )
                            payment_link_sent = True
                            logger.info(f"Payment link triggered for {call_sid}")
                        except Exception as e:
                            logger.warning(f"Failed to send payment link: {e}")

    except WebSocketDisconnect:
        logger.info("Sales WebSocket disconnected")
    except Exception as e:
        logger.error(f"Sales handler error: {e}")
    finally:
        stt.cancel()
        try:
            await stt
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
                logger.warning(f"Failed to save sales transcript: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = []
    if not DEEPGRAM_API_KEY: missing.append("DEEPGRAM_API_KEY")
    if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")
    if not ELEVENLABS_API_KEY: missing.append("ELEVENLABS_API_KEY")
    if missing:
        logger.warning(f"Missing env vars: {missing}")
    else:
        logger.info("All API keys present (sales pipeline)")
    yield
    logger.info("Sales pipeline shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "ai-caller-sales-pipeline",
        "deps": {
            "deepgram": bool(DEEPGRAM_API_KEY),
            "openai": bool(OPENAI_API_KEY),
            "elevenlabs": bool(ELEVENLABS_API_KEY),
        },
        "catalog": {
            "products": len(CATALOG.get("products", [])) if CATALOG else 0,
            "bundles": len(CATALOG.get("bundles", [])) if CATALOG else 0,
        },
        "config": {
            "voice_id": ELEVENLABS_VOICE_ID_SALES,
            "self_close_threshold": SELF_CLOSE_THRESHOLD_USD,
            "human_rep": HUMAN_REP_NUMBER,
        }
    })


@app.websocket("/ws/sales")
async def ws_sales_endpoint(websocket: WebSocket):
    await handle_sales_stream(websocket)
