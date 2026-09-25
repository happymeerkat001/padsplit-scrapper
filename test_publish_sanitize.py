#!/usr/bin/env python3
"""Sanitizer tests. Fixtures are synthetic; never copy snapshot values."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import padsplit_scraper.persist as persist
from padsplit_scraper.publish_sanitize import (
    REDACTION,
    find_violations,
    format_violation_report,
    redact_sensitive_text,
    sanitize_published_snapshot,
)


ROOT = Path(__file__).resolve().parent
DOOR_DIGITS = "900001"
LOCKBOX_DIGITS = "808080"
WIFI_TOKEN = "WifiTokenEXAMPLE9"
PLACEHOLDER = "CODE_PLACEHOLDER"
BARE_DIGITS = "424242"


def _phrase_text() -> dict:
    return {
        "scraped_at": "2026-09-01T00:00:00Z",
        "messages": [
            {
                "id": "chat-1",
                "lastMessage": {
                    "text": f"Front door code {DOOR_DIGITS}. The hallway sink is leaking.",
                },
                "recent_messages": [
                    {
                        "text": f"{LOCKBOX_DIGITS} is the lockbox code, wifi password {WIFI_TOKEN}",
                    },
                    {"text": f"password {PLACEHOLDER}"},
                    {"text": "Room 4 needs paint. Move-in date 2026-09-01. Rent is $850. Call (214) 555-0100."},
                    {"text": "The door is broken and the gate is open."},
                    {"text": "Painted in 2024. Code expires 2026-09-01."},
                ],
            }
        ],
        "tasks": {
            "Requests": [
                {
                    "id": 1,
                    "details": f"Keypad {DOOR_DIGITS} failed",
                    "room_code": DOOR_DIGITS,
                    "has_reused_code": True,
                    "comment": "No secret here",
                }
            ],
            "Complete": [
                {
                    "id": 2,
                    "details": "Trash pickup finished",
                    "room_code": PLACEHOLDER,
                    "has_reused_code": False,
                }
            ],
            "Eviction": [
                {
                    "id": 3,
                    "description": f"lockbox {LOCKBOX_DIGITS}",
                    "room_code": LOCKBOX_DIGITS,
                    "has_reused_code": True,
                }
            ],
            "Other": [
                {
                    "id": 4,
                    "details": "Filter replaced",
                    "room_code": "",
                    "has_reused_code": False,
                }
            ],
        },
    }


class RedactTextTests(unittest.TestCase):
    def test_redacts_door_lockbox_and_wifi_phrases(self) -> None:
        door = redact_sensitive_text(f"Front door code {DOOR_DIGITS} when you arrive")
        self.assertNotIn(DOOR_DIGITS, door)
        self.assertIn(REDACTION, door)
        self.assertIn("when you arrive", door)

        lockbox = redact_sensitive_text(f"Use lockbox {LOCKBOX_DIGITS} by the meter")
        self.assertNotIn(LOCKBOX_DIGITS, lockbox)
        self.assertIn(REDACTION, lockbox)

        wifi = redact_sensitive_text(f"wifi password {WIFI_TOKEN}")
        self.assertNotIn(WIFI_TOKEN, wifi)
        self.assertNotIn("password", wifi.lower())

        placeholder = redact_sensitive_text(f"password {PLACEHOLDER}")
        self.assertNotIn(PLACEHOLDER, placeholder)
        self.assertEqual(REDACTION, "[code hidden, see ops page]")
        self.assertEqual(placeholder, REDACTION)

    def test_redacts_token_before_keyword_and_smashed_code(self) -> None:
        leading = redact_sensitive_text(f"{DOOR_DIGITS} is the door code")
        self.assertNotIn(DOOR_DIGITS, leading)
        self.assertIn(REDACTION, leading)
        smashed = redact_sensitive_text(f"code{DOOR_DIGITS}")
        self.assertNotIn(DOOR_DIGITS, smashed)

    def test_keeps_prose_dates_prices_and_phones(self) -> None:
        prose = "The door is broken and the gate is open. Room 4 needs paint."
        self.assertEqual(redact_sensitive_text(prose), prose)
        dated = "Painted in 2024. Code expires 2026-09-01. Rent is $1,250. Call (214) 555-0100."
        self.assertEqual(redact_sensitive_text(dated), dated)
        self.assertEqual(
            redact_sensitive_text("I'll send the wifi password later"),
            "I'll send the wifi password later",
        )
        mixed = redact_sensitive_text(f"door code {DOOR_DIGITS} on 2026-09-01, rent $850")
        self.assertNotIn(DOOR_DIGITS, mixed)
        self.assertIn("2026-09-01", mixed)
        self.assertIn("$850", mixed)

    def test_redact_is_idempotent(self) -> None:
        original = f"gate code {DOOR_DIGITS}; wifi {WIFI_TOKEN}; sink is leaking"
        once = redact_sensitive_text(original)
        self.assertEqual(redact_sensitive_text(once), once)

    def test_two_digit_room_label_stays(self) -> None:
        text = "Room 12 needs paint"
        self.assertEqual(redact_sensitive_text(text), text)

    def test_added_keywords_redact_without_hitting_keyboard_or_monkey(self) -> None:
        combo = redact_sensitive_text(f"the combo is {BARE_DIGITS}")
        self.assertNotIn(BARE_DIGITS, combo)
        self.assertIn(REDACTION, combo)
        keyed = redact_sensitive_text(f"key: {BARE_DIGITS}")
        self.assertNotIn(BARE_DIGITS, keyed)
        self.assertIn(REDACTION, keyed)
        short = redact_sensitive_text("pw xyz")
        self.assertNotIn("xyz", short)
        self.assertIn(REDACTION, short)
        self.assertEqual(redact_sensitive_text("a monkey at the keyboard"), "a monkey at the keyboard")
        self.assertEqual(redact_sensitive_text("the lock is stuck"), "the lock is stuck")
        self.assertEqual(redact_sensitive_text("the network is down"), "the network is down")

    def test_bare_number_in_message_text_spares_dates_times_prices_and_phones(self) -> None:
        cleaned = redact_sensitive_text(f"use {BARE_DIGITS} to get in", bare_numbers=True)
        self.assertNotIn(BARE_DIGITS, cleaned)
        self.assertIn("to get in", cleaned)
        kept = redact_sensitive_text(
            "Painted in 2024. Arrive 2026-09-01T00:00:00.123456Z at 10:30. "
            "Rent is $850. Call (214) 555-0100.",
            bare_numbers=True,
        )
        self.assertNotIn(REDACTION, kept)
        self.assertIn("2024", kept)
        self.assertIn("2026-09-01T00:00:00.123456Z", kept)
        self.assertIn("10:30", kept)
        self.assertIn("$850", kept)
        self.assertIn("555-0100", kept)

    def test_street_address_keeps_leading_number_unless_keyword_wins(self) -> None:
        plain = "Meet at 1234 main st tomorrow"
        directional = "Meet at 1234 N Oak Hollow Dr. tomorrow"
        five = "Parcel for 12345 Example Ave"
        upper = "Meet at 1234 MAIN ST tomorrow"
        for sample in (plain, directional, five, upper):
            self.assertEqual(redact_sensitive_text(sample, bare_numbers=True), sample)
            self.assertEqual(find_violations({"note": sample}), [])
        coded = redact_sensitive_text("door code 1234", bare_numbers=True)
        self.assertNotIn("1234", coded)
        self.assertEqual(coded, REDACTION)
        coded_street = redact_sensitive_text("door code 1234 main st", bare_numbers=True)
        self.assertNotIn("1234", coded_street)
        self.assertIn("main st", coded_street)
        self.assertIn(REDACTION, coded_street)
        six = redact_sensitive_text("use 900001 main st", bare_numbers=True)
        self.assertNotIn("900001", six)
        self.assertIn("main st", six)
        station = redact_sensitive_text("use 1234 at the station", bare_numbers=True)
        self.assertNotIn("1234", station)
        away = redact_sensitive_text("use 1234 away from here", bare_numbers=True)
        self.assertNotIn("1234", away)
        too_far = redact_sensitive_text("use 1234 N Oak Hollow Extra Dr", bare_numbers=True)
        self.assertNotIn("1234", too_far)

    def test_placeholder_is_not_a_violation_and_stays_idempotent(self) -> None:
        self.assertEqual(find_violations({"text": REDACTION}), [])
        self.assertEqual(find_violations({"note": f"later {REDACTION}"}), [])
        kept = f"{REDACTION} at 1234 main st"
        self.assertEqual(find_violations({"text": kept}), [])
        self.assertEqual(redact_sensitive_text(kept, bare_numbers=True), kept)
        self.assertEqual(redact_sensitive_text(REDACTION), REDACTION)


class SnapshotSanitizeTests(unittest.TestCase):
    def test_strips_room_code_keeps_has_reused_code_and_redacts_text(self) -> None:
        raw = _phrase_text()
        cleaned = sanitize_published_snapshot(raw)
        self.assertEqual(find_violations(cleaned), [])
        self.assertIn("room_code", raw["tasks"]["Requests"][0])
        for bucket in ("Requests", "Complete", "Eviction", "Other"):
            for ticket in cleaned["tasks"][bucket]:
                self.assertNotIn("room_code", ticket)
                self.assertIn("has_reused_code", ticket)
        self.assertTrue(cleaned["tasks"]["Requests"][0]["has_reused_code"])
        self.assertFalse(cleaned["tasks"]["Complete"][0]["has_reused_code"])
        blob = json.dumps(cleaned)
        for secret in (DOOR_DIGITS, LOCKBOX_DIGITS, WIFI_TOKEN, PLACEHOLDER):
            self.assertNotIn(secret, blob)
        kept = cleaned["messages"][0]["recent_messages"][2]["text"]
        self.assertIn("Room 4 needs paint", kept)
        self.assertIn("2026-09-01", kept)
        self.assertIn("$850", kept)
        self.assertIn("555-0100", kept)
        self.assertIn("No secret here", cleaned["tasks"]["Requests"][0]["comment"])

    def test_violation_report_has_counts_and_paths_only(self) -> None:
        violations = find_violations(_phrase_text())
        report = format_violation_report("fixture.json", violations)
        self.assertIn("room_code_keys:", report)
        self.assertIn("sensitive_texts:", report)
        self.assertIn("room_code $.tasks.Requests[].room_code", report)
        for secret in (DOOR_DIGITS, LOCKBOX_DIGITS, WIFI_TOKEN, PLACEHOLDER):
            self.assertNotIn(secret, report)


class CheckerWalkTests(unittest.TestCase):
    def test_unknown_key_with_code_phrase_or_bare_number_fails(self) -> None:
        code_hit = find_violations({"surprise_field": f"code {BARE_DIGITS}"})
        self.assertTrue(any(item.kind == "sensitive_text" and item.path == "$.surprise_field" for item in code_hit))
        bare_hit = find_violations({"surprise_field": f"use {BARE_DIGITS} to get in"})
        self.assertTrue(any(item.kind == "bare_number" and item.path == "$.surprise_field" for item in bare_hit))
        report = format_violation_report("fixture.json", code_hit + bare_hit)
        self.assertNotIn(BARE_DIGITS, report)
        self.assertIn("$.surprise_field", report)

        structural = find_violations(
            {
                "id": BARE_DIGITS,
                "zip": "42424",
                "street1": f"{BARE_DIGITS} Example Lane",
                "created": "2026-09-01T00:00:00.123456Z",
                "picture": f"https://example.test/img/{BARE_DIGITS}.jpg",
            }
        )
        self.assertEqual(structural, [])
        keyword_on_address = find_violations({"street1": f"code {BARE_DIGITS}"})
        self.assertTrue(any(item.kind == "sensitive_text" for item in keyword_on_address))

        cleaned = sanitize_published_snapshot(
            {
                "messages": [
                    {
                        "lastMessage": {"text": f"use {BARE_DIGITS} to get in"},
                        "recent_messages": [{"text": f"the combo is {BARE_DIGITS}. pw xyz"}],
                    }
                ],
                "tasks": {"Requests": [{"details": "sink is leaking", "room_number": BARE_DIGITS}]},
            }
        )
        self.assertEqual(find_violations(cleaned), [])
        self.assertNotIn(BARE_DIGITS, json.dumps(cleaned["messages"]))
        self.assertEqual(cleaned["tasks"]["Requests"][0]["room_number"], BARE_DIGITS)


class PersistWritePathTests(unittest.TestCase):
    def test_published_write_path_has_no_codes(self) -> None:
        raw = _phrase_text()
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            docs_dir = Path(tmp) / "docs" / "data"
            with (
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_dir),
            ):
                persist._persist_latest_payload(
                    raw,
                    scraped_at="2026-09-01T00:00:00Z",
                    write_timestamped=True,
                )
            published = json.loads((output_dir / "latest.json").read_text())
            timestamped = next(output_dir.glob("2026-09-01*.json"))
            copied = docs_dir / "latest.json"
            docs_dir.mkdir(parents=True, exist_ok=True)
            copied.write_text((output_dir / "latest.json").read_text())
            for payload in (published, json.loads(timestamped.read_text()), json.loads(copied.read_text())):
                self.assertEqual(find_violations(payload), [])
                self.assertNotIn("room_code", json.dumps(payload))
            self.assertIn("room_code", raw["tasks"]["Eviction"][0])
            self.assertIn("The hallway sink is leaking", published["messages"][0]["lastMessage"]["text"])

    def test_stats_payload_redacts_ticket_details_without_mutating_kpis(self) -> None:
        kpis = {
            "score": 90,
            "open_ticket_items": [{"id": 7, "details": f"door code {DOOR_DIGITS}", "room_code": DOOR_DIGITS}],
        }
        payload = persist._build_stats_payload(
            scraped_at="2026-09-01T00:00:00Z",
            rooms=[{"id": 1, "base_price": 700}],
            properties=[],
            earnings_payload={"results": []},
            kpis=kpis,
            run_status={"state": "ok"},
        )
        self.assertEqual(find_violations(payload), [])
        self.assertEqual(payload["kpis"]["score"], 90)
        self.assertEqual(payload["rooms"][0]["base_price"], 700)
        self.assertIn("room_code", kpis["open_ticket_items"][0])
        self.assertIn(DOOR_DIGITS, kpis["open_ticket_items"][0]["details"])

    def test_degraded_stats_rewrite_returns_sanitized_payload(self) -> None:
        raw = {
            "scraped_at": "2026-09-01T00:00:00Z",
            "kpis": {"open_ticket_items": [{"details": f"lockbox {LOCKBOX_DIGITS}", "room_code": LOCKBOX_DIGITS}]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            with patch.object(persist, "OUTPUT_DIR", output_dir):
                written = persist._persist_stats_payload(raw)
            on_disk = json.loads((output_dir / "stats.json").read_text())
        self.assertEqual(find_violations(written), [])
        self.assertEqual(find_violations(on_disk), [])
        self.assertIn("room_code", raw["kpis"]["open_ticket_items"][0])


class CheckScriptTests(unittest.TestCase):
    def test_script_reports_counts_without_values_and_fails_dirty_files(self) -> None:
        script = ROOT / "padsplit_scraper" / "check_published_snapshot.py"
        with tempfile.TemporaryDirectory() as tmp:
            dirty = Path(tmp) / "dirty.json"
            clean = Path(tmp) / "clean.json"
            missing = Path(tmp) / "missing.json"
            dirty.write_text(json.dumps(_phrase_text()))
            clean.write_text(json.dumps(sanitize_published_snapshot(_phrase_text())))
            dirty_run = subprocess.run(
                [sys.executable, str(script), str(dirty)],
                check=False,
                capture_output=True,
                text=True,
            )
            clean_run = subprocess.run(
                [sys.executable, str(script), str(clean)],
                check=False,
                capture_output=True,
                text=True,
            )
            missing_run = subprocess.run(
                [sys.executable, str(script), str(missing)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(dirty_run.returncode, 1)
        self.assertEqual(clean_run.returncode, 0)
        self.assertEqual(missing_run.returncode, 2)
        combined = dirty_run.stdout + dirty_run.stderr + clean_run.stdout + missing_run.stdout
        self.assertIn("room_code_keys:", dirty_run.stdout)
        self.assertIn("sensitive_texts:", dirty_run.stdout)
        self.assertIn("$.tasks.Requests[].room_code", dirty_run.stdout)
        self.assertIn("room_code_keys: 0", clean_run.stdout)
        self.assertIn("error: missing", missing_run.stdout)
        for secret in (DOOR_DIGITS, LOCKBOX_DIGITS, WIFI_TOKEN, PLACEHOLDER):
            self.assertNotIn(secret, combined)

    def test_ci_checks_before_git_add_and_does_not_commit_a_sidecar(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "scrape.yml").read_text()
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn("cron: '17,47 * * * *'", workflow)
        self.assertLess(
            workflow.index("check_published_snapshot.py"),
            workflow.index("git add docs/data/latest.json"),
        )
        self.assertIn("docs/data/occupancy.json", workflow)
        self.assertNotIn("latest.private", workflow)
        self.assertNotIn("latest.internal", workflow)
        self.assertIn("padsplit_scraper/output/latest.json", gitignore)
        self.assertIn("padsplit_scraper/output/202*.json", gitignore)


class DashboardStillIgnoresRoomCodeTests(unittest.TestCase):
    def test_pages_do_not_read_room_code(self) -> None:
        for name in ("index.html", "stats.html", "private-messages.html"):
            text = (ROOT / "docs" / name).read_text()
            self.assertNotIn("room_code", text)
        self.assertIn("t.details", (ROOT / "docs" / "index.html").read_text())
        self.assertIn("room_number", (ROOT / "docs" / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
