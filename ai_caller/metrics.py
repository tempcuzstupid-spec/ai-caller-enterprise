"""Prometheus metrics for observability.

Tracks call volume, latency, errors, and AI pipeline performance.
"""
from prometheus_client import Counter, Histogram, Gauge, Info

# Application info
app_info = Info("ai_caller", "AI Caller application information")

# Call metrics
calls_total = Counter(
    "ai_caller_calls_total",
    "Total calls handled",
    ["direction", "purpose", "status"],
)

calls_active = Gauge(
    "ai_caller_calls_active",
    "Currently active calls",
    ["direction"],
)

call_duration_seconds = Histogram(
    "ai_caller_call_duration_seconds",
    "Call duration in seconds",
    buckets=[30, 60, 120, 300, 600, 1800],
)

# Pipeline latency
stt_latency_seconds = Histogram(
    "ai_caller_stt_latency_seconds",
    "Speech-to-text latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

llm_latency_seconds = Histogram(
    "ai_caller_llm_latency_seconds",
    "LLM response latency",
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
)

tts_latency_seconds = Histogram(
    "ai_caller_tts_latency_seconds",
    "Text-to-speech latency",
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
)

# Error tracking
errors_total = Counter(
    "ai_caller_errors_total",
    "Total errors",
    ["component", "error_type"],
)

# Tool usage
tool_calls_total = Counter(
    "ai_caller_tool_calls_total",
    "Total tool invocations",
    ["tool_name", "status"],
)

# WebSocket metrics
ws_connections = Gauge(
    "ai_caller_websocket_connections",
    "Active WebSocket connections",
)

ws_messages_total = Counter(
    "ai_caller_websocket_messages_total",
    "Total WebSocket messages",
    ["direction"],  # "inbound" or "outbound"
)


def record_call(direction: str, purpose: str, status: str):
    calls_total.labels(direction=direction, purpose=purpose, status=status).inc()


def record_error(component: str, error_type: str):
    errors_total.labels(component=component, error_type=error_type).inc()


def record_tool_call(tool_name: str, success: bool):
    status = "success" if success else "failure"
    tool_calls_total.labels(tool_name=tool_name, status=status).inc()
