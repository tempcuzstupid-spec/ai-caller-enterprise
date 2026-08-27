═══════════════════════════════════════════════════════════════
  INTERACTIVE ELEVATION — BARGE-IN DESIGN
═══════════════════════════════════════════════════════════════

CURRENT PROBLEM
─────────────────────────────────────────────────────────────
When the AI is speaking (TTS audio playing to caller), and the
caller interrupts with a question or comment, the AI doesn't
notice. The AI keeps talking through the caller's words.

This is the #1 issue with Voice AI — feels robotic, no respect
for the caller's time. The user calls this "interactive elevation."

THE 3 OPTIONS
─────────────────────────────────────────────────────────────

OPTION A — TWILIO CONVERSATIONRELAY (gold standard)
─────────────────────────────────────────────────────
Twilio handles STT + TTS on their side. We send text tokens.
Twilio sends `interrupt` events when caller speaks.
Barge-in works out of the box.

Pros:
  • Barge-in is built-in
  • Sub-500ms latency (Twilio edge is closer to caller)
  • Our backend becomes a text-only service (simpler code)
  • Twilio charges the same per-second for the whole thing

Cons:
  • Migration: rewrite both WebSocket pipelines to send text
  • Slightly different from current (TwiML <Connect><Stream>)

Effort: 3-4 hours to migrate both pipelines

OPTION B — CUSTOM VAD + STREAM INTERRUPT (engineering solution)
────────────────────────────────────────────────────────────────
Keep current audio pipeline. Add Silero VAD on incoming audio.
When lead speaks during AI's turn:
  1. VAD detects voice activity
  2. We send a `mark` event with a unique tag to Twilio
  3. We cancel the current LLM streaming + TTS streaming
  4. Twilio stops audio playback (via media mark or stop event)
  5. We start processing the new speech

Pros:
  • Full control
  • Works with our existing pipeline
  • No Twilio lock-in

Cons:
  • Complex state management
  • Race conditions
  • Hard to get right at low latency

Effort: 8-12 hours of engineering + testing

OPTION C — FASTER TURN-TAKING (quick win)
─────────────────────────────────────────
Reduce Deepgram endpointing from 300ms to 100-150ms. Add an
echo-cancellation note to Deepgram config so it ignores AI's
own TTS audio leaking into the lead's mic.

Pros:
  • 5-minute change
  • Improves perceived responsiveness
  • No architecture change

Cons:
  • Doesn't actually stop AI from talking over the lead
  • Just reduces the dead time between turns

Effort: 5 minutes

RECOMMENDATION
─────────────────────────────────────────────────────────────
Build Option A (ConversationRelay). It IS the gold standard and
every production Voice AI uses it. The migration is bounded:
  • New TwiML: <Connect><ConversationRelay url="wss://.../>
  • WS message types: setup, prompt (user text in), text (LLM
    response out), interrupt (caller spoke during our turn), end
  • No more audio in/out — just JSON

This is the right time to migrate because:
  1. We have working WebSocket pipeline (just text instead of audio)
  2. Barge-in works out of the box
  3. Latency will be lower
  4. VPS can do less work (no ffmpeg/pydub audio conversion)
  5. ElevenLabs voice selection still ours
  6. The "gold standard" for Voice AI in 2026

REVISED ARCHITECTURE
─────────────────────────────────────────────────────────────

  Caller → Twilio → [ConversationRelay] → VPS WebSocket
                                              ↓
                                          FastAPI app
                                              ↓
                                          OpenAI (gpt-4o-mini)
                                              ↓
                                          Text tokens
                                              ↓
                                  WebSocket → Twilio → Caller

  When caller speaks:
    Twilio → WS {type: "prompt", voicePrompt: "...", last: true}
    Twilio → WS {type: "interrupt"}  ← barge-in signal
    We → WS {type: "text", token: "...", last: true}

  Barge-in:
    User starts speaking → Twilio sends "interrupt"
    We stop sending tokens → audio stops immediately
    We get new "prompt" → generate new response → send text
    Latency: < 300ms from interrupt to silence

═══════════════════════════════════════════════════════════════

NEXT STEP
─────────────────────────────────────────────────────────────
  1. Finish current deploy (click Deploy if not already)
  2. Test a real outbound call to verify sales pipeline
  3. Migrate to ConversationRelay
  4. Add per-persona voices (Rachel for support, Josh/Marcus for sales)

