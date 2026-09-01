#!/usr/bin/env python3
"""Wake Grok Bot Slate when PadSplit Ops is @mentioned in allowlisted channels.

Uses the existing PadSplit Ops Discord application (DISCORD_BOT_TOKEN).
Notify-only: never replies in Discord.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

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


def main() -> None:
    config = load_runtime_config()
    print(
        "Listening for @PadSplit Ops mentions in "
        f"{ASK_AI_AGENT_CHANNEL_ID} and {GENERAL_CHANNEL_ID}",
        flush=True,
    )
    PadSplitOpsGateway(config["token"], config["url"], config["key"]).run_forever()


if __name__ == "__main__":
    main()
