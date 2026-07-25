#!/bin/bash
# =============================================================================
# run.sh — Jarvis launcher
#
# Usage:
#     ./run.sh        -> jarvis-unified.py (default)
#     ./run.sh ui     -> jarvis-ui.py (Phase 5 UI)
# =============================================================================

cd "$(dirname "$0")" || exit 1

export ANTHROPIC_API_KEY="Enter your API Key here"
export PULSE_RUNTIME_PATH=/run/user/$(id -u)/pulse
export PULSE_SERVER=unix:${PULSE_RUNTIME_PATH}/native

if [ ! -f .venv/bin/activate ]; then
  echo "venv not found."
  read -p "Press enter to close..."
  exit 1
fi

source .venv/bin/activate

case "$1" in
ui)
  python3 jarvis-ui.py
  ;;
*)
  python3 jarvis-unified.py
  ;;
esac

echo ""
echo "Jarvis exited. Press enter to close..."
read
