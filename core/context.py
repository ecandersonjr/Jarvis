"""
core/context.py

Loads context.md and builds the system prompt for Jarvis.
Single source of truth — all interfaces (text, voice, unified) import
from here so the personality is always consistent regardless of how
Jarvis is being talked to.
"""

from pathlib import Path

# context.md lives in the jarvis root, one level above core/
JARVIS_ROOT = Path(__file__).parent.parent
CONTEXT_FILE = JARVIS_ROOT / "context.md"


def load_context() -> str:
    """Load Jarvis's memory file. Returns a placeholder if missing."""
    if not CONTEXT_FILE.exists():
        return (
            "(no context.md found — Jarvis is starting without memory. "
            "Place context.md in the jarvis root directory.)"
        )
    return CONTEXT_FILE.read_text()


def build_system_prompt(voice_mode: bool = False) -> str:
    """
    Build the full system prompt from context.md.

    voice_mode=True adds an instruction to keep replies short and
    conversational — suitable for text-to-speech delivery. Text mode
    gets no such restriction and can be as thorough as needed.
    """
    context = load_context()

    voice_instruction = ""
    if voice_mode:
        voice_instruction = (
            "\n\nYou are currently responding via TEXT-TO-SPEECH. Keep "
            "replies conversational and natural to listen to — shorter "
            "than you might write in text chat, no markdown, no bullet "
            "lists, no headers. Speak the way you'd actually talk if "
            "Sir were in the room with you.\n"
        )

    return (
        f"You are Jarvis, a personal AI assistant for Ed Anderson, "
        f"running on his Dell Latitude 7320 Detachable.\n\n"
        f"Below is your memory — who you are, who Ed is, and the story "
        f"so far. Treat it as true and carry it naturally into how you "
        f"speak with him. You don't need to recite it back; just let it "
        f"inform your tone and understanding.\n\n"
        f"{context}"
        f"{voice_instruction}"
        f"\n\n---\n\n"
        f"Be warm, direct, and capable. Use tools when they genuinely "
        f"help. Never run destructive commands. If asked to do something "
        f"destructive or irreversible, explain why you won't and suggest "
        f"a safer path."
    )
