# Coastal Vanguard AI Caller — Prompts & Interaction System

## What's In This Package

| File | Purpose |
|------|---------|
| `prompts/inbound_prompt.py` | System prompt for toll-free number (people calling YOU) |
| `prompts/outbound_prompt.py` | System prompt for local number (YOU calling leads) |
| `prompts/knowledge_base.py` | Complete product catalog: 20 packages, 30+ products, pricing, contraindications |
| `prompts/tools.py` | Function definitions for LLM tool calling |
| `README.md` | This file — how to integrate with any AI calling platform |

---

## How to Use These Prompts

### Step 1: Choose the Right Prompt

| Number Type | Direction | Use This Prompt |
|-------------|-----------|-----------------|
| Toll-Free | Inbound | `inbound_prompt.py` |
| Local | Outbound | `outbound_prompt.py` |

### Step 2: Load the Knowledge Base

The knowledge base contains:
- **20 Complete-Solution Packages** (A1–H5) with outcomes, pricing, protocols
- **30+ individual products** with doses, pricing, administration
- **Universal contraindications** (hard-stop safety screening)
- **Shipping & payment info** ($35 standard, FREE $500+, CashApp/Zelle/Cards/Crypto)
- **Package selector logic** (goal → group → profile → safety check)

**How to load it:**
```python
# Option A: Inject into system prompt (recommended)
system_prompt = INBOUND_SYSTEM_PROMPT + "\n\n=== PRODUCT CATALOG ===\n" + knowledge_base_text

# Option B: Use as RAG context (if your platform supports it)
# Load knowledge_base.py and retrieve relevant products based on caller's goal
```

### Step 3: Register the Tools

Pass the tool definitions from `tools.py` to your LLM's function calling API:

**OpenAI:**
```python
response = await client.chat.completions.create(
    model="gpt-4o",
    messages=messages,
    tools=TOOLS,
    tool_choice="auto",
)
```

**Anthropic Claude:**
```python
response = await client.messages.create(
    model="claude-3-5-sonnet",
    messages=messages,
    tools=TOOLS,
)
```

### Step 4: Implement Tool Handlers

When the LLM calls a tool, execute it and return the result:

| Tool | What It Does | Your Implementation |
|------|-------------|---------------------|
| `transfer_call` | Transfer to live agent | Twilio `<Dial>` or `<Enqueue>` |
| `send_sms` | Send catalog via text | Twilio SMS API |
| `send_email` | Send catalog via email | SendGrid / AWS SES / Mailgun |
| `book_appointment` | Schedule consultation | Calendly / Google Calendar API |
| `lookup_customer` | Check order history | Query your CRM / database |
| `redirect_to_website` | Direct to coastalvanguard.org | Just speak the URL |

### Step 5: Handle the Conversation Flow

**Inbound (Toll-Free):**
```
Caller dials → AI answers with opening script → Discovery (goal, experience, budget)
→ Recommendation (1-2 packages) → Objection handling → Transfer/SMS/Email/Close
```

**Outbound (Local):**
```
AI dials lead → Opening hook (90 seconds?) → Discovery → Value pitch (1 package)
→ Objection handling → Transfer/SMS/Follow-up → Voicemail if no answer
```

---

## Key Rules the AI Follows

1. **Disclaimer first** — Said naturally within 60 seconds of every call
2. **Safety screening** — Contraindications checked before any recommendation
3. **One package at a time** — Never overwhelm with 5+ options
4. **Transfer to human** — When ready to buy, asks medical questions, or requests human
5. **Send catalog** — Via SMS or email when caller needs time to decide
6. **No medical claims** — Uses "reported to," "published studies suggest," "users typically see"

---

## Example Integration (Pseudocode)

```python
from prompts.inbound_prompt import INBOUND_SYSTEM_PROMPT
from prompts.knowledge_base import PACKAGES, PRODUCTS, CONTRAINDICATIONS
from prompts.tools import TOOLS

async def handle_call(direction, phone_number, purpose="general"):
    # 1. Choose prompt
    system = INBOUND_SYSTEM_PROMPT if direction == "inbound" else OUTBOUND_SYSTEM_PROMPT

    # 2. Add knowledge base
    system += f"\n\nAvailable packages: {list(PACKAGES.keys())}"

    # 3. Start conversation
    messages = [
        {"role": "system", "content": system},
        {"role": "assistant", "content": "Thank you for calling Coastal Vanguard..."}
    ]

    # 4. Loop: STT → LLM → TTS
    while call_active:
        user_text = await get_stt_transcript()
        messages.append({"role": "user", "content": user_text})

        response = await llm.generate(messages, tools=TOOLS)

        if response.tool_calls:
            for tool in response.tool_calls:
                result = await execute_tool(tool)
                messages.append({"role": "tool", "tool_call_id": tool.id, "content": result})
        else:
            await speak(response.text)
            messages.append({"role": "assistant", "content": response.text})
```

---

## Contact

Coastal Vanguard LLC — Research & Wellness  
Phone: (386) 843-8160  
Email: Management@coastalvanguard.org  
Website: coastalvanguard.org
