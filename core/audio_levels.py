"""
core/audio_levels.py

Shared ring buffers holding recent raw audio samples for the mic
input (YOUR VOICE panel) and TTS output (JARVIS panel), so the
terminal UI's oscilloscope traces can plot real audio instead of a
synthetic animation.

Kept deliberately dumb: a fixed-size deque per channel, guarded by a
lock, written to by whichever thread is producing audio (PortAudio's
callback thread for mic input, a small pacing thread for TTS
playback) and read by the UI's refresh thread. No processing happens
here beyond storing samples — downsampling/normalizing for display
happens in the UI, since that's the only place that knows the target
trace width.
"""

import collections
import threading


class AudioRingBuffer:
    def __init__(self, maxlen: int = 16000):
        # ~1 second of audio at a 16kHz sample rate by default --
        # plenty for a scope trace, which only ever looks at a small
        # recent window, not the whole buffer.
        self._buf = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, samples) -> None:
        """samples: any 1D iterable of numeric values (int16 PCM is
        the expected case, but anything numeric works)."""
        with self._lock:
            self._buf.extend(samples)

    def snapshot(self, n: int = 512):
        """Return up to the last n samples as a list, oldest first.
        Returns None if the buffer is empty, so callers can fall back
        to a synthetic wave instead of plotting a flat/empty trace."""
        with self._lock:
            if not self._buf:
                return None
            data = list(self._buf)[-n:]
        return data

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


# One buffer per waveform panel.
mic_level_buffer = AudioRingBuffer()
jarvis_level_buffer = AudioRingBuffer()
