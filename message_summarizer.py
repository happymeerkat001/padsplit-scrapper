#!/usr/bin/env python3
"""AI message summarizer for PadSplit data.

Reads padsplit_scraper/output/latest.json, sends the messages to MiniMax AI
for summarization, and posts the result to Discord via DISCORD_WEBHOOK_MESSAGES.
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
    "Here is the latest PadSplit message data. Identify ONLY the most urgent tenant "
    "messages. Respond with ONLY a JSON array, no prose or markdown fences. Each item "
    "must be an object with these string fields: \"chat_id\", \"summary\", and "
    "\"sent_at\". Use the chat's id exactly as provided. The summary should briefly "
    "explain why the message is urgent; sent_at should state when it was sent. Return "
    "[] when there are no urgent tenant messages.\n\n"
)


def format_room(chat: dict) -> str:
    occupancy = chat.get("occupancy") or {}
    room = occupancy.get("room") or {}
    room_number = room.get("roomNumber")
    return str(room_number) if room_number is not None else "Unknown"


def format_address(chat: dict) -> str:
    property_data = chat.get("property") or {}
    address = property_data.get("address") or {}
    if not address:
        return "Unknown"

    city = address.get("city") or {}
    state = city.get("state") or {}
    street1 = address.get("street1") or "Unknown"
    city_name = city.get("name") or "Unknown"
    state_name = state.get("name") or "Unknown"
    return f"{street1}, {city_name}, {state_name}"


def parse_urgent_items(raw: str) -> list[dict]:
    content = raw.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("\n", 1)[0].strip()

    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MiniMax response was not valid JSON") from exc

    if not isinstance(parsed, list):
        raise ValueError("MiniMax response must be a JSON array")

    urgent_items = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("MiniMax response items must be JSON objects")
        if not item.get("chat_id"):
            continue
        if not item.get("summary"):
            raise ValueError("MiniMax response items must include summary")
        urgent_items.append(item)
    return urgent_items


def render_summary(urgent_items: list[dict], messages_by_id: dict) -> str:
    if not urgent_items:
        return "No urgent tenant messages."

    lines = []
    for item in urgent_items:
        chat_id = item["chat_id"]
        chat = messages_by_id.get(chat_id)
        if chat is None:
            print(f"Skipping urgent item with unknown chat_id: {chat_id}")
            continue

        address = format_address(chat)
        room = format_room(chat)
        sent_at = item.get("sent_at") or "Unknown"
        summary = item.get("summary") or "Unknown"
        lines.append(f"{address} — Room {room} — {sent_at} — {summary}")

    return "\n".join(lines) if lines else "No urgent tenant messages."


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


DISCORD_MESSAGE_LIMIT = 2000
TRUNCATION_MARKER = "… [truncated]"


def truncate_for_discord(message: str) -> str:
    if len(message) <= DISCORD_MESSAGE_LIMIT:
        return message
    cutoff = DISCORD_MESSAGE_LIMIT - len(TRUNCATION_MARKER)
    return message[:cutoff] + TRUNCATION_MARKER


def send_to_discord(message: str) -> None:
    webhook = (os.getenv("DISCORD_WEBHOOK_MESSAGES") or "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_MESSAGES not set — skipping Discord send.")
        return

    payload = json.dumps({"content": truncate_for_discord(message)}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if 200 <= resp.getcode() < 300:
                print("Sent to Discord.")
            else:
                print(f"Discord webhook returned status {resp.getcode()}.")
    except urllib.error.HTTPError as exc:
        print(f"Discord webhook HTTP error: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"Discord webhook URL error: {exc}")


def main() -> None:
    if not DATA_PATH.exists():
        sys.exit(f"Data file not found: {DATA_PATH}")

    data = json.loads(DATA_PATH.read_text())
    prompt = PROMPT + json.dumps(data)
    messages_by_id = {
        message["id"]: message
        for message in data.get("messages", []) or []
        if isinstance(message, dict) and message.get("id") is not None
    }

    print("Sending data to MiniMax AI for processing...")
    summary = call_minimax(prompt)
    try:
        message = render_summary(
            parse_urgent_items(summary),
            messages_by_id,
        )
    except ValueError:
        retry_prompt = (
            prompt
            + "\nYour last response was not valid JSON. Respond with ONLY a JSON array, no other text."
        )
        summary = call_minimax(retry_prompt)
        try:
            message = render_summary(
                parse_urgent_items(summary),
                messages_by_id,
            )
        except ValueError:
            message = "⚠️ Formatting fallback (AI response was not valid JSON):\n\n" + summary

    print("\n" + "=" * 50)
    print(f"Discord Message:\n{message}")
    print("=" * 50 + "\n")

    send_to_discord(message)


if __name__ == "__main__":
    main()
