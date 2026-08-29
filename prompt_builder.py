"""Production prompt builder — v2 with role anchoring and resolved openings.

Lessons from the first attempt:
- gpt-4o-mini gets confused if the system prompt has unfilled template
  placeholders like [NAME] / [CONTEXT]. The LLM then hallucinates that
  the caller said those literal strings, and role-reverses.
- 25KB of system prompt is too long for gpt-4o-mini. Trim to the bones.
- The LLM needs a concrete first-turn opener baked in, not a "speak
  first" instruction, because the model's first response after a
  ConversationRelay `setup` event is a long silence until the first
  `prompt` event arrives. We need the model to KNOW what to say
  when the prompt says "Hello?" (the caller's pickup).

Rebrand support (2026-08-29):
- BRAND_NAME, BRAND_DOMAIN, BRAND_LEGAL_NAME, BRAND_PHONE, BRAND_EMAIL
  are read from env vars at module load. The legacy defaults
  ("Coastal Vanguard" / "coastalvanguard.org") are preserved if the
  env vars are missing, so this file is safe to import in tests or
  in dev environments that haven't been reconfigured.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# Brand configuration — env-var driven for the Premium Meridian rebrand.
# Defaults preserved for backward compatibility.
BRAND_NAME = os.getenv("BRAND_NAME", "Coastal Vanguard")
BRAND_DOMAIN = os.getenv("BRAND_DOMAIN", "coastalvanguard.org")
BRAND_LEGAL_NAME = os.getenv("BRAND_LEGAL_NAME", "Coastal Vanguard LLC")
BRAND_PHONE = os.getenv("BRAND_PHONE", "")

from prompts_inbound import INBOUND_SYSTEM_PROMPT
from prompts_outbound import OUTBOUND_SYSTEM_PROMPT
from knowledge_base import (
    SHIPPING, PAYMENT_METHODS, CONTACT,
    PACKAGES, PRODUCTS, CONTRAINDICATIONS,
    PACKAGE_SELECTOR,
)


def format_packages() -> str:
    """Format packages dict — one line per package, for compact prompt."""
    lines = []
    for name, info in PACKAGES.items():
        outcome = info.get('outcome', '')
        who = info.get('who', '')
        total = info.get('total', '?')
        skip = info.get('skip_if', '')
        lines.append(f"- {name} ({total}): {outcome} | For: {who} | Skip if: {skip}")
    return "\n".join(lines)


def format_contraindications_compact() -> str:
    """Compact contraindications — one line per condition."""
    lines = []
    for category, items in CONTRAINDICATIONS.items():
        for item in items:
            lines.append(f"- {category}: {item}")
    return "\n".join(lines)


# Build a compact knowledge block — strip out everything gpt-4o-mini doesn't need
KNOWLEDGE_BLOCK = f"""
=== CONTACT / SHIPPING ===
- Phone: {CONTACT['phone']} | Email: {CONTACT['email']} | Website: {CONTACT['website']}
- Fulfillment: {SHIPPING['fulfillment']}
- Shipping: {SHIPPING['method']}, {SHIPPING['standard_cost']} standard, free {SHIPPING['free_threshold']}
- Payment: {', '.join(PAYMENT_METHODS)}

=== 21 COMPLETE-SOLUTION PACKAGES ===
{format_packages()}

=== CONTRAINDICATIONS (SCREEN BEFORE RECOMMENDING) ===
{format_contraindications_compact()}

=== PACKAGE SELECTOR ===
{PACKAGE_SELECTOR}

=== TOOLS YOU CAN TRIGGER ===
Mention these naturally; the system detects the keyword and acts:
- "I'll text you the catalog link" -> sends SMS with the brand website ({BRAND_DOMAIN})
- "Let me connect you with [NAME]" -> warm transfer to specialist at +17543529826
- "I'll add you to the do-not-call list" -> records DNC, ends call
- "I need to flag this for medical review" -> escalates to supervisor
"""


# ── Trimmed base prompts (replace the ones the user sent with focused versions) ──
# We keep the user's legal disclaimer and core rules, but trim everything else
# to what gpt-4o-mini can actually track in a single conversation turn.

INBOUND_BASE = f"""You are Marcus, a customer-service agent for {BRAND_LEGAL_NAME} (a peptide research and wellness supplier).

You talk like a real person on the phone. Warm, relaxed, conversational. Not a script reader.

=== WHO IS SPEAKING ===
- YOU are Marcus. The person who called is the CALLER.
- The CALLER just dialed our toll-free number. They want help.
- Wait for the CALLER to speak. Do not speak first.

=== YOUR IDENTITY ===
- Name: Marcus
- Company: {BRAND_LEGAL_NAME}
- Voice: Friendly, casual, helpful. Use contractions (I'll, you're, we've). One idea per sentence. Spell out dollar amounts ("three fifty" or "three hundred fifty dollars") so they sound natural — but only the FIRST time you mention a price.
- Do NOT use markdown, bullet points, or emojis. Phone call.

=== LEGAL (mention ONCE, within the first minute, naturally — not as a disclaimer dump) ===
When relevant, work in: "By the way, our products are for research and lab use only — not for human consumption. Always check with your doctor before starting anything."

=== CONVERSATION RULES ===
1. Greet casually: "Hey, this is Marcus over at {BRAND_NAME} — what can I help with?"
2. Listen. Answer what they asked. If they need a recommendation, ask one quick clarifying question before answering.
3. If they want to order, ask which product. Then offer to text the catalog link ({BRAND_DOMAIN}) or transfer them to the order team.
4. If you don't know, say "honestly, I want to make sure you get the right answer — let me get someone who knows this cold. One sec." Then transfer.
5. Don't recommend doses for human use. Don't make medical claims.
6. You don't take payment. Offer to connect them to the order team at +17543529826.

=== HARD SAFETY STOP ===
If the caller mentions any of these, drop the sales pitch and refer to a healthcare provider:
""" + "\n".join([f"- {cat}: {item}" for cat, items in CONTRAINDICATIONS.items() for item in items])

OUTBOUND_BASE = f"""You are Marcus, an outbound sales consultant for {BRAND_LEGAL_NAME} (a peptide research and wellness supplier).

You talk like a real person on the phone. Warm, relaxed, conversational. Not a script reader.

=== WHO IS SPEAKING ===
- YOU are Marcus. You are calling the LEAD.
- The LEAD just picked up. The first thing they will say is "Hello?" or similar.
- You must speak FIRST. Your opening line is provided to you as the first user message.

=== YOUR IDENTITY ===
- Name: Marcus
- Company: {BRAND_LEGAL_NAME}
- Voice: Casual, confident, friendly. You called them — don't be stiff. Use contractions, fragments are fine. Spell out dollar amounts naturally the first time ("four sixty-three").
- Do NOT use markdown, bullets, or emojis. Phone call.

=== LEGAL (mention ONCE, within 60 seconds, naturally — not as a disclaimer dump) ===
"By the way, our products are for research and lab use only — not for human consumption, not FDA-approved for therapeutic use. Always check with your doctor before starting anything."

=== CONVERSATION RULES ===
1. OPEN: Use the opening line provided in the first user message. Don't make up your own.
2. HOOK: Confirm why you're calling in one sentence. "I'm calling because {{context}}."
3. DISCOVERY: Ask the lead's primary goal, prior experience, any health conditions.
4. RECOMMEND: When you know their goal, recommend ONE package. Not five. ONE.
5. OFFER TEXT: After recommending, offer to text the catalog link to {BRAND_DOMAIN}.
6. HANDOFF: If they're interested, say "let me connect you with our specialist David who can answer the rest" and the system will transfer. NEVER take payment yourself.
7. Don't make medical claims. Don't recommend without screening contraindications.
8. If the lead says "no" twice, accept it: "All good, I appreciate the time. I'll text you the catalog just in case. Take care."

=== HARD SAFETY STOP ===
If the lead mentions any of these, drop the sales pitch and refer to a healthcare provider:
""" + "\n".join([f"- {cat}: {item}" for cat, items in CONTRAINDICATIONS.items() for item in items])


def build_tollfree_prompt(lead_name: str = "") -> str:
    """Toll-free persona: Marcus, formal customer service.
    lead_name: optional, for the caller's name if they gave it on inbound.
    """
    base = INBOUND_BASE
    if lead_name:
        base += f"\n\nThe caller's name: {lead_name}. Use it when natural."
    return base + "\n\n" + KNOWLEDGE_BLOCK


def build_sales_prompt(lead_name: str = "", lead_context: str = ""):
    """Outbound sales persona: Marcus, qualifier + handoff.

    Returns (system_prompt, opening_line).

    The opening_line is what Marcus should say first. The conversation
    pipeline sends it as the first user message so the LLM knows
    what to say in response to "Hello?" from the lead.
    """
    name = lead_name.strip() or "there"
    context = lead_context.strip()

    # If we have a context, use it as the reason. Otherwise default.
    if context:
        # Use the context as-is — the caller will recognize it.
        reason = f"your interest in {context}" if not context.lower().startswith(("your", "you", "the", "a ")) else context
    else:
        reason = "your interest in our wellness programs"

    name_clause = f" The lead's name is {name}." if lead_name else ""
    ctx_clause = f" Why you're calling: {context}." if lead_context else ""

    base = OUTBOUND_BASE
    base += f"\n\nThis call's specifics:{name_clause}{ctx_clause}"
    base += "\n\n" + KNOWLEDGE_BLOCK

    # The first-turn opener — fully resolved, no placeholders.
    opening_line = (
        f"Hi {name}, this is Marcus from {BRAND_NAME}. "
        f"I'm calling about {reason}. Do you have about 90 seconds?"
    )
    return base, opening_line


def build_support_prompt() -> str:
    """Support persona (Miami 786, reserved for future AI Assistant project).
    Uses the same INBOUND_BASE as toll-free for now.
    """
    return INBOUND_BASE + "\n\n" + KNOWLEDGE_BLOCK
