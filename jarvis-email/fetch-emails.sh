#!/bin/bash
# =============================================================================
# fetch-emails.sh — wrapper that loads Gmail credentials from a private,
# locked-down env file and runs fetch-emails.py
#
# Credentials live in ~/.config/jarvis/email.env — NOT in .bashrc, NOT in
# jarvis.py, NOT anywhere Jarvis's conversation loop touches directly.
# =============================================================================

ENV_FILE="$HOME/.config/jarvis/email.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing credentials file: $ENV_FILE"
    echo ""
    echo "Create it with:"
    echo "  mkdir -p ~/.config/jarvis"
    echo "  nano $ENV_FILE"
    echo ""
    echo "Contents should be:"
    echo '  GMAIL_ADDRESS="your_address@gmail.com"'
    echo '  GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"'
    echo ""
    echo "Then lock it down:"
    echo "  chmod 600 $ENV_FILE"
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
export GMAIL_ADDRESS GMAIL_APP_PASSWORD

cd "$(dirname "$0")" || exit 1
python3 fetch-emails.py
