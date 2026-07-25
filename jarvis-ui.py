#!/usr/bin/env python3
"""
jarvis-ui.py — Phase 5 UI wrapper for Jarvis.

Layout (matching the mockup):
  ┌─────────────────┬─────────────────┐
  │   YOUR VOICE    │     JARVIS      │
  │   (waveform)    │   (waveform)    │
  ├─────────────────┴─────────────────┤
  │         conversation              │
  │                                   │
  ├───────────┬───────────┬───────────┤
  │  whisper  │ wake word │ response  │
  ├───────────┴───────────┴───────────┤
  │         t  p  w  q               │
  └───────────────────────────────────┘

Same brain, tools, personality as jarvis-unified.py.
Run with: ./run.sh ui
"""

import math
import textwrap
import threading
import time
from collections import deque
from datetime import datetime
from enum import Enum

import numpy as np
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from core.brain import JarvisBrain
from core.audio_levels import mic_level_buffer, jarvis_level_buffer
from core.stt import (
    load_whisper_model,
    record_and_transcribe,
    record_and_transcribe_timed,
)
from core.tts import speak, shutdown as tts_shutdown
from core.wakeword import WakeWordListener

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WAKE_RECORD_SECONDS = 5.0
MAX_HISTORY = 50

# Oscilloscope trace size, in *character cells*. Actual plotting
# resolution is 2x this width and 4x this height, since each braille
# character cell packs a 2x4 dot grid.
WAVEFORM_BARS = 36
OSCILLOSCOPE_ROWS = 5

# Raw int16 amplitude below which we treat the buffer as "no real
# signal" rather than normalizing it. Without this, dividing by a
# frame's own peak stretches quiet room noise up to fill the whole
# display -- it *looks* like activity but it's just noise floor being
# auto-gained. Tune this up if your mic/room is noisy and idle traces
# still look busy; tune it down if quiet speech gets gated out.
AUDIO_SILENCE_THRESHOLD = 350

COLOR_YOU = "#00FFD1"
COLOR_JARVIS = "#FFB703"
COLOR_DIM = "grey50"
COLOR_ERROR = "red"
COLOR_AMBER = "yellow"


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


class Mode(Enum):
    TEXT = "text"
    PTT = "ptt"
    WAKE = "wake"


MODE_ANNOUNCEMENTS = {
    Mode.TEXT: "Text mode active, Sir. Giving me the silent treatment I see.",
    Mode.PTT: "Push to talk mode, Sir. I'll try not to judge the pauses.",
    Mode.WAKE: "Wake mode active, Sir. I am listening.",
}


# ---------------------------------------------------------------------------
# Shared UI state
# ---------------------------------------------------------------------------


class UIState:
    def __init__(self):
        self._lock = threading.Lock()
        self.mode = Mode.TEXT
        self.history = deque(maxlen=MAX_HISTORY)
        # Separate active flags for each waveform
        self.you_active = False
        self.jarvis_active = False
        self.frame = 0
        self.status_text = "Ready"
        self.last_response_time = None
        self.scroll_offset = 0
        self.history_rows = 18  # updated each refresh from the real, rendered region size
        self.history_width = 100  # updated each refresh from the real, rendered region size
        self.last_total_lines = 0  # updated each render_history() call

        self.wave_mode = "scope"  # "scope" or "spectrum", toggled with 'v'
        self.you_peaks = [0.0] * WAVEFORM_BARS
        self.jarvis_peaks = [0.0] * WAVEFORM_BARS
        self.you_smoothed = [0.0] * WAVEFORM_BARS
        self.jarvis_smoothed = [0.0] * WAVEFORM_BARS

    def toggle_wave_mode(self):
        with self._lock:
            self.wave_mode = "spectrum" if self.wave_mode == "scope" else "scope"

    def set_history_region(self, width: int, height: int):
        with self._lock:
            self.history_width = max(10, width)
            self.history_rows = max(1, height)

    def set_last_total_lines(self, n: int):
        with self._lock:
            self.last_total_lines = n

    def history_capacity(self) -> int:
        return self.history_rows

    def history_content_width(self) -> int:
        # subtract 2 for box border + 2 for horizontal padding (0,1)
        return max(10, self.history_width - 4)

    def scroll_up(self):
        with self._lock:
            max_offset = max(0, self.last_total_lines - self.history_rows)
            self.scroll_offset = min(self.scroll_offset + 1, max_offset)

    def scroll_down(self):
        with self._lock:
            self.scroll_offset = max(0, self.scroll_offset - 1)

    def add_message(self, speaker: str, text: str):
        with self._lock:
            self.history.append((speaker, text, datetime.now().strftime("%H:%M")))

    def set_you_active(self, active: bool):
        with self._lock:
            self.you_active = active

    def set_jarvis_active(self, active: bool):
        with self._lock:
            self.jarvis_active = active

    def set_status(self, text: str):
        with self._lock:
            self.status_text = text

    def set_mode(self, mode: Mode):
        with self._lock:
            self.mode = mode

    def tick(self):
        with self._lock:
            if self.you_active or self.jarvis_active:
                self.frame += 1


# ---------------------------------------------------------------------------
# Waveform rendering — oscilloscope trace via braille sub-pixels
# ---------------------------------------------------------------------------
#
# Each braille character packs a 2 (wide) x 4 (tall) dot grid, giving
# 2x the horizontal and 4x the vertical resolution of a plain character
# cell. We plot a continuous trace in that sub-pixel space and connect
# consecutive samples with a vertical fill so it reads as a line rather
# than scattered points — the same trick terminal audio visualizers
# (cava, s-tui, etc.) use.

BRAILLE_BASE = 0x2800

# Bit for each (sub_x, sub_y) position within one braille cell.
_BRAILLE_DOT_BITS = {
    (0, 0): 0x01,
    (0, 1): 0x02,
    (0, 2): 0x04,
    (0, 3): 0x40,
    (1, 0): 0x08,
    (1, 1): 0x10,
    (1, 2): 0x20,
    (1, 3): 0x80,
}


def _set_dot(grid, x: int, y: int, cell_cols: int, cell_rows: int) -> None:
    if x < 0 or y < 0:
        return
    col, row = x // 2, y // 4
    if col >= cell_cols or row >= cell_rows:
        return
    bit = _BRAILLE_DOT_BITS[(x % 2, y % 4)]
    grid[row][col] |= bit


def _downsample_to_columns(samples, columns: int):
    """Map a run of raw audio samples onto `columns` normalized
    (-1..1) values for the trace. Uses the peak-magnitude sample in
    each bucket rather than an average, so short transients (like
    consonants) still show up instead of getting smoothed away.
    """
    arr = np.asarray(samples, dtype=np.float32)
    n = len(arr)
    if n == 0:
        return None
    peak = float(np.max(np.abs(arr)))
    if peak < AUDIO_SILENCE_THRESHOLD:
        # Below this, we're looking at room/mic noise floor, not real
        # signal. Normalizing by this tiny peak would stretch that
        # noise to fill the whole trace -- gate it to flat instead.
        return [0.0] * columns
    bucket = max(1, n // columns)
    values = []
    for i in range(columns):
        chunk = arr[i * bucket : i * bucket + bucket]
        if len(chunk) == 0:
            values.append(values[-1] if values else 0.0)
            continue
        idx = int(np.argmax(np.abs(chunk)))
        values.append(float(chunk[idx]) / peak)
    return values


def _oscilloscope_trace(
    frame: int,
    active: bool,
    color: str,
    level_buffer=None,
    offset: float = 0.0,
    cell_cols: int = WAVEFORM_BARS,
    cell_rows: int = OSCILLOSCOPE_ROWS,
) -> Text:
    sub_w = cell_cols * 2
    sub_h = cell_rows * 4
    mid = sub_h / 2
    grid = [[0] * cell_cols for _ in range(cell_rows)]

    if active:
        # Prefer real audio when we have it; oversample a bit (4x the
        # sub-pixel width) so a bucket-per-column downsample still has
        # enough raw samples to pick a meaningful peak from.
        real_values = None
        if level_buffer is not None:
            samples = level_buffer.snapshot(sub_w * 4)
            if samples:
                real_values = _downsample_to_columns(samples, sub_w)

        prev_y = None
        for x in range(sub_w):
            if real_values is not None:
                val = real_values[x]
            else:
                # Synthetic fallback -- used when no real samples are
                # available yet (buffer not wired up, or a brief gap
                # before audio starts flowing).
                t = x * 0.18 + frame * 0.28 + offset
                val = (
                    math.sin(t) * 0.5
                    + math.sin(t * 2.3 + 1.0) * 0.25
                    + math.sin(t * 0.55 + 2.0) * 0.25
                )
            y = int(round(mid - val * (mid - 1)))
            y = max(0, min(sub_h - 1, y))
            if prev_y is None:
                _set_dot(grid, x, y, cell_cols, cell_rows)
            else:
                lo, hi = (prev_y, y) if prev_y <= y else (y, prev_y)
                for yy in range(lo, hi + 1):
                    _set_dot(grid, x, yy, cell_cols, cell_rows)
            prev_y = y
    else:
        # Flat idle baseline down the middle.
        y = int(mid)
        for x in range(sub_w):
            _set_dot(grid, x, y, cell_cols, cell_rows)

    text = Text()
    for i, row in enumerate(grid):
        line = "".join(chr(BRAILLE_BASE + v) if v else " " for v in row)
        text.append(line, style=color if active else COLOR_DIM)
        if i < len(grid) - 1:
            text.append("\n")
    return text


# ---------------------------------------------------------------------------
# Waveform rendering — spectrum analyzer via FFT
# ---------------------------------------------------------------------------

BAR_CHARS = "▁▂▃▄▅▆▇█"  # 8 chars, one per eighth-of-a-cell fill level
PEAK_CAP_CHAR = "▔"  # upper one-eighth block, used for the floating peak marker

SPECTRUM_FMIN = 80    # Hz -- below typical voice fundamental, not much useful energy
SPECTRUM_FMAX = 4000  # Hz -- most speech intelligibility lives under this


def _fft_bins(samples, n_bins: int, sample_rate: int = 16000):
    """Turn a run of raw audio samples into n_bins normalized (0..1)
    magnitude values, log-spaced across the voice-relevant frequency
    range so low frequencies (which carry more energy but less
    detail) don't just dominate every bar. Returns None if there
    isn't enough signal to bother with an FFT.
    """
    arr = np.asarray(samples, dtype=np.float32)
    if len(arr) < 32:
        return None

    if float(np.max(np.abs(arr))) < AUDIO_SILENCE_THRESHOLD:
        # Room/mic noise floor, not real signal -- skip the FFT
        # entirely rather than computing a spectrum we'd just
        # normalize up to fake full-scale bars.
        return [0.0] * n_bins

    windowed = arr * np.hanning(len(arr))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(arr), d=1.0 / sample_rate)

    fmax = min(SPECTRUM_FMAX, sample_rate / 2)
    edges = np.logspace(np.log10(SPECTRUM_FMIN), np.log10(fmax), n_bins + 1)

    bins = np.zeros(n_bins, dtype=np.float32)
    for i in range(n_bins):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if np.any(mask):
            bins[i] = np.mean(spectrum[mask])

    # sqrt compression -- raw FFT magnitude is dominated by a few loud
    # bins; compressing spreads it out into something that actually
    # looks like a spectrum instead of one tall spike.
    bins = np.sqrt(bins)

    peak = float(bins.max())
    if peak < 1e-6:
        return [0.0] * n_bins
    return (bins / peak).tolist()


def _bar_layout(cell_cols: int, bar_width: int = 2, bar_gap: int = 1):
    """Map character columns to bar indices, with blank gap columns
    between bars so they read as distinct bars instead of one solid
    block. Returns (layout, num_bars) where layout[col] is the bar
    index for that column, or -1 if it's a gap/padding column.
    """
    num_bars = max(1, (cell_cols + bar_gap) // (bar_width + bar_gap))
    used = num_bars * bar_width + (num_bars - 1) * bar_gap
    leading = max(0, (cell_cols - used) // 2)
    layout = [-1] * cell_cols
    col = leading
    for b in range(num_bars):
        for _ in range(bar_width):
            if 0 <= col < cell_cols:
                layout[col] = b
            col += 1
        col += bar_gap
    return layout, num_bars


def _spectrum_bars(
    frame: int,
    active: bool,
    color: str,
    peaks: list,
    smoothed: list,
    level_buffer=None,
    offset: float = 0.0,
    cell_cols: int = WAVEFORM_BARS,
    cell_rows: int = OSCILLOSCOPE_ROWS,
) -> Text:
    """Bar-per-frequency-bin spectrum analyzer with a slowly-decaying
    peak-hold cap per bar. `peaks` and `smoothed` are the caller's
    persistent per-bar state (plain lists, mutated in place):
      - `smoothed` applies an attack/release envelope to each bar so
        a fresh FFT every ~66ms doesn't read as flicker -- real mic
        noise jitters frame to frame in a way a synthetic wave never
        did, so without this the bars visually blur together.
      - `peaks` is the classic VU-meter peak-hold cap, decaying
        slower than the bar itself.
    Bars render with a blank gap column between them (see
    _bar_layout) so they stay visually distinct instead of forming
    one solid mass.
    """
    layout, num_bars = _bar_layout(cell_cols)
    sub_levels = cell_rows * 8  # 8 fill levels per character row

    if active:
        raw = None
        if level_buffer is not None:
            samples = level_buffer.snapshot(2048)
            if samples:
                raw = _fft_bins(samples, num_bars)
        if raw is None:
            raw = []
            for i in range(num_bars):
                v = 0.5 + 0.5 * math.sin(frame * 0.12 + i * 0.4 + offset)
                v *= abs(math.sin(frame * 0.05 + i * 0.15 + offset)) ** 0.6
                raw.append(v)
    else:
        raw = [0.0] * num_bars

    # Attack/release smoothing: react quickly to a bar getting louder,
    # fall back down more slowly. This is what turns frame-to-frame
    # noise jitter into something that reads as a bar moving, rather
    # than static.
    attack, release = 0.6, 0.2
    for i in range(num_bars):
        target = raw[i]
        if target > smoothed[i]:
            smoothed[i] += (target - smoothed[i]) * attack
        else:
            smoothed[i] += (target - smoothed[i]) * release

    # decay rate tuned so a peak takes roughly a second to fall back down
    decay = 0.05 * sub_levels / cell_rows

    grid_units = [0] * num_bars
    for i in range(num_bars):
        units = int(round(smoothed[i] * sub_levels))
        grid_units[i] = units
        if units > peaks[i]:
            peaks[i] = units
        else:
            peaks[i] = max(units, peaks[i] - decay)

    # Build cell_rows x cell_cols character grid, top row first.
    lines = []
    for row in range(cell_rows - 1, -1, -1):
        row_start = row * 8
        chars = []
        for col in range(cell_cols):
            bar = layout[col]
            if bar == -1:
                chars.append(" ")
                continue
            units = grid_units[bar]
            filled = units - row_start
            peak_row = int(peaks[bar] // 8)
            if peaks[bar] > 0 and row == peak_row and (peaks[bar] - row_start) >= 0:
                chars.append(PEAK_CAP_CHAR)
            elif filled >= 8:
                chars.append(BAR_CHARS[7])
            elif filled > 0:
                chars.append(BAR_CHARS[filled - 1])
            else:
                chars.append(" ")
        lines.append("".join(chars))

    text = Text()
    for i, line in enumerate(lines):
        text.append(line, style=color if active else COLOR_DIM)
        if i < len(lines) - 1:
            text.append("\n")
    return text


def render_you_panel(state: UIState) -> Panel:
    label = (
        f"[bold {COLOR_YOU}]YOUR VOICE[/bold {COLOR_YOU}]"
        if state.you_active
        else f"[{COLOR_DIM}]YOUR VOICE[/{COLOR_DIM}]"
    )
    if state.wave_mode == "spectrum":
        waveform = _spectrum_bars(
            state.frame,
            state.you_active,
            COLOR_YOU,
            state.you_peaks,
            state.you_smoothed,
            level_buffer=mic_level_buffer,
            offset=0.0,
        )
    else:
        waveform = _oscilloscope_trace(
            state.frame,
            state.you_active,
            COLOR_YOU,
            level_buffer=mic_level_buffer,
            offset=0.0,
        )
    return Panel(
        Align(waveform, align="center", vertical="middle"),
        title=label,
        border_style=COLOR_YOU if state.you_active else "grey30",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def render_jarvis_panel(state: UIState) -> Panel:
    label = (
        f"[bold {COLOR_JARVIS}]JARVIS[/bold {COLOR_JARVIS}]"
        if state.jarvis_active
        else f"[{COLOR_DIM}]JARVIS[/{COLOR_DIM}]"
    )
    if state.wave_mode == "spectrum":
        waveform = _spectrum_bars(
            state.frame,
            state.jarvis_active,
            COLOR_JARVIS,
            state.jarvis_peaks,
            state.jarvis_smoothed,
            level_buffer=jarvis_level_buffer,
            offset=1.5,
        )
    else:
        waveform = _oscilloscope_trace(
            state.frame,
            state.jarvis_active,
            COLOR_JARVIS,
            level_buffer=jarvis_level_buffer,
            offset=1.5,
        )
    return Panel(
        Align(waveform, align="center", vertical="middle"),
        title=label,
        border_style=COLOR_JARVIS if state.jarvis_active else "grey30",
        box=box.ROUNDED,
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# History panel
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# History panel
# ---------------------------------------------------------------------------


def _wrap_message_lines(speaker: str, text: str, ts: str, width: int):
    """Wrap a single message into the Text lines it will actually take
    up on screen at the given content width, keeping the "[ts] name: "
    prefix on the first line and indenting continuation lines to align
    under it. This mirrors Rich's own wrapping closely enough (plain
    character-width wrapping) without needing a Console instance.
    """
    label = "you: " if speaker == "you" else "Jarvis: "
    label_style = f"bold {COLOR_YOU}" if speaker == "you" else f"bold {COLOR_JARVIS}"
    body_style = COLOR_YOU if speaker == "you" else COLOR_JARVIS
    ts_part = f"[{ts}] "
    prefix_len = len(ts_part) + len(label)

    body_width = max(10, width - prefix_len)
    wrapped_body = textwrap.wrap(text, width=body_width, break_long_words=True) or [""]

    lines = []
    for i, body_line in enumerate(wrapped_body):
        line = Text()
        if i == 0:
            line.append(ts_part, style=COLOR_DIM)
            line.append(label, style=label_style)
        else:
            line.append(" " * prefix_len)
        line.append(body_line, style=body_style)
        lines.append(line)
    return lines


def _wrap_history_lines(history, width: int):
    all_lines = []
    for speaker, text, ts in history:
        all_lines.extend(_wrap_message_lines(speaker, text, ts, width))
    return all_lines


def render_history(state: UIState) -> Panel:
    history = list(state.history)
    capacity = state.history_capacity()
    content_width = state.history_content_width()

    # Wrap every message into the exact lines it occupies on screen,
    # then window over LINES, not messages. Windowing by message count
    # breaks the moment any single message wraps to more than one
    # line: the window can claim to hold N whole messages while the
    # panel can only actually fit fewer lines, and the overflow gets
    # silently cropped by Rich rather than being reachable by scroll.
    all_lines = _wrap_history_lines(history, content_width)
    total_lines = len(all_lines)
    state.set_last_total_lines(total_lines)

    end = max(0, total_lines - state.scroll_offset)
    start = max(0, end - capacity)
    window = all_lines[start:end]

    if not window:
        body = Text("Waiting for conversation...", style=COLOR_DIM)
    else:
        body = Text()
        for i, line in enumerate(window):
            if i > 0:
                body.append("\n")
            body.append_text(line)

    # Position indicator so scrolling is visibly confirmable even when
    # message text alone doesn't make it obvious (e.g. short test runs).
    if total_lines == 0:
        title = "[bold]conversation[/bold]"
    else:
        title = f"[bold]conversation[/bold] [{COLOR_DIM}]({start + 1}-{end} of {total_lines})[/{COLOR_DIM}]"
        if end < total_lines:
            title += f" [{COLOR_DIM}]\u2193 more below[/{COLOR_DIM}]"
        if start > 0:
            title += f" [{COLOR_DIM}]\u2191 more above[/{COLOR_DIM}]"

    return Panel(
        body,
        title=title,
        border_style="grey30",
        box=box.ROUNDED,
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Status and controls
# ---------------------------------------------------------------------------


def render_status(state: UIState) -> Panel:
    mode_colors = {Mode.TEXT: COLOR_DIM, Mode.PTT: COLOR_YOU, Mode.WAKE: COLOR_JARVIS}
    mode_labels = {Mode.TEXT: "TEXT", Mode.PTT: "PTT", Mode.WAKE: "WAKE"}

    t = Text()
    t.append("mode: ", style=COLOR_DIM)
    t.append(f"● {mode_labels[state.mode]}", style=f"bold {mode_colors[state.mode]}")
    t.append("  │  whisper: ", style=COLOR_DIM)
    t.append("tiny", style="white")
    t.append("  │  wake: ", style=COLOR_DIM)
    t.append(
        "active" if state.mode == Mode.WAKE else "standby",
        style=COLOR_JARVIS if state.mode == Mode.WAKE else COLOR_DIM,
    )
    if state.last_response_time:
        t.append("  │  last response: ", style=COLOR_DIM)
        t.append(f"{state.last_response_time:.1f}s", style="white")
    t.append("  │  ", style=COLOR_DIM)
    t.append(state.status_text, style=COLOR_DIM)

    return Panel(t, border_style="grey30", box=box.ROUNDED, padding=(0, 1))


def render_controls() -> Panel:
    t = Text(justify="center")
    t.append("t", style=f"bold {COLOR_JARVIS}")
    t.append("=text  ", style=COLOR_DIM)
    t.append("p", style=f"bold {COLOR_YOU}")
    t.append("=PTT  ", style=COLOR_DIM)
    t.append("w", style=f"bold {COLOR_JARVIS}")
    t.append("=wake  ", style=COLOR_DIM)
    t.append("u", style=f"bold {COLOR_JARVIS}")
    t.append("=scroll up  ", style=COLOR_DIM)
    t.append("d", style=f"bold {COLOR_JARVIS}")
    t.append("=scroll down  ", style=COLOR_DIM)
    t.append("v", style=f"bold {COLOR_JARVIS}")
    t.append("=view  ", style=COLOR_DIM)
    t.append("q", style=f"bold {COLOR_ERROR}")
    t.append("=quit  ", style=COLOR_DIM)
    t.append("  powered by claude sonnet 4.6", style=COLOR_DIM)

    return Panel(t, border_style="grey30", box=box.ROUNDED, padding=(0, 0))


# ---------------------------------------------------------------------------
# Layout builder
# ---------------------------------------------------------------------------


def _measure_history_region(console: Console):
    """Build a throwaway layout with the same split structure used in
    build_layout and ask Rich how much space the 'history' region
    actually gets. This replaces guessing via ratio arithmetic, which
    didn't account for how Rich's Layout rounds/allocates space in
    practice and drifted from the real on-screen size.
    Returns (width, height) of the region, border/padding included.
    """
    skeleton = Layout()
    skeleton.split_column(
        Layout(name="waveforms", ratio=4),
        Layout(name="history", ratio=6),
        Layout(name="status", ratio=1),
        Layout(name="controls", ratio=1),
    )
    render_map = skeleton.render(console, console.options.update(height=console.size.height))
    for region_layout, render in render_map.items():
        if region_layout.name == "history":
            # subtract 2 rows for the panel's own top/bottom border
            return render.region.width, max(1, render.region.height - 2)
    return 100, 18


def build_layout(state: UIState) -> Layout:
    layout = Layout()

    layout.split_column(
        Layout(name="waveforms", ratio=4),
        Layout(name="history", ratio=6),
        Layout(name="status", ratio=1),
        Layout(name="controls", ratio=1),
    )

    # Side by side waveforms
    layout["waveforms"].split_row(
        Layout(name="you_wave"),
        Layout(name="jarvis_wave"),
    )
    layout["you_wave"].update(render_you_panel(state))
    layout["jarvis_wave"].update(render_jarvis_panel(state))

    layout["history"].update(render_history(state))
    layout["status"].update(render_status(state))
    layout["controls"].update(render_controls())

    return layout


# ---------------------------------------------------------------------------
# Response handler
# ---------------------------------------------------------------------------


def handle_response(brain: JarvisBrain, text: str, state: UIState):
    if not text or not text.strip():
        state.set_status("heard nothing — try again")
        return

    state.add_message("you", text)
    state.set_status("thinking...")
    state.set_you_active(False)

    full_reply = ""
    start = time.time()

    for sentence in brain.chat_streaming(text):
        full_reply += sentence + " "
        state.set_jarvis_active(True)
        speak(sentence)
        state.set_jarvis_active(False)

    state.last_response_time = time.time() - start
    state.add_message("jarvis", full_reply.strip())
    state.set_status("ready")


# ---------------------------------------------------------------------------
# Mode switching
# ---------------------------------------------------------------------------


def switch_mode(
    new_mode: Mode,
    current_mode: Mode,
    brain: JarvisBrain,
    listener: WakeWordListener,
    state: UIState,
) -> Mode:
    if new_mode == current_mode:
        return current_mode

    listener.enabled = new_mode == Mode.WAKE
    state.set_mode(new_mode)

    announcement = MODE_ANNOUNCEMENTS[new_mode]
    state.add_message("jarvis", announcement)
    state.set_jarvis_active(True)
    speak(announcement)
    state.set_jarvis_active(False)
    state.set_status("ready")

    return new_mode


# ---------------------------------------------------------------------------
# Animation ticker
# ---------------------------------------------------------------------------


def animation_ticker(state: UIState, stop_event: threading.Event):
    while not stop_event.is_set():
        state.tick()
        time.sleep(0.066)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    console = Console()
    state = UIState()
    brain = JarvisBrain(voice_mode=True)
    wake_active = threading.Event()
    stop_animation = threading.Event()
    current_mode = Mode.TEXT

    def on_wake_detected():
        nonlocal current_mode
        if wake_active.is_set():
            return
        if current_mode != Mode.WAKE:
            return
        wake_active.set()
        state.set_status("Jarvis heard — listening...")
        state.set_you_active(True)
        try:
            text = record_and_transcribe_timed(WAKE_RECORD_SECONDS)
            state.set_you_active(False)
            handle_response(brain, text, state)
        finally:
            wake_active.clear()

    listener = WakeWordListener(on_detected=on_wake_detected)

    try:
        from core.waveform_window import start_waveform_window_thread

        start_waveform_window_thread(get_wave_mode=lambda: state.wave_mode)
        console.print("[grey50]Waveform window opened.[/grey50]")
    except Exception as e:
        # No display available (e.g. SSH session without X forwarding),
        # or tkinter isn't installed -- keep running with just the
        # terminal panels rather than crashing the whole app over a
        # cosmetic extra.
        console.print(f"[grey50]  (waveform window unavailable: {e})[/grey50]")

    console.print("[grey50]Loading whisper (tiny)...[/grey50]")
    load_whisper_model()
    console.print("[grey50]Loading wake word model...[/grey50]")
    listener.start()

    opening = "Good to see you, Sir. I'm ready when you are."
    state.add_message("jarvis", opening)

    anim_thread = threading.Thread(
        target=animation_ticker,
        args=(state, stop_animation),
        daemon=True,
    )
    anim_thread.start()

    try:
        with Live(
            build_layout(state),
            console=console,
            refresh_per_second=15,
            screen=True,
        ) as live:

            def refresh_loop():
                while not stop_animation.is_set():
                    width, height = _measure_history_region(console)
                    state.set_history_region(width, height)
                    live.update(build_layout(state))
                    time.sleep(0.066)

            refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
            refresh_thread.start()

            # Speak opening after Live starts
            state.set_jarvis_active(True)
            speak(opening)
            state.set_jarvis_active(False)

            while True:
                try:
                    user_input = input("").strip()
                except (EOFError, KeyboardInterrupt):
                    break

                lower = user_input.lower()

                if lower == "q":
                    break
                elif lower == "t":
                    current_mode = switch_mode(
                        Mode.TEXT, current_mode, brain, listener, state
                    )
                elif lower == "p":
                    current_mode = switch_mode(
                        Mode.PTT, current_mode, brain, listener, state
                    )
                elif lower == "w":
                    current_mode = switch_mode(
                        Mode.WAKE, current_mode, brain, listener, state
                    )
                elif lower == "u":
                    state.scroll_up()
                elif lower == "d":
                    state.scroll_down()
                elif lower == "v":
                    state.toggle_wave_mode()
                elif current_mode == Mode.TEXT and user_input:
                    threading.Thread(
                        target=handle_response,
                        args=(brain, user_input, state),
                        daemon=True,
                    ).start()

                elif current_mode == Mode.PTT and user_input == "":
                    if wake_active.is_set():
                        state.set_status("already listening — please wait")
                        continue
                    state.set_status("recording...")
                    state.set_you_active(True)
                    text = record_and_transcribe()
                    state.set_you_active(False)
                    threading.Thread(
                        target=handle_response,
                        args=(brain, text, state),
                        daemon=True,
                    ).start()

                elif current_mode == Mode.WAKE and user_input == "":
                    state.set_status("say 'Jarvis' or press p for PTT")

    finally:
        stop_animation.set()
        listener.stop()
        tts_shutdown()
        farewell = "Until next time, Sir."
        console.print(f"\n[cyan]Jarvis:[/cyan] {farewell}")
        speak(farewell)


if __name__ == "__main__":
    main()
