#!/usr/bin/env python3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from obsidian_daily_digest import (
    END_MARKER,
    START_MARKER,
    format_draft_replies,
    get_daily_note_path,
    upsert_managed_block,
    write_daily_digest,
)


LOCAL_TZ = ZoneInfo("America/Chicago")


def sample_padsplit_payload() -> dict:
    return {
        "scraped_at": "2026-04-13T11:00:00Z",
        "tasks": {
            "Requests": [
                {
                    "property_address": {"street1": "10235 Ridge Oak", "city": "Dallas", "state": "TX"},
                    "details": "Leak under kitchen sink",
                    "room_number": 5,
                }
            ],
            "Open": [
                {
                    "property_address": {"street1": "4100 N Main St", "city": "Fort Worth", "state": "TX"},
                    "details": "Replace hallway smoke detector",
                    "room_number": 1,
                }
            ],
            "Complete": [
                {
                    "property_address": {"street1": "900 Elm St"},
                    "details": "Trash pickup finished",
                    "room_number": 2,
                }
            ],
        },
        "messages": [
            {
                "title": "Roshawn Porter",
                "occupancy": {
                    "room": {"roomNumber": 5},
                    "user": {"firstName": "Roshawn", "lastName": "Porter"},
                },
                "recent_messages": [
                    {
                        "created": "2026-04-13T10:30:00Z",
                        "text": "The kitchen light is flickering again.",
                        "sender": {"firstName": "Roshawn", "lastName": "Porter"},
                    },
                    {
                        "created": "2026-04-13T10:45:00Z",
                        "text": "I will check it this afternoon.",
                        "sender": {"firstName": "Ang", "lastName": "Li"},
                    },
                ],
            }
        ],
    }


def sample_thermostat_payload() -> dict:
    return {
        "scraped_at": "2026-04-13T10:55:00Z",
        "locations": [
            {
                "name": "10235 Ridge Oak",
                "devices": [
                    {
                        "temp": 73.0,
                        "heat_setpoint": 68.0,
                        "cool_setpoint": 78.0,
                        "humidity": 49.0,
                    }
                ],
            }
        ],
    }


def test_upsert_managed_block_replaces_existing_section() -> None:
    original = "Before\n\n<!-- padsplit-daily-digest:start -->\nold\n<!-- padsplit-daily-digest:end -->\n\nAfter\n"
    updated = upsert_managed_block(original, f"{START_MARKER}\nnew\n{END_MARKER}\n")
    assert updated.count(START_MARKER) == 1
    assert "new" in updated
    assert "old" not in updated
    assert updated.startswith("Before")
    assert updated.rstrip().endswith("After")


def test_write_daily_digest_creates_note_when_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        note_path = write_daily_digest(
            padsplit_payload=sample_padsplit_payload(),
            thermostat_payload=sample_thermostat_payload(),
            daily_notes_dir=Path(tmpdir),
            generated_at=datetime(2026, 4, 13, 6, 0, tzinfo=LOCAL_TZ),
        )
        content = note_path.read_text()
        assert note_path.name == "2026-04-13.md"
        assert "## PadSplit Daily Digest" in content
        assert "10235 Ridge Oak, Dallas, TX: Requests Room 5: Leak under kitchen sink" in content
        assert "4100 N Main St, Fort Worth, TX: Open Room 1: Replace hallway smoke detector" in content
        assert "Roshawn Porter (Room 5)" in content
        assert "10235 Ridge Oak: 73.0F, heat 68.0F, cool 78.0F, humidity 49.0%" in content


def test_write_daily_digest_is_idempotent_for_same_note() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        daily_dir = Path(tmpdir)
        note_path = get_daily_note_path(daily_dir, datetime(2026, 4, 13, 6, 0, tzinfo=LOCAL_TZ))
        note_path.write_text("# Existing Note\n\nPersonal content\n")

        for _ in range(2):
            write_daily_digest(
                padsplit_payload=sample_padsplit_payload(),
                thermostat_payload=sample_thermostat_payload(),
                daily_notes_dir=daily_dir,
                generated_at=datetime(2026, 4, 13, 6, 0, tzinfo=LOCAL_TZ),
            )

        content = note_path.read_text()
        assert content.count(START_MARKER) == 1
        assert content.count(END_MARKER) == 1
        assert "# Existing Note" in content
        assert "Personal content" in content


def test_write_daily_digest_handles_missing_thermostat() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        note_path = write_daily_digest(
            padsplit_payload=sample_padsplit_payload(),
            thermostat_payload=None,
            daily_notes_dir=Path(tmpdir),
            generated_at=datetime(2026, 4, 13, 6, 0, tzinfo=LOCAL_TZ),
        )
        content = note_path.read_text()
        assert "Thermostat data unavailable." in content


def test_format_draft_replies_requires_contract_fields() -> None:
    lines = format_draft_replies(
        {
            "drafts": [
                {
                    "chat_id": "chat_123",
                    "message_id": "msg_456",
                    "tenant_name": "Roshawn Porter",
                    "room_number": 5,
                    "property": "10235 Ridge Oak",
                    "category": "maintenance",
                    "urgency": "high",
                    "inbound_at": "2026-04-13T10:30:00Z",
                    "draft_reply": "Hi Roshawn, thanks for letting me know.",
                },
                {
                    "chat_id": "missing_message_id",
                    "tenant_name": "Malformed",
                    "draft_reply": "Skip me",
                },
            ]
        }
    )

    rendered = "\n".join(lines)
    assert "### Draft Replies" in rendered
    assert "Roshawn Porter" in rendered
    assert "Open PadSplit thread" in rendered
    assert "Malformed" not in rendered


def test_get_daily_note_path_requires_existing_directory() -> None:
    missing = Path(tempfile.gettempdir()) / "codex-missing-obsidian-dir"
    if missing.exists():
        raise AssertionError(f"Test precondition failed: {missing} already exists")
    try:
        get_daily_note_path(missing, datetime(2026, 4, 13, 6, 0, tzinfo=LOCAL_TZ))
        raise AssertionError("Expected RuntimeError for missing daily notes directory")
    except RuntimeError as exc:
        assert "OBSIDIAN_DAILY_NOTES_DIR" in str(exc)


if __name__ == "__main__":
    tests = [
        test_upsert_managed_block_replaces_existing_section,
        test_write_daily_digest_creates_note_when_missing,
        test_write_daily_digest_is_idempotent_for_same_note,
        test_write_daily_digest_handles_missing_thermostat,
        test_format_draft_replies_requires_contract_fields,
        test_get_daily_note_path_requires_existing_directory,
    ]
    for test in tests:
        test()
    print(f"PASS {len(tests)} tests")
