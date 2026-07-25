# core/__init__.py
# Makes core/ a proper Python package so its modules can be imported
# cleanly from any interface script sitting above it.
#
# Usage from any interface script:
#   from core.brain import JarvisBrain
#   from core.tools import ToolRegistry
#   from core.context import build_system_prompt
#   from core.tts import speak
#   from core.stt import transcribe
