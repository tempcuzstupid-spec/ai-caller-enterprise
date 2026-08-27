# COASTAL VANGUARD — TOOL DEFINITIONS
# Pass these to your LLM's function calling API (OpenAI, Anthropic, etc.)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a consultation call with a Coastal Vanguard specialist for a later time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in ISO 8601 format (YYYY-MM-DD)"},
                    "time": {"type": "string", "description": "Time in 24-hour format (HH:MM)"},
                    "name": {"type": "string", "description": "Customer name"},
                    "phone": {"type": "string", "description": "Customer phone number"},
                    "topic": {"type": "string", "description": "What the consultation is about"},
                },
                "required": ["date", "time", "name", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_call",
            "description": "Transfer the caller to a live human agent who can finalize an order, process payment, or answer medical questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "enum": ["sales", "support", "medical_review", "general"],
                        "description": "Department to transfer to",
                    },
                    "reason": {"type": "string", "description": "Why the transfer is needed"},
                    "context": {"type": "string", "description": "Summary of conversation for the live agent"},
                },
                "required": ["department", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Send a text message (SMS) to the caller with catalog, package details, or website link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Destination phone number"},
                    "message_type": {
                        "type": "string",
                        "enum": ["full_catalog", "package_details", "website_link", "follow_up"],
                        "description": "Type of message to send",
                    },
                    "package_id": {"type": "string", "description": "Optional: specific package code (e.g., A1, H2)"},
                    "custom_note": {"type": "string", "description": "Optional: custom note to append"},
                },
                "required": ["phone", "message_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email to the caller with catalog, package details, or order information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Destination email address"},
                    "message_type": {
                        "type": "string",
                        "enum": ["full_catalog", "package_details", "order_summary", "follow_up"],
                        "description": "Type of email to send",
                    },
                    "package_id": {"type": "string", "description": "Optional: specific package code"},
                    "custom_note": {"type": "string", "description": "Optional: custom note to append"},
                },
                "required": ["email", "message_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Look up a customer record by phone number to see order history, previous packages, and notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "Phone number to look up"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "redirect_to_website",
            "description": "Direct the caller to visit coastalvanguard.org for browsing, ordering, or more information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "enum": ["home", "catalog", "packages", "contact", "faq"],
                        "description": "Which page to direct them to",
                    },
                    "package_id": {"type": "string", "description": "Optional: specific package to deep-link to"},
                },
                "required": ["page"],
            },
        },
    },
]
