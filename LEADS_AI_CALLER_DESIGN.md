═══════════════════════════════════════════════════════════════
  AI CALLER — LEADS / SALES / MARKETING VERTICAL
═══════════════════════════════════════════════════════════════

MISSION
─────────────────────────────────────────────────────────────
Outbound + inbound AI caller that:
  1. Engages leads in natural conversation (qualifies, educates, builds rapport)
  2. Closes sales itself (simple transactions) OR
  3. Hands off to a real human rep when the lead is qualified + ready
  4. Logs every call to CRM with full transcript, sentiment, intent, outcome

REVENUE PATTERN (assumed)
─────────────────────────────────────────────────────────────
  Per-call cost:    ~$0.40 (LLM 0.05 + STT 0.02 + TTS 0.10 + Twilio 0.13 + VPS 0.10)
  Conversion lift:  3-5x over email/voicemail
  Rep cost saved:   $5-15 per call (humans doing dials)
  Break-even:      1 sale per 20 calls

ARCHITECTURE
─────────────────────────────────────────────────────────────

  ┌─ TRIGGER SOURCES ─────────────────────────────────────┐
  │  • CSV/CRM import (batch cold-call)                   │
  │  • Inbound missed call                                │
  │  • Web form "request a call"                          │
  │  • Manual rep click-to-call                           │
  │  • Webhook from ad platforms (FB lead ads)            │
  └────────────────────┬──────────────────────────────────┘
                       ↓
  ┌─ AI CALLER PLATFORM (Fly + VPS — existing) ───────────┐
  │                                                       │
  │  1. PROVISIONING  →  assign Twilio number per region  │
  │  2. CAMPAIGNS     →  cohort, schedule, A/B variant   │
  │  3. CALL QUEUE    →  rate-limit per area code, retry  │
  │  4. AI PIPELINE   →  STT → LLM → TTS                  │
  │  5. HANDOFF       →  warm transfer to rep, or self-   │
  │                       close via payment link SMS      │
  │  6. OUTCOMES      →  qualified/not, sentiment, $$    │
  │  7. CRM SYNC      →  push to HubSpot/Salesforce/CSV  │
  └───────────────────────────────────────────────────────┘

CRITICAL DESIGN DECISIONS
─────────────────────────────────────────────────────────────

A. SELF-CLOSE VS HANDOFF DECISION TREE
   - Self-close (AI handles payment link via SMS):
     * Order under $X (configurable, e.g. $200)
     * Product is a known SKU
     * Customer has payment method on file
     * No objection signals in last 2 turns

   - Warm hand-off (transfer to rep):
     * Order above $X
     * Custom configuration
     * Customer asks for human ("can I talk to someone")
     * Negotiation signal ("what's your best price")
     * Sentiment shift negative

B. COMPLIANCE (TCPA / DNC)
   - Scrub against DNC list before every call
   - Respect calling hours (8am-9pm local)
   - Provide opt-out: "press 9 to be removed"
   - Record consent + recording disclosure at start
   - Store consent proof (timestamp, transcript)

C. CONVERSATION DESIGN
   - System prompt per campaign: vertical, persona, ICP
   - RAG over product catalog + FAQs (vector DB)
   - BANT-style qualification (Budget, Authority, Need, Timeline)
   - Objection handling playbook (per-product)
   - Calendly integration for booking

D. HUMAN HANDOFF MECHANICS
   - Twilio <Dial><Number> with <SipRefer> or warm transfer
   - Whisper coaching: AI stays on line, gives rep context
   - Rep on standby, gets SMS with call summary
   - Fallback: AI takes detailed message if rep unavailable

E. SCALABILITY
   - 1 VPS can handle ~50 concurrent calls
   - 10 VPS for 500 concurrent (~$60/mo infra)
   - Twilio number pool scales horizontally
   - LLM rate limit per OpenAI tier

F. CRITICAL: DIFFERENT FROM SUPPORT AI CALLER
   - Support: long-context, patient, knowledge-base lookup
   - Sales: aggressive, short, objection-handling, goal-oriented
   - Different system prompt, different metrics, different persona
   - Different ElevenLabs voice (sales: confident male or female)

WHAT I'M NOT BUILDING
─────────────────────────────────────────────────────────────
  ✗ Real-time voice cloning
  ✗ Sentiment-graded voice synthesis (yet)
  ✗ Multi-language beyond English/Spanish
  ✗ Full Salesforce/HubSpot integration (use Zapier middle layer)
  ✗ Predictive dialer with answering machine detection (yet)

DELIVERABLES
─────────────────────────────────────────────────────────────
  1. /workspace/ai-caller-enterprise-v2/  (new repo, separate code)
     - Reuse: deploy infra (Fly + VPS), Twilio client, security, metrics
     - New: campaign engine, lead import, CRM sync, handoff logic
  2. New VPS or reuse existing ai-caller-ws-gateway for sales WebSocket
  3. New Twilio number pool (1 per region to start: 305 Miami for Florida)
  4. Dashboard: campaign list, call list, transcript search, outcomes
  5. Rep mobile app or browser console for handoff

TIMELINE
─────────────────────────────────────────────────────────────
  Week 1:  Core outbound + self-close (no CRM, just Stripe link)
  Week 2:  Handoff to human with warm transfer
  Week 3:  Campaign management + CSV import + outcome logging
  Week 4:  HubSpot/Zapier integration + dashboard

QUESTIONS FOR YOU BEFORE I BUILD
─────────────────────────────────────────────────────────────
  1. What's the product/service you're selling? (need this for prompt)
  2. What's the average order value? (sets self-close threshold)
  3. What's the geographic target? (which states, time zones)
  4. Do you have a CRM already (HubSpot, Salesforce, Pipedrive, none)?
  5. Rep contact method (mobile, softphone, RingCentral, etc)?
  6. How will leads be sourced (CSV, web form, FB ads, manual)?
  7. Compliance: do you have legal review for AI calling in your state(s)?
  8. Recording: do you want calls recorded for QA?
  9. Brand voice: continue with Rachel (ElevenLabs) or switch to a different persona?
  10. Budget for scale: starting with 100 calls/day, 1k/day, or 10k/day?

