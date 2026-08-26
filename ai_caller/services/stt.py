"""Deepgram Streaming Speech-to-Text with resilience.

Features:
  - Circuit breaker for Deepgram API
  - Automatic reconnection on disconnect
  - Connection health monitoring
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable
import websockets

from ai_caller.circuit_breaker import CircuitBreaker
from ai_caller.metrics import record_error

logger = logging.getLogger("ai_caller.stt")


class DeepgramSTT:
    """Real-time streaming STT via Deepgram WebSocket with resilience."""

    def __init__(
        self,
        api_key: str,
        on_transcript: Callable[[str, bool, bool], Awaitable[None]],
        circuit_breaker: CircuitBreaker = None,
    ):
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.circuit_breaker = circuit_breaker
        self.ws = None
        self._receive_task = None
        self._closed = False
        self._reconnect_count = 0
        self._max_reconnects = 3

    async def connect(self):
        """Open persistent WebSocket to Deepgram with circuit breaker."""
        async def _do_connect():
            url = (
                "wss://api.deepgram.com/v1/listen?"
                "encoding=mulaw&sample_rate=8000&channels=1&"
                "model=nova-2-phonecall&language=en&"
                "smart_format=true&interim_results=true&"
                "endpointing=300&filler_words=true&"
                "profanity_filter=false&punctuation=true"
            )
            self.ws = await websockets.connect(
                url,
                extra_headers={"Authorization": f"Token {self.api_key}"},
                ping_interval=20,
                ping_timeout=10,
            )
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._reconnect_count = 0
            logger.info("Deepgram STT connected")

        if self.circuit_breaker:
            await self.circuit_breaker.call(_do_connect)
        else:
            await _do_connect()

    async def _receive_loop(self):
        """Consume Deepgram messages with auto-reconnect."""
        try:
            async for message in self.ws:
                if self._closed:
                    break
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "Results":
                    channel = data.get("channel", {})
                    alternatives = channel.get("alternatives", [])
                    if not alternatives:
                        continue
                    transcript = alternatives[0].get("transcript", "").strip()
                    if not transcript:
                        continue
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)
                    await self.on_transcript(transcript, is_final, speech_final)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("Deepgram connection closed")
            await self._attempt_reconnect()
        except Exception as exc:
            record_error("stt", type(exc).__name__)
            logger.error(f"STT receive loop error: {exc}")
            await self._attempt_reconnect()

    async def _attempt_reconnect(self):
        """Attempt to reconnect with backoff."""
        if self._closed or self._reconnect_count >= self._max_reconnects:
            logger.error("Max STT reconnects exceeded")
            return
        self._reconnect_count += 1
        delay = min(2 ** self._reconnect_count, 30)
        logger.info(f"Reconnecting to Deepgram in {delay}s (attempt {self._reconnect_count})")
        await asyncio.sleep(delay)
        try:
            await self.connect()
        except Exception as exc:
            logger.error(f"STT reconnect failed: {exc}")

    async def send(self, audio_chunk: bytes):
        """Send raw μ-law audio bytes to Deepgram."""
        if self.ws and self.ws.open:
            try:
                await self.ws.send(audio_chunk)
            except Exception as exc:
                logger.warning(f"STT send error: {exc}")

    async def close(self):
        """Graceful shutdown."""
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            await self.ws.close()
        logger.info("Deepgram STT closed")
