# Jarvis

A voice-enabled AI assistant with a rich terminal UI, built from scratch —
starting as a plain text-in/text-out loop and evolving into a full
voice assistant with real-time audio visualization, a tool-calling
backend, and (in progress) a native GUI.

Runs on Arch Linux / Sway, developed on a Dell Latitude 7320 2-in-1.

## Features

- **Voice interaction**: push-to-talk, wake-word, and text modes,
  switchable mid-session
- **Local, private STT**: OpenAI Whisper (`tiny` model) for fast
  on-device transcription
- **Natural TTS**: piper-tts with a persistent process to avoid
  per-sentence model-load overhead
- **Wake word detection**: openwakeword, currently using its bundled
  stock `hey_jarvis` model. A custom-trained model exists at
  `models/hey_jarvis_v0.1.onnx` but isn't wired in yet — see
  `core/wakeword.py`'s `wakeword_model_paths` if you want to switch
  to it
- **Tool-calling backend**: shell command execution, app launching,
  email summarization (Gmail IMAP), weather, web search, stock
  lookup, and a simple task system
- **Persistent memory**: `context.md`, loaded fresh on every launch,
  so Jarvis stays caught up on ongoing projects and open threads
  without you re-explaining context each session
- **Live audio visualization**: real microphone/TTS output rendered
  as an oscilloscope trace or FFT-based spectrum analyzer, with a
  proper noise gate so ambient room noise doesn't get auto-gained
  into fake activity
- **Rich terminal UI**: side-by-side animated waveform panels,
  scrollable conversation history, live status bar

## Architecture

```
core/
  brain.py            -- JarvisBrain: Claude API calls, capped history
  context.py          -- loads context.md into the system prompt
  tools.py             -- tool suite (shell, apps, email, weather, etc.)
  stt.py               -- Whisper-based speech-to-text, PTT + timed modes
  tts.py               -- piper-tts speech synthesis
  wakeword.py          -- openwakeword-based wake detection
  audio_levels.py      -- thread-safe ring buffers feeding real audio
                           samples to the waveform displays
  waveform_dsp.py       -- shared audio-to-visual math (noise gate, FFT
                           binning, attack/release smoothing) used by
                           both the terminal and (in progress) Tk UI
  waveform_window.py    -- standalone Tk window rendering waveforms at
                           pixel resolution, running alongside the
                           terminal UI

models/
  hey_jarvis_v0.1.onnx  -- custom-trained wake word model (not
                           currently loaded -- see core/wakeword.py)

jarvis-ui.py             -- current entry point: Rich-based terminal UI
context.md                -- Jarvis's persistent memory (see below)
run.sh                    -- launcher
```

The backend (`core/`) is UI-agnostic — it doesn't know or care
whether a Rich terminal, a Tk window, or something else is driving
it. That's what makes the in-progress UI rewrite (see Roadmap) a
contained, low-risk change rather than a full rebuild.

## Evolution

Jarvis has gone through several complete stages, each kept as a
historical reference rather than deleted:

| Stage | File | What it added |
|---|---|---|
| v0.1 | `jarvis.py` | Plain text-in/text-out loop |
| v0.2 | `jarvis-voice.py` | Voice input/output via STT/TTS |
| v0.3 | `jarvis-unified.py` | Unified tool-calling backend |
| v1.0 | `jarvis-ui.py` | Rich terminal UI: waveforms, scrollable history, mode switching |
| v1.1 | `jarvis-ui.py` | Real audio-driven waveforms (oscilloscope + spectrum analyzer), noise gate, scroll fixes |

See git tags for snapshots of each stage.

## Setup

Requires Arch Linux (or any Linux with the equivalent packages) and
Python 3.12+.

### 1. Get an Anthropic API key

1. Go to https://console.anthropic.com/settings/keys
2. Sign in with the same account as your Claude.ai login
3. Click "Create Key" and copy it (starts with `sk-ant-...`)

This is billed separately from a Claude.ai Pro subscription — it's
pay-per-use. For personal-assistant-scale use (a handful of
conversations a day) this typically runs a few dollars a month, not
a fixed fee.

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-your-key-here"' >> ~/.bashrc
source ~/.bashrc
```

**Do not commit this key.** See `.gitignore`.

### 2. Install dependencies

```bash
# System packages (Arch)
sudo pacman -S tk   # needed for core/waveform_window.py

# Python packages
pip install --user -r requirements.txt
```

`piper-tts` and `paplay` are separate system binaries, not pip
packages — see `requirements.txt` for notes on getting those set up.

Whisper will download the `tiny` model on first run; this is not
tracked in the repo.

### 3. Run

```bash
cd ~/jarvis
python3 jarvis-ui.py
# or: ./run.sh
```

### 4. (Optional) Add a Sway keybinding

Open Jarvis in a floating terminal from anywhere:

```
bindsym $mod+F6 exec foot --app-id jarvis --title jarvis ~/jarvis/run.sh
```

```
for_window [app_id="jarvis"] floating enable
```

## Controls (terminal UI)

| Key | Action |
|---|---|
| `t` | Text input mode |
| `p` | Push-to-talk mode |
| `w` | Wake-word mode |
| `u` / `d` | Scroll conversation history up/down |
| `v` | Toggle waveform view (oscilloscope / spectrum analyzer) |
| `q` | Quit |

## Growing Jarvis's memory

Edit `context.md` any time. Add finished projects, recurring tasks,
things you want him to remember or follow up on. Every time you
start `jarvis-ui.py`, whatever is in that file becomes part of what
he knows — the same way you'd catch a friend up before picking a
conversation back up.

There's an "Open threads" section at the bottom of `context.md` — a
good spot for a running list of things in progress.

**`context.md` is gitignored** — it holds personal details, not just
project notes. A placeholder, `context.example.md`, is committed
instead so the mechanism is documented without exposing the contents.
Copy it to `context.md` and fill in your own.

## Roadmap

- **v2 (in progress)**: Replace the Rich terminal UI with a native
  Tk interface — buttons + touch/stylus support for tablet mode,
  scrollable conversation panel, same waveform visualizations at
  pixel resolution instead of character-cell resolution. This also
  covers the original "always-on sidebar" idea from early planning —
  a persistent Tk window is a more natural fit for that than
  relaunching a terminal each time. Being developed on a branch to
  keep `main` stable on the terminal UI until it's ready.
- **v3**: Adaptive layout that responds to tablet-mode state
  (larger touch targets and hidden keyboard hints when the
  keyboard folio is detached).
- Sway IPC tools (toggle OSK, switch workspace, control rotation) —
  not yet built, still a good candidate for `core/tools.py`.

## License

Personal project — no license specified yet.
