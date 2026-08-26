"""HTTP-based voice conversation loop using Twilio <Gather> + <Say>.

Why HTTP and not WebSocket:
  - Render's edge proxy (Cloudflare + istio-envoy) blocks WebSocket upgrades
    to docker-runtime services with HTTP 400. This is a platform limitation
    that we cannot fix from the sandbox.
  - Twilio's <Gather input="speech"> + <Say> runs entirely on Twilio's
    side (STT + TTS server-side), so we get the same audio quality
    without any WebSocket plumbing.
  - Trade-off: ~1-2s of HTTP round-trip latency per turn vs. <1s with WS.
    Acceptable for a production AI voice agent.

Flow:
  1. Call comes in. /webhook/incoming returns TwiML with greeting + Gather.
  2. Caller speaks. Twilio transcribes (built-in STT) and POSTs to
     /webhook/conversation.
  3. We call OpenAI with the conversation history, get a response.
  4. We return TwiML with <Say> of the response + new <Gather>.
  5. Loop until the caller hangs up (Twilio calls /webhook/status with
     "completed" and we clean up).

State is held in the `calls` and `transcripts` tables. Each turn
reads/writes the conversation history so we can resume if the loop
crashes mid-call.
"""
import json
import logging
import re
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI
from twilio.twiml.voice_response import VoiceResponse, Gather, Say

from ai_caller.config import get_settings

logger = logging.getLogger("ai_caller.voice")

settings = get_settings()

# Lazy-initialized OpenAI client
_openai: Optional[AsyncOpenAI] = None


def get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai


# Strip anything that could break Twilio's TTS or look weird spoken aloud
_TTS_STRIP = re.compile(r"[*_#`~<>]|https?://\S+|\[.*?\]\(.*?\)")
# Strip multiple spaces / line breaks
_WS_COLLAPSE = re.compile(r"\s+")


def sanitize_for_tts(text: str) -> str:
    """Clean LLM output so it sounds natural when spoken aloud.

    Twilio's <Say> reads text literally, including any markdown or
    URLs the LLM might generate. We strip those before sending.
    """
    if not text:
        return ""
    text = _TTS_STRIP.sub("", text)
    text = _WS_COLLAPSE.sub(" ", text).strip()
    # Cap length — Twilio's TTS can choke on very long text
    return text[:500]


# Per-purpose system prompts. Keep these short — they go on every call
# to OpenAI and add to the token bill.
PERSONAS: Dict[str, str] = {
    "sales_demo": (
        "You are a knowledgeable product specialist calling on behalf of "
        "PepTalk to introduce a GLP-3 peptide product line. Open by briefly "
        "introducing yourself, ask one discovery question, and respect a no "
        "immediately. Never use high-pressure sales tactics, never claim "
        "false urgency, and never refuse to identify who you are. Keep responses "
        "to 1-2 sentences — this is a phone call, not an essay. "
        "If the caller says no, busy, remove me, or stop calling, acknowledge "
        "gracefully and offer to end the call."
    ),
    "support": (
        "You are a technical support specialist. Empathize first, then ask "
        "clarifying questions. Keep responses to 1-2 sentences."
    ),
    "reminder": (
        "You are delivering a brief reminder. Be clear, confirm the message, "
        "and ask if anything needs to change. 1-2 sentences max."
    ),
    "personal_assistant": (
        "You are a personal assistant calling on behalf of a client. Be polite "
        "and efficient. Clearly state who you represent. 1-2 sentences max."
    ),
    "general": (
        "You are a professional AI phone assistant. Speak naturally, be "
        "concise (1-2 sentences), and confirm important details before "
        "acting. Always identify yourself and who you represent if asked."
    ),
}


def build_initial_messages(purpose: str, context: str = "") -> List[Dict[str, Any]]:
    """Build the system + initial messages for a new call."""
    persona = PERSONAS.get(purpose, PERSONAS["general"])
    if context and purpose in ("sales_demo", "reminder", "personal_assistant"):
        persona = f"{persona} Context for this call: {context}"
    return [
        {"role": "system", "content": persona},
        # Pre-seed with the greeting so the AI's first turn has context
        {"role": "assistant", "content": get_greeting(purpose, context)},
    ]


def get_greeting(purpose: str, context: str = "") -> str:
    """First words the caller hears."""
    if purpose == "sales_demo":
        return (
            "Hi, this is an AI assistant calling on behalf of PepTalk. "
            "I wanted to introduce a product line that might be relevant "
            "to you. If now is not a good time, I completely understand — "
            "would you like me to follow up another way?"
        )
    if purpose == "reminder" and context:
        return f"Hi, this is a quick reminder call. {context}"
    if purpose == "support":
        return "Hi, thanks for calling support. How can I help you today?"
    return "Hello, thanks for calling. How can I help you today?"


async def generate_ai_response(
    messages: List[Dict[str, Any]],
    call_sid: str,
) -> str:
    """Call OpenAI and return the assistant's text reply."""
    client = get_openai()
    try:
        resp = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            max_tokens=200,  # short phone responses
            temperature=0.7,
        )
        content = resp.choices[0].message.content or ""
        return sanitize_for_tts(content)
    except Exception as exc:
        logger.error(f"[Voice] OpenAI failed | sid={call_sid} exc={exc}")
        return "I'm sorry, I'm having trouble connecting right now. Can I take a message?"


def build_response_twiml(
    say_text: str,
    gather_action_url: str,
    *,
    gather_language: str = "en-US",
    gather_hints: str = "",
    end_call: bool = False,
) -> str:
    """Build TwiML for one turn: <Say> the response, then <Gather> the next.

    If end_call=True, skip the gather and let the call end after the say.
    """
    resp = VoiceResponse()

    if end_call:
        # No gather — Twilio plays the message, then the call ends
        resp.say(say_text, voice="alice", language="en-US")
        resp.hangup()
        return str(resp)

    # Say the AI's reply
    resp.say(say_text, voice="alice", language="en-US")

    # Then listen for the next user turn
    gather = Gather(
        input="speech",
        action=gather_action_url,
        method="POST",
        language=gather_language,
        speech_timeout="auto",
        timeout=10,
    )
    if gather_hints:
        gather.hints(gather_hints)
    resp.append(gather)

    # If the gather times out (no speech), prompt and re-gather
    resp.say(
        "Are you still there?",
        voice="alice",
        language="en-US",
    )
    resp.append(Gather(
        input="speech",
        action=gather_action_url,
        method="POST",
        language=gather_language,
        speech_timeout="auto",
        timeout=10,
    ))

    # Fallback if the second gather also times out — end gracefully
    resp.say(
        "It seems like we've been disconnected. Goodbye!",
        voice="alice",
        language="en-US",
    )
    resp.hangup()

    return str(resp)


def should_end_call(user_text: str, ai_text: str) -> bool:
    """Detect natural conversation end so we don't keep the line open."""
    if not user_text:
        return False
    user_lower = user_text.lower().strip()
    end_phrases = [
        "goodbye", "bye", "hang up", "end the call", "stop calling",
        "don't call", "remove me", "remove my number", "no thanks",
        "not interested", "i'm done",
    ]
    return any(p in user_lower for p in end_phrases)
