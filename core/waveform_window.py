"""
core/waveform_window.py

A small, separate Tkinter window that renders the YOUR VOICE / JARVIS
waveform panels at pixel resolution instead of character-cell
resolution. Runs in its own thread inside the same process as
jarvis-ui.py so it can read directly from the same audio_levels ring
buffers -- no IPC needed, no second process to keep in sync.

Threading note: Tk's mainloop() is conventionally expected to run on
the main thread, but running it in a dedicated background thread
works fine on Linux/X11 (including XWayland, which is what Tk uses
under Sway -- Tk has no native Wayland backend) as long as *all* Tk
calls happen from that one thread. We keep that invariant by doing
every bit of drawing inside the Tk .after() callback, which always
fires on Tk's own thread. The only thing actually shared across
threads is the audio ring buffers, and those already have their own
locks.
"""

import threading
import tkinter as tk

from core.audio_levels import mic_level_buffer, jarvis_level_buffer
from core.waveform_dsp import downsample_peak, fft_bins, BarTracker

BG = "#0a0a0a"
COLOR_YOU = "#00e5ff"
COLOR_JARVIS = "#ffb703"

FRAME_MS = 16          # ~60fps
SCOPE_SNAPSHOT = 1024  # samples pulled from the ring buffer per scope frame
SPECTRUM_BARS = 24
SPECTRUM_SNAPSHOT = 2048
BAR_GAP = 4


class WaveformPanel:
    """One canvas, one audio source, one trace/bar state."""

    def __init__(self, canvas: tk.Canvas, color: str, level_buffer):
        self.canvas = canvas
        self.color = color
        self.level_buffer = level_buffer
        self.mode = "scope"  # "scope" or "spectrum"
        self.tracker = BarTracker(SPECTRUM_BARS)

    def draw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return  # window not laid out yet
        self.canvas.delete("all")
        if self.mode == "spectrum":
            self._draw_spectrum(w, h)
        else:
            self._draw_scope(w, h)

    def _draw_scope(self, w: int, h: int):
        mid = h / 2
        samples = self.level_buffer.snapshot(SCOPE_SNAPSHOT)
        values = downsample_peak(samples, w) if samples else None
        if values is None:
            values = [0.0] * w

        points = []
        for x in range(w):
            y = mid - values[x] * (mid - 4)
            points.extend((x, y))

        if w >= 2:
            self.canvas.create_line(*points, fill=self.color, width=2, joinstyle="round")
        else:
            self.canvas.create_line(0, mid, w, mid, fill=self.color, width=2)

    def _draw_spectrum(self, w: int, h: int):
        samples = self.level_buffer.snapshot(SPECTRUM_SNAPSHOT)
        raw = fft_bins(samples, SPECTRUM_BARS) if samples else None
        if raw is None:
            raw = [0.0] * SPECTRUM_BARS

        smoothed, peaks = self.tracker.update(raw)

        bar_w = (w - BAR_GAP * (SPECTRUM_BARS - 1)) / SPECTRUM_BARS
        for i in range(SPECTRUM_BARS):
            x0 = i * (bar_w + BAR_GAP)
            x1 = x0 + bar_w

            bar_h = max(2, smoothed[i] * (h - 8))
            self.canvas.create_rectangle(
                x0, h - bar_h, x1, h, fill=self.color, outline=""
            )

            peak_h = max(2, peaks[i] * (h - 8))
            peak_y = h - peak_h
            self.canvas.create_rectangle(
                x0, peak_y - 2, x1, peak_y, fill=self.color, outline=""
            )


def run_waveform_window(get_wave_mode=None):
    """Build and run the waveform window. Blocks on Tk's mainloop --
    call this from a dedicated thread, not the main one, since
    jarvis-ui.py already uses the main thread for the terminal's
    blocking input() loop.

    get_wave_mode: optional zero-arg callable returning "scope" or
    "spectrum" (pass `lambda: state.wave_mode`) so this window
    follows the same 'v'-key toggle as the terminal panels. Defaults
    to "scope" if omitted.
    """
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"\033[2m  (waveform window unavailable: {e})\033[0m")
        return

    root.title("Jarvis Waveforms")
    root.configure(bg=BG)
    root.geometry("900x220")
    root.minsize(400, 140)

    you_canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    you_canvas.place(relx=0.0, rely=0.0, relwidth=0.5, relheight=1.0)
    jarvis_canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
    jarvis_canvas.place(relx=0.5, rely=0.0, relwidth=0.5, relheight=1.0)

    you_panel = WaveformPanel(you_canvas, COLOR_YOU, mic_level_buffer)
    jarvis_panel = WaveformPanel(jarvis_canvas, COLOR_JARVIS, jarvis_level_buffer)

    def tick():
        mode = get_wave_mode() if get_wave_mode else "scope"
        you_panel.mode = mode
        jarvis_panel.mode = mode
        you_panel.draw()
        jarvis_panel.draw()
        root.after(FRAME_MS, tick)

    root.after(FRAME_MS, tick)
    root.mainloop()


def start_waveform_window_thread(get_wave_mode=None):
    """Start the window in a daemon thread and return immediately, so
    the caller isn't blocked. Returns the Thread object. If Tk can't
    initialize (no display available, e.g. an SSH session with no
    X forwarding), this raises inside the thread and the thread just
    dies -- callers should wrap the *call site* in a try/except if
    they want to keep running headless without the window.
    """
    t = threading.Thread(target=run_waveform_window, args=(get_wave_mode,), daemon=True)
    t.start()
    return t
