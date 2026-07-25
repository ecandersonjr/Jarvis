#!/usr/bin/env python3
"""
fetch-emails.py — pulls the last N personal Gmail messages via IMAP and
saves a clean snapshot to a local JSON file for Jarvis to summarize.

This script is the ONLY thing that touches your email credentials.
Jarvis himself never sees your password — he only ever reads the
output file this script produces.

Setup:
    1. Generate a Gmail App Password (not your normal password):
       https://myaccount.google.com/apppasswords
       (Requires 2-Step Verification to be enabled on your Google account)

    2. export GMAIL_ADDRESS="your_address@gmail.com"
       export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
       (add both to ~/.bashrc, or better — into a dedicated env file,
       see fetch-emails.sh)

Run manually:
    python3 fetch-emails.py

Or on a schedule via systemd timer (see README.md in this folder).
"""

import email
import imaplib
import json
import os
import sys
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
NUM_EMAILS = 15  # how many recent emails to fetch, regardless of read status
OUTPUT_FILE = Path(__file__).parent / "inbox-snapshot.json"
MAX_BODY_CHARS = 1500  # truncate long email bodies before saving


def decode_mime_words(s: str) -> str:
    """Decode MIME-encoded email headers (subject, from) into plain text."""
    if not s:
        return ""
    decoded_parts = decode_header(s)
    result = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="replace")
        else:
            result += part
    return result


def get_body(msg: email.message.Message) -> str:
    """Extract a plain-text body from an email message, best effort."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                except Exception:
                    continue
        return "(no plain-text body found)"
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return "(could not decode body)"


def fetch_recent_emails(address: str, app_password: str, count: int) -> list[dict]:
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(address, app_password)
    mail.select("INBOX")

    status, data = mail.search(None, "ALL")
    if status != "OK":
        raise RuntimeError("IMAP search failed")

    all_ids = data[0].split()
    recent_ids = all_ids[-count:] if len(all_ids) > count else all_ids
    recent_ids.reverse()  # newest first

    emails = []
    for msg_id in recent_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = decode_mime_words(msg.get("Subject", "(no subject)"))
        sender = decode_mime_words(msg.get("From", "(unknown sender)"))
        date = msg.get("Date", "")

        body = get_body(msg)
        body = " ".join(body.split())  # collapse whitespace
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "... (truncated)"

        emails.append(
            {
                "subject": subject,
                "from": sender,
                "date": date,
                "body": body,
            }
        )

    mail.logout()
    return emails


def main():
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not address or not app_password:
        print("GMAIL_ADDRESS and/or GMAIL_APP_PASSWORD not set.")
        print("See the setup instructions at the top of this script.")
        sys.exit(1)

    print(f"Fetching last {NUM_EMAILS} emails for {address}...")

    try:
        emails = fetch_recent_emails(address, app_password, NUM_EMAILS)
    except imaplib.IMAP4.error as e:
        print(f"IMAP login/fetch failed: {e}")
        print("Double check your App Password is correct and current.")
        sys.exit(1)

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "account": address,
        "count": len(emails),
        "emails": emails,
    }

    OUTPUT_FILE.write_text(json.dumps(snapshot, indent=2))
    # Lock the file down — it contains email content, keep it private
    os.chmod(OUTPUT_FILE, 0o600)

    print(f"Saved {len(emails)} emails to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
