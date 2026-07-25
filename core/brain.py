"""
core/brain.py

JarvisBrain — the Claude API conversation engine.
Handles message history, tool call loops, and response extraction.

Two response modes:
  chat()           — waits for complete response, returns string
                     used by text interface (jarvis.py)
  chat_streaming() — yields sentences as they arrive from the API
                     used by voice interface for low-latency TTS
"""

import os
import re
import sys
from typing import Generator

try:
    import anthropic
except ImportError:
    print("Missing dependency: pip install anthropic")
    sys.exit(1)

from core.context import build_system_prompt
from core.tools import TOOL_DEFINITIONS, execute_tool

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 512

# Sentence boundary pattern — split on . ! ? followed by space or end
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, keeping each non-empty."""
    return [s.strip() for s in SENTENCE_END.split(text) if s.strip()]


class JarvisBrain:
    """
    Stateful conversation engine for Jarvis.

    One instance per session — keeps full message history in memory
    so context accumulates naturally across exchanges.

    Args:
        voice_mode: if True, system prompt instructs Claude to keep
                    replies short and spoken-word friendly.
    """

    def __init__(self, voice_mode: bool = False):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ANTHROPIC_API_KEY is not set.")
            sys.exit(1)

        self.client = anthropic.Anthropic(api_key=api_key)
        self.system_prompt = build_system_prompt(voice_mode=voice_mode)
        self.messages: list[dict] = []
        self.voice_mode = voice_mode

    def chat(self, user_input: str) -> str:
        """
        Send a message and get the complete response as a string.
        Handles tool call loops internally.
        Used by the text interface.
        """
        self.messages.append({"role": "user", "content": user_input})

        while True:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                tools=TOOL_DEFINITIONS,
                messages=self.messages[-16:],
            )

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                self.messages.append({"role": "user", "content": tool_results})
                continue

            return "".join(
                block.text for block in response.content if block.type == "text"
            )

    def chat_streaming(self, user_input: str) -> Generator[str, None, None]:
        """
        Send a message and yield complete sentences as they arrive.

        This is the low-latency voice path:
        - Streams the API response word by word
        - Buffers into a sentence accumulator
        - Yields each sentence the moment it completes
        - Piper can speak sentence 1 while Claude generates sentence 2

        Tool calls are handled transparently — if Claude needs a tool,
        the stream pauses, the tool runs, and streaming resumes with
        the final response. The caller never sees the tool machinery.

        Message history is updated with the full accumulated reply
        once the stream completes, keeping context intact.

        Usage:
            for sentence in brain.chat_streaming("Hello Jarvis"):
                speak(sentence)
        """
        self.messages.append({"role": "user", "content": user_input})

        # Tool call loop — may iterate more than once if Claude chains tools
        while True:
            # First check if this turn needs a tool (non-streaming for tools)
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                tools=TOOL_DEFINITIONS,
                messages=self.messages[-16:],
            )

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                # Run tools, feed results back, loop again
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                self.messages.append({"role": "user", "content": tool_results})
                continue

            # No tool call — stream the final text response
            full_reply = ""
            buffer = ""

            with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=self.messages[-16:][:-1],  # exclude the just-appended reply
            ) as stream:
                for text in stream.text_stream:
                    buffer += text
                    full_reply += text

                    # Yield complete sentences as they form
                    sentences = SENTENCE_END.split(buffer)
                    # Last element may be incomplete — keep it in buffer
                    for sentence in sentences[:-1]:
                        sentence = sentence.strip()
                        if sentence:
                            yield sentence
                    buffer = sentences[-1]

                # Yield any remaining text at end of stream
                if buffer.strip():
                    yield buffer.strip()

            # Replace the non-streamed assistant message with the
            # full streamed reply so history stays accurate
            self.messages[-1] = {
                "role": "assistant",
                "content": full_reply,
            }
            return

    def clear_history(self):
        """Start fresh without re-loading the system prompt."""
        self.messages = []
