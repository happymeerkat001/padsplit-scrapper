#!/usr/bin/env python3
"""Rename PadSplit Discord channels. Idempotent.

#ops -> #ai-tasks-temp
#ai-summaries -> #ai-msg-summaries

Requires DISCORD_BOT_TOKEN with Manage Channels in the target guild.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
MANAGE_CHANNELS = 0x10
TARGETS = {
    "ai-tasks-temp": ("ops",),
    "ai-msg-summaries": ("ai-summaries", "ai-message-summaries"),
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
        raise RuntimeError(f"Discord {method} {path} failed: {exc.code} {exc.reason} {detail}") from exc


def invite_url(token: str) -> str | None:
    try:
        app = api(token, "GET", "/oauth2/applications/@me") or {}
    except RuntimeError as exc:
        print(f"Could not load application id: {exc}")
        return None
    app_id = app.get("id")
    if not app_id:
        return None
    # Manage Channels (0x10) so a re-invite can grant the missing permission.
    return (
        f"https://discord.com/oauth2/authorize?client_id={app_id}"
        f"&permissions={MANAGE_CHANNELS}&scope=bot"
    )


def main() -> None:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit("Missing DISCORD_BOT_TOKEN")

    url = invite_url(token)
    if url:
        print(f"Manage Channels invite: {url}")

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
        wanted = set(TARGETS) | {alias for aliases in TARGETS.values() for alias in aliases}
        print(f"Guild {guild.get('name')}: " + ", ".join(f"#{name}" for name in sorted(names)))
        if wanted.intersection(names):
            target_guild = guild
            text_channels = names
            break

    if target_guild is None:
        sys.exit("Could not find #ops or #ai-summaries in bot guilds")

    print(f"Using guild: {target_guild.get('name')}")
    renamed = 0
    missing_permissions = False
    for new_name, aliases in TARGETS.items():
        channel = text_channels.get(new_name)
        if channel is None:
            for alias in aliases:
                channel = text_channels.get(alias)
                if channel is not None:
                    break
        if channel is None:
            print(f"Channel #{new_name} (aliases: {', '.join(aliases)}) not found in {target_guild.get('name')}")
            continue
        current = channel.get("name") or ""
        if current == new_name:
            print(f"#{current} already named {new_name} ({channel['id']})")
            continue
        try:
            result = api(token, "PATCH", f"/channels/{channel['id']}", {"name": new_name}) or {}
        except RuntimeError as exc:
            print(exc)
            if "50013" in str(exc) or "Missing Permissions" in str(exc):
                missing_permissions = True
                continue
            sys.exit(1)
        print(f"Renamed #{current} -> #{result.get('name')} ({channel['id']})")
        renamed += 1

    if missing_permissions:
        url = invite_url(token)
        print("Bot is in the server but lacks Manage Channels.")
        if url:
            print(f"Re-invite the bot with Manage Channels, then re-run this script:\n{url}")
        sys.exit(1)

    print(f"Done. Renamed {renamed} channel(s).")


if __name__ == "__main__":
    main()
