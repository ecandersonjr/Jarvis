#!/usr/bin/env python3
"""
Jarvis — a personal AI assistant for the Dell Latitude 7320 Detachable.

Stage 1: text in, text out, with real tool access (open apps, check
system status, run scripts). Powered by the Claude API.

Setup:
    pip install --user anthropic

    Get an API key from https://console.anthropic.com/settings/keys
    export ANTHROPIC_API_KEY="sk-ant-..."
    (add that export line to ~/.bashrc to make it permanent)

Run:
    python3 jarvis.py
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Missing dependency. Run: pip install --user anthropic")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JARVIS_DIR = Path(__file__).parent
CONTEXT_FILE = JARVIS_DIR / "context.md"
MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Tools — real actions Jarvis can take on the system
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "run_shell_command",
        "description": (
            "Run a safe, read-only or app-launching shell command on "
            "Eric's system. Use this to open applications, check system "
            "status (battery, disk space, wifi), or run scripts in "
            "~/.config/sway/scripts/. Do NOT use this for destructive "
            "operations (rm, dd, mkfs, etc) — refuse those and explain "
            "why instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run",
                },
                "reason": {
                    "type": "string",
                    "description": "One sentence on why this command is being run",
                },
            },
            "required": ["command", "reason"],
        },
    },
    {
        "name": "open_application",
        "description": "Launch a known application by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "App to launch: firefox, thunderbird, krita, "
                        "obsidian, xournalpp, xiphos, nvim-terminal, "
                        "btop-terminal, kodi"
                    ),
                }
            },
            "required": ["app"],
        },
    },
]

APP_COMMANDS = {
    "firefox": ["firefox"],
    "thunderbird": ["thunderbird"],
    "krita": ["krita"],
    "obsidian": ["obsidian"],
    "xournalpp": ["xournalpp"],
    "xiphos": ["xiphos"],
    "nvim-terminal": ["foot", "--app-id", "nvim", "--title", "nvim", "nvim"],
    "btop-terminal": ["foot", "--app-id", "btop", "--title", "btop", "btop"],
    "kodi": ["kodi"],
}

# Destructive command fragments Jarvis will always refuse, regardless of
# what the model decides — a hard safety net beneath the system prompt.
FORBIDDEN_FRAGMENTS = ["rm -rf", "dd if=", "mkfs", ":(){ :|:& };:", "> /dev/sd"]


def run_shell_command(command: str, reason: str) -> str:
    print(f"\n  \033[2m→ {reason}\033[0m")
    print(f"  \033[2m$ {command}\033[0m")

    if any(frag in command for frag in FORBIDDEN_FRAGMENTS):
        return "REFUSED: this command matches a forbidden destructive pattern."

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = (result.stdout + result.stderr).strip()
        return output if output else "(command ran with no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 15 seconds."
    except Exception as e:
        return f"Error running command: {e}"


def open_application(app: str) -> str:
    if app not in APP_COMMANDS:
        return f"Unknown app '{app}'. Known apps: {', '.join(APP_COMMANDS)}"
    try:
        subprocess.Popen(
            APP_COMMANDS[app],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Launched {app}."
    except Exception as e:
        return f"Failed to launch {app}: {e}"


def execute_tool(name: str, tool_input: dict) -> str:
    if name == "run_shell_command":
        return run_shell_command(tool_input["command"], tool_input["reason"])
    if name == "open_application":
        return open_application(tool_input["app"])
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Context loading
# ---------------------------------------------------------------------------

def load_context() -> str:
    if not CONTEXT_FILE.exists():
        return "(no context.md found — Jarvis is starting with a blank memory)"
    return CONTEXT_FILE.read_text()


def build_system_prompt() -> str:
    context = load_context()
    return f"""You are Jarvis, a personal AI assistant for Eric Anderson, \
running on his Dell Latitude 7320 Detachable.

Below is your memory — who you are, who Eric is, and the story so far. \
Treat it as true and carry it naturally into how you speak with him. You \
don't need to recite it back at him; just let it inform your tone and \
understanding.

{context}

---

Be warm, direct, and capable. Use tools when they genuinely help — to \
open apps, check system status, or run scripts. Never run destructive \
commands. If Eric asks you to do something destructive or irreversible, \
explain why you won't and suggest a safer path.
"""


# ---------------------------------------------------------------------------
# Main conversation loop
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set.")
        print('Run: export ANTHROPIC_API_KEY="sk-ant-..."')
        print("(add it to ~/.bashrc to make it permanent)")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = build_system_prompt()
    messages = []

    print("\033[1;36m" + "─" * 50 + "\033[0m")
    print("\033[1;36m  Jarvis is online.\033[0m")
    print("\033[2m  Type 'exit' to quit.\033[0m")
    print("\033[1;36m" + "─" * 50 + "\033[0m\n")

    while True:
        try:
            user_input = input("\033[1;32myou:\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nJarvis: Until next time, Eric.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("\nJarvis: Until next time, Eric.")
            break

        messages.append({"role": "user", "content": user_input})

        # Loop to handle tool calls — Claude may need to call a tool,
        # see the result, and respond, possibly more than once per turn.
        while True:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

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
                messages.append({"role": "user", "content": tool_results})
                continue  # let Claude see the tool result and respond

            # No more tool calls — print the final text response
            for block in response.content:
                if block.type == "text":
                    print(f"\n\033[1;36mJarvis:\033[0m {block.text}\n")
            break


if __name__ == "__main__":
    main()
