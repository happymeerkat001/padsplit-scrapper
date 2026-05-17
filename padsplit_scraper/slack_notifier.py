import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import firebase_admin
import requests
from firebase_admin import credentials, firestore

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


def _init_firestore_app() -> firebase_admin.App:
    if firebase_admin._apps:
        return firebase_admin.get_app()

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        return firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))

    google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if google_credentials:
        return firebase_admin.initialize_app(credentials.Certificate(google_credentials))

    raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS")


def _load_ac_filter_dates(app: firebase_admin.App) -> List[Dict]:
    client = firestore.client(app=app)
    docs = client.collection("property_codes").stream()
    overdue: List[Dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    for doc in docs:
        data = doc.to_dict() or {}
        raw = (data.get("ac_filter_date") or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        if dt < cutoff:
            overdue.append({
                "slug": doc.id,
                "address": data.get("address") or data.get("property_address") or doc.id,
                "ac_filter_date": raw,
            })

    overdue.sort(key=lambda item: item["ac_filter_date"])
    return overdue


def post_slack_message(text: str, *, token: Optional[str] = None, channel: Optional[str] = None) -> Dict:
    token = token or os.getenv("SLACK_BOT_TOKEN")
    channel = channel or os.getenv("SLACK_CHANNEL_ID")
    if not token or not channel:
        raise RuntimeError("Missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID")

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

    return data


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    payload = _load_latest_payload(base_dir)
    text = _build_digest(payload)
    app = _init_firestore_app()
    overdue = _load_ac_filter_dates(app)
    if overdue:
        lines = ["", "AC Filter overdue (>90 days):"]
        for item in overdue:
            lines.append(f"- {item['address']} ({item['slug']}): last changed {item['ac_filter_date']}")
        text += "\n".join(lines)
    data = post_slack_message(text)

    meta = {
        "channel": data.get("channel", os.getenv("SLACK_CHANNEL_ID")),
        "thread_ts": data.get("ts"),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    meta_path = base_dir / "docs" / "data" / "slack_digest_meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()
