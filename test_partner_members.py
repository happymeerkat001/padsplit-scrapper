#!/usr/bin/env python3
"""Partner member reader tests. No live PadSplit calls."""

import base64
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from padsplit_scraper import partner_members


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
PII_NAME = "ZqxPat"
PII_BALANCE = "99123.45"
PII_SCORE = "88441"


def _raw_member(**overrides) -> dict:
    row = {
        "id": 555,
        "occupancy_id": "555",
        "room_number": 2,
        "move_in_date": "2026-08-01",
        "move_out_date": None,
        "is_terminated": False,
        "occupancy_status": "active",
        "finance_status": "current",
        "is_on_payment_plan": False,
        "payment_plan_end_date": "2026-12-31",
        "first_name": PII_NAME,
        "last_name": "Example",
        "name": PII_NAME,
        "balance": PII_BALANCE,
        "member_score": PII_SCORE,
        "address": "1 Example Street",
    }
    row.update(overrides)
    return row


class ParseTests(unittest.TestCase):
    def test_pii_fields_are_dropped(self) -> None:
        parsed = partner_members.parse_member_row(_raw_member())
        blob = str(parsed)
        self.assertNotIn(PII_NAME, blob)
        self.assertNotIn(PII_BALANCE, blob)
        self.assertNotIn(PII_SCORE, blob)
        for key in ("first_name", "last_name", "name", "balance", "member_score", "address"):
            self.assertNotIn(key, parsed)
        self.assertEqual(
            set(parsed),
            {
                "occupancy_id",
                "room_number",
                "move_in_date",
                "move_out_date",
                "is_terminated",
                "occupancy_status",
                "finance_status",
                "is_on_payment_plan",
                "payment_plan_end_date",
            },
        )
        self.assertEqual(parsed["occupancy_id"], "555")
        self.assertEqual(parsed["room_number"], "2")
        self.assertIs(parsed["is_terminated"], False)
        self.assertEqual(parsed["occupancy_status"], "active")
        self.assertEqual(parsed["finance_status"], "current")
        self.assertIs(parsed["is_on_payment_plan"], False)
        self.assertEqual(parsed["payment_plan_end_date"], "2026-12-31")

    def test_balance_is_only_on_the_eviction_row(self) -> None:
        raw = _raw_member(finance_status="Behind", is_on_payment_plan=True)
        public = partner_members.parse_member_row(raw)
        eviction = partner_members.eviction_member_row(raw)
        self.assertNotIn("balance", public)
        self.assertNotIn(PII_BALANCE, str(public))
        self.assertNotIn(PII_NAME, str(eviction))
        self.assertEqual(eviction["balance"], PII_BALANCE)
        self.assertEqual(eviction["finance_status"], "Behind")
        self.assertIs(eviction["is_on_payment_plan"], True)
        self.assertNotIn("first_name", eviction)

    def test_property_index_drops_names_and_money(self) -> None:
        ref = partner_members.parse_property_ref(
            {
                "id": 11,
                "address": "6623 Leana Avenue",
                "name": PII_NAME,
                "balance": PII_BALANCE,
            }
        )
        self.assertEqual(ref, {"id": "11", "street": "6623 Leana Avenue"})
        self.assertNotIn(PII_NAME, str(ref))
        self.assertNotIn(PII_BALANCE, str(ref))
        self.assertEqual(partner_members.house_address(ref), "6623 Leana Avenue")

    def test_property_address_keeps_street_city_without_owner_name(self) -> None:
        ref = partner_members.parse_property_ref(
            {
                "id": 11,
                "name": PII_NAME,
                "address": {
                    "street1": "100 Example Lane",
                    "city": "Dallas",
                    "state": "TX",
                    "zip": "75201",
                },
            }
        )
        self.assertEqual(
            partner_members.house_address(ref),
            "100 Example Lane, Dallas, TX 75201",
        )
        self.assertNotIn(PII_NAME, str(ref))


class FetchTests(unittest.TestCase):
    def test_pagination_and_cache_omit_finance_filter(self) -> None:
        calls = []

        def request_fn(url, params):
            calls.append((url, dict(params or {})))
            page = int((params or {}).get("page") or 1)
            if page == 1:
                return {
                    "count": 3,
                    "next": "https://www.padsplit.com/api/partner/members/?page=2",
                    "results": [_raw_member(occupancy_id="1", room_number=1), _raw_member()],
                }
            if page == 2:
                return {
                    "count": 3,
                    "next": None,
                    "results": [_raw_member(occupancy_id="3", room_number=3, is_terminated=True)],
                }
            raise AssertionError(f"unexpected page {page}")

        directory = partner_members.MemberDirectory(object(), {}, request_fn=request_fn)
        first = directory.members_for("9001")
        second = directory.members_for("9001")
        self.assertEqual(len(first), 3)
        self.assertIs(first, second)
        self.assertEqual(len(calls), 2)
        self.assertEqual([params["page"] for _url, params in calls], [1, 2])
        for _url, params in calls:
            self.assertEqual(params["property_id"], "9001")
            self.assertNotIn("finance_status", params)
            self.assertNotIn("occupancy_status", params)
        blob = str(first)
        self.assertNotIn(PII_NAME, blob)
        self.assertNotIn(PII_BALANCE, blob)
        self.assertNotIn(PII_SCORE, blob)
        self.assertIs(first[2]["is_terminated"], True)
        self.assertNotIn("balance", first[0])

        eviction_rows = partner_members.fetch_eviction_members(
            object(), {}, "9001", request_fn=request_fn
        )
        self.assertEqual(eviction_rows[0]["balance"], PII_BALANCE)
        self.assertNotIn(PII_NAME, str({k: v for k, v in eviction_rows[0].items() if k != "balance"}))

    def test_http_error_and_auth_error_have_no_body(self) -> None:
        def response(status):
            resp = MagicMock()
            resp.status_code = status
            resp.text = f"{PII_NAME} {PII_BALANCE}"
            resp.json.return_value = {"results": []}
            return resp

        with patch.object(partner_members, "_authed_request", return_value=response(500)):
            with self.assertRaises(partner_members.MembersRequestError) as caught:
                partner_members.fetch_property_members(object(), {"email": "a"}, "9001")
        self.assertEqual(caught.exception.detail, "request failed")
        self.assertNotIn(PII_NAME, str(caught.exception))
        self.assertNotIn(PII_BALANCE, str(caught.exception))

        with patch.object(partner_members, "_authed_request", return_value=response(401)):
            with self.assertRaises(partner_members.MembersRequestError) as caught:
                partner_members.fetch_property_members(object(), {"email": "a"}, "9001")
        self.assertEqual(caught.exception.detail, "auth failed")
        self.assertNotIn(PII_NAME, str(caught.exception))

    def test_truncated_pagination_fails_closed(self) -> None:
        def request_fn(_url, _params):
            return {"next": "https://example.test/more", "results": [_raw_member()]}

        with patch.object(partner_members, "MAX_PAGES", 1):
            with self.assertRaises(partner_members.MembersRequestError) as caught:
                partner_members.fetch_property_members(object(), {}, "9001", request_fn=request_fn)
        self.assertEqual(caught.exception.detail, "request failed")

    def test_fetch_logs_do_not_include_pii(self) -> None:
        def request_fn(_url, params):
            if int(params["page"]) == 1:
                return {"next": None, "results": [_raw_member()]}
            return {"next": None, "results": []}

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rows = partner_members.fetch_property_members(object(), {}, "9001", request_fn=request_fn)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(PII_NAME, stdout.getvalue())
        self.assertNotIn(PII_NAME, stderr.getvalue())
        self.assertNotIn(PII_BALANCE, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(PII_SCORE, stdout.getvalue() + stderr.getvalue())


class MatchTests(unittest.TestCase):
    def test_graphql_occupancy_id_matches_numeric_row(self) -> None:
        gid = base64.b64encode(b"MessengerOccupancyType:555").decode("ascii")
        rows = [_raw_member(occupancy_id="555", room_number=9, is_terminated=False)]
        # parse first so the match sees status fields only
        parsed = [partner_members.parse_member_row(row) for row in rows]
        match = partner_members.select_member(parsed, occupancy_id=gid, room_number="2", now=NOW)
        self.assertTrue(match.ok)
        self.assertNotIn(PII_NAME, str(parsed))

    def test_room_match_requires_open_move_out(self) -> None:
        rows = [
            partner_members.parse_member_row(
                _raw_member(occupancy_id="1", room_number=2, move_out_date="2020-01-01", is_terminated=True)
            ),
            partner_members.parse_member_row(
                _raw_member(occupancy_id="2", room_number="02", move_out_date="2026-12-01", is_terminated=False)
            ),
        ]
        match = partner_members.select_member(rows, occupancy_id="", room_number="2", now=NOW)
        self.assertTrue(match.ok)

    def test_two_open_rows_do_not_match(self) -> None:
        rows = [
            {"occupancy_id": "1", "room_number": "2", "move_out_date": None, "is_terminated": False},
            {"occupancy_id": "2", "room_number": "2", "move_out_date": "", "is_terminated": False},
        ]
        match = partner_members.select_member(rows, occupancy_id="", room_number="2", now=NOW)
        self.assertFalse(match.ok)
        self.assertEqual(match.detail, "multiple matches")
        self.assertFalse(match.terminal)

    def test_missing_terminated_flag_is_not_terminal(self) -> None:
        rows = [{"occupancy_id": "1", "room_number": "2", "move_out_date": None}]
        match = partner_members.select_member(rows, occupancy_id="1", room_number="2", now=NOW)
        self.assertEqual(match.detail, "is_terminated missing")
        self.assertFalse(match.terminal)

    def test_terminated_true_is_terminal(self) -> None:
        rows = [{"occupancy_id": "1", "room_number": "2", "move_out_date": None, "is_terminated": True}]
        match = partner_members.select_member(rows, occupancy_id="1", room_number="9", now=NOW)
        self.assertEqual(match.detail, "terminated")
        self.assertTrue(match.terminal)


class ProbeTests(unittest.TestCase):
    def test_probe_prints_field_names_without_values(self) -> None:
        def request_fn(_url, _params):
            return {
                "count": 1,
                "next": None,
                "results": [_raw_member(occupancy_id="occ-probe-token")],
            }

        summary = partner_members.probe_property(object(), {}, "9001", request_fn=request_fn)
        text = partner_members.format_probe(summary)
        self.assertIn("pages: 1", text)
        self.assertIn("rows: 1", text)
        for name in (
            "is_terminated",
            "first_name",
            "balance",
            "finance_status",
            "is_on_payment_plan",
            "payment_plan_end_date",
        ):
            self.assertIn(name, text)
        self.assertNotIn(PII_NAME, text)
        self.assertNotIn(PII_BALANCE, text)
        self.assertNotIn(PII_SCORE, text)
        self.assertNotIn("occ-probe-token", text)
        self.assertNotIn("2026-12-31", text)
        self.assertNotIn("current", text)
        self.assertNotIn("results", str(summary.get("rows")))
        self.assertNotIn("balance", summary)  # summary has no balance value key from rows

    def test_probe_cli_prints_summary_only(self) -> None:
        summary = {
            "pages": 2,
            "rows": 4,
            "response_fields": ["next", "results"],
            "fields": ["is_terminated", "occupancy_id", "room_number"],
        }
        session = MagicMock()
        session.__enter__.return_value = session
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(partner_members, "load_credentials", return_value={"email": "a", "password": "b"}), \
             patch.object(partner_members, "create_session", return_value=session), \
             patch.object(partner_members, "login") as login, \
             patch.object(partner_members, "probe_property", return_value=summary), \
             redirect_stdout(stdout), \
             redirect_stderr(stderr):
            code = partner_members.main(["--probe", "9001"])
        self.assertEqual(code, 0)
        login.assert_called_once()
        text = stdout.getvalue()
        self.assertIn("pages: 2", text)
        self.assertIn("fields: is_terminated, occupancy_id, room_number", text)
        self.assertNotIn(PII_NAME, text)
        self.assertNotIn(PII_BALANCE, text)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
