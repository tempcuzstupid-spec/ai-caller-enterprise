"""AI Caller Platform — Enterprise FastAPI Entry Point.

Architecture (HTTP-only, no WebSockets):
  - Twilio <Gather input="speech"> captures the caller's voice
  - Twilio's built-in STT transcribes it and POSTs to /webhook/conversation
  - We call OpenAI with conversation history (persisted in Postgres)
  - We return TwiML: <Say> the AI's reply + new <Gather>
  - Twilio's built-in TTS plays the response, then the loop repeats
  - Caller hangup -> /webhook/status with "completed" -> we clean up

Why this architecture:
  - Render's edge proxy (Cloudflare + istio-envoy) blocks WebSocket
    upgrades to docker-runtime services. This is a platform-level
    constraint we cannot bypass from inside the sandbox.
  - <Gather> + <Say> puts all audio processing on Twilio's side —
    same voice quality, zero WebSocket plumbing, works on any HTTP host.
  - Trade-off: ~1-2s HTTP round-trip per turn (vs. <1s with WS).
    Acceptable for a production voice agent.

Features:
  - Twilio webhook signature validation (HMAC-SHA1)
  - API key authentication for admin endpoints
  - Structured JSON logging
  - Prometheus metrics
  - PII redaction in logs
  - Request tracing
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather, Say

from ai_caller.config import get_settings
from ai_caller.database import init_pool, close_pool
from ai_caller.store import call_store
from ai_caller.models import OutboundCallRequest, CallResponse, HealthResponse
from ai_caller.security import verify_admin_api_key, verify_twilio_signature, redact_phone
from ai_caller.metrics import app_info, record_call, record_error
from ai_caller import voice

# ── Logging Setup ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_caller")

# ── Globals ──
settings = get_settings()
twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

app_info.info({"version": "1.0.0", "env": settings.ENV})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB pool (resilient). Shutdown: close everything."""
    logger.info(f"AI Caller Enterprise starting | ENV={settings.ENV} | BASE_URL={settings.BASE_URL}")
    try:
        await init_pool()
    except Exception as exc:
        logger.error(f"DB init failed (continuing in degraded mode): {exc}", exc_info=True)
    yield
    logger.info("AI Caller Enterprise shutting down")
    try:
        await close_pool()
    except Exception as exc:
        logger.warning(f"DB pool close error: {exc}")


app = FastAPI(
    title="AI Caller Platform — Enterprise",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV == "development" else None,
    redoc_url="/redoc" if settings.ENV == "development" else None,
)

# ── Middleware ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ═══════════════════════════════════════════════════════════════
# Twilio Webhooks
# ═══════════════════════════════════════════════════════════════

@app.post("/webhook/incoming")
async def incoming_call_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Inbound call: greet, then start the conversation loop."""
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From", "unknown")

    await call_store.create(
        call_sid=call_sid,
        phone_number=from_number,
        direction="inbound",
        purpose="general",
    )
    # Seed the conversation history with the greeting
    await call_store.add_transcript(call_sid, "assistant", voice.get_greeting("general"))
    record_call("inbound", "general", "initiated")
    logger.info(f"[Webhook] Incoming | sid={call_sid} from={redact_phone(from_number)}")

    # Build the initial TwiML: say the greeting, then start gathering
    conversation_url = f"{settings.BASE_URL}/webhook/conversation?call_sid={call_sid}&purpose=general"
    twiml = voice.build_response_twiml(
        say_text=voice.get_greeting("general"),
        gather_action_url=conversation_url,
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/webhook/outbound")
async def outbound_call_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Outbound call: called when the callee answers. Starts the conversation."""
    form = await request.form()
    call_sid = form.get("CallSid")

    # Look up the call to get purpose + context
    call_state = await call_store.get(call_sid)
    purpose = call_state.purpose if call_state else "general"
    context = call_state.context if call_state else ""
    await call_store.update(call_sid, status="in-progress")
    record_call("outbound", purpose, "answered")
    logger.info(f"[Webhook] Outbound answered | sid={call_sid} purpose={purpose}")

    # Build the conversation history + greeting
    greeting = voice.get_greeting(purpose, context)
    await call_store.add_transcript(call_sid, "assistant", greeting)

    conversation_url = (
        f"{settings.BASE_URL}/webhook/conversation"
        f"?call_sid={call_sid}&purpose={purpose}"
    )
    twiml = voice.build_response_twiml(
        say_text=greeting,
        gather_action_url=conversation_url,
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/webhook/conversation")
async def conversation_turn(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Each turn of the conversation loop.

    Twilio POSTs the caller's transcribed speech here. We:
      1. Read the prior conversation history from the DB
      2. Call OpenAI to generate a response
      3. Persist the new user + assistant messages
      4. Return TwiML: <Say> the response + new <Gather>
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    user_speech = (form.get("SpeechResult") or "").strip()
    digits = (form.get("Digits") or "").strip()

    # If the gather timed out with no speech, user_speech is empty
    if not user_speech and not digits:
        # Re-prompt with a new gather
        conversation_url = f"{settings.BASE_URL}/webhook/conversation?call_sid={call_sid}"
        twiml = voice.build_response_twiml(
            say_text="",
            gather_action_url=conversation_url,
        )
        # The build_response_twiml already handles the empty-say case by
        # just doing the gather + fallback. But we need a real "say" — let's
        # return a minimal gather-only TwiML
        resp = VoiceResponse()
        resp.append(Gather(
            input="speech",
            action=conversation_url,
            method="POST",
            language="en-US",
            speech_timeout="auto",
            timeout=10,
        ))
        resp.say("I didn't catch that. Goodbye!", voice="alice", language="en-US")
        resp.hangup()
        return Response(content=str(resp), media_type="application/xml")

    user_text = user_speech or f"[dtmf:{digits}]"
    logger.info(f"[Conv] {call_sid} user: {user_text[:120]}")

    # Load call state to get purpose
    call_state = await call_store.get(call_sid)
    purpose = (call_state.purpose if call_state else "general") or "general"
    context = (call_state.context if call_state else "") or ""

    # Load full transcript history
    history = await call_store.get_transcript(call_sid)
    messages = voice.build_initial_messages(purpose, context)
    # Append all prior user/assistant turns (skip the seeded greeting already
    # in the system messages)
    for turn in history:
        if turn["role"] in ("user", "assistant"):
            messages.append({"role": turn["role"], "content": turn["content"]})

    # Persist the user's turn
    await call_store.add_transcript(call_sid, "user", user_text)

    # Generate the AI response
    ai_text = await voice.generate_ai_response(messages, call_sid)
    logger.info(f"[Conv] {call_sid} ai:   {ai_text[:120]}")

    # Persist the assistant's turn
    await call_store.add_transcript(call_sid, "assistant", ai_text)

    # Check if the user wants to end the call
    end_call = voice.should_end_call(user_text, ai_text)

    conversation_url = f"{settings.BASE_URL}/webhook/conversation?call_sid={call_sid}&purpose={purpose}"
    twiml = voice.build_response_twiml(
        say_text=ai_text,
        gather_action_url=conversation_url,
        end_call=end_call,
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/webhook/status")
async def call_status_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Call status callbacks: ring, answer, complete, etc."""
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    duration = form.get("CallDuration")

    update = {"status": call_status}
    if duration:
        try:
            update["duration"] = int(duration)
        except ValueError:
            pass

    await call_store.update(call_sid, **update)
    record_call("unknown", "general", call_status)
    logger.info(f"[Webhook] Status | sid={call_sid} status={call_status}")
    return Response(status_code=200)


# ═══════════════════════════════════════════════════════════════
# Outbound Call API
# ═══════════════════════════════════════════════════════════════

@app.post("/call", response_model=CallResponse)
async def trigger_outbound_call(
    request: Request,
    body: OutboundCallRequest,
    _=Depends(verify_admin_api_key),
):
    """Trigger an AI-powered outbound call. Requires admin API key."""
    call = twilio_client.calls.create(
        to=body.to,
        from_=settings.TWILIO_PHONE_NUMBER,
        url=f"{settings.BASE_URL}/webhook/outbound",
        status_callback=f"{settings.BASE_URL}/webhook/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
    )

    await call_store.create(
        call_sid=call.sid,
        phone_number=body.to,
        direction="outbound",
        purpose=body.purpose,
        context=body.context,
    )
    record_call("outbound", body.purpose, "initiated")
    logger.info(
        f"[API] Outbound | sid={call.sid} to={redact_phone(body.to)} purpose={body.purpose}"
    )

    return CallResponse(
        success=True,
        call_sid=call.sid,
        to=body.to,
        purpose=body.purpose,
        status=call.status,
    )


# ═══════════════════════════════════════════════════════════════
# Health, Calls, Transcripts, Metrics
# ═══════════════════════════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
async def health():
    """Deep health check. Returns 200 with status='ok' or 'degraded'."""
    dependencies = {"database": "unknown", "twilio": "unknown"}
    overall_status = "ok"

    try:
        stats = await call_store.get_call_stats()
        dependencies["database"] = "healthy"
    except Exception as exc:
        dependencies["database"] = f"unhealthy: {str(exc)}"
        logger.error(f"[Health] DB check failed: {exc}")
        overall_status = "degraded"
        stats = {"active_calls": 0, "total_calls": 0, "calls_today": 0}

    try:
        twilio_client.api.v2010.accounts.list(limit=1)
        dependencies["twilio"] = "healthy"
    except Exception as exc:
        dependencies["twilio"] = f"unhealthy: {str(exc)}"

    return HealthResponse(
        status=overall_status,
        env=settings.ENV,
        active_calls=stats["active_calls"],
        total_calls=stats["total_calls"],
        calls_today=stats["calls_today"],
        dependencies=dependencies,
    )


@app.get("/calls")
async def list_calls(_=Depends(verify_admin_api_key)):
    """List active calls. Requires admin API key."""
    calls = await call_store.list_active()
    return {
        "active_calls": len(calls),
        "calls": [
            {
                "call_sid": c.call_sid,
                "phone": redact_phone(c.phone_number),
                "direction": c.direction,
                "purpose": c.purpose,
                "status": c.status,
                "started_at": c.started_at.isoformat() if c.started_at else None,
            }
            for c in calls.values()
        ],
    }


@app.get("/calls/{call_sid}/transcript")
async def get_transcript(call_sid: str, _=Depends(verify_admin_api_key)):
    """Get full call transcript. Requires admin API key."""
    state = await call_store.get(call_sid)
    if not state:
        return JSONResponse({"error": "Call not found"}, status_code=404)
    transcript = await call_store.get_transcript(call_sid)
    return {"call_sid": call_sid, "transcript": transcript}


@app.get("/calls/{call_sid}/metrics")
async def get_call_metrics(call_sid: str, _=Depends(verify_admin_api_key)):
    """Get call performance metrics. Requires admin API key."""
    metrics = await call_store.get_metrics(call_sid)
    if not metrics:
        return JSONResponse({"error": "Metrics not found"}, status_code=404)
    return {"call_sid": call_sid, "metrics": metrics}
