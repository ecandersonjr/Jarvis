#!/usr/bin/env python3
"""
jarvis-unified.py — Phase 4 unified Jarvis interface.

Three input modes, toggled mid-session:

  TEXT mode (default):
    Type to talk. Jarvis prints AND speaks his reply.

  PTT mode:
    Press Enter to record, speak, press Enter to stop.
    Jarvis prints and speaks his reply.

  WAKE WORD mode:
    Say "Jarvis" — he listens for a fixed duration,
    then prints and speaks his reply.

Single conversation history and context across all mode switches.
Full toolbox available in every mode.

Commands (always available regardless of mode):
  t + Enter  → switch to text mode
  p + Enter  → switch to PTT mode
  w + Enter  → switch to wake word mode
  q + Enter  → quit
"""

import threading
from enum import Enum

from core.brain import JarvisBrain
from core.stt import (
    load_whisper_model,
    record_and_transcribe,
    record_and_transcribe_timed,
)
from core.tts import speak, shutdown as tts_shutdown
from core.wakeword import WakeWordListener

BANNER = "\033[1;36m" + "─" * 60 + "\033[0m"
WAKE_RECORD_SECONDS = 5.0


class Mode(Enum):
    TEXT = "text"
    PTT = "ptt"
    WAKE = "wake"


MODE_LABELS = {
    Mode.TEXT: "\033[2m○ TEXT\033[0m   — type to talk",
    Mode.PTT: "\033[1;32m● PTT\033[0m    — Enter=record",
    Mode.WAKE: "\033[1;36m● WAKE\033[0m   — say 'Jarvis'",
}

MODE_ANNOUNCEMENTS = {
    Mode.TEXT: ("Text mode active, Sir. Giving me the silent treatment I see."),
    Mode.PTT: ("Push to talk mode, Sir. I'll try not to judge the pauses."),
    Mode.WAKE: ("Wake mode active, Sir. I am listening."),
}


def print_status(mode: Mode):
    controls = "  t=text  p=PTT  w=wake  q=quit"
    print(f"  {MODE_LABELS[mode]}{controls}")


def handle_response(brain: JarvisBrain, text: str):
    """Shared response handler — prints and speaks in all modes."""
    if not text or not text.strip():
        print("\033[2m  (heard nothing — try again)\033[0m\n")
        return

    print(f"\033[1;32myou:\033[0m {text}")
    print(f"\033[1;36mJarvis:\033[0m ", end="", flush=True)

    for sentence in brain.chat_streaming(text):
        print(sentence, end=" ", flush=True)
        speak(sentence)

    print("\n")


def switch_mode(
    new_mode: Mode,
    current_mode: Mode,
    brain: JarvisBrain,
    listener: WakeWordListener,
) -> Mode:
    """
    Switch to a new mode, announce the change verbosely,
    and update the wake word listener state.
    """
    if new_mode == current_mode:
        return current_mode

    # Update wake word listener
    listener.enabled = new_mode == Mode.WAKE

    # Announce the mode change in Jarvis's voice
    announcement = MODE_ANNOUNCEMENTS[new_mode]
    print(f"\n\033[1;36mJarvis:\033[0m ", end="", flush=True)
    print(announcement)
    speak(announcement)
    print()

    print_status(new_mode)
    print()

    return new_mode


def main():
    brain = JarvisBrain(voice_mode=True)
    current_mode = Mode.TEXT
    wake_active = threading.Event()

    def on_wake_detected():
        if wake_active.is_set():
            return
        if current_mode != Mode.WAKE:
            return
        wake_active.set()
        try:
            print("\n\033[1;36m  ◉ Jarvis heard — listening...\033[0m")
            text = record_and_transcribe_timed(WAKE_RECORD_SECONDS)
            handle_response(brain, text)
            print_status(current_mode)
            print()
        finally:
            wake_active.clear()

    listener = WakeWordListener(on_detected=on_wake_detected)

    print(BANNER)
    print("\033[1;36m  Jarvis — unified interface\033[0m")
    print(BANNER + "\n")

    # Pre-load whisper at startup
    load_whisper_model()
    listener.start()

    # Opening remark from Jarvis
    opening = "Good to see you, Sir. I'm ready when you are."
    print(f"\033[1;36mJarvis:\033[0m {opening}")
    speak(opening)
    print()
    print_status(current_mode)
    print()

    try:
        while True:
            try:
                user_input = input("").strip()
            except (EOFError, KeyboardInterrupt):
                break

            lower = user_input.lower()

            # Mode switching commands
            if lower == "q":
                break
            elif lower == "t":
                current_mode = switch_mode(Mode.TEXT, current_mode, brain, listener)
                continue
            elif lower == "p":
                current_mode = switch_mode(Mode.PTT, current_mode, brain, listener)
                continue
            elif lower == "w":
                current_mode = switch_mode(Mode.WAKE, current_mode, brain, listener)
                continue

            # Mode-specific input handling
            if current_mode == Mode.TEXT:
                if user_input:
                    handle_response(brain, user_input)
                    print_status(current_mode)
                    print()

            elif current_mode == Mode.PTT:
                if user_input == "":
                    if wake_active.is_set():
                        print("\033[2m  Already listening — please wait.\033[0m\n")
                        continue
                    text = record_and_transcribe()
                    handle_response(brain, text)
                    print_status(current_mode)
                    print()

            elif current_mode == Mode.WAKE:
                if user_input == "":
                    print(
                        "\033[2m  Wake word active — say 'Jarvis' or press p for PTT.\033[0m\n"
                    )

    finally:
        listener.stop()
        tts_shutdown()
        farewell = "Until next time, Sir."
        print(f"\nJarvis: {farewell}")
        speak(farewell)


if __name__ == "__main__":
    main()
