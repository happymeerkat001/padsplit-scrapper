#!/usr/bin/env python3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from padsplit_scraper import occupancy


CHICAGO = ZoneInfo("America/Chicago")
NOW = datetime(2026, 8, 26, 16, 0, tzinfo=CHICAGO)
ROW_KEYS = {
    "property_id",
    "address",
    "room_number",
    "occupant_present",
    "listed_move_out",
    "next_move_in",
    "open_turn_ticket_ids",
    "open_eviction_ticket_ids",
    "open_hold_ticket_ids",
    "move_out_photos",
    "vacant",
    "turned",
    "rent_ready",
    "days_vacant",
    "seo_eligible",
}
FORBIDDEN_KEYS = {
    "room_code",
    "user",
    "name",
    "reported_by",
    "lastMessage",
    "extra_data",
    "media",
    "details",
    "has_reused_code",
}


def curtis_chat(*, street: str = "1025 Broken Crest") -> dict:
    return {
        "id": "chat-curtis",
        "title": "Curtis Palmer",
        "chatType": "HOST_SUPPORT_CHAT",
        "isArchived": False,
        "occupancy": {
            "moveInDate": "2026-03-31",
            "moveOutDate": "2026-06-29",
            "room": {"pk": 99241, "roomNumber": 3},
            "user": {
                "firstName": "Curtis",
                "lastName": "Palmer",
                "displayName": None,
            },
        },
        "property": {"address": {"street1": street}},
        "lastMessage": {
            "created": "2026-08-19T22:08:40.328409",
            "text": "Parking reminder",
            "messageType": "TEXT",
            "attachments": [],
        },
    }


def eviction_433568(*, street: str = "1025 Broken Crest") -> dict:
    return {
        "id": 433568,
        "status": "eviction",
        "category": "room-turn",
        "media": [],
        "room_number": 3,
        "room_code": "7633",
        "details": "Collections",
        "property_id": 31523,
        "property_address": {"street1": street},
        "reported_by": {"name": "Curtis Palmer"},
        "extra_data": {
            "user_id": 1018843,
            "move_out_date": "2026-06-29",
            "is_present_after_move_out": True,
        },
        "has_reused_code": True,
        "moveout_photos_count": 0,
    }


def find_room(payload: dict, *, address_contains: str, room_number) -> dict:
    for row in payload["rooms"]:
        if address_contains.lower() in str(row.get("address", "")).lower() and str(row.get("room_number")) == str(
            room_number
        ):
            return row
    raise AssertionError(f"no row for {address_contains} room {room_number}: {payload['rooms']}")


def assert_allowlist(test: unittest.TestCase, row: dict) -> None:
    test.assertEqual(set(row.keys()), ROW_KEYS)
    for key in FORBIDDEN_KEYS:
        test.assertNotIn(key, row)


class PadSplitOccupancyTests(unittest.TestCase):
    def test_broken_crest_rm3_stays_present_and_not_rent_ready(self) -> None:
        payload = occupancy.compute_occupancy(
            [curtis_chat()],
            {"Eviction": [eviction_433568()]},
            NOW,
        )
        row = find_room(payload, address_contains="1025 Broken Crest", room_number=3)
        assert_allowlist(self, row)
        self.assertEqual(row["property_id"], 31523)
        self.assertTrue(row["occupant_present"])
        self.assertFalse(row["vacant"])
        self.assertFalse(row["turned"])
        self.assertFalse(row["rent_ready"])
        self.assertFalse(row["seo_eligible"])
        self.assertEqual(row["move_out_photos"], 0)
        self.assertEqual(row["listed_move_out"], "2026-06-29")
        self.assertEqual(row["open_eviction_ticket_ids"], [433568])
        self.assertEqual(row["open_turn_ticket_ids"], [])
        self.assertEqual(payload["derived_from"], ["messages", "tasks"])

    def test_broken_crest_rd_joins_same_row(self) -> None:
        payload = occupancy.compute_occupancy(
            [curtis_chat(street="1025 Broken Crest")],
            {"Eviction": [eviction_433568(street="1025 Broken Crest Rd")]},
            NOW,
        )
        self.assertEqual(len(payload["rooms"]), 1)
        row = payload["rooms"][0]
        self.assertEqual(row["property_id"], 31523)
        self.assertTrue(row["occupant_present"])
        self.assertFalse(row["rent_ready"])

    def test_past_move_out_user_present_dead_chat_still_present(self) -> None:
        chat = curtis_chat()
        chat["lastMessage"] = {"created": "2026-06-01T00:00:00", "messageType": "TEXT"}
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=3)
        self.assertTrue(row["occupant_present"])

    def test_past_move_out_null_user_last_message_after_move_out_present(self) -> None:
        chat = curtis_chat()
        chat["occupancy"]["user"] = None
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=3)
        self.assertTrue(row["occupant_present"])

    def test_past_move_out_dead_chat_no_tickets_is_vacant(self) -> None:
        chat = curtis_chat()
        chat["occupancy"]["user"] = None
        chat["lastMessage"] = {"created": "2026-06-01T00:00:00", "messageType": "TEXT"}
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=3)
        self.assertFalse(row["occupant_present"])
        self.assertTrue(row["vacant"])
        self.assertFalse(row["turned"])

    def test_present_after_move_out_flag_alone(self) -> None:
        ticket = eviction_433568()
        ticket["status"] = "submitted"
        ticket["category"] = "unclassified"
        ticket["extra_data"]["is_present_after_move_out"] = True
        payload = occupancy.compute_occupancy([], {"Requests": [ticket]}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=3)
        self.assertTrue(row["occupant_present"])
        self.assertEqual(row["property_id"], 31523)

    def test_missing_occupancy_defaults_present(self) -> None:
        chat = {
            "property": {"address": {"street1": "100 Empty"}},
            "occupancy": None,
            "lastMessage": {},
        }
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        self.assertEqual(len(payload["rooms"]), 1)
        self.assertTrue(payload["rooms"][0]["occupant_present"])

    def test_structured_vacant_and_turned_days_vacant_boundaries(self) -> None:
        chat = {
            "occupancy": {
                "moveOutDate": "2026-08-16",
                "user": None,
                "room": {"roomNumber": 1},
            },
            "property": {"address": {"street1": "200 Ready"}},
            "lastMessage": {"created": "2026-08-10T00:00:00"},
        }
        ticket = {
            "id": 99,
            "status": "completed",
            "category": "room-turn",
            "room_number": 1,
            "property_id": 1,
            "property_address": {"street1": "200 Ready"},
            "media": [{"mediaType": "PICTURE"}, {"mediaType": "PICTURE"}],
            "moveout_photos_count": 2,
            "extra_data": {"move_out_date": "2026-08-16", "is_present_after_move_out": False},
        }
        at_10 = occupancy.compute_occupancy([chat], {"Complete": [ticket]}, datetime(2026, 8, 26, tzinfo=CHICAGO))
        row_10 = find_room(at_10, address_contains="200 Ready", room_number=1)
        self.assertTrue(row_10["vacant"])
        self.assertTrue(row_10["turned"])
        self.assertTrue(row_10["rent_ready"])
        self.assertEqual(row_10["days_vacant"], 10)
        self.assertFalse(row_10["seo_eligible"])

        at_14 = occupancy.compute_occupancy([chat], {"Complete": [ticket]}, datetime(2026, 8, 30, tzinfo=CHICAGO))
        self.assertFalse(find_room(at_14, address_contains="200 Ready", room_number=1)["seo_eligible"])

        at_15 = occupancy.compute_occupancy([chat], {"Complete": [ticket]}, datetime(2026, 8, 31, tzinfo=CHICAGO))
        row_15 = find_room(at_15, address_contains="200 Ready", room_number=1)
        self.assertTrue(row_15["rent_ready"])
        self.assertTrue(row_15["seo_eligible"])
        self.assertNotIn("Bobby", str(row_15))

    def test_completed_turn_plus_open_eviction_is_not_turned(self) -> None:
        ticket_turn = {
            "id": 1,
            "status": "completed",
            "category": "room-turn",
            "room_number": 2,
            "property_address": {"street1": "300 Holdover"},
            "moveout_photos_count": 3,
            "media": [{}],
        }
        ticket_eviction = {
            "id": 2,
            "status": "eviction",
            "category": "room-turn",
            "room_number": 2,
            "property_address": {"street1": "300 Holdover"},
            "moveout_photos_count": 0,
        }
        payload = occupancy.compute_occupancy([], {"Complete": [ticket_turn], "Eviction": [ticket_eviction]}, NOW)
        row = find_room(payload, address_contains="300 Holdover", room_number=2)
        self.assertFalse(row["turned"])
        self.assertEqual(row["open_eviction_ticket_ids"], [2])

    def test_open_hold_blocks_turned(self) -> None:
        payload = occupancy.compute_occupancy(
            [],
            {
                "Complete": [
                    {
                        "id": 1,
                        "status": "completed",
                        "category": "room-turn",
                        "room_number": 4,
                        "property_address": {"street1": "400 Hold"},
                        "moveout_photos_count": 1,
                    }
                ],
                "On Hold": [
                    {
                        "id": 8,
                        "status": "on_hold",
                        "category": "unclassified",
                        "room_number": 4,
                        "property_address": {"street1": "400 Hold"},
                    }
                ],
            },
            NOW,
        )
        row = find_room(payload, address_contains="400 Hold", room_number=4)
        self.assertFalse(row["turned"])
        self.assertEqual(row["open_hold_ticket_ids"], [8])

    def test_unclassified_completed_does_not_turn(self) -> None:
        payload = occupancy.compute_occupancy(
            [],
            {
                "Complete": [
                    {
                        "id": 7,
                        "status": "completed",
                        "category": "unclassified",
                        "room_number": 5,
                        "property_address": {"street1": "500 Doorlock"},
                        "moveout_photos_count": 4,
                    }
                ]
            },
            NOW,
        )
        row = find_room(payload, address_contains="500 Doorlock", room_number=5)
        self.assertFalse(row["turned"])

    def test_future_move_in_with_user_is_present(self) -> None:
        chat = {
            "occupancy": {
                "moveInDate": "2026-09-03",
                "moveOutDate": None,
                "user": {"firstName": "Terius"},
                "room": {"roomNumber": 6},
            },
            "property": {"address": {"street1": "Pioneer Lane"}},
            "lastMessage": {"created": "2026-08-20T00:00:00"},
        }
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="Pioneer", room_number=6)
        self.assertTrue(row["occupant_present"])
        self.assertEqual(row["next_move_in"], "2026-09-03")
        self.assertFalse(row["seo_eligible"])
        self.assertIsNone(row["property_id"])

    def test_chat_only_null_property_id(self) -> None:
        chat = curtis_chat()
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=3)
        self.assertIsNone(row["property_id"])
        self.assertTrue(row["occupant_present"])

    def test_task_only_without_vacant_signal_is_present(self) -> None:
        ticket = {
            "id": 11,
            "status": "submitted",
            "category": "room-turn",
            "room_number": 8,
            "property_id": 31523,
            "property_address": {"street1": "1025 Broken Crest"},
            "extra_data": {},
            "moveout_photos_count": 0,
        }
        payload = occupancy.compute_occupancy([], {"Requests": [ticket]}, NOW)
        row = find_room(payload, address_contains="Broken Crest", room_number=8)
        self.assertTrue(row["occupant_present"])
        self.assertEqual(row["open_turn_ticket_ids"], [11])

    def test_two_chats_same_room_or_presence(self) -> None:
        present = curtis_chat()
        incoming = {
            "occupancy": {
                "moveInDate": "2026-09-10",
                "moveOutDate": None,
                "user": None,
                "room": {"roomNumber": 3},
            },
            "property": {"address": {"street1": "1025 Broken Crest"}},
            "lastMessage": {"created": "2026-08-01T00:00:00"},
        }
        payload = occupancy.compute_occupancy([present, incoming], {}, NOW)
        self.assertEqual(len(payload["rooms"]), 1)
        row = payload["rooms"][0]
        self.assertTrue(row["occupant_present"])
        self.assertEqual(row["listed_move_out"], "2026-06-29")
        self.assertEqual(row["next_move_in"], "2026-09-10")

    def test_room_number_int_and_string_join(self) -> None:
        chat = curtis_chat()
        chat["occupancy"]["room"]["roomNumber"] = "3"
        ticket = eviction_433568()
        ticket["room_number"] = 3
        ticket["category"] = "ROOM_TURN"
        payload = occupancy.compute_occupancy([chat], {"Eviction": [ticket]}, NOW)
        self.assertEqual(len(payload["rooms"]), 1)
        self.assertEqual(payload["rooms"][0]["open_eviction_ticket_ids"], [433568])

    def test_null_listed_move_out_is_unsure_present(self) -> None:
        chat = {
            "occupancy": {"moveOutDate": None, "user": None, "room": {"roomNumber": 9}},
            "property": {"address": {"street1": "600 Unknown"}},
            "lastMessage": {},
        }
        payload = occupancy.compute_occupancy([chat], {}, NOW)
        row = find_room(payload, address_contains="600 Unknown", room_number=9)
        self.assertTrue(row["occupant_present"])
        self.assertEqual(row["days_vacant"], 0)
        self.assertFalse(row["seo_eligible"])

    def test_does_not_read_stats(self) -> None:
        self.assertFalse(hasattr(occupancy.compute_occupancy, "stats"))
        self.assertNotIn("stats.json", occupancy.__file__)

    def test_operator_lists_split_incoming_holdover_and_rent_ready(self) -> None:
        payload = occupancy.compute_occupancy(
            [
                curtis_chat(),
                {
                    "occupancy": {
                        "moveInDate": "2026-08-29",
                        "moveOutDate": "2026-08-25",
                        "user": {"firstName": "Incoming"},
                        "room": {"roomNumber": 7},
                    },
                    "property": {"address": {"street1": "5509 Burton Avenue"}},
                    "lastMessage": {"created": "2026-08-26T20:00:00"},
                },
            ],
            {
                "Eviction": [eviction_433568()],
                "Complete": [
                    {
                        "id": 99,
                        "status": "completed",
                        "category": "room-turn",
                        "room_number": 4,
                        "property_address": {"street1": "1404 Pioneer Lane"},
                        "extra_data": {"move_out_date": "2026-08-10"},
                        "moveout_photos_count": 2,
                    }
                ],
            },
            NOW,
        )
        lists = occupancy.operator_lists(payload["rooms"], NOW.date())
        incoming_keys = [(row["address"], row["room_number"], row["next_move_in"]) for row in lists["incoming"]]
        self.assertEqual(incoming_keys, [("5509 Burton Avenue", 7, "2026-08-29")])
        holdover = find_room({"rooms": lists["occupied_after_move_out"]}, address_contains="Broken Crest", room_number=3)
        self.assertTrue(holdover["occupant_present"])
        self.assertFalse(holdover["rent_ready"])
        self.assertFalse(holdover["seo_eligible"])
        ready = find_room({"rooms": lists["rent_ready"]}, address_contains="Pioneer", room_number=4)
        self.assertTrue(ready["rent_ready"])
        self.assertTrue(ready["vacant"])

    def test_past_next_move_in_is_not_incoming(self) -> None:
        rooms = [
            {
                "address": "5509 Burton Avenue",
                "room_number": 7,
                "occupant_present": True,
                "listed_move_out": None,
                "next_move_in": "2026-08-25",
                "vacant": False,
                "rent_ready": False,
            }
        ]
        lists = occupancy.operator_lists(rooms, NOW.date())
        self.assertEqual(lists["incoming"], [])

    def test_live_occupancy_json_broken_crest_rm3_and_incoming(self) -> None:
        from pathlib import Path
        import json

        path = Path(__file__).resolve().parent / "docs" / "data" / "occupancy.json"
        payload = json.loads(path.read_text())
        row = find_room(payload, address_contains="1025 Broken Crest", room_number=3)
        assert_allowlist(self, row)
        self.assertEqual(row["property_id"], 31523)
        self.assertTrue(row["occupant_present"])
        self.assertFalse(row["rent_ready"])
        self.assertFalse(row["seo_eligible"])
        self.assertNotIn("room_code", row)
        for room in payload["rooms"]:
            self.assertNotIn("room_code", room)
            self.assertEqual(set(room.keys()), ROW_KEYS)
        scraped = datetime.fromisoformat(payload["scraped_at"].replace("Z", "+00:00")).astimezone(CHICAGO).date()
        lists = occupancy.operator_lists(payload["rooms"], scraped)
        incoming_dates = {row["next_move_in"] for row in lists["incoming"]}
        self.assertTrue(incoming_dates)
        self.assertTrue(all(move_in >= scraped.isoformat() for move_in in incoming_dates if move_in))
        incoming_keys = {(row["address"], row["room_number"], row["next_move_in"]) for row in lists["incoming"]}
        self.assertIn(("5509 Burton Avenue", 7, "2026-08-29"), incoming_keys)
        holdover_keys = {(row["address"], row["room_number"]) for row in lists["occupied_after_move_out"]}
        self.assertIn(("1025 Broken Crest", 3), holdover_keys)


if __name__ == "__main__":
    unittest.main()
