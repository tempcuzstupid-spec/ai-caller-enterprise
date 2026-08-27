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
    """Twilio inbound call webhook. Signature validated via dependency."""
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From", "unknown")

    await call_store.create(
        call_sid=call_sid,
        phone_number=from_number,
        direction="inbound",
        purpose="general",
    )
    record_call("inbound", "general", "initiated")
    logger.info(f"[Webhook] Incoming call | sid={call_sid} from={redact_phone(from_number)}")

    response = VoiceResponse()
    connect = Connect()
    # WebSocket URL points to the VPS (bypasses Fly's broken istio-envoy proxy).
    # Override WS_GATEWAY_URL env var to change.
    ws_url = os.getenv("WS_GATEWAY_URL", "wss://ws.coastalvanguard.org/ws")
    connect.stream(url=ws_url)
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/outbound")
async def outbound_call_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Twilio outbound call connect webhook."""
    form = await request.form()
    call_sid = form.get("CallSid")
    await call_store.update(call_sid, status="answered")
    logger.info(f"[Webhook] Outbound answered | sid={call_sid}")

    response = VoiceResponse()
    connect = Connect()
    # WebSocket URL points to the VPS (bypasses Fly's broken istio-envoy proxy).
    ws_url = os.getenv("WS_GATEWAY_URL", "wss://ws.coastalvanguard.org/ws")
    connect.stream(url=ws_url)
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


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
    logger.info(f"[API] Outbound call | sid={call.sid} to={redact_phone(body.to)} purpose={body.purpose}")

    return CallResponse(
        success=True,
        call_sid=call.sid,
        to=body.to,
        purpose=body.purpose,
        status=call.status,
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


@app.get("/calls/{call_sid}/metrics")
async def get_call_metrics(call_sid: str, _=Depends(verify_admin_api_key)):
    """Get call performance metrics. Requires admin API key."""
    metrics = await call_store.get_metrics(call_sid)
    if not metrics:
        return JSONResponse({"error": "Metrics not found"}, status_code=404)
    return {"call_sid": call_sid, "metrics": metrics}
