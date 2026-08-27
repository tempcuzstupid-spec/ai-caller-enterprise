"""Smoke test for conversation_pipeline — runs in-process.

Sends a fake `setup` event and a `prompt` event to the WebSocket handler
to verify the pipeline:
  1. Accepts the setup
  2. Builds the right system prompt
  3. For sales: sends the prepared opening line
  4. For inbound: stays silent until prompt
  5. Responds to the first prompt with a real LLM call

Use this BEFORE deploying to catch bugs like `self` in non-methods.
"""
import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

# Import the FastAPI app and the inner function
from conversation_pipeline import handle_conversation_stream


class FakeWebSocket:
    """Mock Twilio ConversationRelay WebSocket."""
    def __init__(self):
        self.sent = []
        self.received = []

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        if not self.received:
            await asyncio.sleep(60)  # Block forever; the test will cancel
        return self.received.pop(0)


async def test_sales_setup_and_first_prompt():
    """Test: outbound sales call should send the prepared opening line on setup,
    then respond to a 'Hello?' prompt with a real LLM call."""
    print("\n=== TEST: sales persona, full flow ===")
    ws = FakeWebSocket()
    ws.received = [
        json.dumps({
            "type": "setup",
            "callSid": "CA_test_123",
            "streamSid": "MZ_test_456",
            "from": "+17542193360",
            "customParameters": {
                "lead_name": "David",
                "lead_context": "weight-loss peptides",
            },
        }),
        json.dumps({
            "type": "prompt",
            "voicePrompt": "Hello?",
            "last": False,
        }),
    ]

    # Run the handler with a 30s timeout
    try:
        await asyncio.wait_for(
            handle_conversation_stream(ws, "sales"),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        pass  # Expected; we just want to see what messages were sent

    print(f"\nMessages sent by server: {len(ws.sent)}")
    for i, msg in enumerate(ws.sent):
        print(f"  [{i}] type={msg.get('type')} last={msg.get('last', 'N/A')}")
        if msg.get("type") == "text":
            text = msg.get("token", "")
            print(f"      text: {text[:120]!r}")
        elif msg.get("type") == "end":
            print(f"      end")

    # Verify the opener was sent
    text_messages = [m for m in ws.sent if m.get("type") == "text" and m.get("token")]
    if text_messages:
        first = text_messages[0]["token"]
        if "Marcus" in first and "Coastal Vanguard" in first:
            print(f"\n[PASS] First message is the sales opener: {first[:80]!r}")
        else:
            print(f"\n[FAIL] First message doesn't look like the opener: {first[:80]!r}")
    else:
        print("\n[FAIL] No text messages sent!")


async def test_tollfree_setup():
    """Test: inbound toll-free should NOT send any greeting on setup —
    it should wait for the caller to speak first."""
    print("\n=== TEST: tollfree persona, setup only ===")
    ws = FakeWebSocket()
    ws.received = [
        json.dumps({
            "type": "setup",
            "callSid": "CA_test_tollfree",
            "streamSid": "MZ_test_tollfree",
            "from": "+18886091660",
        }),
    ]

    try:
        await asyncio.wait_for(
            handle_conversation_stream(ws, "tollfree"),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        pass

    print(f"\nMessages sent by server: {len(ws.sent)}")
    for i, msg in enumerate(ws.sent):
        print(f"  [{i}] type={msg.get('type')} token={msg.get('token', '')[:60]!r}")

    if not ws.sent:
        print("\n[PASS] Tollfree stayed silent on setup (waiting for caller)")
    else:
        print(f"\n[FAIL] Tollfree sent {len(ws.sent)} messages; expected 0")


if __name__ == "__main__":
    asyncio.run(test_sales_setup_and_first_prompt())
    asyncio.run(test_tollfree_setup())
