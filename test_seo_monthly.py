#!/usr/bin/env python3
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from padsplit_scraper.seo_monthly import (
    DataBundle,
    attach_discord_section,
    build_advice_pack,
    build_launchd_plist,
    collect_bundle,
    listed_or_vacant_over_14,
    normalize_price_status,
    posting_allowed,
    pricing_outliers,
    render_joe_lines,
    render_markdown,
    run_pack,
)


CT = ZoneInfo("America/Chicago")


def listing_room(
    *,
    street: str,
    room_number: int,
    days: float,
    status: str = "listed",
    price_status: str = "good",
    base_price: float = 170,
    recommended: float = 169,
    cover: bool = True,
    room_id: int = 1,
) -> dict:
    payload = {
        "id": room_id,
        "room_number": room_number,
        "detailed_status": status,
        "days_in_current_status": days,
        "base_price": base_price,
        "last_room_price": base_price,
        "recommended_price": recommended,
        "recommended_price_status": price_status,
        "price_deviation": base_price - recommended,
        "address": {"full_street": street, "city": "Dallas", "state": "TX"},
        "cover": {"location": f"img/psproperty/{street}/cover.jpg"} if cover else None,
    }
    return payload


def occ_row(
    *,
    street: str,
    room_number: int,
    vacant: bool,
    days_vacant: int,
    photos: int = 2,
    present: bool = False,
    rent_ready: bool = False,
    seo_eligible: bool = False,
) -> dict:
    return {
        "property_id": 99,
        "address": street,
        "room_number": room_number,
        "occupant_present": present,
        "vacant": vacant,
        "turned": photos > 0,
        "rent_ready": rent_ready,
        "days_vacant": days_vacant,
        "seo_eligible": seo_eligible,
        "move_out_photos": photos,
    }


class SeoMonthlyTests(unittest.TestCase):
    def test_vacant_over_14d_included_and_14d_excluded(self) -> None:
        rooms = [
            listing_room(street="100 Ready Ave", room_number=1, days=15, room_id=11),
            listing_room(street="200 Almost Ave", room_number=2, days=14, room_id=12),
            listing_room(street="300 Fresh Ave", room_number=3, days=3, room_id=13),
        ]
        occupancy = [
            occ_row(street="100 Ready Ave", room_number=1, vacant=True, days_vacant=15, rent_ready=True, seo_eligible=True),
            occ_row(street="200 Almost Ave", room_number=2, vacant=True, days_vacant=14),
            occ_row(street="400 Presence St", room_number=4, vacant=True, days_vacant=21, photos=0),
        ]
        stale = listed_or_vacant_over_14(rooms, occupancy)
        labels = {_room_key(row) for row in stale}
        self.assertIn("100 Ready Ave Rm 1", labels)
        self.assertIn("400 Presence St Rm 4", labels)
        self.assertNotIn("200 Almost Ave Rm 2", labels)
        self.assertNotIn("300 Fresh Ave Rm 3", labels)

    def test_high_and_very_high_flagged_not_minus_one(self) -> None:
        rooms = [
            listing_room(
                street="10 High St",
                room_number=1,
                days=20,
                price_status="high",
                base_price=190,
                recommended=170,
                room_id=21,
            ),
            listing_room(
                street="20 Very High St",
                room_number=2,
                days=30,
                price_status="too-high",
                base_price=220,
                recommended=180,
                room_id=22,
            ),
            listing_room(
                street="30 Penny St",
                room_number=3,
                days=25,
                price_status="good",
                base_price=169,
                recommended=170,
                room_id=23,
            ),
        ]
        occupancy = [
            occ_row(street=street, room_number=n, vacant=True, days_vacant=20)
            for street, n in (("10 High St", 1), ("20 Very High St", 2), ("30 Penny St", 3))
        ]
        stale = listed_or_vacant_over_14(rooms, occupancy)
        flagged = pricing_outliers(stale)
        labels = {_room_key(row) for row in flagged}
        self.assertEqual(normalize_price_status("too-high"), "Very High")
        self.assertEqual(normalize_price_status("very-high"), "Very High")
        self.assertIn("10 High St Rm 1", labels)
        self.assertIn("20 Very High St Rm 2", labels)
        self.assertNotIn("30 Penny St Rm 3", labels)
        pack = build_advice_pack(
            DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
            datetime(2026, 9, 1, 9, 0, tzinfo=CT),
        )
        self.assertIn("High", pack.markdown)
        self.assertIn("Very High", pack.markdown)
        pricing_section = pack.markdown.split("## Pricing Analysis")[1].split("## Cover-photo")[0]
        self.assertNotIn("Penny", pricing_section)
        self.assertNotRegex(pricing_section, r"(drop|cut|lower)\s+\$1")
        self.assertIn("No across-the-board −$1", pricing_section)

    def test_instant_book_is_not_recommended(self) -> None:
        rooms = [listing_room(street="10 High St", room_number=1, days=20, price_status="high")]
        occupancy = [occ_row(street="10 High St", room_number=1, vacant=True, days_vacant=20)]
        pack = build_advice_pack(
            DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
            datetime(2026, 9, 1, 9, 0, tzinfo=CT),
        )
        lowered = pack.markdown.lower()
        self.assertIn("instant book = skip", lowered)
        self.assertNotIn("enable instant book", lowered)
        self.assertNotIn("turn on instant book", lowered)
        self.assertNotIn("recommend instant book", lowered.replace("do not recommend instant book", ""))
        joe = "\n".join(render_joe_lines(pack)).lower()
        self.assertIn("instant book skip", joe)
        self.assertNotIn("enable instant book", joe)
        self.assertNotIn("cindy", joe)
        self.assertNotIn("@cindy", pack.markdown.lower())

    def test_missing_cover_and_turn_photos_flagged(self) -> None:
        rooms = [
            listing_room(street="50 Bare Cover Ln", room_number=1, days=20, cover=False, room_id=51),
        ]
        occupancy = [
            occ_row(street="50 Bare Cover Ln", room_number=1, vacant=True, days_vacant=20, photos=0, rent_ready=False),
        ]
        pack = build_advice_pack(
            DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
            datetime(2026, 9, 1, 9, 0, tzinfo=CT),
        )
        self.assertIn("missing listing cover photo", pack.markdown)
        self.assertIn("missing empty/turn photos", pack.markdown)
        self.assertIn("not detectable", pack.markdown.lower())

    def test_presence_conflict_called_out(self) -> None:
        rooms = [listing_room(street="1025 Broken Crest", room_number=3, days=51, room_id=99)]
        occupancy = [
            occ_row(
                street="1025 Broken Crest",
                room_number=3,
                vacant=False,
                days_vacant=0,
                present=True,
                photos=0,
            )
        ]
        stale = listed_or_vacant_over_14(rooms, occupancy)
        self.assertEqual(len(stale), 1)
        self.assertTrue(stale[0].presence_conflict)
        pack = build_advice_pack(
            DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
            datetime(2026, 9, 1, 9, 0, tzinfo=CT),
        )
        self.assertIn("occupant still present", pack.markdown)

    def test_live_fail_vs_stale_fallback_called_out(self) -> None:
        rooms = [listing_room(street="100 Ready Ave", room_number=1, days=20, room_id=11)]
        occupancy = [occ_row(street="100 Ready Ave", room_number=1, vacant=True, days_vacant=20)]

        def boom(_now: datetime) -> DataBundle:
            raise RuntimeError("login 401")

        def stale() -> DataBundle:
            return DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="unused")

        bundle = collect_bundle(datetime(2026, 9, 1, 9, tzinfo=CT), live_fetcher=boom, stale_loader=stale)
        self.assertTrue(bundle.live_fetch_failed)
        self.assertEqual(bundle.source, "stale_fallback")
        self.assertIn("401", bundle.fallback_reason)
        pack = build_advice_pack(bundle, datetime(2026, 9, 1, 9, tzinfo=CT))
        self.assertIn("STALE FALLBACK", pack.markdown)
        self.assertIn("login 401", pack.markdown)

        live = collect_bundle(
            datetime(2026, 9, 1, 9, tzinfo=CT),
            live_fetcher=lambda _n: DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
            stale_loader=stale,
        )
        self.assertFalse(live.live_fetch_failed)
        self.assertEqual(live.source, "live")
        self.assertIn("LIVE", build_advice_pack(live, datetime(2026, 9, 1, 9, tzinfo=CT)).markdown)

    def test_ci_never_discord_posts(self) -> None:
        posted: list[str] = []
        rooms = [listing_room(street="10 High St", room_number=1, days=20, price_status="high")]
        occupancy = [occ_row(street="10 High St", room_number=1, vacant=True, days_vacant=20)]
        with tempfile.TemporaryDirectory() as tmp:
            old_token = os.environ.get("DISCORD_BOT_TOKEN")
            os.environ["DISCORD_BOT_TOKEN"] = "fake-token-for-test"
            try:
                pack = run_pack(
                    now=datetime(2026, 9, 1, 9, 0, tzinfo=CT),
                    live_fetcher=lambda _n: DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
                    poster=lambda lines: posted.append("\n".join(lines)),
                    output_dir=Path(tmp),
                    ci=True,
                    dry_run=False,
                )
                self.assertTrue((Path(tmp) / "seo-monthly-2026-09.md").exists())
            finally:
                if old_token is None:
                    os.environ.pop("DISCORD_BOT_TOKEN", None)
                else:
                    os.environ["DISCORD_BOT_TOKEN"] = old_token
        self.assertEqual(posted, [])
        self.assertFalse(posting_allowed(ci=True))
        self.assertIn("Would post", pack.markdown)
        self.assertIn("CI must not Discord-post", pack.markdown)
        self.assertIn("@Joe", pack.markdown)
        self.assertNotIn("cindy", "\n".join(pack.joe_lines).lower())
        self.assertNotIn("@cindy", pack.markdown.lower())

    def test_mac_post_path_uses_joe_only(self) -> None:
        posted: list[str] = []
        rooms = [listing_room(street="10 High St", room_number=1, days=20, price_status="high")]
        occupancy = [occ_row(street="10 High St", room_number=1, vacant=True, days_vacant=20)]
        with tempfile.TemporaryDirectory() as tmp:
            pack = run_pack(
                now=datetime(2026, 9, 1, 9, 0, tzinfo=CT),
                live_fetcher=lambda _n: DataBundle(rooms=rooms, occupancy_rooms=occupancy, source="live"),
                poster=lambda lines: posted.append("\n".join(lines)),
                output_dir=Path(tmp),
                ci=False,
                dry_run=False,
            )
        self.assertEqual(len(posted), 1)
        self.assertIn("@Joe", posted[0])
        self.assertNotIn("Cindy", posted[0])
        self.assertIn("Posted to #ai-tasks-temp", pack.markdown)
        markdown = attach_discord_section("# x\n", ["@Joe hello"], posted=False, reason="Ops bot not wired")
        self.assertIn("Would post", markdown)
        self.assertIn("@Joe hello", markdown)

    def test_launchd_plist_is_9am_ct_on_the_first(self) -> None:
        payload = build_launchd_plist(Path("/Users/leon/Documents/Code/padsplit-scraper"))
        slot = payload["StartCalendarInterval"]
        self.assertEqual(slot["Day"], 1)
        self.assertEqual(slot["Hour"], 9)
        self.assertEqual(slot["Minute"], 0)
        self.assertNotIn("Weekday", slot)
        self.assertEqual(payload["Label"], "com.padsplit.seo-monthly")
        self.assertTrue(str(payload["ProgramArguments"][1]).endswith("run_seo_monthly.sh"))
        self.assertEqual(payload["EnvironmentVariables"]["TZ"], "America/Chicago")


def _room_key(row) -> str:
    return f"{row.property_label.split(',')[0]} Rm {row.room_number}"


if __name__ == "__main__":
    unittest.main()
