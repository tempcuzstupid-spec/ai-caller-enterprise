"""Circuit breaker pattern for external API resilience.

Prevents cascading failures when Deepgram, OpenAI, or ElevenLabs are down.
"""
import asyncio
import time
from enum import Enum
from typing import Callable, Optional, Any
from functools import wraps


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject fast
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreaker:
    """Circuit breaker for async functions.

    Args:
        failure_threshold: Number of failures before opening circuit
        recovery_timeout: Seconds to wait before trying half-open
        expected_exception: Exception types that count as failures
        fallback: Optional fallback function when circuit is open
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        expected_exception: tuple = (Exception,),
        fallback: Optional[Callable] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.fallback = fallback

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                else:
                    if self.fallback:
                        return await self.fallback(*args, **kwargs)
                    raise CircuitBreakerOpenError(
                        f"Circuit '{self.name}' is OPEN. Last failure: "
                        f"{self.last_failure_time}"
                    )

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except self.expected_exception as exc:
            await self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        return (time.time() - self.last_failure_time) >= self.recovery_timeout

    async def _on_success(self):
        async with self._lock:
            self.failure_count = 0
            self.last_failure_time = None
            self.state = CircuitState.CLOSED

    async def _on_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await self.call(func, *args, **kwargs)
        return wrapper


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass
