"""AI Caller — WebSocket Gateway for Twilio Media Streams.

This runs on the VPS (not Fly) to bypass Fly's istio-envoy WebSocket
rejection. It accepts Twilio media stream WebSocket, pipes audio to
Deepgram (STT), sends transcript to OpenAI (LLM), and streams tokens
to ElevenLabs (TTS) back to the caller.

Run: uvicorn ws_gateway:app --host 127.0.0.1 --port 8765
nginx reverse-proxies wss://ws.dahliastrategic.com/ws -> 127.0.0.1:8765
"""
import asyncio
import base64
import json
import logging
import os
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
logger = logging.getLogger("ai_caller_ws")

# ── Config (from env) ──
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel default
FLY_API_URL = os.getenv("FLY_API_URL", "https://ai-caller-enterprise.fly.dev")
FLY_ADMIN_KEY = os.getenv("FLY_ADMIN_KEY", "")

# ── Pipeline constants ──
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a friendly, professional AI phone agent. Keep responses to 1-2 sentences. "
    "Speak naturally as if on a phone call. Spell out all numbers (e.g. 'twenty dollars' not '$20'). "
    "If you don't know, say so briefly. Never use markdown, bullet points, or emojis.",
)
WELCOME_GREETING = os.getenv(
    "WELCOME_GREETING",
    "Hi, this is the AI assistant. How can I help you today?",
)

# ── Deepgram live STT ──
async def deepgram_stt(audio_queue: asyncio.Queue, transcript_queue: asyncio.Queue) -> None:
    """Connect to Deepgram live, stream audio from queue, push transcripts to queue."""
    if not DEEPGRAM_API_KEY:
        logger.error("DEEPGRAM_API_KEY not set")
        return

    url = "wss://api.deepgram.com/v1/listen?encoding=mulaw&sample_rate=8000&channels=1&model=nova-2&interim_results=false&endpointing=300&utterance_end_ms=1000"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    try:
        async with websockets.connect(url, additional_headers=headers, max_size=10_000_000) as dg:
            logger.info("Connected to Deepgram")

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
                                logger.info(f"STT: {transcript}")
                                await transcript_queue.put(transcript)
                    except Exception as e:
                        logger.warning(f"Deepgram parse error: {e}")

            sender_task = asyncio.create_task(sender())
            receiver_task = asyncio.create_task(receiver())
            await asyncio.gather(sender_task, receiver_task)
    except Exception as e:
        logger.error(f"Deepgram error: {e}")


# ── OpenAI LLM ──
async def openai_llm_stream(transcript: str, history: list) -> str:
    """Stream LLM response from OpenAI, return full text."""
    if not OPENAI_API_KEY:
        return "I'm sorry, my language model is not configured."

    history.append({"role": "user", "content": transcript})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

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
            logger.info(f"LLM: {text}")
            return text
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "I'm sorry, I had trouble processing that."


# ── ElevenLabs TTS ──
async def elevenlabs_tts(text: str) -> bytes:
    """Get audio bytes from ElevenLabs (mulaw 8000Hz for Twilio)."""
    if not ELEVENLABS_API_KEY:
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
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


# ── Twilio media stream handler ──
async def handle_twilio_stream(websocket: WebSocket) -> None:
    """Main handler for Twilio media stream WebSocket."""
    await websocket.accept()
    logger.info("Twilio WebSocket accepted")

    stream_sid: Optional[str] = None
    call_sid: Optional[str] = None
    audio_queue: asyncio.Queue = asyncio.Queue()
    transcript_queue: asyncio.Queue = asyncio.Queue()
    history: list = []

    async def stt_task():
        await deepgram_stt(audio_queue, transcript_queue)

    stt = asyncio.create_task(stt_task())

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            event = data.get("event")

            if event == "connected":
                logger.info(f"Twilio connected: {data}")

            elif event == "start":
                start = data.get("start", {})
                stream_sid = start.get("streamSid")
                call_sid = start.get("callSid")
                logger.info(f"Stream started: stream_sid={stream_sid} call_sid={call_sid}")

                # Send welcome greeting
                welcome_audio = await elevenlabs_tts(WELCOME_GREETING)
                if welcome_audio:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(welcome_audio).decode()},
                    }))

                # Mark stream start in history
                history.append({"role": "system", "content": "[call started]"})

            elif event == "media":
                media = data.get("media", {})
                payload_b64 = media.get("payload", "")
                if payload_b64:
                    audio_bytes = base64.b64decode(payload_b64)
                    await audio_queue.put(audio_bytes)

            elif event == "stop":
                logger.info(f"Stream stopped: stream_sid={stream_sid}")
                await audio_queue.put(None)
                break

            # Process any pending transcripts
            while not transcript_queue.empty():
                transcript = transcript_queue.get_nowait()
                if transcript:
                    response = await openai_llm_stream(transcript, history)
                    audio = await elevenlabs_tts(response)
                    if audio and stream_sid:
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(audio).decode()},
                        }))

    except WebSocketDisconnect:
        logger.info("Twilio WebSocket disconnected")
    except Exception as e:
        logger.error(f"Twilio handler error: {e}")
    finally:
        stt.cancel()
        try:
            await stt
        except (asyncio.CancelledError, Exception):
            pass
        # Notify Fly to save transcript
        if call_sid and FLY_ADMIN_KEY:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{FLY_API_URL}/calls/{call_sid}/transcript",
                        headers={"X-API-Key": FLY_ADMIN_KEY},
                        json={"messages": history},
                    )
            except Exception as e:
                logger.warning(f"Failed to save transcript to Fly: {e}")


# ── FastAPI app ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify required keys
    missing = []
    if not DEEPGRAM_API_KEY: missing.append("DEEPGRAM_API_KEY")
    if not OPENAI_API_KEY: missing.append("OPENAI_API_KEY")
    if not ELEVENLABS_API_KEY: missing.append("ELEVENLABS_API_KEY")
    if missing:
        logger.warning(f"Missing env vars: {missing}")
    else:
        logger.info("All API keys present")
    yield
    logger.info("Shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "ai-caller-ws-gateway",
        "deps": {
            "deepgram": bool(DEEPGRAM_API_KEY),
            "openai": bool(OPENAI_API_KEY),
            "elevenlabs": bool(ELEVENLABS_API_KEY),
        },
    })


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await handle_twilio_stream(websocket)
