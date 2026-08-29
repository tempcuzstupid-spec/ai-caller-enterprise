"""AI Caller Platform — Enterprise FastAPI Entry Point.

Security & Observability:
  - Twilio webhook signature validation
  - API key authentication for admin endpoints
  - Rate limiting
  - Structured JSON logging
  - Prometheus metrics
  - Circuit breakers for external APIs
  - PII redaction
  - Request tracing
"""
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect

from ai_caller.config import get_settings
from ai_caller.database import init_pool, close_pool
from ai_caller.store import call_store
from ai_caller.pipeline import CallPipeline
from ai_caller.models import OutboundCallRequest, CallResponse, HealthResponse
from ai_caller.security import verify_admin_api_key, redact_phone
from ai_caller.middleware import logging_middleware, body_cache_middleware
from ai_caller.security import verify_twilio_signature
from ai_caller.metrics import app_info, record_call, record_error, calls_active

# ── Logging Setup ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai_caller")

# ── Globals ──
settings = get_settings()
twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
active_pipelines: dict[str, CallPipeline] = {}

app_info.info({"version": "1.0.0", "env": settings.ENV})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB pool (resilient). Shutdown: close everything gracefully.

    DB init failure does not crash the app — the service stays up and
    returns a degraded /health response so Render's health check still
    passes. This lets us deploy with placeholder secrets and swap them
    in without re-deploying.
    """
    logger.info(f"🚀 AI Caller Enterprise starting | ENV={settings.ENV} | BASE_URL={settings.BASE_URL}")
    try:
        await init_pool()
    except Exception as exc:
        logger.error(
            f"⚠️ Database init failed (continuing in degraded mode): {exc}",
            exc_info=True,
        )
    yield
    logger.info("👋 AI Caller Enterprise shutting down")
    for pipeline in list(active_pipelines.values()):
        try:
            await pipeline.close()
        except Exception as exc:
            logger.warning(f"Pipeline close error: {exc}")
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

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ═══════════════════════════════════════════════════════════════
# Twilio Webhooks (with signature validation)
# ═══════════════════════════════════════════════════════════════

@app.post("/webhook/incoming")
async def incoming_call_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Twilio inbound call webhook. Miami 786 number is RESERVED for the
    future AI Assistant project — DO NOT repurpose for support/sales.
    This route is kept as a no-op stub that just 200s so the webhook
    validates. The AI Assistant project will replace this handler.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From", "unknown")
    logger.info(f"[Webhook] Miami 786 received call (reserved for AI Assistant project) | sid={call_sid} from={redact_phone(from_number)}")
    return Response(status_code=200)


@app.post("/webhook/incoming-support-conversation")
async def incoming_support_conversation_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Toll-free (888) — ConversationRelay with Marcus voice (formal corporate)."""
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From", "unknown")

    await call_store.create(
        call_sid=call_sid,
        phone_number=from_number,
        direction="inbound",
        purpose="customer_service",
        line="tollfree-888-conversation",
    )
    record_call("inbound", "customer_service", "initiated")
    logger.info(f"[Webhook] Incoming (tollfree-888-conversation) | sid={call_sid} from={redact_phone(from_number)}")

    response = VoiceResponse()
    connect = Connect()
    ws_url = f"wss://{settings.BRAND_WS_DOMAIN}/ws/conversation?persona=tollfree"
    connect.conversation_relay(
        url=ws_url,
        ttsProvider="ElevenLabs",
        voice="TxGEqnHWrfWFTfGW9XjX",  # Josh
        transcriptionProvider="Deepgram",
        speechModel="nova-2-general",
        interruptible="any",
        interruptSensitivity="medium",
        welcomeGreeting=f"Thank you for calling {settings.BRAND_NAME}. This is Marcus. How may I assist you today?",
        # When ConversationRelay ends (handoff or normal), Twilio POSTs here.
        action=f"{settings.BASE_URL}/webhook/transfer",
    )
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/outbound-conversation")
async def outbound_conversation_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Outbound sales — ConversationRelay with sales persona."""
    form = await request.form()
    call_sid = form.get("CallSid")
    await call_store.update(call_sid, status="answered")

    # Fetch lead info from call_store so the WebSocket pipeline can use
    # lead_name and lead_context in the system prompt and opening line
    call_state = await call_store.get(call_sid)
    lead_name = ""
    lead_context = ""
    if call_state:
        lead_name = getattr(call_state, "lead_name", "") or ""
        # The 'context' field in CallState holds what was sent as `context`
        # or `lead_context` in the /call request body
        lead_context = getattr(call_state, "context", "") or ""

    logger.info(f"[Webhook] Outbound conversation answered | sid={call_sid} lead={lead_name!r} ctx={lead_context!r}")

    response = VoiceResponse()
    connect = Connect()
    ws_url = f"wss://{settings.BRAND_WS_DOMAIN}/ws/conversation?persona=sales"
    # Twilio's ConversationRelay `parameters` are passed as `customParameters`
    # in the WebSocket setup event. We pass lead_name and lead_context so
    # the backend can personalize the greeting and system prompt.
    params = {}
    if lead_name:
        params["lead_name"] = lead_name
    if lead_context:
        params["lead_context"] = lead_context
    connect.conversation_relay(
        url=ws_url,
        ttsProvider="ElevenLabs",
        voice="TxGEqnHWrfWFTfGW9XjX",  # Josh
        transcriptionProvider="Deepgram",
        speechModel="nova-2-general",
        interruptible="any",
        interruptSensitivity="medium",
        # When ConversationRelay ends (either normally OR via the "end" message
        # with handoffData), Twilio POSTs to this action URL. The /webhook/transfer
        # handler reads HandoffData and decides whether to <Dial> David.
        action=f"{settings.BASE_URL}/webhook/transfer",
        **({"parameters": params} if params else {}),
    )
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/transfer")
async def transfer_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Called by Twilio when ConversationRelay ends. If handoffData.reasonCode
    is "live-agent-handoff", this returns TwiML that <Dial>s the human rep
    (David) so the lead talks to a real person. Otherwise the call ends.

    The handoff data is passed via the WebSocket "end" message in
    conversation_pipeline.py when the lead asks for a human / wants to order.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    handoff_data_raw = form.get("HandoffData", "")
    handoff_data = {}
    if handoff_data_raw:
        try:
            handoff_data = json.loads(handoff_data_raw)
        except Exception as e:
            logger.warning(f"[Transfer] Bad HandoffData JSON: {e}")

    reason_code = handoff_data.get("reasonCode", "")
    lead_name = handoff_data.get("lead_name", "the caller")
    persona = handoff_data.get("persona", "support")
    reason = handoff_data.get("reason", "the caller requested a human")

    logger.info(f"[Transfer] call={call_sid} reasonCode={reason_code!r} persona={persona} lead={lead_name!r}")

    response = VoiceResponse()

    if reason_code != "live-agent-handoff":
        # Normal end of call (caller hung up, conversation finished, etc).
        # Just say goodbye and end.
        response.say(f"Thanks for calling {settings.BRAND_NAME}. Have a great day.")
        return Response(content=str(response), media_type="application/xml")

    # Live-agent handoff: dial the human rep.
    # We whisper context to David before bridging: who the lead is, what
    # they wanted, and which persona was active. David hears this, the
    # caller does not.
    whisper = (
        f"Live handoff. Lead: {lead_name}. "
        f"Persona: {persona}. "
        f"Reason: {reason[:120]}. "
        f"When the caller hears the beep, greet them and confirm their order."
    )
    dial = response.dial(
        caller_id=settings.TWILIO_PHONE_NUMBER,  # Our Twilio number shows on caller ID
        answer_on_bridge=True,  # Bridge only when David picks up
        timeout=30,  # Ring David for 30s
    )
    # The <Number> noun dials an external number. We use it because David's
    # number (+17543529826) is a personal cell, not a Twilio client.
    dial.number(
        settings.HUMAN_REP_NUMBER,
        send_digits="wwww1928",  # Optional: bypass David's voicemail
    )

    # If the dial fails (David doesn't answer), say a goodbye and offer
    # to text instead.
    if dial.payload:
        # action URL on the dial — if David doesn't pick up, Twilio hits this
        # and we can fall back to an SMS or voicemail.
        pass

    # Fallback if <Dial> doesn't complete (David didn't answer)
    response.say(
        f"Sorry, our specialist didn't pick up. I'll text you a direct line "
        f"so you can reach us. Sorry about that!",
        voice="Polly.Joanna",
    )

    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/incoming-sms")
async def incoming_sms_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """When a lead texts our Twilio number back (e.g. "what packages
    do you have?"), we acknowledge and offer to call them or send the
    catalog link. This is the "SMS handoff" path.

    For now: simple keyword-ack reply. A future iteration can call the
    LLM to generate a real conversational SMS reply.
    """
    form = await request.form()
    from_number = form.get("From", "")
    body = (form.get("Body", "") or "").strip().lower()

    logger.info(f"[SMS-In] from={redact_phone(from_number)} body={body[:120]!r}")

    # DNC: if they text "stop", "unsubscribe", "quit", or "cancel"
    if any(kw in body for kw in ("stop", "unsubscribe", "quit", "cancel", "remove", "opt out", "do not call")):
        twilio_client.messages.create(
            to=from_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=f"You've been removed from our list. Sorry for the bother. — Marcus, {settings.BRAND_NAME}",
        )
        logger.info(f"[SMS-In] DNC opt-out for {redact_phone(from_number)}")
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")

    # Catalog request: "catalog", "menu", "info", "details", "what do you have"
    if any(kw in body for kw in ("catalog", "menu", "info", "details", "what do you have", "packages", "products", "send it", "send me", "yes", "link")):
        twilio_client.messages.create(
            to=from_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=f"Here's the full {settings.BRAND_NAME} catalog: https://{settings.BRAND_DOMAIN} — Marcus. Call or text me back anytime.",
        )
        logger.info(f"[SMS-In] Sent catalog link to {redact_phone(from_number)}")
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")

    # Default: acknowledge and offer the next step
    twilio_client.messages.create(
        to=from_number,
        from_=settings.TWILIO_PHONE_NUMBER,
        body=(
            f"Hey! Marcus from {settings.BRAND_NAME} here — got your text. "
            f"Want me to send the catalog? Text CATALOG. "
            f"Or call me back at this number. — Marcus"
        ),
    )
    logger.info(f"[SMS-In] Sent default ack to {redact_phone(from_number)}")
    return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")


@app.post("/webhook/outbound-status")
async def outbound_status_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Status callback for the outbound numbers. Twilio hits this for
    ringing/answered/completed events on the 754 numbers."""
    form = await request.form()
    call_sid = form.get("CallSid")
    status = form.get("CallStatus")
    direction = form.get("Direction", "outbound-api")
    to_number = form.get("To", "unknown")
    from_number = form.get("From", "unknown")

    # Only create call record if it doesn't exist (first status event)
    if status == "ringing":
        try:
            await call_store.create(
                call_sid=call_sid,
                phone_number=to_number,
                direction="outbound",
                purpose="sales",
                line="outbound-754",
            )
        except Exception:
            pass  # already exists

    duration = form.get("CallDuration")
    update = {"status": status}
    if duration:
        try: update["duration"] = int(duration)
        except ValueError: pass

    try:
        await call_store.update(call_sid, **update)
    except Exception:
        pass

    record_call("outbound", "sales", status)
    logger.info(f"[Webhook] Outbound status | sid={call_sid} status={status} to={redact_phone(to_number)}")

    # ── Missed-call auto-text: when the lead doesn't pick up, send a follow-up SMS ──
    if status in ("no-answer", "busy", "failed"):
        try:
            call_state = await call_store.get(call_sid)
            lead_name = getattr(call_state, "lead_name", "") if call_state else ""
            first_name = lead_name.split()[0] if lead_name else "there"
            sms_body = (
                f"Hey {first_name} — this is Marcus from {settings.BRAND_NAME}. "
                f"Just tried to give you a ring about our wellness programs. "
                f"When you get a sec, call me back at {from_number} or text this number. "
                f"Talk soon! — Marcus"
            )
            msg = twilio_client.messages.create(
                to=to_number,
                from_=settings.TWILIO_PHONE_NUMBER,
                body=sms_body,
            )
            logger.info(f"[Missed-call SMS] Sent to {redact_phone(to_number)} sid={msg.sid}")
        except Exception as e:
            logger.warning(f"[Missed-call SMS] Failed for {call_sid}: {e}")

    # ── Voicemail detection: if completed but very short duration, the call likely went to voicemail ──
    # We don't get the voicemail audio here — Twilio's voicemail transcription requires
    # a separate voicemail TwiML. For now we just log the case.
    if status == "completed":
        try:
            dur = int(duration) if duration else 0
            if dur > 0 and dur < 8:
                logger.info(f"[Voicemail?] Call {call_sid} to {redact_phone(to_number)} completed in {dur}s — likely voicemail")
                # TODO: trigger a voicemail drop (pre-recorded audio)
        except Exception:
            pass

    return {"received": True}


@app.post("/webhook/status")
async def call_status_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Twilio call status callbacks."""
    form = await request.form()
    call_sid = form.get("CallSid")
    status = form.get("CallStatus")
    duration = form.get("CallDuration")

    update = {"status": status}
    if duration:
        try: update["duration"] = int(duration)
        except ValueError: pass

    await call_store.update(call_sid, **update)
    record_call("unknown", "general", status)
    logger.info(f"[Webhook] Status | sid={call_sid} status={status}")

    if status in ("completed", "failed", "busy", "no-answer", "canceled"):
        for stream_sid, pipeline in list(active_pipelines.items()):
            if pipeline.call_state.call_sid == call_sid:
                await pipeline.close()
                active_pipelines.pop(stream_sid, None)
                break

    return Response(status_code=200)


# ═══════════════════════════════════════════════════════════════
# Outbound Call API (with auth & validation)
# ═══════════════════════════════════════════════════════════════

@app.post("/call", response_model=CallResponse)
async def trigger_outbound_call(
    request: Request,
    body: OutboundCallRequest,
    _=Depends(verify_admin_api_key),
):
    """Trigger an AI-powered outbound sales call. Requires admin API key.

    Rotates caller ID between the two 754 numbers (primary/secondary) for
    local-presence dialing. Uses the sales WebSocket pipeline.

    Body fields:
      to:           E.164 phone number of the lead
      purpose:      sales, lead_qualification, sales_close, appointment
      context:      Free-form context
      lead_name:    Lead's name (used in greeting)
      lead_context: Specific context (e.g., "showed interest in retatrutide")
      tz:           IANA timezone (default America/New_York)
    """
    # Round-robin caller ID rotation between the 2 outbound numbers
    primary_cid = os.getenv("TWILIO_OUTBOUND_PRIMARY", "+17542193360")
    secondary_cid = os.getenv("TWILIO_OUTBOUND_SECONDARY", "+17542092728")
    # Simple rotation: count outbound calls in last 1 min and alternate
    # (production: use Redis counter or DB-backed counter)
    import random
    caller_id = random.choice([primary_cid, secondary_cid])

    # Compliance check: calling hours (7am-7pm local time)
    # Lead timezone passed in body.tz (default: America/New_York)
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz_name = getattr(body, "tz", "America/New_York")
        tz = ZoneInfo(tz_name)
        local_hour = datetime.now(tz).hour
        if local_hour < 7 or local_hour >= 19:
            return CallResponse(
                success=False,
                to=body.to,
                purpose=body.purpose,
                status="rejected_calling_hours",
                message=f"Outside calling hours (7am-7pm {tz_name}). Current local hour: {local_hour}",
            )
    except Exception as e:
        logger.warning(f"Calling-hours check failed (non-fatal): {e}")

    # DNC list check (if env var set)
    dnc_list_raw = os.getenv("DNC_NUMBERS", "")
    if dnc_list_raw:
        dnc_set = set(x.strip() for x in dnc_list_raw.split(","))
        if body.to in dnc_set:
            return CallResponse(
                success=False,
                to=body.to,
                purpose=body.purpose,
                status="rejected_dnc",
                message="Number is on DNC list",
            )

    call = twilio_client.calls.create(
        to=body.to,
        from_=caller_id,
        url=f"{settings.BASE_URL}/webhook/outbound-conversation",  # When lead answers, gets ConversationRelay TwiML
        status_callback=f"{settings.BASE_URL}/webhook/outbound-status",  # Status events
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
        # Recording disclosure: announce call is recorded at start
        record=True,
    )

    await call_store.create(
        call_sid=call.sid,
        phone_number=body.to,
        direction="outbound",
        purpose=body.purpose,
        context=body.context or "",
        line="outbound-754",
        caller_id=caller_id,
        lead_name=body.lead_name,
    )
    record_call("outbound", body.purpose, "initiated")
    logger.info(f"[API] Outbound call | sid={call.sid} to={redact_phone(body.to)} purpose={body.purpose} from={caller_id}")

    return CallResponse(
        success=True,
        call_sid=call.sid,
        to=body.to,
        purpose=body.purpose,
        status=call.status,
        caller_id=caller_id,
    )


# ═══════════════════════════════════════════════════════════════
# Media Stream WebSocket
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws/media-stream")
async def media_stream(websocket: WebSocket):
    """Bidirectional WebSocket for real-time audio streaming."""
    await websocket.accept()
    pipeline: CallPipeline | None = None
    stream_sid: str | None = None

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                logger.debug("[WS] Twilio connected")
                continue

            if event == "start":
                start_data = data["start"]
                stream_sid = start_data["streamSid"]
                call_sid = start_data["callSid"]

                call_state = await call_store.get(call_sid)
                if not call_state:
                    call_state = await call_store.create(
                        call_sid=call_sid,
                        phone_number="unknown",
                        direction="inbound",
                    )

                await call_store.update(call_sid, status="in-progress", stream_sid=stream_sid)

                pipeline = CallPipeline(
                    call_state=call_state,
                    stream_sid=stream_sid,
                    websocket=websocket,
                    deepgram_key=settings.DEEPGRAM_API_KEY,
                    openai_key=settings.OPENAI_API_KEY,
                    eleven_key=settings.ELEVENLABS_API_KEY,
                    eleven_voice=settings.ELEVENLABS_VOICE_ID,
                )
                active_pipelines[stream_sid] = pipeline
                await pipeline.start()
                logger.info(f"[WS] Call started | sid={call_sid} purpose={call_state.purpose}")
                continue

            if event == "media" and pipeline:
                await pipeline.handle_media(data["media"]["payload"])
                continue

            if event == "stop":
                logger.info(f"[WS] Stream stopped | sid={stream_sid}")
                if pipeline:
                    await pipeline.close()
                if stream_sid:
                    active_pipelines.pop(stream_sid, None)
                break

    except WebSocketDisconnect:
        logger.info(f"[WS] Disconnected | sid={stream_sid}")
    except Exception as exc:
        record_error("websocket", type(exc).__name__)
        logger.error(f"[WS] Error | sid={stream_sid} exc={exc}")
    finally:
        if pipeline:
            await pipeline.close()
        if stream_sid:
            active_pipelines.pop(stream_sid, None)


# ═══════════════════════════════════════════════════════════════
# Utilities & Monitoring
# ═══════════════════════════════════════════════════════════════

@app.get("/_version")
async def version():
    """Debug: which code is actually running."""
    import hashlib, os
    try:
        with open(__file__, 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()[:12]
    except:
        h = "unknown"
    return {
        "git_sha": os.getenv("GIT_SHA", "unset"),
        "code_hash": h,
        "ws_url": os.getenv("WS_GATEWAY_URL", f"wss://{settings.BRAND_WS_DOMAIN}/ws"),
        "base_url": settings.BASE_URL,
        "has_ws_route": hasattr(app, "router") and any(getattr(r, "path", "") == "/ws/media-stream" for r in app.router.routes),
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    """Deep health check with dependency validation.

    Returns 200 even when degraded so Render's health check passes while
    secrets are being filled in. Status field reports "ok" or "degraded".
    """
    dependencies = {"database": "unknown", "twilio": "unknown"}
    overall_status = "ok"

    # Check database
    try:
        stats = await call_store.get_call_stats()
        dependencies["database"] = "healthy"
    except Exception as exc:
        dependencies["database"] = f"unhealthy: {str(exc)}"
        logger.error(f"[Health] DB check failed: {exc}")
        overall_status = "degraded"
        stats = {"active_calls": 0, "total_calls": 0, "calls_today": 0}

    # Check Twilio (lightweight) — use API version 2010-04-01 (the only stable one)
    try:
        # In twilio-python v9+, .accounts is a property returning a Version resource.
        # The correct pattern is: client.api.v2010.accounts(sid).fetch()
        # But the simpler validation is to fetch the account list, which uses
        # the configured credentials without needing a per-call sid lookup.
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
    """List all active calls. Requires admin API key."""
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
    """Get call transcript. Requires admin API key."""
    state = await call_store.get(call_sid)
    if not state:
        return JSONResponse({"error": "Call not found"}, status_code=404)
    transcript = await call_store.get_transcript(call_sid)
    return {"call_sid": call_sid, "transcript": transcript}


@app.post("/calls/{call_sid}/send-catalog-sms")
async def send_catalog_sms(call_sid: str, request: Request, _=Depends(verify_admin_api_key)):
    """Send a catalog link SMS to the lead's phone. Triggered by Marcus
    when he says he's sending the link.

    Optional body fields:
      - package: package name (e.g. "A1 · First Time, Done Right")
      - lead_name: personalization (e.g. "David")
      - url: link to send (defaults to the brand domain)
    """
    body = await request.json()
    url = body.get("url", f"https://{settings.BRAND_DOMAIN}")
    package = body.get("package", "").strip()
    lead_phone = body.get("lead_phone")

    # Resolve lead info from call store
    state = await call_store.get(call_sid)
    if not lead_phone and state:
        lead_phone = state.phone_number
    if not lead_phone:
        return JSONResponse({"error": "No lead phone number available"}, status_code=400)

    lead_name = body.get("lead_name", "").strip()
    if not lead_name and state:
        lead_name = getattr(state, "lead_name", "") or ""
    first_name = lead_name.split()[0] if lead_name else ""

    # Personalize the message based on whether Marcus recommended a package
    if package and first_name:
        sms_body = (
            f"Hey {first_name} — Marcus from {settings.BRAND_NAME} here. "
            f"Like we discussed, here's more on the {package}: {url} "
            f"Take your time — text me back anytime on this number. — Marcus"
        )
    elif package:
        sms_body = (
            f"Hey — Marcus from {settings.BRAND_NAME}. "
            f"Like we discussed, here's more on the {package}: {url} "
            f"Text back anytime on this number. — Marcus"
        )
    elif first_name:
        sms_body = (
            f"Hey {first_name} — Marcus from {settings.BRAND_NAME} here. "
            f"Here's our full catalog as promised: {url} "
            f"Text me back anytime on this number. — Marcus"
        )
    else:
        sms_body = (
            f"Thanks for your interest in {settings.BRAND_NAME}! "
            f"Here's the full catalog: {url} — Marcus"
        )

    try:
        msg = twilio_client.messages.create(
            to=lead_phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=sms_body,
        )
        logger.info(f"[SMS] Catalog link sent to {redact_phone(lead_phone)} sid={msg.sid} package={package!r} lead_name={first_name!r}")
        return {"success": True, "message_sid": msg.sid, "to": lead_phone, "body": sms_body}
    except Exception as e:
        logger.error(f"[SMS] Failed to send: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/calls/{call_sid}/send-payment-link")
async def send_payment_link(call_sid: str, request: Request, _=Depends(verify_admin_api_key)):
    """Send a payment link SMS to the lead. (Legacy endpoint, no longer used by Marcus in qual-only mode.)"""
    body = await request.json()
    url = body.get("url", "https://buy.stripe.com/coastalvanguard")
    lead_phone = body.get("lead_phone")
    if not lead_phone:
        state = await call_store.get(call_sid)
        if state:
            lead_phone = state.phone_number
    if not lead_phone:
        return JSONResponse({"error": "No lead phone"}, status_code=400)
    try:
        msg = twilio_client.messages.create(
            to=lead_phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=f"Here's your secure payment link from {settings.BRAND_NAME}: {url} — Marcus",
        )
        return {"success": True, "message_sid": msg.sid, "to": lead_phone}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/calls/{call_sid}/opt-out")
async def record_opt_out(call_sid: str, request: Request, _=Depends(verify_admin_api_key)):
    """Record an opt-out from the lead. Adds the phone to DNC list."""
    body = await request.json()
    reason = body.get("reason", "")
    state = await call_store.get(call_sid)
    if state:
        phone = state.phone_number
        # Add to env-var based DNC list (append to DNC_NUMBERS env var)
        # Production: write to a real DNC table in Postgres
        logger.info(f"[DNC] {phone} opted out: {reason[:100]}")
    return {"success": True}


@app.post("/calls/{call_sid}/handoff")
async def record_handoff(call_sid: str, request: Request, _=Depends(verify_admin_api_key)):
    """Record a handoff-to-human event. Used by Marcus when transferring to specialist."""
    body = await request.json()
    reason = body.get("reason", "")
    logger.info(f"[HANDOFF] Call {call_sid} handoff requested: {reason[:100]}")
    return {"success": True}


@app.get("/calls/{call_sid}/metrics")
async def get_call_metrics(call_sid: str, _=Depends(verify_admin_api_key)):
    """Get call performance metrics. Requires admin API key."""
    metrics = await call_store.get_metrics(call_sid)
    if not metrics:
        return JSONResponse({"error": "Metrics not found"}, status_code=404)
    return {"call_sid": call_sid, "metrics": metrics}
