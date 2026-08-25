"""OpenAI GPT-4o with streaming, function calling, and resilience.

Features:
  - Circuit breaker for OpenAI API
  - Retry with exponential backoff
  - Structured tool definitions
"""
import json
import logging
import time
from typing import List, Dict, Any, Callable, Awaitable, Optional
import openai
from tenacity import retry, stop_after_attempt, wait_exponential

from ai_caller.circuit_breaker import CircuitBreaker
from ai_caller.metrics import record_error, record_tool_call

logger = logging.getLogger("ai_caller.llm")


class LLMService:
    """Streaming LLM with tool use and resilience."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        circuit_breaker: CircuitBreaker = None,
    ):
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.circuit_breaker = circuit_breaker

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "description": "Book an appointment or meeting for the caller.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "ISO 8601 date (YYYY-MM-DD)"},
                            "time": {"type": "string", "description": "24-hour time (HH:MM)"},
                            "name": {"type": "string"},
                            "phone": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["date", "time", "name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lookup_customer",
                    "description": "Look up a customer record by phone number.",
                    "parameters": {
                        "type": "object",
                        "properties": {"phone": {"type": "string"}},
                        "required": ["phone"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "transfer_call",
                    "description": "Transfer the caller to a human agent or department.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "department": {
                                "type": "string",
                                "enum": ["sales", "support", "billing", "general"],
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["department"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_sms",
                    "description": "Send a follow-up SMS to the caller.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "phone": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "required": ["phone", "message"],
                    },
                },
            },
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        on_text_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> tuple[str, List[Dict]]:
        """Stream LLM response with retry logic."""
        start_time = time.time()

        async def _do_generate():
            return await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                stream=True,
                temperature=0.7,
                max_tokens=800,
            )

        try:
            if self.circuit_breaker:
                response = await self.circuit_breaker.call(_do_generate)
            else:
                response = await _do_generate()
        except Exception as exc:
            record_error("llm", type(exc).__name__)
            raise

        full_content = ""
        tool_calls: List[Dict] = []

        try:
            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    full_content += delta.content
                    if on_text_chunk:
                        await on_text_chunk(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        while len(tool_calls) <= idx:
                            tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.id: tool_calls[idx]["id"] = tc.id
                        if tc.function.name: tool_calls[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments: tool_calls[idx]["function"]["arguments"] += tc.function.arguments
        except Exception as exc:
            record_error("llm", type(exc).__name__)
            raise

        latency = time.time() - start_time
        logger.info(f"LLM response generated in {latency:.2f}s")
        return full_content, tool_calls

    async def execute_tool(self, tool_call: Dict, call_state: Any = None) -> str:
        """Execute a tool call with error tracking."""
        name = tool_call["function"]["name"]
        try:
            args = json.loads(tool_call["function"]["arguments"])
        except json.JSONDecodeError:
            args = {}

        try:
            if name == "book_appointment":
                result = f"Appointment confirmed for {args.get('name')} on {args.get('date')} at {args.get('time')}."
            elif name == "lookup_customer":
                phone = args.get("phone", call_state.phone_number if call_state else "unknown")
                result = f"Customer {phone}: Premium tier, active since 2022."
            elif name == "transfer_call":
                result = f"Transferring you to {args.get('department', 'general')}. Please hold."
            elif name == "send_sms":
                result = f"SMS queued to {args.get('phone')}."
            else:
                result = f"Tool '{name}' not implemented."
            record_tool_call(name, success=True)
            return result
        except Exception as exc:
            record_tool_call(name, success=False)
            record_error("tool", type(exc).__name__)
            logger.error(f"Tool execution failed: {name} - {exc}")
            return f"Error executing {name}: {str(exc)}"
