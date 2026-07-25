#!/usr/bin/env python3
"""
jarvis-voice.py — voice interface for Jarvis.

Two exclusive input modes toggled by 'w' + Enter:

  PTT mode (default):
    Press Enter to start, speak, press Enter to stop.

  Wake word mode:
    Say "Jarvis" — records for a fixed duration then responds.
    Enter key is disabled to prevent mic collisions.

Streaming TTS: Jarvis speaks each sentence as it arrives from the
API rather than waiting for the complete response. Piper starts
speaking sentence 1 while Claude is still generating sentence 2.
"""

import threading

from core.brain import JarvisBrain
from core.stt import load_whisper_model, record_and_transcribe, record_and_transcribe_timed
from core.tts import speak, shutdown as tts_shutdown
from core.wakeword import WakeWordListener

BANNER = "\033[1;36m" + "─" * 50 + "\033[0m"
WAKE_RECORD_SECONDS = 5.0


def print_status(wake_enabled: bool):
    if wake_enabled:
        mode = "\033[1;32m● WAKE WORD\033[0m  — say 'Jarvis' | w=PTT | q=quit"
    else:
        mode = "\033[2m○ PTT mode\033[0m   — Enter=talk  | w=wake | q=quit"
    print(f"  {mode}")


def handle_response(brain: JarvisBrain, text: str):
    """
    Streaming response handler — speaks each sentence as it arrives.
    Used by both PTT and wake word paths.
    """
    if not text:
        print("\033[2m  (heard nothing — try again)\033[0m\n")
        return

    print(f"\033[1;32myou:\033[0m {text}")
    print(f"\033[1;36mJarvis:\033[0m ", end="", flush=True)

    first = True
    full_reply = ""

    for sentence in brain.chat_streaming(text):
        if first:
            print(sentence, end=" ", flush=True)
            first = False
        else:
            print(sentence, end=" ", flush=True)
        full_reply += sentence + " "
        speak(sentence)  # speak immediately — next sentence generates while this plays

    print("\n")  # newline after full reply


def main():
    brain = JarvisBrain(voice_mode=True)
    wake_active = threading.Event()

    def on_wake_detected():
        if wake_active.is_set():
            return
        wake_active.set()
        try:
            print("\n\033[1;36m  ◉ Jarvis heard — listening...\033[0m")
            text = record_and_transcribe_timed(WAKE_RECORD_SECONDS)
            handle_response(brain, text)
            print_status(listener.enabled)
            print()
        finally:
            wake_active.clear()

    listener = WakeWordListener(on_detected=on_wake_detected)

    print(BANNER)
    print("\033[1;36m  Jarvis voice interface\033[0m")
    print(BANNER + "\n")

    load_whisper_model()
    listener.start()
    print_status(listener.enabled)
    print()

    try:
        while True:
            try:
                user_input = input("").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if user_input == "q":
                break

            elif user_input == "w":
                listener.enabled = not listener.enabled
                print_status(listener.enabled)
                print()

            elif user_input == "":
                if listener.enabled:
                    print("\033[2m  Wake word active — press w to switch to PTT.\033[0m\n")
                    continue
                if wake_active.is_set():
                    print("\033[2m  Already listening — please wait.\033[0m\n")
                    continue
                text = record_and_transcribe()
                handle_response(brain, text)
                print_status(listener.enabled)
                print()

    finally:
        listener.stop()
        tts_shutdown()
        print("\nJarvis: Until next time, Sir.")


if __name__ == "__main__":
    main()
