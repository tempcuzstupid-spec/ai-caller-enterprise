"""Enterprise call orchestrator.

Manages STT → LLM → TTS pipeline with:
  - Circuit breaker protection for all external APIs
  - Prometheus metrics collection
  - Barge-in handling
  - PostgreSQL persistence
  - Structured logging
"""
import asyncio
import logging
import time
from typing import Optional

from ai_caller.services.stt import DeepgramSTT
from ai_caller.services.llm import LLMService
from ai_caller.services.tts import ElevenLabsTTS
from ai_caller.utils.audio import decode_twilio_media, encode_twilio_media
from ai_caller.store import CallState, call_store
from ai_caller.circuit_breaker import CircuitBreaker
from ai_caller.metrics import (
    calls_active, stt_latency_seconds, llm_latency_seconds,
    tts_latency_seconds, ws_messages_total,
)
from ai_caller.config import get_settings

logger = logging.getLogger("ai_caller.pipeline")
settings = get_settings()


class CallPipeline:
    """Manages one phone call end-to-end with enterprise observability."""

    def __init__(
        self,
        call_state: CallState,
        stream_sid: str,
        websocket,
        deepgram_key: str,
        openai_key: str,
        eleven_key: str,
        eleven_voice: str,
    ):
        self.call_state = call_state
        self.stream_sid = stream_sid
        self.websocket = websocket
        self.start_time = time.time()

        # Circuit breakers for each external service
        self.cb_stt = CircuitBreaker(
            name="deepgram_stt",
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout=settings.CB_RECOVERY_TIMEOUT,
        )
        self.cb_llm = CircuitBreaker(
            name="openai_llm",
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout=settings.CB_RECOVERY_TIMEOUT,
        )
        self.cb_tts = CircuitBreaker(
            name="elevenlabs_tts",
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout=settings.CB_RECOVERY_TIMEOUT,
        )

        # Services
        self.stt = DeepgramSTT(deepgram_key, self._on_transcript, circuit_breaker=self.cb_stt)
        self.llm = LLMService(openai_key, circuit_breaker=self.cb_llm)
        self.tts = ElevenLabsTTS(eleven_key, eleven_voice, circuit_breaker=self.cb_tts)

        # Conversation state
        self.messages: list[dict] = []
        self._setup_system_prompt()

        # Pipeline state machines
        self.current_utterance = ""
        self.is_processing = False
        self.interrupted = False
        self.interruption_count = 0
        self.tool_call_count = 0
        self.tts_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self.tts_player_task: Optional[asyncio.Task] = None
        self.tts_generation_task: Optional[asyncio.Task] = None

        # Metrics
        calls_active.labels(direction=call_state.direction).inc()

    def _setup_system_prompt(self):
        """Configure AI persona based on call purpose."""
        purpose = self.call_state.purpose
        context = self.call_state.context

        personas = {
            "sales_demo": (
                f"You are a friendly sales rep. {context} "
                "Ask discovery questions, handle objections calmly, "
                "and push for a meeting booking. Be persistent but polite."
            ),
            "support": (
                "You are a technical support agent. Empathize with the user, "
                "ask clarifying questions, and attempt to resolve before transferring."
            ),
            "reminder": (
                "You are delivering a reminder. Be brief and clear. "
                "Confirm they received the message and ask if they need to reschedule."
            ),
            "personal_assistant": (
                "You are a personal assistant making calls on behalf of your user. "
                "Be polite, efficient, and clearly state who you represent."
            ),
        }
        persona = personas.get(purpose, (
            "You are a professional AI phone assistant. Speak naturally and concisely. "
            "Always confirm important details before taking action."
        ))
        self.messages.append({"role": "system", "content": persona})

    # ── Lifecycle ──

    async def start(self):
        """Initialize connections and start background workers."""
        await self.stt.connect()
        self.tts_player_task = asyncio.create_task(self._tts_player_loop())

        greeting = "Hello, thanks for calling. How can I help you today?"
        if self.call_state.purpose == "reminder":
            greeting = f"Hi, this is a reminder call. {self.call_state.context}"
        elif self.call_state.purpose == "sales_demo":
            greeting = "Hey! I wanted to reach out about a special offer. Do you have a quick minute?"

        self.messages.append({"role": "assistant", "content": greeting})
        await self._speak(greeting)
        logger.info(f"Call pipeline started | sid={self.call_state.call_sid} purpose={self.call_state.purpose}")

    async def close(self):
        """Graceful teardown with metrics persistence."""
        self.interrupted = True
        calls_active.labels(direction=self.call_state.direction).dec()

        for task in (self.tts_player_task, self.tts_generation_task):
            if task and not task.done():
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

        await self.stt.close()
        await self.tts.close()

        # Persist call metrics
        duration = int(time.time() - self.start_time)
        await call_store.update(self.call_state.call_sid, status="completed", duration=duration)
        await call_store.add_metrics(
            call_sid=self.call_state.call_sid,
            interruption_count=self.interruption_count,
            tool_call_count=self.tool_call_count,
        )
        logger.info(f"Call pipeline closed | sid={self.call_state.call_sid} duration={duration}s")

    # ── Media Handlers ──

    async def handle_media(self, payload_b64: str):
        """Incoming audio from Twilio → forward to Deepgram."""
        audio = decode_twilio_media(payload_b64)
        await self.stt.send(audio)
        ws_messages_total.labels(direction="inbound").inc()

    # ── STT Callback ──

    async def _on_transcript(self, transcript: str, is_final: bool, speech_final: bool):
        """Process transcript from Deepgram."""
        if not is_final:
            if not self.interrupted and self.is_processing:
                self.interrupted = True
                self.interruption_count += 1
                await self._cancel_speaking()
            return

        self.current_utterance += " " + transcript

        if speech_final:
            full_text = self.current_utterance.strip()
            self.current_utterance = ""
            if full_text and not self.is_processing:
                await call_store.add_transcript(self.call_state.call_sid, "user", full_text)
                await self._process_user_message(full_text)

    # ── Core Processing ──

    async def _process_user_message(self, text: str):
        """STT → LLM → TTS pipeline with metrics."""
        self.is_processing = True
        self.interrupted = False
        pipeline_start = time.time()

        try:
            self.messages.append({"role": "user", "content": text})

            # LLM generation
            llm_start = time.time()
            response_text, tool_calls = await self.llm.generate_response(self.messages)
            llm_latency = int((time.time() - llm_start) * 1000)
            llm_latency_seconds.observe(llm_latency / 1000)

            # Tool execution
            if tool_calls:
                self.messages.append({
                    "role": "assistant",
                    "content": response_text or None,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    result = await self.llm.execute_tool(tc, call_state=self.call_state)
                    self.tool_call_count += 1
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                    await call_store.add_transcript(
                        self.call_state.call_sid, "tool", result,
                        tool_name=tc["function"]["name"],
                        tool_args=tc["function"].get("arguments"),
                    )
                response_text, _ = await self.llm.generate_response(self.messages)

            if response_text:
                self.messages.append({"role": "assistant", "content": response_text})
                await call_store.add_transcript(self.call_state.call_sid, "assistant", response_text)
                if not self.interrupted:
                    await self._speak(response_text)

            # Persist pipeline metrics
            total_latency = int((time.time() - pipeline_start) * 1000)
            await call_store.add_metrics(
                call_sid=self.call_state.call_sid,
                llm_latency_ms=llm_latency,
                total_latency_ms=total_latency,
            )

        except Exception as exc:
            logger.error(f"Pipeline processing error: {exc}", exc_info=True)
        finally:
            self.is_processing = False

    # ── TTS & Playback ──

    async def _speak(self, text: str):
        """Send text to TTS and enqueue audio."""
        self.interrupted = False
        tts_start = time.time()

        async def enqueue_chunk(chunk: bytes):
            if not self.interrupted:
                await self.tts_queue.put(chunk)

        self.tts_generation_task = asyncio.create_task(
            self.tts.stream_tts(text, enqueue_chunk)
        )
        try:
            await self.tts_generation_task
            tts_latency = int((time.time() - tts_start) * 1000)
            tts_latency_seconds.observe(tts_latency / 1000)
        except asyncio.CancelledError:
            pass
        await self.tts_queue.put(None)

    async def _cancel_speaking(self):
        """Stop current TTS (barge-in handling)."""
        self.interrupted = True
        if self.tts_generation_task and not self.tts_generation_task.done():
            self.tts_generation_task.cancel()
            try: await self.tts_generation_task
            except asyncio.CancelledError: pass
        while not self.tts_queue.empty():
            try: self.tts_queue.get_nowait()
            except asyncio.QueueEmpty: break

    async def _tts_player_loop(self):
        """Background task: consume audio queue → Twilio WebSocket."""
        while True:
            try:
                chunk = await self.tts_queue.get()
                if chunk is None:
                    continue
                if self.interrupted:
                    continue
                payload = encode_twilio_media(chunk)
                await self.websocket.send_json({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": payload},
                })
                ws_messages_total.labels(direction="outbound").inc()
            except Exception as exc:
                logger.error(f"TTS player error: {exc}")
