"""
core/stt.py

Speech-to-text for Jarvis via OpenAI Whisper (local, free).
Records from the default audio input and transcribes to text.

Two recording modes:
  record_and_transcribe()       — PTT, waits for Enter to stop
  record_and_transcribe_timed() — fixed duration, used by wake word mode

Uses whisper 'tiny' model for best CPU performance on the 7320.
Accuracy is slightly lower than 'base' but response time is
meaningfully faster — the right tradeoff for a voice assistant.
Switch WHISPER_MODEL_SIZE to 'base' or 'small' if accuracy matters
more than speed for your use case.
"""

import sys
import tempfile
import time
import warnings
from pathlib import Path

# Suppress ONNX/whisper warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

try:
    import numpy as np
    import sounddevice as sd
    import scipy.io.wavfile as wavfile
except ImportError:
    print("Missing dependencies: pip install sounddevice scipy numpy")
    sys.exit(1)

try:
    import whisper
except ImportError:
    print("Missing dependency: pip install openai-whisper")
    sys.exit(1)

from core.audio_levels import mic_level_buffer

SAMPLE_RATE = 16000
WHISPER_MODEL_SIZE = "tiny"   # fastest on CPU — change to "base" or
                               # "small" if accuracy is more important

_whisper_model = None


def load_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        print(f"\033[2m  Loading whisper ({WHISPER_MODEL_SIZE})...\033[0m")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
    return _whisper_model


def _save_audio(frames: list, path: Path):
    audio = np.concatenate(frames, axis=0)
    wavfile.write(path, SAMPLE_RATE, audio)


def transcribe(audio_path: Path) -> str:
    model = load_whisper_model()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = model.transcribe(str(audio_path), fp16=False)
    return result["text"].strip()


def record_until_enter() -> Path:
    """PTT — record until user presses Enter."""
    print("\033[1;32m●  Recording... press Enter to stop\033[0m")
    frames = []
    mic_level_buffer.clear()

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())
        # mono channel, raw int16 samples for the waveform panel
        mic_level_buffer.push(indata[:, 0])

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=callback,
    )
    with stream:
        input()
    mic_level_buffer.clear()

    print("\033[2m  ...transcribing\033[0m")
    tmp_path = Path(tempfile.mktemp(suffix=".wav"))
    _save_audio(frames, tmp_path)
    return tmp_path


def record_for_duration(seconds: float = 5.0) -> Path:
    """
    Wake word mode — record for a fixed duration without waiting for Enter.
    Returns path to a temporary WAV file.
    """
    print(f"\033[1;32m●  Listening for {seconds:.0f}s...\033[0m")
    frames = []
    mic_level_buffer.clear()

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())
        mic_level_buffer.push(indata[:, 0])

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=callback,
    ):
        time.sleep(seconds)
    mic_level_buffer.clear()

    print("\033[2m  ...transcribing\033[0m")
    tmp_path = Path(tempfile.mktemp(suffix=".wav"))
    _save_audio(frames, tmp_path)
    return tmp_path


def record_and_transcribe() -> str:
    """PTT — record until Enter, transcribe, clean up."""
    audio_path = record_until_enter()
    try:
        return transcribe(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)


def record_and_transcribe_timed(seconds: float = 5.0) -> str:
    """Wake word — record for fixed duration, transcribe, clean up."""
    audio_path = record_for_duration(seconds)
    try:
        return transcribe(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)
