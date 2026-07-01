"""Mock AI assistant.

The assignment is explicit: the AI is not under test. This returns a canned/echoed
reply after a small simulated delay. It is async and never blocks the event loop, so
swapping in a real provider later is a drop-in change.
"""

import asyncio

from app.config import get_settings

_CANNED_PREFIXES = [
    "Got it.",
    "Sure thing.",
    "Here's a thought:",
    "Interesting —",
]


async def generate_reply(user_content: str) -> str:
    settings = get_settings()
    if settings.ai_reply_delay_seconds > 0:
        await asyncio.sleep(settings.ai_reply_delay_seconds)

    # Deterministic-ish pick so tests can assert without randomness surprises.
    prefix = _CANNED_PREFIXES[len(user_content) % len(_CANNED_PREFIXES)]
    echoed = user_content.strip()
    if len(echoed) > 280:
        echoed = echoed[:277] + "..."
    return f"{prefix} You said: {echoed!r}. (This is a mock assistant reply.)"
