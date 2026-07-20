#!/usr/bin/env python3
"""AI message summarizer for PadSplit data.

Reads padsplit_scraper/output/latest.json, sends the messages to MiniMax AI
for summarization, and posts the result to Slack via SLACK_WEBHOOK_MESSAGES.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


_BASE = Path(__file__).parent
DATA_PATH = next(
    (p for p in [
        _BASE / "padsplit_scraper" / "output" / "latest.json",
        _BASE / "docs" / "data" / "latest.json",
    ] if p.exists()),
    _BASE / "padsplit_scraper" / "output" / "latest.json",  # keep original for error message
)

PROMPT = (
    "Here is the latest PadSplit message data. Please summarize ONLY the most urgent "
    "tenant messages. CRITICAL: For every message you summarize, you MUST explicitly "
    "state the date and time it was sent so I know if it is outdated. "
    "Also include the tenant's room number (from occupancy.room.roomNumber) in each summary.\n\n"
)


def call_minimax(prompt: str) -> str:
    api_key = (os.getenv("MINIMAX_API_KEY") or "").strip()
    if not api_key:
        sys.exit("Missing MINIMAX_API_KEY in environment")
    model = (os.getenv("MINIMAX_MODEL") or "MiniMax-M2.5").strip()

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.minimax.io/v1/text/chatcompletion_v2",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 529) and attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                print(f"MiniMax returned {exc.code}, retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                req = urllib.request.Request(
                    "https://api.minimax.io/v1/text/chatcompletion_v2",
                    data=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                continue
            sys.exit(f"MiniMax API error: {exc.code} {exc.reason}")
        except urllib.error.URLError as exc:
            if attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                print(f"MiniMax request failed, retrying in {wait}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                req = urllib.request.Request(
                    "https://api.minimax.io/v1/text/chatcompletion_v2",
                    data=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                continue
            sys.exit(f"MiniMax request failed: {exc}")
    sys.exit("MiniMax API: all retries exhausted")


def send_to_slack(message: str) -> None:
    webhook = (os.getenv("SLACK_WEBHOOK_MESSAGES") or "").strip()
    if not webhook:
        print("SLACK_WEBHOOK_MESSAGES not set — skipping Slack send.")
        return

    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if 200 <= resp.getcode() < 300:
                print("Sent to Slack.")
            else:
                print(f"Slack webhook returned status {resp.getcode()}.")
    except urllib.error.HTTPError as exc:
        print(f"Slack webhook HTTP error: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"Slack webhook URL error: {exc}")


def main() -> None:
    if not DATA_PATH.exists():
        sys.exit(f"Data file not found: {DATA_PATH}")

    data = json.loads(DATA_PATH.read_text())
    prompt = PROMPT + json.dumps(data)

    print("Sending data to MiniMax AI for processing...")
    summary = call_minimax(prompt)

    print("\n" + "=" * 50)
    print(f"AI Response:\n{summary}")
    print("=" * 50 + "\n")

    send_to_slack(summary)


if __name__ == "__main__":
    main()
