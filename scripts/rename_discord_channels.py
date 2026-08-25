#!/usr/bin/env python3
"""Rename PadSplit Discord channels. Idempotent.

#ops -> #ai-tasks-temp
#ai-summaries -> #ai-msg-summaries
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
RENAMES = {
    "ops": "ai-tasks-temp",
    "ai-summaries": "ai-msg-summaries",
}
PREFERRED_GUILDS = ("Liaison Ops", "WORK", "ANG's server")
USER_AGENT = "padsplit-scraper (https://github.com/happymeerkat001/padsplit-scrapper, 1.0)"


def api(token: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"Discord {method} {path} failed: {exc.code} {exc.reason} {detail}")


def main() -> None:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit("Missing DISCORD_BOT_TOKEN")

    guilds = api(token, "GET", "/users/@me/guilds") or []
    print("Bot guilds:", [guild.get("name") for guild in guilds])

    def guild_rank(guild: dict) -> int:
        name = guild.get("name") or ""
        try:
            return PREFERRED_GUILDS.index(name)
        except ValueError:
            return len(PREFERRED_GUILDS)

    target_guild = None
    text_channels: dict[str, dict] = {}
    for guild in sorted(guilds, key=guild_rank):
        channels = api(token, "GET", f"/guilds/{guild['id']}/channels") or []
        names = {
            (channel.get("name") or "").lower(): channel
            for channel in channels
            if channel.get("type") == 0
        }
        if any(old in names or new in names for old, new in RENAMES.items()):
            target_guild = guild
            text_channels = names
            break

    if target_guild is None:
        sys.exit("Could not find #ops or #ai-summaries in bot guilds")

    print(f"Using guild: {target_guild.get('name')}")
    renamed = 0
    for old_name, new_name in RENAMES.items():
        channel = text_channels.get(old_name) or text_channels.get(new_name)
        if channel is None:
            sys.exit(f"Channel #{old_name} (or #{new_name}) not found in {target_guild.get('name')}")
        current = channel.get("name") or ""
        if current == new_name:
            print(f"#{current} already named {new_name} ({channel['id']})")
            continue
        result = api(token, "PATCH", f"/channels/{channel['id']}", {"name": new_name}) or {}
        print(f"Renamed #{current} -> #{result.get('name')} ({channel['id']})")
        renamed += 1

    print(f"Done. Renamed {renamed} channel(s).")


if __name__ == "__main__":
    main()
