"""
jarvis-tk.py

Tk-based UI for Jarvis (v2). Sits alongside jarvis-ui.py rather than
replacing it -- develop/test this on its own until it's proven, per
the tk-ui-rewrite branch plan.

Architecture
------------
Tk's mainloop() IS the main thread here -- everything is event-driven
(button clicks, Entry <Return>, .after() timers) rather than the
blocking input() loop jarvis-ui.py used. Background work (recording,
the Claude API call, TTS playback) runs on worker threads and reports
back to the UI through a single thread-safe queue.Queue(), drained by
a periodic root.after() callback. Widgets are NEVER touched directly
from a worker thread -- only from inside that drain callback, which
always runs on the Tk thread. This is the same discipline
waveform_window.py already uses for the audio buffers, just applied
to more of the app.

Known unverified assumption
----------------------------
None currently. core/brain.py's real interface (chat_streaming()
yielding sentences one at a time) has been confirmed against source
-- see _run_turn() below, which follows its documented usage pattern
directly.
"""

import queue
import threading
import tkinter as tk
from datetime import datetime

from core.audio_levels import mic_level_buffer, jarvis_level_buffer
from core.waveform_window import WaveformPanel
from core.wakeword import WakeWordListener
from core.stt import record_and_transcribe_timed, record_and_transcribe_toggle
from core.tts import speak, shutdown as tts_shutdown
from core.brain import JarvisBrain

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

BG = "#0a0a0a"
PANEL = "#111111"
COLOR_YOU = "#00e5ff"
COLOR_JARVIS = "#ffb703"
TEXT = "#e0e0e0"
DIM = "#666666"
ERROR = "#ff4d4d"
RECORDING = "#ff5c5c"
FONT = ("monospace", 11)
FONT_BOLD = ("monospace", 11, "bold")

FRAME_MS = 16      # waveform redraw cadence, ~60fps
QUEUE_MS = 50       # how often the UI drains events from worker threads


def _btn_style():
    # Generous padding -- touch/stylus targets, not mouse-precision ones.
    return dict(
        bg=PANEL, fg=TEXT, activebackground="#222222", activeforeground=TEXT,
        font=FONT, relief="flat", padx=16, pady=12, bd=0, highlightthickness=0,
    )


class JarvisApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Jarvis")
        self.root.configure(bg=BG)
        self.root.geometry("900x700")
        self.root.minsize(600, 480)

        self.event_queue = queue.Queue()
        self.busy = threading.Event()  # set while a turn is in progress
        self.wave_mode = "scope"
        self.wake_enabled = False
        self.ptt_recording = False
        self.ptt_stop_event = None

        self.brain = JarvisBrain(voice_mode=True)
        self.listener = WakeWordListener(on_detected=self._on_wake_detected)
        self.listener.start()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit)

        self.root.after(FRAME_MS, self._tick_waveforms)
        self.root.after(QUEUE_MS, self._drain_queue)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        wave_frame = tk.Frame(self.root, bg=BG)
        wave_frame.pack(fill="x", padx=8, pady=(8, 4))

        you_canvas = tk.Canvas(wave_frame, bg=PANEL, height=110, highlightthickness=0)
        you_canvas.pack(side="left", fill="both", expand=True, padx=(0, 4))
        jarvis_canvas = tk.Canvas(wave_frame, bg=PANEL, height=110, highlightthickness=0)
        jarvis_canvas.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self.you_panel = WaveformPanel(you_canvas, COLOR_YOU, mic_level_buffer)
        self.jarvis_panel = WaveformPanel(jarvis_canvas, COLOR_JARVIS, jarvis_level_buffer)

        convo_frame = tk.Frame(self.root, bg=BG)
        convo_frame.pack(fill="both", expand=True, padx=8, pady=4)

        scrollbar = tk.Scrollbar(convo_frame)
        scrollbar.pack(side="right", fill="y")

        self.text = tk.Text(
            convo_frame, bg=PANEL, fg=TEXT, wrap="word", state="disabled",
            font=FONT, padx=10, pady=10, relief="flat",
            yscrollcommand=scrollbar.set,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

        self.text.tag_configure("you_label", foreground=COLOR_YOU, font=FONT_BOLD)
        self.text.tag_configure("jarvis_label", foreground=COLOR_JARVIS, font=FONT_BOLD)
        self.text.tag_configure("body", foreground=TEXT)
        self.text.tag_configure("dim", foreground=DIM)
        self.text.tag_configure("error", foreground=ERROR)

        self.status_var = tk.StringVar(value="Ready")
        status = tk.Label(
            self.root, textvariable=self.status_var, bg=BG, fg=DIM,
            anchor="w", font=("monospace", 10),
        )
        status.pack(fill="x", padx=10)

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=8, pady=8)

        self.entry = tk.Entry(
            controls, bg=PANEL, fg=TEXT, insertbackground=TEXT,
            font=("monospace", 12), relief="flat",
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 6))
        self.entry.bind("<Return>", self._on_send)

        self.send_btn = tk.Button(controls, text="Send", command=self._on_send, **_btn_style())
        self.send_btn.pack(side="left", padx=2)

        self.ptt_btn = tk.Button(controls, text="\U0001F399 PTT", command=self._on_ptt, **_btn_style())
        self.ptt_btn.pack(side="left", padx=2)

        self.wake_btn = tk.Button(
            controls, text="\U0001F442 Wake: Off", command=self._on_toggle_wake, **_btn_style()
        )
        self.wake_btn.pack(side="left", padx=2)

        view_btn = tk.Button(controls, text="View", command=self._on_toggle_view, **_btn_style())
        view_btn.pack(side="left", padx=2)

        quit_btn = tk.Button(controls, text="Quit", command=self._on_quit, **_btn_style())
        quit_btn.pack(side="left", padx=2)

    # -- Rendering helpers (Tk-thread only) ---------------------------------

    def _append(self, speaker: str, text: str):
        self.text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        if speaker == "you":
            self.text.insert("end", f"[{ts}] ", "dim")
            self.text.insert("end", "you: ", "you_label")
            self.text.insert("end", text + "\n\n", "body")
        else:  # error
            self.text.insert("end", text + "\n\n", "error")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _begin_jarvis_message(self):
        self.text.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.text.insert("end", f"[{ts}] ", "dim")
        self.text.insert("end", "Jarvis: ", "jarvis_label")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _append_jarvis_chunk(self, sentence: str):
        self.text.configure(state="normal")
        self.text.insert("end", sentence + " ", "body")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _end_jarvis_message(self):
        self.text.configure(state="normal")
        self.text.insert("end", "\n\n")
        self.text.configure(state="disabled")
        self.text.see("end")

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.send_btn.configure(state=state)
        self.ptt_btn.configure(state=state, text="\U0001F399 PTT", bg=PANEL)

    def _tick_waveforms(self):
        self.you_panel.mode = self.wave_mode
        self.jarvis_panel.mode = self.wave_mode
        self.you_panel.draw()
        self.jarvis_panel.draw()
        self.root.after(FRAME_MS, self._tick_waveforms)

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "user_text":
                    self._append("you", payload)
                elif kind == "jarvis_start":
                    self._begin_jarvis_message()
                elif kind == "jarvis_chunk":
                    self._append_jarvis_chunk(payload)
                elif kind == "jarvis_end":
                    self._end_jarvis_message()
                elif kind == "status":
                    self._set_status(payload)
                elif kind == "error":
                    self._append("error", payload)
                elif kind == "turn_done":
                    self._set_controls_enabled(True)
                elif kind == "controls_disable":
                    self._set_controls_enabled(False)
        except queue.Empty:
            pass
        self.root.after(QUEUE_MS, self._drain_queue)

    # -- Event handlers (Tk-thread; spawn workers for anything blocking) ---

    def _on_send(self, event=None):
        if self.busy.is_set():
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._start_turn(text)

    def _on_ptt(self):
        if self.ptt_recording:
            # Stopping -- this click handler runs on the Tk thread, so
            # it's safe to touch widgets directly here (unlike updates
            # coming FROM a worker thread, which must go through the
            # queue). The worker's blocking stop_event.wait() unblocks
            # the instant we set() it below.
            self.ptt_recording = False
            self.ptt_stop_event.set()
            self.ptt_btn.configure(text="\U0001F399 PTT", bg=PANEL, state="disabled")
            self.event_queue.put(("status", "Transcribing..."))
            return

        if self.busy.is_set():
            return
        self.busy.set()
        self.ptt_recording = True
        self.ptt_stop_event = threading.Event()
        self.ptt_btn.configure(text="\u23F9 Stop", bg=RECORDING)
        self.send_btn.configure(state="disabled")
        self.event_queue.put(("status", "Recording... click PTT again to stop"))
        threading.Thread(target=self._ptt_worker, daemon=True).start()

    def _ptt_worker(self):
        stop_event = self.ptt_stop_event
        try:
            text = record_and_transcribe_toggle(stop_event)
        except Exception as e:
            self.event_queue.put(("error", f"(recording error: {e})"))
            self._finish_turn()
            return
        if not text.strip():
            self.event_queue.put(("status", "Heard nothing"))
            self._finish_turn()
            return
        self._run_turn(text)

    def _on_wake_detected(self):
        # Fires on WakeWordListener's own thread -- must not touch
        # widgets directly, and must not block that thread for long.
        if self.busy.is_set():
            return
        self.busy.set()
        self.event_queue.put(("controls_disable", None))
        self.event_queue.put(("status", "Wake word heard, listening..."))
        threading.Thread(target=self._wake_worker, daemon=True).start()

    def _wake_worker(self):
        try:
            text = record_and_transcribe_timed()
        except Exception as e:
            self.event_queue.put(("error", f"(recording error: {e})"))
            self._finish_turn()
            return
        if not text.strip():
            self._finish_turn()
            return
        self._run_turn(text)

    def _on_toggle_wake(self):
        self.wake_enabled = not self.wake_enabled
        self.listener.enabled = self.wake_enabled
        self.wake_btn.configure(text=f"\U0001F442 Wake: {'On' if self.wake_enabled else 'Off'}")

    def _on_toggle_view(self):
        self.wave_mode = "spectrum" if self.wave_mode == "scope" else "scope"

    def _on_quit(self):
        try:
            self.listener.stop()
        except Exception:
            pass
        try:
            tts_shutdown()
        except Exception:
            pass
        self.root.destroy()

    # -- Turn execution (runs on a worker thread) ---------------------------

    def _start_turn(self, user_text: str):
        self.busy.set()
        self._set_controls_enabled(False)
        threading.Thread(target=self._run_turn, args=(user_text,), daemon=True).start()

    def _run_turn(self, user_text: str):
        """
        Announces user_text itself, at the top, so every caller --
        typed (_start_turn spawns this on a new thread), PTT
        (_ptt_worker calls this directly, already on a worker
        thread), and wake word (_wake_worker, same) -- all get the
        transcript displayed consistently instead of only text mode
        showing it.
        """
        self.event_queue.put(("user_text", user_text))
        try:
            self.event_queue.put(("status", "Thinking..."))
            sentences = []
            started = False
            for sentence in self.brain.chat_streaming(user_text):
                if not started:
                    self.event_queue.put(("jarvis_start", None))
                    self.event_queue.put(("status", "Speaking..."))
                    started = True
                sentences.append(sentence)
                self.event_queue.put(("jarvis_chunk", sentence))
                speak(sentence)
            if started:
                self.event_queue.put(("jarvis_end", None))
            else:
                self.event_queue.put(("error", "(Jarvis had nothing to say)"))
            self.event_queue.put(("status", "Ready"))
        except Exception as e:
            self.event_queue.put(("error", f"(error: {e})"))
        finally:
            self._finish_turn()

    def _finish_turn(self):
        self.busy.clear()
        self.event_queue.put(("turn_done", None))


def main():
    root = tk.Tk()
    app = JarvisApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
