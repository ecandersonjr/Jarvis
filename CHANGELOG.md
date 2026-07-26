# Changelog

All notable changes to Jarvis are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Planned
- v3: adaptive layout responding to tablet-mode state (larger touch
  targets, hidden keyboard-shortcut hints when the folio is detached)
- Single-instance guard in `run.sh` (check whether Jarvis is already
  running before launching a second instance from the keybinding)
- Dark-theme styling for the conversation panel's scrollbar in
  `jarvis-tk.py` (currently Tk's default light-gray — `tk.Scrollbar`
  doesn't take a simple `bg=` the way other widgets do)
- `<FocusIn>`/`<FocusOut>` hook on the text `Entry` to auto-toggle the
  on-screen keyboard (SIGUSR1/SIGUSR2) in tablet mode, rather than
  relying on the manual Waybar toggle
- Wire in the custom-trained wake word model
  (`models/hey_jarvis_v0.1.onnx`) — currently `core/wakeword.py` loads
  openwakeword's bundled stock `hey_jarvis` model instead

---

## [v2.0.1] — Tk UI: native GUI rewrite

The Rich terminal UI (`jarvis-ui.py`) is superseded as the primary
interface by a native Tk application (`jarvis-tk.py`), developed and
tested on its own before being promoted. `jarvis-ui.py` and every
prior stage remain in the repo as working references.

### Added
- `jarvis-tk.py`: full Tk rewrite. Waveform panels (reusing
  `core/waveform_window.py`'s `WaveformPanel` directly), a scrollable
  conversation `Text` widget (native scrolling replaces the manual
  line-windowing math `render_history` needed), a status bar, and a
  button-driven control row (Send, PTT, Wake toggle, View toggle, Quit)
- No more modal "press t/p/w to switch modes" — text entry, PTT, and
  wake word are all available simultaneously via buttons, which a
  keyboard-only interface couldn't offer
- Thread-safe UI architecture: `root.mainloop()` is the single main
  thread; all recording/API/TTS work happens on worker threads and
  reports back through one `queue.Queue()`, drained on a
  `root.after()` timer. No widget is ever touched from a worker
  thread directly — verified explicitly for the wake-word path, since
  `WakeWordListener`'s callback fires on its own thread
- PTT is a real toggle button: click to start recording, click again
  to stop — `core/stt.py` gained `record_until_stopped()` /
  `record_and_transcribe_toggle()`, which wait on a `threading.Event`
  instead of blocking on `input()` (which has no meaning for a button
  click). The terminal-facing `record_until_enter()` /
  `record_and_transcribe()` are unchanged
- Jarvis's replies stream into the conversation panel sentence by
  sentence and are spoken sentence by sentence, following
  `core/brain.py`'s own documented usage pattern for
  `chat_streaming()` (`for sentence in chat_streaming(...): speak(sentence)`)
  rather than buffering the full reply before speaking

### Fixed
- PTT and wake-word transcripts were never appearing in the
  conversation panel — only typed text was. Root cause: the
  `user_text` announcement lived in `_start_turn`, which only the
  text-entry path called. Moved into `_run_turn` itself so every input
  path (typed, PTT, wake word) announces consistently, in the correct
  speaker color
- Wake-word-triggered turns weren't disabling the Send/PTT controls,
  allowing overlapping actions to be triggered mid-turn

### Verified
- Full text-mode turn, PTT toggle (start/stop/transcribe/reply), and
  a wake-word trigger fired from a genuinely separate thread — all
  tested end-to-end under Xvfb against the real widget tree and real
  `queue`/`threading` code paths, not just import-time checks
- Confirmed the app runs fully detached (no stdin/stdout/stderr, no
  controlling terminal) exactly as a Sway `exec` keybinding would
  launch it — and separately confirmed the failure mode when
  `ANTHROPIC_API_KEY` is unset: the process dies silently with no
  window and no visible error, which is why `run.sh` now redirects
  output to `jarvis.log`

---

## [v1.1] — Live audio visualization

### Added
- `core/audio_levels.py`: thread-safe ring buffers (`mic_level_buffer`,
  `jarvis_level_buffer`) feeding real audio samples to the UI
- Oscilloscope waveform display using braille sub-pixel resolution
  for a smooth line trace
- FFT-based spectrum analyzer mode (`core/waveform_dsp.py`), toggled
  with `v`: log-spaced frequency bins, peak-hold caps, attack/release
  smoothing
- Noise gate (`AUDIO_SILENCE_THRESHOLD`) so ambient mic/room noise
  doesn't get auto-gained into a false "active" display
- `core/waveform_window.py`: standalone Tk window rendering the same
  waveforms at pixel resolution, running on its own thread alongside
  the terminal UI

### Fixed
- Conversation history scroll (`u`/`d`) — three separate bugs found
  and fixed in sequence: message-count-based windowing instead of
  real rendered-line windowing, a hand-estimated panel-height formula
  that drifted from Rich's actual layout allocation, and long/wrapped
  messages silently overflowing the panel uncounted
- Waveform panel centering (oscilloscope trace was left-justified
  instead of centered in its panel)
- Spectrum analyzer peak-hold marker rendering on never-active
  columns (bottom-row artifact)

---

## [v1.0] — Terminal UI (`jarvis-ui.py`)

### Added
- Rich-based terminal UI: side-by-side waveform panels, scrollable
  conversation history, live status bar
- Text, push-to-talk, and wake-word modes, switchable mid-session
- Synthetic (non-audio-driven) animated waveform bars

---

## [v0.3] — Unified backend (`jarvis-unified.py`)

### Added
- Unified tool-calling backend: shell command execution, app
  launching, email summarization (Gmail IMAP), weather, web search,
  stock lookup, task system

---

## [v0.2] — Voice (`jarvis-voice.py`)

### Added
- Speech-to-text via Whisper (`tiny` model)
- Text-to-speech via piper-tts

---

## [v0.1] — Text loop (`jarvis.py`)

### Added
- Plain text-in/text-out loop against the Claude API
