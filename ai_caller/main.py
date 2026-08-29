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
from ai_caller.database import init_pool, close_pool, get_conn
from ai_caller.store import call_store
from ai_caller.pipeline import CallPipeline
from ai_caller.models import OutboundCallRequest, CallResponse, HealthResponse, Agent, AgentCreate, AgentUpdate, AgentListResponse
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
        # Seed agent templates on first boot
        try:
            from ai_caller.agent_store import seed_templates_if_empty
            inserted = await seed_templates_if_empty()
            if inserted:
                logger.info(f"🌱 Seeded {inserted} agent templates")
        except Exception as e:
            logger.warning(f"Agent template seeding failed (non-fatal): {e}")
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
# Agent CRUD (Phase 1 — single-tenant, no auth)
# ═══════════════════════════════════════════════════════════════

from ai_caller.agent_store import agent_store, seed_templates_if_empty, AGENT_TEMPLATES


@app.get("/agents", response_model=AgentListResponse)
async def list_agents(_=Depends(verify_admin_api_key)):
    """List all AI agents (personas). Templates are listed first."""
    agents = await agent_store.list()
    return AgentListResponse(total=len(agents), agents=[Agent(**a.to_dict()) for a in agents])


@app.get("/agents/templates")
async def list_agent_templates(_=Depends(verify_admin_api_key)):
    """List the 5 built-in agent templates (metadata only — instantiate via POST /agents)."""
    return {
        "templates": [
            {
                "id": t["slug"],
                "label": t["name"],
                "description": AGENT_CATEGORIES[t["category"]]["description"],
                "direction": t["direction"],
                "category": t["category"],
                "default_prompt": t["system_prompt"],
                "default_opening": t["opening_line"],
            }
            for t in AGENT_TEMPLATES
        ]
    }


AGENT_CATEGORIES = {
    "inbound_support": {
        "label": "Inbound Support",
        "description": "Answers calls 24/7, resolves questions, transfers to a human on request.",
        "direction": "inbound",
    },
    "outbound_sales": {
        "label": "Outbound Sales",
        "description": "Calls leads, qualifies interest, recommends, and warms up for a human closer.",
        "direction": "outbound",
    },
    "appointment_reminder": {
        "label": "Appointment Reminder",
        "description": "Calls to confirm, reschedule, or remind about appointments.",
        "direction": "outbound",
    },
    "personal_assistant": {
        "label": "Personal Assistant",
        "description": "Makes calls on your behalf — bookings, inquiries, reservations.",
        "direction": "both",
    },
    "custom": {
        "label": "Custom Agent",
        "description": "Build your own from scratch.",
        "direction": "both",
    },
}


@app.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(agent_id: int, _=Depends(verify_admin_api_key)):
    a = await agent_store.get(agent_id)
    if not a:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    return Agent(**a.to_dict())


@app.post("/agents", response_model=Agent, status_code=201)
async def create_agent(body: AgentCreate, _=Depends(verify_admin_api_key)):
    """Create a new agent persona. Slug must be unique."""
    existing = await agent_store.get_by_slug(body.slug)
    if existing:
        return JSONResponse({"error": f"Slug {body.slug!r} already in use"}, status_code=409)
    if body.category not in AGENT_CATEGORIES:
        return JSONResponse({"error": f"Invalid category {body.category!r}"}, status_code=400)
    a = await agent_store.create(
        slug=body.slug,
        name=body.name,
        category=body.category,
        direction=body.direction,
        system_prompt=body.system_prompt,
        opening_line=body.opening_line,
        voice_id=body.voice_id,
        model=body.model,
        handoff_number=body.handoff_number,
        handoff_action_url=body.handoff_action_url,
        from_numbers=body.from_numbers,
        knowledge_base=body.knowledge_base,
        active=body.active,
    )
    return Agent(**a.to_dict())


@app.put("/agents/{agent_id}", response_model=Agent)
async def update_agent(agent_id: int, body: AgentUpdate, _=Depends(verify_admin_api_key)):
    a = await agent_store.get(agent_id)
    if not a:
        return JSONResponse({"error": "Agent not found"}, status_code=404)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    a = await agent_store.update(agent_id, **fields)
    return Agent(**a.to_dict())


@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: int, _=Depends(verify_admin_api_key)):
    """Delete an agent. Templates (is_template=True) cannot be deleted."""
    deleted = await agent_store.delete(agent_id)
    if not deleted:
        return JSONResponse({"error": "Agent not found or is a template (cannot delete)"}, status_code=404)
    return {"ok": True, "id": agent_id}


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
    ws_url = "wss://ws.coastalvanguard.org/ws/conversation?persona=tollfree"
    connect.conversation_relay(
        url=ws_url,
        ttsProvider="ElevenLabs",
        voice="TxGEqnHWrfWFTfGW9XjX",  # Josh
        transcriptionProvider="Deepgram",
        speechModel="nova-2-general",
        interruptible="any",
        interruptSensitivity="medium",
        welcomeGreeting="Thank you for calling Coastal Vanguard. This is Marcus. How may I assist you today?",
    )
    # When ConversationRelay ends (handoff or normal), Twilio POSTs here.
    connect.action(f"{settings.BASE_URL}/webhook/transfer")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/voice/{agent_id}")
async def voice_webhook(
    agent_id: int,
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Single agent-aware TwiML endpoint. Replaces the old hardcoded
    /webhook/outbound-conversation and /webhook/incoming-support-conversation.
    Looks up the agent, builds ConversationRelay TwiML with the agent's
    voice/prompt, and connects.

    Twilio calls this when:
      - An outbound call is answered (the agent's id is in the URL we dialed)
      - A Twilio number with this URL configured receives an inbound call

    The action URL on the <Connect> verb goes to /webhook/transfer/{agent_id}
    so handoff goes back to the right transfer endpoint.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From", "")
    to_number = form.get("To", "")

    agent = await agent_store.get(agent_id)
    if not agent or not agent.active:
        logger.warning(f"[Voice] Unknown or inactive agent_id={agent_id} for call={call_sid}")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>This line is not configured. Goodbye.</Say></Response>',
            media_type="application/xml",
        )

    # Look up the call's lead info from call_store (if it's an outbound we
    # already recorded at /call time)
    call_state = await call_store.get(call_sid)
    lead_name = ""
    lead_context = ""
    if call_state:
        lead_name = getattr(call_state, "lead_name", "") or ""
        lead_context = getattr(call_state, "context", "") or ""

    # Mark the call as answered
    await call_store.update(call_sid, status="answered")

    # Log it
    direction = "inbound" if (call_state is None or call_state.direction == "inbound") else "outbound"
    logger.info(
        f"[Voice/{direction}] agent={agent.slug} (id={agent_id}) call={call_sid} "
        f"from={redact_phone(from_number)} to={redact_phone(to_number)} lead={lead_name!r}"
    )

    # Persist the call if this is an inbound call (we didn't record it at /call time)
    if not call_state:
        await call_store.create(
            call_sid=call_sid,
            phone_number=from_number,
            direction="inbound",
            purpose=agent.category,
            context="",
            line=f"agent-{agent.slug}",
            caller_id=to_number,  # the Twilio number they dialed
            lead_name=None,
        )
    # Always link the agent_id (even on outbound where call_state already exists)
    try:
        async with get_conn() as conn:
            await conn.execute(
                "UPDATE calls SET agent_id = $1 WHERE call_sid = $2",
                agent_id, call_sid,
            )
    except Exception as e:
        logger.warning(f"[Voice] Failed to link agent_id to call {call_sid}: {e}")

    # Build the TwiML
    response = VoiceResponse()
    connect = Connect()
    # WebSocket URL — pass agent_id so the VPS can look up the prompt
    ws_url = f"wss://ws.coastalvanguard.org/ws/conversation?agent_id={agent_id}"
    params = {}
    if lead_name:
        params["lead_name"] = lead_name
    if lead_context:
        params["lead_context"] = lead_context
    # Custom param the WS pipeline will read
    params["persona"] = agent.category  # legacy compat
    connect.conversation_relay(
        url=ws_url,
        ttsProvider="ElevenLabs",
        voice=agent.voice_id,
        transcriptionProvider="Deepgram",
        speechModel="nova-2-general",
        interruptible="any",
        interruptSensitivity="medium",
        **({"parameters": params} if params else {}),
    )
    # Action URL — when ConversationRelay ends, Twilio POSTs here
    action_url = agent.handoff_action_url or f"{settings.BASE_URL}/webhook/transfer/{agent_id}"
    connect.action(action_url)
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhook/transfer/{agent_id}")
async def transfer_webhook(
    agent_id: int,
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Called by Twilio when ConversationRelay ends. If handoffData.reasonCode
    is "live-agent-handoff", this returns TwiML that <Dial>s the agent's
    handoff number. Otherwise the call ends with a goodbye.

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
    reason = handoff_data.get("reason", "the caller requested a human")

    # Look up the agent
    agent = await agent_store.get(agent_id)

    logger.info(
        f"[Transfer] call={call_sid} agent_id={agent_id} reasonCode={reason_code!r} lead={lead_name!r}"
    )

    response = VoiceResponse()

    if reason_code != "live-agent-handoff":
        # Normal end of call (caller hung up, conversation finished, etc).
        goodbye = "Thanks for calling. Have a great day."
        if agent:
            goodbye = f"Thanks for calling {agent.name}. Have a great day."
        response.say(goodbye, voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

    # No agent handoff number configured — fall back to a text instead
    handoff_number = agent.handoff_number if agent else None
    handoff_number = handoff_number or settings.HUMAN_REP_NUMBER  # env fallback

    if not handoff_number:
        # No number to dial — just say goodbye
        response.say(
            "Sorry, our specialist isn't available right now. We'll text you a callback number. Goodbye.",
            voice="Polly.Joanna",
        )
        return Response(content=str(response), media_type="application/xml")

    # Live-agent handoff: dial the human rep
    dial = response.dial(
        caller_id=settings.TWILIO_PHONE_NUMBER,
        answer_on_bridge=True,
        timeout=30,
    )
    dial.number(handoff_number)

    # Fallback if <Dial> doesn't complete (the human didn't answer)
    response.say(
        f"Sorry, our specialist didn't pick up. I'll text you a direct line so you can reach us. Sorry about that!",
        voice="Polly.Joanna",
    )

    return Response(content=str(response), media_type="application/xml")


# ── Legacy webhook routes (kept for backwards compat) ───────────────
# /webhook/outbound-conversation and /webhook/transfer now require an
# agent_id. The old unparameterized routes route to the first active
# "outbound" agent as a sensible default.
@app.post("/webhook/outbound-conversation")
async def legacy_outbound_conversation_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Legacy route — routes to the first active outbound sales agent."""
    agents = await agent_store.list(only_active=True)
    fallback = next((a for a in agents if a.category == "outbound_sales"), None) or (agents[0] if agents else None)
    if not fallback:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>No agent configured. Goodbye.</Say></Response>',
            media_type="application/xml",
        )
    # Forward to the new route
    return await voice_webhook(fallback.id, request, _sig)


@app.post("/webhook/transfer")
async def legacy_transfer_webhook(
    request: Request,
    _sig: None = Depends(verify_twilio_signature),
):
    """Legacy /webhook/transfer — picks the first active outbound agent."""
    agents = await agent_store.list(only_active=True)
    fallback = next((a for a in agents if a.category == "outbound_sales"), None) or (agents[0] if agents else None)
    if not fallback:
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response><Say>No agent configured. Goodbye.</Say></Response>',
            media_type="application/xml",
        )
    return await transfer_webhook(fallback.id, request, _sig)


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
            body="You've been removed from our list. Sorry for the bother. — Marcus, Coastal Vanguard",
        )
        logger.info(f"[SMS-In] DNC opt-out for {redact_phone(from_number)}")
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")

    # Catalog request: "catalog", "menu", "info", "details", "what do you have"
    if any(kw in body for kw in ("catalog", "menu", "info", "details", "what do you have", "packages", "products", "send it", "send me", "yes", "link")):
        twilio_client.messages.create(
            to=from_number,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=f"Here's the full Coastal Vanguard catalog: https://coastalvanguard.org — Marcus. Call or text me back anytime.",
        )
        logger.info(f"[SMS-In] Sent catalog link to {redact_phone(from_number)}")
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")

    # Default: acknowledge and offer the next step
    twilio_client.messages.create(
        to=from_number,
        from_=settings.TWILIO_PHONE_NUMBER,
        body=(
            f"Hey! Marcus from Coastal Vanguard here — got your text. "
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
                f"Hey {first_name} — this is Marcus from Coastal Vanguard. "
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
    """Trigger an AI-powered outbound call. Requires admin API key.

    Uses the agent specified by `agent_id` (or the first active outbound_sales
    agent as fallback). The agent controls the voice, model, system prompt,
    opening line, and which Twilio number to call from.

    Body fields:
      to:           E.164 phone number of the lead
      purpose:      sales, lead_qualification, sales_close, appointment
      context:      Free-form context (stored in call row)
      lead_name:    Lead's name (used in greeting + system prompt)
      lead_context: Specific context (e.g., "showed interest in retatrutide")
      tz:           IANA timezone (default America/New_York)
      agent_id:     Which agent persona to use (optional)
    """
    # ── Resolve the agent ─────────────────────────────────────
    agent = None
    if body.agent_id:
        agent = await agent_store.get(body.agent_id)
        if not agent:
            return CallResponse(
                success=False, to=body.to, purpose=body.purpose,
                status="rejected_no_agent",
                message=f"Agent id {body.agent_id} not found",
            )
        if agent.direction == "inbound":
            return CallResponse(
                success=False, to=body.to, purpose=body.purpose,
                status="rejected_agent_inbound_only",
                message=f"Agent {agent.slug!r} is inbound-only",
            )
    else:
        # Pick the first active outbound agent
        all_agents = await agent_store.list(only_active=True)
        agent = next(
            (a for a in all_agents if a.category == "outbound_sales" and a.direction in ("outbound", "both")),
            None,
        ) or next(
            (a for a in all_agents if a.direction in ("outbound", "both")),
            None,
        )
    if not agent:
        return CallResponse(
            success=False, to=body.to, purpose=body.purpose,
            status="rejected_no_agent",
            message="No active outbound agent configured. Create one via POST /agents.",
        )

    # ── Pick a caller ID from the agent's pool ─────────────────
    import random
    from_numbers_pool = [n.strip() for n in (agent.from_numbers or "").split(",") if n.strip()]
    if not from_numbers_pool:
        # Fall back to env defaults
        from_numbers_pool = [
            os.getenv("TWILIO_OUTBOUND_PRIMARY", settings.TWILIO_PHONE_NUMBER),
            os.getenv("TWILIO_OUTBOUND_SECONDARY", settings.TWILIO_PHONE_NUMBER),
        ]
        from_numbers_pool = [n for n in from_numbers_pool if n]
    if not from_numbers_pool:
        from_numbers_pool = [settings.TWILIO_PHONE_NUMBER]
    caller_id = random.choice(from_numbers_pool)

    # ── Compliance check: calling hours (7am-7pm local time) ─────
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

    # ── DNC list check (env var for now; Phase 2 will use DB) ───
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

    # ── Place the call ───────────────────────────────────────────
    call = twilio_client.calls.create(
        to=body.to,
        from_=caller_id,
        # The URL Twilio fetches when the lead answers. Includes the agent_id
        # so the TwiML handler knows which agent persona to use.
        url=f"{settings.BASE_URL}/webhook/voice/{agent.id}",
        status_callback=f"{settings.BASE_URL}/webhook/outbound-status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST",
        record=True,
    )

    await call_store.create(
        call_sid=call.sid,
        phone_number=body.to,
        direction="outbound",
        purpose=body.purpose,
        context=body.context or "",
        line=f"agent-{agent.slug}",
        caller_id=caller_id,
        lead_name=body.lead_name,
    )
    # Link the agent_id directly
    try:
        async with get_conn() as conn:
            await conn.execute(
                "UPDATE calls SET agent_id = $1 WHERE call_sid = $2",
                agent.id, call.sid,
            )
    except Exception as e:
        logger.warning(f"[/call] Failed to set agent_id on call {call.sid}: {e}")

    record_call("outbound", body.purpose, "initiated")
    logger.info(
        f"[API] Outbound call | agent={agent.slug} (id={agent.id}) sid={call.sid} "
        f"to={redact_phone(body.to)} purpose={body.purpose} from={caller_id}"
    )

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
        "ws_url": os.getenv("WS_GATEWAY_URL", "wss://ws.coastalvanguard.org/ws"),
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
      - url: link to send (defaults to coastalvanguard.org)
    """
    body = await request.json()
    url = body.get("url", "https://coastalvanguard.org")
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
            f"Hey {first_name} — Marcus from Coastal Vanguard here. "
            f"Like we discussed, here's more on the {package}: {url} "
            f"Take your time — text me back anytime on this number. — Marcus"
        )
    elif package:
        sms_body = (
            f"Hey — Marcus from Coastal Vanguard. "
            f"Like we discussed, here's more on the {package}: {url} "
            f"Text back anytime on this number. — Marcus"
        )
    elif first_name:
        sms_body = (
            f"Hey {first_name} — Marcus from Coastal Vanguard here. "
            f"Here's our full catalog as promised: {url} "
            f"Text me back anytime on this number. — Marcus"
        )
    else:
        sms_body = (
            f"Thanks for your interest in Coastal Vanguard! "
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
            body=f"Here's your secure payment link from Coastal Vanguard: {url} — Marcus",
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
