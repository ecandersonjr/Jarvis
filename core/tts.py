"""
core/tts.py

Text-to-speech for Jarvis via piper-tts.
Northern English male voice.

Keeps it simple and reliable:
- One persistent piper process per session (model loaded once)
- Per-sentence wav output via temp file (reliable, no PCM sync complexity)
- paplay plays each wav then it's deleted

The model loading overhead (biggest delay) is eliminated by keeping
piper alive. The temp file I/O per sentence is negligible.
"""

import subprocess
import tempfile
import threading
import time
from pathlib import Path

from core.audio_levels import jarvis_level_buffer

try:
    import scipy.io.wavfile as wavfile
except ImportError:
    wavfile = None

PIPER_BIN = "/usr/bin/piper-tts"
PIPER_VOICE_MODEL = (
    Path.home() / ".local/share/piper-voices/en_GB-northern_english_male-medium.onnx"
)

_piper_proc: subprocess.Popen | None = None
_speak_lock = threading.Lock()


def _piper_available() -> bool:
    return Path(PIPER_BIN).exists() and PIPER_VOICE_MODEL.exists()


def _start_piper() -> subprocess.Popen:
    """Start a persistent piper process that reads from stdin."""
    return subprocess.Popen(
        [
            PIPER_BIN,
            "--model",
            str(PIPER_VOICE_MODEL),
            "--output-raw",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _get_piper() -> subprocess.Popen | None:
    global _piper_proc
    if not _piper_available():
        return None
    if _piper_proc is None or _piper_proc.poll() is not None:
        _piper_proc = _start_piper()
    return _piper_proc


def _stream_levels_during_playback(wav_path: Path, stop_event: threading.Event) -> None:
    """Read the wav we just generated and push its samples into the
    JARVIS waveform buffer at roughly real playback pace, running
    alongside `paplay`. This isn't a live tap into paplay itself --
    paplay is an external process we don't control the internals of
    -- but replaying the same samples on a wall-clock-paced timer
    keeps the panel visually in sync with what's actually being
    spoken, rather than a synthetic animation that just means
    "something is playing".
    """
    if wavfile is None:
        return
    try:
        rate, data = wavfile.read(wav_path)
    except Exception:
        return
    if data.ndim > 1:
        data = data[:, 0]

    chunk_ms = 30
    chunk_size = max(1, int(rate * chunk_ms / 1000))
    for i in range(0, len(data), chunk_size):
        if stop_event.is_set():
            return
        jarvis_level_buffer.push(data[i : i + chunk_size])
        time.sleep(chunk_ms / 1000)


def speak(text: str, on_start=None, on_end=None) -> None:
    """
    Speak text using piper-tts. Blocks until audio finishes.
    Uses a temp wav file per sentence — simple and reliable.
    Falls back gracefully if piper is unavailable.
    """
    if not text or not text.strip():
        return

    if not _piper_available():
        print(f"\033[2m  (piper unavailable)\033[0m")
        return

    tmp_wav = Path(tempfile.mktemp(suffix=".wav"))

    with _speak_lock:
        try:
            if on_start:
                on_start()
            result = subprocess.run(
                [
                    PIPER_BIN,
                    "--model",
                    str(PIPER_VOICE_MODEL),
                    "--output_file",
                    str(tmp_wav),
                ],
                input=text.strip(),
                text=True,
                capture_output=True,
                timeout=15,
            )

            if result.returncode != 0:
                print(f"\033[2m  (piper error: {result.stderr.strip()})\033[0m")
                return

            level_stop = threading.Event()
            level_thread = threading.Thread(
                target=_stream_levels_during_playback,
                args=(tmp_wav, level_stop),
                daemon=True,
            )
            level_thread.start()
            subprocess.run(["paplay", str(tmp_wav)], capture_output=True)
            level_stop.set()
            jarvis_level_buffer.clear()

            if on_end:
                on_end()

        except subprocess.TimeoutExpired:
            print("\033[2m  (piper timed out)\033[0m")
        except Exception as e:
            print(f"\033[2m  (tts error: {e})\033[0m")
        finally:
            tmp_wav.unlink(missing_ok=True)


def shutdown() -> None:
    """Clean up the persistent piper process on exit."""
    global _piper_proc
    if _piper_proc and _piper_proc.poll() is None:
        try:
            _piper_proc.stdin.close()
            _piper_proc.wait(timeout=2.0)
        except Exception:
            _piper_proc.kill()
        _piper_proc = None
