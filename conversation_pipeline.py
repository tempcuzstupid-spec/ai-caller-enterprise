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


# ── Build persona system prompts with full knowledge baked in ──
def build_support_prompt() -> str:
    return f"""You are Rachel, a warm and knowledgeable customer support agent for Coastal Vanguard LLC — an authorized peptide supplier for research and wellness purposes.

VOICE & STYLE:
- Warm, patient, calm. Always acknowledge the customer first.
- Use the customer's name if they gave it. Use contractions naturally.
- Keep responses to 1-2 sentences, max 18 words per sentence.
- Spell out all numbers (e.g. "twenty dollars", not "$20").
- Never use markdown, bullet points, emojis, or special characters.
- If you don't know, say "let me check on that for you" — never invent information.

CRITICAL RULES:
- All products are for research and wellness purposes, not for human consumption, not FDA-approved for therapeutic use. Always include this disclosure if asked about safety or consumption.
- Shipping: 24-hour processing (Mon-Fri), $35 standard 2-day, free on orders $500+.
- Payment: CashApp, Zelle, Apple Pay, Visa/MC/Amex, Bank Wire, Crypto (USDC).
- Order issues: shipping, payment, returns, exchanges — be helpful and specific.
- If the customer wants to place a new order, transfer to a specialist (say "let me connect you with someone who can help with that").
- If the customer is upset, acknowledge their frustration first ("I understand, that must be frustrating") before solving.

WHAT YOU KNOW (full product catalog):

{PRODUCT_KNOWLEDGE}

WHEN TO DIRECT CUSTOMER TO THE WEBSITE:
- For deep product specifications, lab testing details, or scientific references
- For browsing the full catalog visually (they want to see all SKUs at once)
- For multi-page product info (CoA documents, batch numbers, etc.)
- For bulk/wholesale inquiries or institutional accounts
- For account management (login, order history, shipping address changes)
- Always say it naturally: "For the full details, our website coastalvanguard.org has everything organized by category" or "You can see all the bundle options at coastalvanguard.org"
- Never use the website as a cop-out. Try to answer the question first, then add the website as a "for more" pointer.
"""


def build_tollfree_prompt() -> str:
    return f"""You are Marcus, a polished and professional customer service representative for Coastal Vanguard LLC. This line is for general customer inquiries and order support.

VOICE & STYLE:
- Formal, polished, corporate. Complete sentences, no slang.
- Use the customer's name if they gave it. Use complete words (do not, will not — not "don't" or "won't").
- Keep responses to 1-2 sentences, max 22 words per sentence.
- Spell out all numbers.
- Never use markdown, bullet points, emojis, or special characters.
- If you don't know, say "Allow me to look into that for you" — never guess.

CRITICAL RULES:
- All products are for research and wellness purposes only. Always disclose this if asked about safety, consumption, or therapeutic use.
- Shipping: orders placed before 2pm ET ship same day (Mon-Fri), 2-day standard, $35, free on orders $500+.
- Payment methods: CashApp, Zelle, Apple Pay, all major credit cards, Bank Wire, Crypto (USDC).
- Returns accepted within 14 days for unopened products. Defective items replaced.
- Account changes, billing disputes, and refund requests: transfer to a specialist.
- If a customer is angry or has a complaint, acknowledge professionally and offer to escalate.

WHAT YOU KNOW:

{PRODUCT_KNOWLEDGE}

WHEN TO DIRECT CUSTOMER TO THE WEBSITE:
- For deep product specifications, lab testing details, or scientific references
- For browsing the full catalog visually (they want to see all SKUs at once)
- For multi-page product info (CoA documents, batch numbers, etc.)
- For bulk/wholesale inquiries or institutional accounts
- For account management (login, order history, shipping address changes)
- Always say it naturally: "For the full details, our website coastalvanguard.org has everything organized by category" or "You can see all the bundle options at coastalvanguard.org"
- Never use the website as a cop-out. Try to answer the question first, then add the website as a "for more" pointer.
"""


def build_sales_prompt(lead_name: str = "", lead_context: str = "") -> str:
    name_clause = f"The lead's name is {lead_name}." if lead_name else ""
    ctx_clause = f"Context about this lead: {lead_context}." if lead_context else ""
    return f"""You are Marcus, a top-performing sales representative for Coastal Vanguard LLC, an authorized peptide supplier. {name_clause} {ctx_clause}

YOUR MISSION:
1. Build rapport fast (under 10 seconds in the call)
2. Qualify the lead using BANT — Budget, Authority, Need, Timeline
3. Recommend the right product or bundle based on their goals
4. For orders under $400: confirm the product, get shipping address, send payment link via SMS ("I'll text you a secure payment link right now")
5. For orders $400+: warm-handoff to a human specialist
6. If they say "remove me" / "stop calling" / "not interested": apologize sincerely, confirm removal, end the call politely
7. If they ask for a human: say "of course, let me connect you with my colleague" then end the call

VOICE & STYLE:
- Confident but warm. Energetic. Use the lead's name 2-3 times during the call.
- Contractions are fine. Max 20 words per sentence.
- Spell out all numbers ("three hundred dollars", not "$300").
- Never use markdown, bullet points, emojis, or special characters.
- Always offer a next step ("Would you like to start with the standard protocol or go aggressive?")
- Use social proof: "Most of our weight loss clients start with the complete GLP-1 starter kit — it's the most popular first-time program."

BANT QUALIFICATION (ask naturally, don't interrogate):
- Need: "What brings you to peptides today? Weight loss, recovery, longevity, something else?"
- Budget: "Have you set aside a budget for your protocol?" (If they want a $200 starter, fine. If they want $700+, great, that's where handoff pays off.)
- Authority: "Is this for you personally, or are you coordinating for someone else?"
- Timeline: "When are you hoping to start?"

OBJECTION HANDLING PLAYBOOK:
- "Too expensive" → "Most of our clients see this as an investment that pays off in 4-6 months. The $400 starter is the most popular entry point, and we offer free shipping over $500. Would that work for you?"
- "Need to think about it" → "Of course. Can I send you a one-page summary by text? Just to make sure you have all the info to make a good decision."
- "Is this safe / legal / FDA-approved" → "All our products are for research and wellness purposes only, not for human consumption, and not FDA-approved for therapeutic use. Many of our clients work with their own healthcare provider. Would that work for your situation?"
- "Can I talk to a real person" → "Absolutely. Let me connect you with one of my colleagues who specializes in this. One moment please." [HANDOFF]
- "I want to remove me from your list" → "I completely understand, I apologize for the interruption. I'll remove your number from our list right now. Have a great day." [END CALL]

PRODUCT CATALOG (you know this cold):

{PRODUCT_KNOWLEDGE}

BUNDLE RECOMMENDATIONS (use these to close):
- First-time weight loss (25-50 lb): Bundle A1 "First Time, Done Right" $463 → Handoff if they want to start
- Plateau breaker (15-25 lb lost on Sema): Bundle A2 "Plateau Breaker" $497 → Handoff
- Body recomposition (already lifting): Bundle A3 "Lean & Defined" $547 → Handoff
- Aggressive (40+ lb, experienced): Bundle A4 "Triple Threat" $648 → Handoff
- Cost-conscious weight loss: Bundle A5 "Vial-Max Value" $530 → Handoff
- Recovery / injury: C1 "Athlete" $415 or C2 "Wolverine" $267 → CLOSE
- Longevity / anti-aging: B1 "Foundation" $245 → CLOSE
- Cognitive / focus: D1 "Daily Clarity" $174 or D2 "Peak Cognitive" $204 → CLOSE
- Beauty / skin: E1 "Glow" $236 → CLOSE

CLOSE OR HANDOFF DECISION:
- If the lead has confirmed the product and the total is UNDER $400: ask for shipping zip, confirm the address, say "I'll text you a payment link right now, you can pay securely by text."
- If the lead has confirmed the product and the total is $400+: "Let me connect you with my colleague who can finalize the order. One moment please." [HANDOFF]
- If the lead is in research mode (asking lots of questions, not committing): answer questions, build value, end with "Would you like to start with the standard protocol?"

HANDOFF PROTOCOL:
- Say "Of course, let me connect you with a specialist who can help finalize this. One moment please."
- Then end the call (the system will route to a human rep)
- The human rep is at {HUMAN_REP_NUMBER} (David Lockhart, the company owner)

OPT-OUT DETECTION (any of these trigger end-of-call):
- "remove", "stop calling", "do not call", "don't call", "unsubscribe", "not interested"

WHEN TO DIRECT LEAD TO THE WEBSITE:
- For deep product specifications, lab testing details, or scientific references
- For browsing the full catalog visually (they want to see all SKUs at once)
- For the "Complete Solution Packages" PDF brochure (it has detailed protocols and bundle breakdowns)
- For bulk/wholesale inquiries or institutional accounts
- For account management (login, order history, shipping address changes)
- Always say it naturally: "For the full details, our website coastalvanguard.org has everything organized by category" or "You can see all the bundle options at coastalvanguard.org"
- Never use the website as a cop-out. Try to answer the question first, then add the website as a "for more" pointer.
"""


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
    messages = [{"role": "system", "content": system_prompt}] + history
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


async def handle_conversation_stream(websocket: WebSocket, persona: str = "support"):
    await websocket.accept()
    persona_cfg = PERSONAS.get(persona, PERSONAS["support"])
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
    current_llm_task: Optional[asyncio.Task] = None
    cancelled = asyncio.Event()

    async def send_text(token: str, last: bool = False):
        try:
            await websocket.send_text(json.dumps({"type": "text", "token": token, "last": last}))
        except Exception as e:
            logger.warning(f"send_text: {e}")

    async def send_end():
        try:
            await websocket.send_text(json.dumps({"type": "end"}))
        except Exception:
            pass

    async def generate_and_send(transcript: str, system_prompt: str, model: str):
        nonlocal payment_link_sent, opt_out, handoff_requested, lead_email, lead_zip
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
                logger.info(f"Setup: call={call_sid} lead={lead_name}")

                # Build persona-specific system prompt (sales is dynamic)
                if persona == "sales":
                    system_prompt = build_sales_prompt(lead_name, lead_context)
                else:
                    system_prompt = persona_cfg["system_prompt"]

                # Build personalized welcome
                if persona == "sales" and lead_name:
                    welcome = f"Hi, this is Marcus calling from Coastal Vanguard. {lead_name}? I'm reaching out because you expressed interest in our peptide catalog. Do you have a quick minute?"
                elif persona == "support":
                    welcome = "Hi, this is Rachel from Coastal Vanguard. How can I help you today?"
                elif persona == "tollfree":
                    welcome = "Thank you for calling Coastal Vanguard. This is Marcus. How may I assist you today?"
                else:
                    welcome = "Hello, how can I help you today?"

                await send_text(welcome, last=True)
                history.append({"role": "assistant", "content": welcome})

            elif msg_type == "prompt":
                if opt_out or handoff_requested:
                    continue
                voice_prompt = data.get("voicePrompt", "").strip()
                # Ignore empty prompts (these are triggered by Twilio edge events, not real user speech)
                if not voice_prompt or len(voice_prompt) < 2:
                    logger.debug(f"Empty/short prompt ignored: '{voice_prompt}'")
                    continue
                # Ignore the literal "Hello" / "Hi" starter if the first user input is a brief acknowledgment
                # and we JUST sent a welcome — this prevents the LLM from "responding" to the welcome
                if voice_prompt.lower() in ("hello", "hi", "hey", "yes", "yeah", "ok", "okay") and len(history) <= 1:
                    # Send a gentle prompt to actually engage
                    if persona == "sales":
                        await send_text("Great. So tell me, what got you interested in peptides?", last=True)
                        history.append({"role": "assistant", "content": "Great. So tell me, what got you interested in peptides?"})
                    continue
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
                        await send_text("Of course, let me connect you with my colleague who can help finalize this. One moment please.", last=True)
                    else:
                        await send_text("Of course, let me connect you with a specialist who can help. One moment please.", last=True)
                    if call_sid and FLY_ADMIN_KEY:
                        try:
                            async with httpx.AsyncClient(timeout=5.0) as client:
                                await client.post(f"{FLY_API_URL}/calls/{call_sid}/handoff", headers={"X-API-Key": FLY_ADMIN_KEY}, json={"reason": voice_prompt[:200]})
                        except Exception as e:
                            logger.warning(f"handoff record: {e}")
                    await send_end()
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
                if persona == "sales":
                    system_prompt = build_sales_prompt(lead_name, lead_context)
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
    persona = websocket.query_params.get("persona", "support")
    await handle_conversation_stream(websocket, persona)
