import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

import requests

from scraper import create_session, load_credentials, login, update_task_status

DEFAULT_TIMEOUT = (10, 30)
SLACK_REPLIES_URL = "https://slack.com/api/conversations.replies"
COMPLETE_RE = re.compile(r"\bComplete\b", re.IGNORECASE)
STREET_ABBREVIATIONS = {
    "st": "street",
    "rd": "road",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "cir": "circle",
    "pl": "place",
    "pkwy": "parkway",
    "blvd": "boulevard",
    "hwy": "highway",
    "trl": "trail",
    "ter": "terrace",
    "ave": "avenue",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}
STREET_SUFFIX_TOKENS = {
    "street",
    "road",
    "drive",
    "lane",
    "court",
    "circle",
    "place",
    "parkway",
    "boulevard",
    "highway",
    "trail",
    "terrace",
    "avenue",
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _load_latest_payload(base_dir: Path) -> Dict:
    candidates = [
        base_dir / "docs" / "data" / "latest.json",
        base_dir / "output" / "latest.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError("Could not find latest.json in docs/data or output")


def _tokenize(value: str) -> List[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return [token for token in normalized.split() if token]


def _canonicalize_tokens(tokens: List[str]) -> List[str]:
    return [STREET_ABBREVIATIONS.get(token, token) for token in tokens]


def _canonicalize_text(value: str) -> str:
    return " ".join(_canonicalize_tokens(_tokenize(value)))


def _address_variants(address: str) -> List[str]:
    raw = address.strip().lower()
    compact = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    canonical = _canonicalize_text(address)
    return [v for v in {raw, compact, canonical} if v]


def _address_core_tokens(tokens: List[str]) -> Set[str]:
    return {t for t in tokens if not t.isdigit() and t not in STREET_SUFFIX_TOKENS}


def _build_address_matcher(tasks: Dict[str, List[Dict]]) -> Dict:
    substring_index: Dict[str, List[int]] = {}
    street_key_to_task_ids: Dict[str, List[int]] = {}

    for bucket in ("Requests", "Open"):
        for task in tasks.get(bucket) or []:
            task_id = task.get("id")
            if task_id is None:
                continue
            addr_obj = task.get("property_address") or {}
            address = addr_obj.get("street1") or ""
            city = addr_obj.get("city") or ""
            state = addr_obj.get("state") or ""
            combined = ", ".join([part for part in [address, city, state] if part])
            for variant in _address_variants(combined) + _address_variants(address):
                substring_index.setdefault(variant, []).append(int(task_id))

            street_key = _canonicalize_text(address)
            if street_key:
                street_key_to_task_ids.setdefault(street_key, []).append(int(task_id))

    token_entries: List[Dict] = []
    for street_key, task_ids in street_key_to_task_ids.items():
        tokens = _canonicalize_tokens(_tokenize(street_key))
        numeric_tokens = {t for t in tokens if t.isdigit()}
        core_tokens = _address_core_tokens(tokens)
        token_entries.append(
            {
                "street_key": street_key,
                "task_ids": sorted(set(task_ids)),
                "numeric_tokens": numeric_tokens,
                "core_tokens": core_tokens,
            }
        )

    return {"substring_index": substring_index, "token_entries": token_entries}


def _load_processed_replies(path: Path) -> Set[str]:
    data = _load_json(path, {"processed_reply_ts": []})
    if isinstance(data, list):
        return set(str(x) for x in data)
    values = data.get("processed_reply_ts") if isinstance(data, dict) else []
    return set(str(x) for x in values or [])


def _save_processed_replies(path: Path, processed_ts: Set[str]) -> None:
    payload = {
        "processed_reply_ts": sorted(processed_ts),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _fetch_replies(token: str, channel: str, thread_ts: str) -> List[Dict]:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"channel": channel, "ts": thread_ts, "inclusive": "true", "limit": 200}
    response = requests.get(SLACK_REPLIES_URL, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data}")
    return data.get("messages") or []


def _find_matching_tasks(text: str, matcher: Dict) -> Tuple[str, List[int]]:
    lowered = text.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    canonical = _canonicalize_text(text)
    substring_index = matcher.get("substring_index") or {}

    for known_address, task_ids in substring_index.items():
        if known_address and (known_address in lowered or known_address in normalized):
            return known_address, task_ids

    reply_tokens = set(_canonicalize_tokens(_tokenize(canonical)))
    best_match_key = ""
    best_match_task_ids: List[int] = []
    best_score = -1

    for entry in matcher.get("token_entries") or []:
        numeric_tokens = entry.get("numeric_tokens") or set()
        core_tokens = entry.get("core_tokens") or set()
        if numeric_tokens and not numeric_tokens.issubset(reply_tokens):
            continue
        core_overlap = len(core_tokens.intersection(reply_tokens))
        min_core_required = 1 if len(core_tokens) <= 2 else 2
        if core_overlap < min_core_required:
            continue
        score = (10 if numeric_tokens else 0) + core_overlap
        if score > best_score:
            best_score = score
            best_match_key = str(entry.get("street_key") or "")
            best_match_task_ids = list(entry.get("task_ids") or [])

    if best_match_task_ids:
        return best_match_key, best_match_task_ids

    return "", []


def _extract_completed_task_ids(text: str, matcher: Dict) -> List[int]:
    if not COMPLETE_RE.search(text):
        return []
    _, task_ids = _find_matching_tasks(text, matcher)
    return sorted(set(task_ids))


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    meta_path = base_dir / "docs" / "data" / "slack_digest_meta.json"
    processed_path = base_dir / "docs" / "data" / "processed_replies.json"

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing SLACK_BOT_TOKEN")

    meta = _load_json(meta_path, None)
    if not isinstance(meta, dict) or not meta.get("channel") or not meta.get("thread_ts"):
        raise RuntimeError("Missing or invalid docs/data/slack_digest_meta.json")

    payload = _load_latest_payload(base_dir)
    tasks = payload.get("tasks") or {}
    matcher = _build_address_matcher(tasks)
    processed_ts = _load_processed_replies(processed_path)

    replies = _fetch_replies(token, str(meta["channel"]), str(meta["thread_ts"]))

    creds = load_credentials()
    updated_count = 0

    with create_session() as session:
        login(session, creds["email"], creds["password"])

        for reply in replies:
            reply_ts = str(reply.get("ts") or "")
            if not reply_ts or reply_ts == str(meta["thread_ts"]):
                continue
            if reply_ts in processed_ts:
                continue

            text = str(reply.get("text") or "")
            task_ids = _extract_completed_task_ids(text, matcher)
            for task_id in task_ids:
                update_task_status(session, creds, task_id, "completed")
                updated_count += 1

            processed_ts.add(reply_ts)

    _save_processed_replies(processed_path, processed_ts)
    print(f"Processed replies: {len(replies)} | Updated PadSplit tasks: {updated_count}")


if __name__ == "__main__":
    main()
