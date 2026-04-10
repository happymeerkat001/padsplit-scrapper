import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests

DEFAULT_TIMEOUT = (10, 30)
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


def _load_latest_payload(base_dir: Path) -> Dict:
    candidates = [
        base_dir / "docs" / "data" / "latest.json",
        base_dir / "output" / "latest.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError("Could not find latest.json in docs/data or output")


def _format_task_line(task: Dict) -> str:
    address = (task.get("property_address") or {}).get("street1", "Unknown address")
    location = task.get("location") or "General"
    details = (task.get("details") or "").strip().replace("\n", " ")
    if len(details) > 90:
        details = details[:87] + "..."
    return f"- {address} | {location} | {details}"


def _build_digest(payload: Dict) -> str:
    tasks = payload.get("tasks") or {}
    requests_items: List[Dict] = tasks.get("Requests") or []
    open_items: List[Dict] = tasks.get("Open") or []

    lines = [
        f"Daily maintenance digest ({datetime.now(timezone.utc).strftime('%Y-%m-%d UTC')})",
        f"Requests: {len(requests_items)} | Open: {len(open_items)}",
        "Reply in this thread with: <address> Complete",
        "",
        "Top requests:",
    ]

    for task in requests_items[:15]:
        lines.append(_format_task_line(task))

    if open_items:
        lines.append("")
        lines.append("Open tasks:")
        for task in open_items[:15]:
            lines.append(_format_task_line(task))

    return "\n".join(lines)


def main() -> None:
    token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("SLACK_CHANNEL_ID")
    if not token or not channel:
        raise RuntimeError("Missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID")

    base_dir = Path(__file__).resolve().parent
    payload = _load_latest_payload(base_dir)
    text = _build_digest(payload)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
    response = requests.post(SLACK_POST_URL, headers=headers, json=body, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data}")

    meta = {
        "channel": data.get("channel", channel),
        "thread_ts": data.get("ts"),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    meta_path = base_dir / "docs" / "data" / "slack_digest_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
