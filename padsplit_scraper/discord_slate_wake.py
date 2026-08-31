#!/usr/bin/env python3
"""PadSplit Ops Discord gateway: Slate mention-wake plus task Done buttons.

Uses the existing PadSplit Ops Discord application (DISCORD_BOT_TOKEN).
Mention-wake is notify-only and never replies in Discord. Button taps edit
the same task message (no chatty replies). Tapping Done is a claim only —
not proof for pay.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_TIMEOUT = (10, 30)
USER_AGENT = "PadSplitOps (https://github.com/happymeerkat001/padsplit-scrapper, 1.0)"

# Existing PadSplit Ops application in guild Liaison Ops. Same bot as the REST notifier.
PADSPLIT_OPS_APPLICATION_ID = "1541076271675605043"
LIAISON_OPS_GUILD_ID = "1540475742104719380"
ASK_AI_AGENT_CHANNEL_ID = "1542641481393774612"
GENERAL_CHANNEL_ID = "1540475742800842884"
WAKE_CHANNEL_IDS = frozenset({ASK_AI_AGENT_CHANNEL_ID, GENERAL_CHANNEL_ID})
CHANNEL_NAME_FALLBACKS = {
    ASK_AI_AGENT_CHANNEL_ID: "ask-ai-agent",
    GENERAL_CHANNEL_ID: "communication-mgmt",
}

# #to-do-joe and PadSplit ticket / temp tasks. Buttons only here.
TODO_JOE_CHANNEL_ID = "1541435122006630471"
AI_TASKS_TEMP_CHANNEL_ID = "1540475874955231343"
TASK_BUTTON_CHANNEL_IDS = frozenset({TODO_JOE_CHANNEL_ID, AI_TASKS_TEMP_CHANNEL_ID})
TASK_BUTTON_CHANNEL_NAMES = {
    TODO_JOE_CHANNEL_ID: "to-do-joe",
    AI_TASKS_TEMP_CHANNEL_ID: "ai-tasks-temp",
}
TASK_DONE_CUSTOM_ID = "task_done"
TASK_UNDO_CUSTOM_ID = "task_undo"
TASK_DONE_MARKER = "✅ Done"
UNDO_WINDOW_SEC = 5 * 60
STALE_OPEN_AFTER_SEC = 48 * 3600
RECENTLY_TICKED_AFTER_SEC = 24 * 3600
LIST_TASKS_LIMIT = 100

# Interaction types / callbacks (Discord API v10).
INTERACTION_TYPE_MESSAGE_COMPONENT = 3
CALLBACK_DEFERRED_UPDATE_MESSAGE = 6
CALLBACK_UPDATE_MESSAGE = 7
COMPONENT_ACTION_ROW = 1
COMPONENT_BUTTON = 2
BUTTON_STYLE_SECONDARY = 2
BUTTON_STYLE_SUCCESS = 3

DONE_FOOTER_RE = re.compile(
    r"\n\n✅ Done · (?P<who>.+?) · (?P<when>\S+)\s*$"
)

# GUILDS | GUILD_MESSAGES | MESSAGE_CONTENT
GATEWAY_INTENTS = (1 << 0) | (1 << 9) | (1 << 15)
GATEWAY_ENCODING = "json"
MAX_SEEN_MESSAGE_IDS = 512
RECONNECT_BACKOFF_SEC = (1, 2, 5, 10, 20, 30)


def load_root_env() -> None:
    load_dotenv(ROOT_DIR / ".env")


def _has_explicit_mention(content: str, bot_user_id: str) -> bool:
    if not content or not bot_user_id:
        return False
    return f"<@{bot_user_id}>" in content or f"<@!{bot_user_id}>" in content


def author_display_name(message: Dict) -> str:
    member = message.get("member") or {}
    author = message.get("author") or {}
    return (
        (member.get("nick") or "").strip()
        or (author.get("global_name") or "").strip()
        or (author.get("username") or "").strip()
        or "unknown"
    )


def should_notify_slate(message: Dict, bot_user_id: str) -> bool:
    """True only for an explicit @mention of PadSplit Ops in a wake channel."""
    if not isinstance(message, dict) or not bot_user_id:
        return False
    if str(message.get("channel_id") or "") not in WAKE_CHANNEL_IDS:
        return False
    guild_id = str(message.get("guild_id") or "")
    if guild_id and guild_id != LIAISON_OPS_GUILD_ID:
        return False
    if message.get("webhook_id"):
        return False
    author = message.get("author") or {}
    author_id = str(author.get("id") or "")
    if not author_id or author_id == str(bot_user_id) or author.get("bot"):
        return False
    # Reply threading can put the bot in `mentions` without an actual @mention.
    return _has_explicit_mention(str(message.get("content") or ""), str(bot_user_id))


def build_slate_payload(message: Dict, *, channel_name: str) -> Dict[str, str]:
    channel_id = str(message.get("channel_id") or "")
    message_id = str(message.get("id") or "")
    author = message.get("author") or {}
    timestamp = str(message.get("timestamp") or "").strip()
    if not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "author_display_name": author_display_name(message),
        "author_id": str(author.get("id") or ""),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message_id": message_id,
        "message_text": str(message.get("content") or ""),
        "jump_url": (
            f"https://discord.com/channels/{LIAISON_OPS_GUILD_ID}/{channel_id}/{message_id}"
        ),
        "timestamp": timestamp,
    }


def resolve_channel_name(channel_id: str, channel_names: Optional[Dict[str, str]] = None) -> str:
    if channel_names and channel_id in channel_names:
        return channel_names[channel_id]
    return CHANNEL_NAME_FALLBACKS.get(channel_id, channel_id)


def slate_webhook_headers(key: str) -> Dict[str, str]:
    # Grok Bot webhook routines copy as: Authorization: Bearer <sender key>
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def post_slate_ask(payload: Dict, *, url: str, key: str) -> requests.Response:
    response = requests.post(
        url,
        headers=slate_webhook_headers(key),
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response


def parse_iso_datetime(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def format_iso_utc(now: Optional[datetime] = None) -> str:
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_task_button_channel(channel_id: str) -> bool:
    return str(channel_id or "") in TASK_BUTTON_CHANNEL_IDS


def _button(*, custom_id: str, label: str, style: int, disabled: bool = False) -> Dict[str, Any]:
    return {
        "type": COMPONENT_BUTTON,
        "style": style,
        "custom_id": custom_id,
        "label": label,
        "disabled": disabled,
    }


def task_done_components() -> List[Dict[str, Any]]:
    return [
        {
            "type": COMPONENT_ACTION_ROW,
            "components": [
                _button(
                    custom_id=TASK_DONE_CUSTOM_ID,
                    label="Done",
                    style=BUTTON_STYLE_SUCCESS,
                )
            ],
        }
    ]


def task_undo_components() -> List[Dict[str, Any]]:
    return [
        {
            "type": COMPONENT_ACTION_ROW,
            "components": [
                _button(
                    custom_id=TASK_UNDO_CUSTOM_ID,
                    label="Undo",
                    style=BUTTON_STYLE_SECONDARY,
                )
            ],
        }
    ]


def task_done_disabled_components() -> List[Dict[str, Any]]:
    return [
        {
            "type": COMPONENT_ACTION_ROW,
            "components": [
                _button(
                    custom_id=TASK_DONE_CUSTOM_ID,
                    label=TASK_DONE_MARKER,
                    style=BUTTON_STYLE_SUCCESS,
                    disabled=True,
                )
            ],
        }
    ]


def build_ops_task_payload(content: str, channel_id: str) -> Dict[str, Any]:
    """Message body for a PadSplit Ops task post. Buttons only on the two boards."""
    payload: Dict[str, Any] = {"content": content}
    if is_task_button_channel(channel_id):
        payload["components"] = task_done_components()
    return payload


def flatten_message_components(message: Dict) -> List[Dict[str, Any]]:
    rows = message.get("components") or []
    buttons: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for item in row.get("components") or []:
            if isinstance(item, dict):
                buttons.append(item)
    return buttons


def message_custom_ids(message: Dict) -> List[str]:
    return [str(item.get("custom_id") or "") for item in flatten_message_components(message)]


def has_enabled_task_done_button(message: Dict) -> bool:
    for item in flatten_message_components(message):
        if str(item.get("custom_id") or "") != TASK_DONE_CUSTOM_ID:
            continue
        if not item.get("disabled"):
            return True
    return False


def has_task_button_marker(message: Dict) -> bool:
    custom_ids = set(message_custom_ids(message))
    if TASK_DONE_CUSTOM_ID in custom_ids or TASK_UNDO_CUSTOM_ID in custom_ids:
        return True
    return TASK_DONE_MARKER in str(message.get("content") or "")


def parse_task_done_footer(content: str) -> Optional[Tuple[str, str]]:
    match = DONE_FOOTER_RE.search(content or "")
    if not match:
        return None
    return match.group("who"), match.group("when")


def mark_task_content_done(content: str, *, who: str, when: str) -> str:
    original = restore_task_content(content).strip()
    return f"~~{original}~~\n\n{TASK_DONE_MARKER} · {who} · {when}"


def restore_task_content(content: str) -> str:
    text = DONE_FOOTER_RE.sub("", content or "")
    stripped = text.strip()
    if stripped.startswith("~~") and stripped.endswith("~~") and len(stripped) >= 4:
        return stripped[2:-2]
    return stripped


def interaction_user(interaction: Dict) -> Dict:
    member = interaction.get("member") or {}
    user = member.get("user") or interaction.get("user") or {}
    return user if isinstance(user, dict) else {}


def interaction_user_id(interaction: Dict) -> str:
    return str(interaction_user(interaction).get("id") or "")


def interaction_display_name(interaction: Dict) -> str:
    member = interaction.get("member") or {}
    return author_display_name({"member": member, "author": interaction_user(interaction)})


def _update_message_callback(content: str, components: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "type": CALLBACK_UPDATE_MESSAGE,
        "data": {"content": content, "components": components},
    }


def build_task_interaction_callback(
    interaction: Dict,
    *,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Return the interaction callback body, or None if this is not our button."""
    if not isinstance(interaction, dict):
        return None
    if int(interaction.get("type") or 0) != INTERACTION_TYPE_MESSAGE_COMPONENT:
        return None
    data = interaction.get("data") or {}
    custom_id = str(data.get("custom_id") or "")
    if custom_id not in {TASK_DONE_CUSTOM_ID, TASK_UNDO_CUSTOM_ID}:
        return None
    channel_id = str(interaction.get("channel_id") or "")
    if not is_task_button_channel(channel_id):
        return {"type": CALLBACK_DEFERRED_UPDATE_MESSAGE}
    guild_id = str(interaction.get("guild_id") or "")
    if guild_id and guild_id != LIAISON_OPS_GUILD_ID:
        return {"type": CALLBACK_DEFERRED_UPDATE_MESSAGE}

    message = interaction.get("message") or {}
    content = str(message.get("content") or "")
    current_now = utc_now(now)
    when = format_iso_utc(current_now)
    actor = interaction_user(interaction)
    if not interaction_user_id(interaction) or actor.get("bot"):
        return {"type": CALLBACK_DEFERRED_UPDATE_MESSAGE}
    who = interaction_display_name(interaction)

    if custom_id == TASK_DONE_CUSTOM_ID:
        if parse_task_done_footer(content) or TASK_UNDO_CUSTOM_ID in message_custom_ids(message):
            return {"type": CALLBACK_DEFERRED_UPDATE_MESSAGE}
        return _update_message_callback(
            mark_task_content_done(content, who=who, when=when),
            task_undo_components(),
        )

    footer = parse_task_done_footer(content)
    if footer is None:
        return {"type": CALLBACK_DEFERRED_UPDATE_MESSAGE}
    _who, done_at = footer
    done_dt = parse_iso_datetime(done_at)
    if done_dt is None or (current_now - done_dt).total_seconds() > UNDO_WINDOW_SEC:
        return _update_message_callback(content, task_done_disabled_components())
    return _update_message_callback(restore_task_content(content), task_done_components())


def ack_interaction(
    interaction: Dict,
    callback: Dict,
    *,
    token: str = "",
    poster=None,
) -> requests.Response:
    post = poster or requests.post
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bot {token}"
    response = post(
        (
            f"{DISCORD_API_BASE}/interactions/"
            f"{interaction.get('id')}/{interaction.get('token')}/callback"
        ),
        headers=headers,
        json=callback,
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response


def handle_interaction_create(
    interaction: Dict,
    *,
    token: str,
    now: Optional[datetime] = None,
    poster=None,
) -> bool:
    callback = build_task_interaction_callback(interaction, now=now)
    if callback is None:
        return False
    ack_interaction(interaction, callback, token=token, poster=poster)
    custom_id = str((interaction.get("data") or {}).get("custom_id") or "")
    user_id = interaction_user_id(interaction)
    message_id = str((interaction.get("message") or {}).get("id") or "")
    print(
        f"Task button custom_id={custom_id} user={user_id} message={message_id} "
        f"callback_type={callback.get('type')}",
        flush=True,
    )
    return True


def load_bot_token() -> str:
    load_root_env()
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN")
    return token


def post_ops_task(
    content: str,
    channel_id: str,
    *,
    token: Optional[str] = None,
    poster=None,
) -> Dict[str, Any]:
    """POST a task as PadSplit Ops. Adds a Done button only in the two task boards."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Task content is required")
    bot_token = token if token is not None else load_bot_token()
    post = poster or requests.post
    response = post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        json=build_ops_task_payload(text, channel_id),
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def classify_ops_task_message(
    message: Dict,
    *,
    now: Optional[datetime] = None,
    stale_after_sec: int = STALE_OPEN_AFTER_SEC,
    recently_ticked_sec: int = RECENTLY_TICKED_AFTER_SEC,
) -> Optional[Dict[str, Any]]:
    """Classify one Discord message as open or ticked from button/✅ Done state."""
    if not isinstance(message, dict) or not has_task_button_marker(message):
        return None
    content = str(message.get("content") or "")
    footer = parse_task_done_footer(content)
    custom_ids = set(message_custom_ids(message))
    ticked = bool(
        footer
        or TASK_UNDO_CUSTOM_ID in custom_ids
        or (
            TASK_DONE_CUSTOM_ID in custom_ids
            and not has_enabled_task_done_button(message)
        )
        or (TASK_DONE_MARKER in content and not has_enabled_task_done_button(message))
    )
    who = footer[0] if footer else None
    when = footer[1] if footer else None
    current_now = utc_now(now)
    created = parse_iso_datetime(str(message.get("timestamp") or ""))
    done_dt = parse_iso_datetime(when or "")
    age_sec = None
    if created is not None:
        age_sec = max(0, int((current_now - created).total_seconds()))
    stale = (not ticked) and age_sec is not None and age_sec >= stale_after_sec
    recently_ticked = False
    if ticked and done_dt is not None:
        recently_ticked = (current_now - done_dt).total_seconds() <= recently_ticked_sec
    channel_id = str(message.get("channel_id") or "")
    message_id = str(message.get("id") or "")
    return {
        "status": "ticked" if ticked else "open",
        "who": who,
        "when": when,
        "stale": stale,
        "recently_ticked": recently_ticked,
        "channel_id": channel_id,
        "channel_name": TASK_BUTTON_CHANNEL_NAMES.get(channel_id, channel_id),
        "message_id": message_id,
        "content": restore_task_content(content) if ticked else content,
        "jump_url": (
            f"https://discord.com/channels/{LIAISON_OPS_GUILD_ID}/{channel_id}/{message_id}"
            if channel_id and message_id
            else ""
        ),
        "timestamp": str(message.get("timestamp") or ""),
    }


def summarize_ops_task_messages(
    messages: Iterable[Dict],
    *,
    bot_user_id: str = PADSPLIT_OPS_APPLICATION_ID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    open_items: List[Dict[str, Any]] = []
    ticked_items: List[Dict[str, Any]] = []
    for message in messages:
        author = (message or {}).get("author") or {}
        if str(author.get("id") or "") != str(bot_user_id):
            continue
        classified = classify_ops_task_message(message, now=now)
        if classified is None:
            continue
        if classified["status"] == "ticked":
            ticked_items.append(classified)
        else:
            open_items.append(classified)
    return {
        "open_count": len(open_items),
        "stale_unticked_count": sum(1 for item in open_items if item["stale"]),
        "recently_ticked_count": sum(1 for item in ticked_items if item["recently_ticked"]),
        "ticked_count": len(ticked_items),
        "open": open_items,
        "ticked": ticked_items,
    }


def fetch_channel_messages(
    channel_id: str,
    *,
    token: str,
    limit: int = LIST_TASKS_LIMIT,
    getter=None,
) -> List[Dict[str, Any]]:
    get = getter or requests.get
    response = get(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        },
        params={"limit": limit},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json() or []
    return payload if isinstance(payload, list) else []


def list_ops_tasks(
    *,
    token: Optional[str] = None,
    now: Optional[datetime] = None,
    getter=None,
    limit: int = LIST_TASKS_LIMIT,
) -> Dict[str, Any]:
    bot_token = token if token is not None else load_bot_token()
    boards: Dict[str, Any] = {}
    for channel_id in (TODO_JOE_CHANNEL_ID, AI_TASKS_TEMP_CHANNEL_ID):
        messages = fetch_channel_messages(
            channel_id,
            token=bot_token,
            limit=limit,
            getter=getter,
        )
        summary = summarize_ops_task_messages(messages, now=now)
        summary["channel_id"] = channel_id
        summary["channel_name"] = TASK_BUTTON_CHANNEL_NAMES[channel_id]
        boards[channel_id] = summary
    return {
        "open_count": sum(board["open_count"] for board in boards.values()),
        "stale_unticked_count": sum(
            board["stale_unticked_count"] for board in boards.values()
        ),
        "recently_ticked_count": sum(
            board["recently_ticked_count"] for board in boards.values()
        ),
        "channels": boards,
    }


def load_runtime_config() -> Dict[str, str]:
    load_root_env()
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    url = (os.getenv("SLATE_ASK_WEBHOOK_URL") or "").strip()
    key = (os.getenv("SLATE_ASK_WEBHOOK_KEY") or "").strip()
    missing = [
        name
        for name, value in (
            ("DISCORD_BOT_TOKEN", token),
            ("SLATE_ASK_WEBHOOK_URL", url),
            ("SLATE_ASK_WEBHOOK_KEY", key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing " + ", ".join(missing))
    return {"token": token, "url": url, "key": key}


class _SeenMessageIds:
    def __init__(self, maxlen: int = MAX_SEEN_MESSAGE_IDS) -> None:
        self.maxlen = maxlen
        self._ids: OrderedDict[str, None] = OrderedDict()

    def contains(self, message_id: str) -> bool:
        return bool(message_id) and message_id in self._ids

    def remember(self, message_id: str) -> None:
        if not message_id:
            return
        self._ids[message_id] = None
        self._ids.move_to_end(message_id)
        while len(self._ids) > self.maxlen:
            self._ids.popitem(last=False)


def _index_guild_channels(guilds: Iterable[Dict], channel_names: Dict[str, str]) -> None:
    for guild in guilds:
        if str(guild.get("id") or "") != LIAISON_OPS_GUILD_ID:
            continue
        for channel in guild.get("channels") or []:
            channel_id = str(channel.get("id") or "")
            name = str(channel.get("name") or "").strip()
            if channel_id and name:
                channel_names[channel_id] = name


def handle_message_create(
    message: Dict,
    *,
    bot_user_id: str,
    webhook_url: str,
    webhook_key: str,
    channel_names: Optional[Dict[str, str]] = None,
    seen: Optional[_SeenMessageIds] = None,
    poster=post_slate_ask,
) -> bool:
    if not should_notify_slate(message, bot_user_id):
        return False
    message_id = str(message.get("id") or "")
    if seen is not None and seen.contains(message_id):
        return False
    channel_id = str(message.get("channel_id") or "")
    payload = build_slate_payload(
        message,
        channel_name=resolve_channel_name(channel_id, channel_names),
    )
    poster(payload, url=webhook_url, key=webhook_key)
    if seen is not None:
        seen.remember(message_id)
    author_id = payload["author_id"]
    print(
        f"Woke Slate channel={channel_id} message={message_id} author={author_id}",
        flush=True,
    )
    return True


class PadSplitOpsGateway:
    def __init__(self, token: str, webhook_url: str, webhook_key: str) -> None:
        self.token = token
        self.webhook_url = webhook_url
        self.webhook_key = webhook_key
        self.bot_user_id = PADSPLIT_OPS_APPLICATION_ID
        self.channel_names: Dict[str, str] = dict(CHANNEL_NAME_FALLBACKS)
        self.seen = _SeenMessageIds()
        self.sequence: Optional[int] = None
        self.session_id: Optional[str] = None
        self.resume_gateway_url: Optional[str] = None
        self._ws = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_ack = threading.Event()
        self._send_lock = threading.Lock()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "User-Agent": USER_AGENT,
        }

    def _gateway_url(self) -> str:
        if self.session_id and self.resume_gateway_url:
            base = self.resume_gateway_url
        else:
            response = requests.get(
                f"{DISCORD_API_BASE}/gateway",
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            base = str((response.json() or {}).get("url") or "wss://gateway.discord.gg")
        return f"{base}?v=10&encoding={GATEWAY_ENCODING}"

    def _send(self, payload: Dict) -> None:
        if self._ws is None:
            return
        with self._send_lock:
            self._ws.send(json.dumps(payload))

    def _heartbeat_loop(self, interval_ms: int) -> None:
        interval = max(interval_ms / 1000.0, 5.0)
        if self._heartbeat_stop.wait(interval * 0.4):
            return
        while not self._heartbeat_stop.is_set():
            self._heartbeat_ack.clear()
            try:
                self._send({"op": 1, "d": self.sequence})
            except Exception as exc:
                print(f"Gateway heartbeat send failed: {exc}", flush=True)
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                return
            if self._heartbeat_stop.wait(interval):
                return
            if not self._heartbeat_ack.is_set():
                print("Gateway heartbeat ACK missing; closing socket", flush=True)
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                return

    def _start_heartbeat(self, interval_ms: int) -> None:
        self._heartbeat_stop.set()
        self._heartbeat_stop = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(interval_ms,),
            name="discord-gateway-heartbeat",
            daemon=True,
        )
        thread.start()

    def _identify_or_resume(self) -> None:
        if self.session_id and self.sequence is not None:
            self._send(
                {
                    "op": 6,
                    "d": {
                        "token": self.token,
                        "session_id": self.session_id,
                        "seq": self.sequence,
                    },
                }
            )
            return
        self._send(
            {
                "op": 2,
                "d": {
                    "token": self.token,
                    "intents": GATEWAY_INTENTS,
                    "properties": {
                        "os": sys.platform,
                        "browser": "padsplit-ops",
                        "device": "padsplit-ops",
                    },
                },
            }
        )

    def _cache_channel(self, channel: Dict) -> None:
        channel_id = str(channel.get("id") or "")
        name = str(channel.get("name") or "").strip()
        if channel_id and name:
            self.channel_names[channel_id] = name

    def _handle_dispatch(self, event: str, data: Dict) -> None:
        if event == "READY":
            user = data.get("user") or {}
            if user.get("id"):
                self.bot_user_id = str(user["id"])
            self.session_id = str(data.get("session_id") or "") or None
            resume_url = str(data.get("resume_gateway_url") or "").strip()
            self.resume_gateway_url = resume_url or None
            _index_guild_channels(data.get("guilds") or [], self.channel_names)
            print(f"PadSplit Ops gateway ready as {self.bot_user_id}", flush=True)
            return
        if event == "RESUMED":
            print("PadSplit Ops gateway resumed", flush=True)
            return
        if event == "GUILD_CREATE":
            _index_guild_channels([data], self.channel_names)
            return
        if event in {"CHANNEL_CREATE", "CHANNEL_UPDATE"}:
            self._cache_channel(data)
            return
        if event == "CHANNEL_DELETE":
            self.channel_names.pop(str(data.get("id") or ""), None)
            return
        if event == "INTERACTION_CREATE":
            try:
                handle_interaction_create(data, token=self.token)
            except Exception as exc:
                print(f"Task button interaction failed: {exc}", flush=True)
            return
        if event != "MESSAGE_CREATE":
            return
        try:
            handle_message_create(
                data,
                bot_user_id=self.bot_user_id,
                webhook_url=self.webhook_url,
                webhook_key=self.webhook_key,
                channel_names=self.channel_names,
                seen=self.seen,
            )
        except Exception as exc:
            print(f"Slate wake POST failed: {exc}", flush=True)

    def _handle_payload(self, payload: Dict) -> None:
        op = payload.get("op")
        if payload.get("s") is not None:
            self.sequence = int(payload["s"])
        if op == 10:
            hello = payload.get("d") or {}
            self._start_heartbeat(int(hello.get("heartbeat_interval") or 41250))
            self._identify_or_resume()
            return
        if op == 11:
            self._heartbeat_ack.set()
            return
        if op == 1:
            self._send({"op": 1, "d": self.sequence})
            return
        if op == 7:
            if self._ws is not None:
                self._ws.close()
            return
        if op == 9:
            resumable = bool(payload.get("d"))
            if not resumable:
                self.session_id = None
                self.sequence = None
                self.resume_gateway_url = None
            time.sleep(1)
            self._identify_or_resume()
            return
        if op == 0:
            self._handle_dispatch(str(payload.get("t") or ""), payload.get("d") or {})

    def run_forever(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                "Missing websocket-client. Install padsplit_scraper/requirements.txt"
            ) from exc

        backoff_index = 0
        while True:
            url = self._gateway_url()
            try:
                self._ws = websocket.WebSocket(enable_multithread=True)
                self._ws.connect(url, header=[f"User-Agent: {USER_AGENT}"])
                backoff_index = 0
                while True:
                    raw = self._ws.recv()
                    if not raw:
                        break
                    self._handle_payload(json.loads(raw))
            except Exception as exc:
                print(f"Gateway disconnected: {exc}", flush=True)
            finally:
                self._heartbeat_stop.set()
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None
            delay = RECONNECT_BACKOFF_SEC[min(backoff_index, len(RECONNECT_BACKOFF_SEC) - 1)]
            backoff_index += 1
            print(f"Reconnecting Discord gateway in {delay}s", flush=True)
            time.sleep(delay)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="PadSplit Ops gateway (mention-wake + task Done buttons)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("list-tasks",),
        help="list-tasks prints open vs ticked PadSplit Ops posts (JSON)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "list-tasks":
        print(json.dumps(list_ops_tasks(), indent=2))
        return
    config = load_runtime_config()
    print(
        "Listening for @PadSplit Ops mentions in "
        f"{ASK_AI_AGENT_CHANNEL_ID} and {GENERAL_CHANNEL_ID}",
        flush=True,
    )
    PadSplitOpsGateway(config["token"], config["url"], config["key"]).run_forever()


if __name__ == "__main__":
    main()
