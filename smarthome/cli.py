"""Operator CLI: list, set, off, status. Targets SmartHome names only."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Optional

from smarthome import clocks, cloud, identity, intent, policy, session


def _notify(text: str) -> None:
    try:
        from padsplit_scraper.discord_notifier import post_discord_message

        post_discord_message(text)
    except Exception as exc:
        sys.stderr.write(f"Discord notify failed: {exc}\n")


def _slot_key_for(name: str, now: Optional[datetime] = None) -> Optional[str]:
    clock = clocks.active_cool(name, now)
    if clock is None:
        return None
    return clocks.slot_key(clock[0], clock[1])


def cmd_list(_args: argparse.Namespace) -> int:
    with session.session_lock():
        fingerprint = identity.current_fingerprint()
        if session.cooldown_active(fingerprint=fingerprint):
            sys.stderr.write("SmartHome cloud cooldown active (65027). Try later.\n")
            return 2
        try:
            client = cloud.connect()
            for item in cloud.list_acs(client):
                print(f"{item.get('name')}  id={item.get('id')}")
        except cloud.SmartHomeSessionLimitError as exc:
            session.start_cooldown(fingerprint=fingerprint)
            sys.stderr.write(f"SmartHome list failed: session limit. {exc}\n")
            raise
        except cloud.SmartHomeError as exc:
            _notify(f"SmartHome list failed: {exc}")
            raise
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    name = args.name
    fahrenheit = args.temp
    with session.session_lock():
        fingerprint = identity.current_fingerprint()
        if session.cooldown_active(fingerprint=fingerprint):
            sys.stderr.write("SmartHome cloud cooldown active (65027). Try later.\n")
            return 2
        try:
            client = cloud.connect()
            cloud.set_temp(client, name, fahrenheit)
        except cloud.SmartHomeSessionLimitError as exc:
            session.start_cooldown(fingerprint=fingerprint)
            sys.stderr.write(f"SmartHome set {name}: session limit. {exc}\n")
            raise
        except cloud.SmartHomeError as exc:
            _notify(f"SmartHome set: 0/1 OK. Failed: {name}. {exc}")
            raise
        intent.record_hold(name, fahrenheit, _slot_key_for(name))
    print(f"set {name} {fahrenheit}°F")
    return 0


def cmd_off(args: argparse.Namespace) -> int:
    name = args.name
    with session.session_lock():
        fingerprint = identity.current_fingerprint()
        if session.cooldown_active(fingerprint=fingerprint):
            sys.stderr.write("SmartHome cloud cooldown active (65027). Try later.\n")
            return 2
        try:
            client = cloud.connect()
            cloud.turn_off(client, name)
        except cloud.SmartHomeSessionLimitError as exc:
            session.start_cooldown(fingerprint=fingerprint)
            sys.stderr.write(f"SmartHome off {name}: session limit. {exc}\n")
            raise
        except cloud.SmartHomeError as exc:
            _notify(f"SmartHome off: 0/1 OK. Failed: {name}. {exc}")
            raise
        intent.record_off(name)
    print(f"off {name}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    payload = intent.load_intent()
    names = sorted(payload.get("units", {}))
    if not names:
        print("no stored intent")
        return 0
    now = datetime.now()
    for name in names:
        action = policy.resolve_action(name, now)
        house = action.get("house") or "unmapped"
        kind = action["kind"]
        extra = ""
        if kind == "floor":
            extra = f" floor={action['f']}°F"
        elif kind == "night_off":
            extra = " night-off"
        elif kind == "off":
            extra = " sticky-off"
        print(f"{name}  house={house}  {kind}{extra}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SmartHome window AC control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    set_p = sub.add_parser("set")
    set_p.add_argument("name")
    set_p.add_argument("temp", type=float)
    set_p.set_defaults(func=cmd_set)
    off_p = sub.add_parser("off")
    off_p.add_argument("name")
    off_p.set_defaults(func=cmd_off)
    sub.add_parser("status").set_defaults(func=cmd_status)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except cloud.SmartHomeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
