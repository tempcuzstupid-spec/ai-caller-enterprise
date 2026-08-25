"""ElevenLabs Text-to-Speech with resilience.

Features:
  - Circuit breaker for ElevenLabs API
  - Retry with exponential backoff
  - Streaming audio conversion
"""
import httpx
import logging
import time
from typing import Callable, Awaitable
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_caller.utils.audio import mp3_to_mulaw_8k, chunk_audio
from ai_caller.circuit_breaker import CircuitBreaker
from ai_caller.metrics import record_error

logger = logging.getLogger("ai_caller.tts")


class ElevenLabsTTS:
    """Streaming TTS via ElevenLabs API with resilience."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        circuit_breaker: CircuitBreaker = None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.circuit_breaker = circuit_breaker
        self.client = httpx.AsyncClient(timeout=30.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def stream_tts(
        self,
        text: str,
        on_audio_chunk: Callable[[bytes], Awaitable[None]],
    ):
        """Convert text to μ-law 8kHz chunks with retry logic."""
        start_time = time.time()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "output_format": "mp3_44100_128",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }

        async def _do_tts():
            async with self.client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                mp3_buffer = bytearray()
                async for chunk in resp.aiter_bytes():
                    mp3_buffer.extend(chunk)
                return bytes(mp3_buffer)

        try:
            if self.circuit_breaker:
                mp3_buffer = await self.circuit_breaker.call(_do_tts)
            else:
                mp3_buffer = await _do_tts()
        except Exception as exc:
            record_error("tts", type(exc).__name__)
            logger.error(f"TTS generation failed: {exc}")
            raise

        if not mp3_buffer:
            logger.warning("TTS returned empty response")
            return

        try:
            ulaw = mp3_to_mulaw_8k(mp3_buffer)
            for frame in chunk_audio(ulaw, frame_duration_ms=20, sample_rate=8000):
                await on_audio_chunk(frame)
        except Exception as exc:
            record_error("tts", type(exc).__name__)
            logger.error(f"TTS audio conversion failed: {exc}")
            raise

        latency = time.time() - start_time
        logger.info(f"TTS completed in {latency:.2f}s")

    async def close(self):
        await self.client.aclose()
