"""
core/wakeword.py

Wake word detection for Jarvis using openwakeword.
Listens continuously for "Jarvis" and fires a callback when detected.
Runs in a background thread, controlled by WakeWordListener.enabled.
"""

import queue
import sys
import threading
import warnings
from typing import Callable

warnings.filterwarnings("ignore", category=UserWarning)

try:
    import numpy as np
except ImportError:
    print("Missing dependency: pip install numpy")
    sys.exit(1)

try:
    import sounddevice as sd
except ImportError:
    print("Missing dependency: pip install sounddevice")
    sys.exit(1)

try:
    from openwakeword.model import Model
except ImportError:
    print("Missing dependency: pip install openwakeword")
    sys.exit(1)

SAMPLE_RATE = 16000
CHUNK_SIZE = 1280
THRESHOLD = 0.5
COOLDOWN_SECONDS = 3.0
IDLE_SLEEP = 0.05


class WakeWordListener:

    def __init__(self, on_detected: Callable[[], None]):
        self.on_detected = on_detected
        self.enabled = False
        self._stop_event = threading.Event()
        self._thread = None
        self._model = None
        self._cooldown = False
        self._cooldown_lock = threading.Lock()  # prevent race on cooldown flag

    def _load_model(self):
        if self._model is None:
            print("\033[2m  Loading wake word model...\033[0m")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._model = Model(
                    wakeword_model_paths=[],
                    enable_speex_noise_suppression=False,
                )

    def _listen_loop(self):
        self._load_model()
        audio_queue = queue.Queue()

        def audio_callback(indata, frames, time, status):
            audio_queue.put(indata[:, 0].copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            callback=audio_callback,
        ):
            while not self._stop_event.is_set():
                if not self.enabled:
                    # Drain stale audio so it can't fire on re-enable
                    try:
                        while not audio_queue.empty():
                            audio_queue.get_nowait()
                    except Exception:
                        pass
                    self._stop_event.wait(timeout=0.1)
                    continue

                try:
                    chunk = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    prediction = self._model.predict(chunk)

                score = max(
                    (v for k, v in prediction.items()
                     if "jarvis" in k.lower()),
                    default=0.0,
                )

                if score >= THRESHOLD:
                    with self._cooldown_lock:
                        if not self._cooldown:
                            self._cooldown = True
                            # Fire in a thread so the listen loop keeps running
                            threading.Thread(
                                target=self._fire_detected, daemon=True
                            ).start()
                else:
                    self._stop_event.wait(timeout=IDLE_SLEEP)

    def _fire_detected(self):
        """Run callback then schedule cooldown reset."""
        try:
            self.on_detected()
        finally:
            # Reset cooldown after delay — use a timer so listen loop
            # is never blocked waiting for the callback to finish
            threading.Timer(COOLDOWN_SECONDS, self._reset_cooldown).start()

    def _reset_cooldown(self):
        with self._cooldown_lock:
            self._cooldown = False

    def start(self):
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    @property
    def status(self) -> str:
        return "listening" if self.enabled else "standby"
