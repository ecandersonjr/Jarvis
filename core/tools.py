"""
core/tools.py

All of Jarvis's tools live here — definitions, implementations, and the
dispatcher. Adding a new tool means adding it once here; every interface
(text, voice, unified) gets it automatically.

Current toolbox:
  - run_shell_command   safe read-only shell access
  - open_application    launch known apps
  - summarize_recent_emails  read the pre-fetched Gmail snapshot

Future tools get added to TOOL_DEFINITIONS and implemented as functions
following the same pattern. Register them in execute_tool() at the bottom.
"""

import json
import subprocess
from pathlib import Path
import urllib.request
import urllib.parse

# yfinance and duckduckgo_search are imported inside their functions
# to fail gracefully if not installed

JARVIS_ROOT = Path(__file__).parent.parent
EMAIL_SNAPSHOT = JARVIS_ROOT.parent / "jarvis-email" / "inbox-snapshot.json"

# ---------------------------------------------------------------------------
# Safety net — these fragments are refused regardless of what the model asks
# ---------------------------------------------------------------------------

FORBIDDEN_FRAGMENTS = [
    "rm -rf",
    "dd if=",
    "mkfs",
    ":(){ :|:& };:",
    "> /dev/sd",
]

# ---------------------------------------------------------------------------
# Known applications
# ---------------------------------------------------------------------------

APP_COMMANDS: dict[str, list[str]] = {
    "firefox": ["firefox"],
    "thunderbird": ["thunderbird"],
    "krita": ["krita"],
    "obsidian": ["obsidian"],
    "xournalpp": ["xournalpp"],
    "xiphos": ["xiphos"],
    "kodi": ["kodi"],
    "btop": ["foot", "--app-id", "btop", "--title", "btop", "btop"],
    "nvim": ["foot", "--app-id", "nvim", "--title", "nvim", "nvim"],
    "newsflash": ["newsflash"],
    "shortwave": ["shortwave"],
    "blanket": ["blanket"],
}

# ---------------------------------------------------------------------------
# Tool definitions (sent to the Claude API)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "run_shell_command",
        "description": (
            "Run a safe, read-only or app-launching shell command on "
            "Sir's system. Use this to open applications, check system "
            "status (battery, disk space, wifi, time), or run scripts "
            "in ~/.config/sway/scripts/. Do NOT use for destructive "
            "operations (rm, dd, mkfs, etc) — refuse those."
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
                        f"App to launch. Known apps: {', '.join(APP_COMMANDS.keys())}"
                    ),
                }
            },
            "required": ["app"],
        },
    },
    {
        "name": "fetch_and_summarize_emails",
        "description": (
            "Fetch the latest emails from Sir's Gmail inbox "
            "and summarize them. Use this when asked to check "
            " or summarize emails - it always gets fresh data first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "summarize_recent_emails",
        "description": (
            "Read the most recently fetched snapshot of Sir's personal "
            "Gmail inbox and return the email data for summarization. "
            "The snapshot is refreshed by a separate script — this tool "
            "reads whatever is currently saved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_weather",
        "description": (
            "Get current weather and forcast for a location. "
            "Use when Sir asks about weather, temperature, or "
            "whether to bring an umbrella. Default to Sir's "
            "home location if none specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": (
                        "City name or location e.g. 'Schenectady NY' "
                        "or 'New York'. Defaults to home if not provided."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web for current information. Use for news, "
            "facts, recent events, or anything Sir asks about that "
            "requires up to date information beyond your training data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_stock",
        "description": (
            "Look up current stock price and basic info for a ticker symbol. "
            "Use when Sir asks about stocks, investments, or market prices."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol e.g. AAPL, TSLA, PLUG",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "read_tasks",
        "description": (
            "Read Sir's current task list from memory. "
            "Use when asked about todos, tasks, or what needs to be done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "add_task",
        "description": (
            "Add a new task to Sir's task list. "
            "Use when Sir asks to remember something, add a todo, "
            "or note something for later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or note to add",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark a task as complete and remove it from the task list. "
            "Use when Sir indicates a task is done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_number": {
                    "type": "integer",
                    "description": "The number of tasks to mark complete",
                },
            },
            "required": ["task_number"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

DEFAULT_LOCATION = "Schenectady NY"

TASKS_FILE = JARVIS_ROOT / "tasks.md"


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
        return f"Unknown app '{app}'. Known apps: {', '.join(APP_COMMANDS.keys())}"
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


def fetch_and_summarize_emails() -> str:
    """Fetch fresh emails then summarize them."""
    # Step 1 - run the fetch script
    fetch_result = run_shell_command(
        "~/.config/jarvis/fetch-emails.sh", "Fetching latest emails"
    )

    # Step 2 - read and return the snapshot
    return summarize_recent_emails()


def summarize_recent_emails() -> str:
    if not EMAIL_SNAPSHOT.exists():
        return (
            "No email snapshot found. Run the fetch script first: "
            "~/jarvis-email/fetch-emails.sh"
        )
    try:
        data = json.loads(EMAIL_SNAPSHOT.read_text())
    except Exception as e:
        return f"Could not read email snapshot: {e}"

    fetched_at = data.get("fetched_at", "unknown time")
    emails = data.get("emails", [])

    if not emails:
        return f"Snapshot from {fetched_at} contains no emails."

    lines = [f"Snapshot fetched at {fetched_at}, {len(emails)} emails:\n"]
    for i, e in enumerate(emails, 1):
        lines.append(
            f"{i}. From: {e['from']}\n"
            f"   Subject: {e['subject']}\n"
            f"   Date: {e['date']}\n"
            f"   Body: {e['body']}\n"
        )
    return "\n".join(lines)


def get_weather(location: str = "") -> str:
    """Fetch weather from wttr.in - no API key needed"""
    loc = location.strip() if location.strip() else DEFAULT_LOCATION
    encoded = urllib.parse.quote(loc)
    url = f"https://wttr.in/{encoded}?format=3"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception as e:
        return f"Could not fetch weather for {loc}: {e}"


def web_search(query: str, num_results: int = 5) -> str:
    """Search the web via DuckDuckGo."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "Web search unavailable. Run: pip install duckduckgo-search"

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=min(num_results, 10)):
                results.append(f"**{r['title']}**\n{r['body']}\n{r['href']}")
        if not results:
            return f"No results found for: {query}"
        return "\n\n".join(results)
    except Exception as e:
        return f"Search failed: {e}"


def get_stock(ticker: str) -> str:
    """Look up stock prices via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return "Stock lookup unavailable. Run: pip install yfinance"

    try:
        stock = yf.Ticker(ticker.upper())
        info = stock.info
        price = info.get("currentPrice") or info.get("regularMarketPrice", "N/A")
        name = info.get("longName", ticker.upper())
        change = info.get("regularMarketChangePercent", 0)
        high = info.get("dayHigh", "N/A")
        low = info.get("dayLow", "N/A")
        direction = "U" if change >= 0 else "D"
        return (
            f"{name} ({ticker.upper()})\n"
            f"Price: ${price:.2f} {direction} {abs(change):.2f}%\n"
            f"Day range: ${low} - ${high}"
        )
    except Exception as e:
        return f"Could not fetch {ticker}: {e}"


def read_tasks() -> str:
    """Read the current task list."""
    if not TASKS_FILE.exists():
        return "No tasks yet, Sir. Your slate is clean"
    content = TASKS_FILE.read_text().strip()
    if not content:
        return "No tasks yet, Sir. Your slate is clean"
    return content


def add_task(task: str) -> str:
    """Append a task to the task list"""
    # Read existing tasks to get the next number
    existing = TASKS_FILE.read_text() if TASKS_FILE.exists() else ""
    lines = [l for l in existing.strip().splitlines() if l.strip()]

    # Find the highest existing task number
    next_num = len(lines) + 1

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y-%m-%d")
    new_line = f"{next_num}. [ ] {task} (added {timestamp})\n"

    with open(TASKS_FILE, "a") as f:
        f.write(new_line)

    return f"Added task {next_num}: {task}"


def complete_task(task_number: int) -> str:
    """Mark a task as complete by number"""
    if not TASKS_FILE.exists():
        return "No task list found, Sir."

    lines = TASKS_FILE.read_text().splitlines()
    updated = []
    found = False

    for line in lines:
        if line.startswith(f"{task_number}.") and "[ ]" in line:
            line = line.replace("[ ]", "[x]", 1)
            found = True
        updated.append(line)

    if not found:
        return f"Task {task_number} not found or already complete."

    TASKS_FILE.write_text("\n".join(updated) + "\n")
    return f"Task {task_number} marked complete, Sir."


# ---------------------------------------------------------------------------
# Dispatcher — routes tool calls from the Claude API to implementations
# ---------------------------------------------------------------------------


def execute_tool(name: str, tool_input: dict) -> str:
    """
    Called by JarvisBrain when the Claude API returns a tool_use block.
    Add new tools here as they're implemented above.
    """
    if name == "run_shell_command":
        return run_shell_command(tool_input["command"], tool_input["reason"])
    if name == "open_application":
        return open_application(tool_input["app"])
    if name == "fetch_and_summarize_emails":
        return fetch_and_summarize_emails()
    if name == "summarize_recent_emails":
        return summarize_recent_emails()
    if name == "get_weather":
        return get_weather(tool_input.get("location", ""))
    if name == "web_search":
        return web_search(tool_input["query"], tool_input.get("num_results", 5))
    if name == "get_stock":
        return get_stock(tool_input["ticker"])
    if name == "read_tasks":
        return read_tasks()
    if name == "add_task":
        return add_task(tool_input["task"])
    if name == "complete_task":
        return complete_task(tool_input["task_number"])
    return f"Unknown tool: {name}"
