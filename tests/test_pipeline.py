"""Unit tests for call pipeline."""
import pytest
from ai_caller.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)

    async def fail():
        raise ConnectionError("fail")

    # First failure
    with pytest.raises(ConnectionError):
        await cb.call(fail)
    assert cb.state == CircuitState.CLOSED

    # Second failure - circuit opens
    with pytest.raises(ConnectionError):
        await cb.call(fail)
    assert cb.state == CircuitState.OPEN

    # Third call - circuit breaker rejects
    with pytest.raises(Exception) as exc_info:
        await cb.call(fail)
    assert "OPEN" in str(exc_info.value)
