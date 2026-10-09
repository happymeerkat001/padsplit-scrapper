#!/usr/bin/env python3
"""Read PadSplit partner members for one property.

GET https://www.padsplit.com/api/partner/members/

The query shape (property_id, page, page_size, and optional finance_status /
occupancy_status filters) comes from the site JS and is not verified at
runtime. This module does not send finance_status. Probe before relying on
the field names.

Returned rows keep status fields: occupancy_id, room_number, move dates,
is_terminated, occupancy_status, finance_status, is_on_payment_plan, and
payment_plan_end_date. Names are dropped at parse time and are never logged.

Balance is not on those rows. ``eviction_member_row`` / ``fetch_eviction_members``
keep it for ``padsplit_scraper.evictions`` only. Do not write that value to
latest.json, docs/, Firestore, Discord text, or logs. The probe prints field
names and counts only.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

try:
    from padsplit_scraper.new_booking import occupancy_pk_from_gid
    from padsplit_scraper.scraper import (
        BASE_URL,
        DEFAULT_TIMEOUT,
        _authed_request,
        create_session,
        load_credentials,
        login,
    )
except ModuleNotFoundError:  # python padsplit_scraper/partner_members.py
    from new_booking import occupancy_pk_from_gid  # type: ignore
    from scraper import (  # type: ignore
        BASE_URL,
        DEFAULT_TIMEOUT,
        _authed_request,
        create_session,
        load_credentials,
        login,
    )


MEMBERS_URL = f"{BASE_URL}/api/partner/members/"
PROPERTIES_URL = f"{BASE_URL}/api/partner/properties/"
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 50

RequestFn = Callable[[str, Optional[Dict[str, Any]]], Any]

_OCCUPANCY_ID_KEYS = ("occupancy_id", "occupancyId")
_ROOM_KEYS = ("room_number", "roomNumber")
_MOVE_IN_KEYS = ("move_in_date", "moveInDate", "move_in")
_MOVE_OUT_KEYS = ("move_out_date", "moveOutDate", "move_out")
_TERMINATED_KEYS = ("is_terminated", "isTerminated")
_STATUS_KEYS = ("occupancy_status", "occupancyStatus", "status")
_FINANCE_STATUS_KEYS = ("finance_status", "financeStatus")
_PAYMENT_PLAN_KEYS = ("is_on_payment_plan", "isOnPaymentPlan")
_PAYMENT_PLAN_END_KEYS = (
    "payment_plan_end_date",
    "paymentPlanEndDate",
    "payment_plan_end",
)
_BALANCE_KEYS = ("balance", "balance_due", "balanceDue")


class MembersRequestError(Exception):
    """Fail-closed member lookup. ``detail`` is safe to log (no body, no PII)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass
class MemberMatch:
    ok: bool
    detail: str
    terminal: bool = False


def _first(payload: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    return None


def _date_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    return text or None


def _terminated_value(raw: Dict[str, Any]) -> Optional[bool]:
    for key in _TERMINATED_KEYS:
        if key in raw:
            return _parse_bool(raw.get(key))
    return None


def _status_text(raw: Dict[str, Any]) -> Optional[str]:
    for key in _STATUS_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _finance_status_text(raw: Dict[str, Any]) -> Optional[str]:
    for key in _FINANCE_STATUS_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _payment_plan_value(raw: Dict[str, Any]) -> Optional[bool]:
    for key in _PAYMENT_PLAN_KEYS:
        if key in raw:
            return _parse_bool(raw.get(key))
    return None


def _balance_text(raw: Dict[str, Any]) -> Optional[str]:
    """Copy a balance string. Callers must not log or post the result."""
    for key in _BALANCE_KEYS:
        if key not in raw:
            continue
        value = raw.get(key)
        if value in (None, "") or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return f"{value:.2f}"
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _room_text(raw: Dict[str, Any]) -> str:
    room = _first(raw, _ROOM_KEYS)
    if room in (None, "") and isinstance(raw.get("room"), dict):
        room = _first(raw["room"], ("room_number", "roomNumber", "number"))
    if room in (None, ""):
        return ""
    return str(room).strip()


def parse_member_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Copy status fields only. Names and balance never leave this function."""
    occupancy_id = _first(raw, _OCCUPANCY_ID_KEYS)
    if occupancy_id in (None, ""):
        occupancy_id = raw.get("id")
    return {
        "occupancy_id": "" if occupancy_id in (None, "") else str(occupancy_id).strip(),
        "room_number": _room_text(raw),
        "move_in_date": _date_text(_first(raw, _MOVE_IN_KEYS)),
        "move_out_date": _date_text(_first(raw, _MOVE_OUT_KEYS)),
        "is_terminated": _terminated_value(raw),
        "occupancy_status": _status_text(raw),
        "finance_status": _finance_status_text(raw),
        "is_on_payment_plan": _payment_plan_value(raw),
        "payment_plan_end_date": _date_text(_first(raw, _PAYMENT_PLAN_END_KEYS)),
    }


def eviction_member_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Status row plus balance, for the evictions module only.

    Do not log this row, write it to latest.json / docs/ / Firestore, or put
    balance in Discord text.
    """
    row = parse_member_row(raw)
    row["balance"] = _balance_text(raw)
    return row


def parse_property_ref(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Property id plus street label. Drops names and money."""
    property_id = raw.get("id")
    if property_id in (None, ""):
        property_id = raw.get("pk")
    if property_id in (None, ""):
        property_id = raw.get("psproperty_id")
    if property_id in (None, ""):
        return None
    address = raw.get("address")
    street = ""
    city = ""
    state = ""
    postal = ""
    if isinstance(address, dict):
        street = str(address.get("street1") or address.get("full_street") or "").strip()
        city = str(address.get("city") or "").strip()
        state = str(address.get("state") or address.get("region") or "").strip()
        postal = str(address.get("zip") or address.get("postal_code") or "").strip()
    elif isinstance(address, str):
        street = address.strip()
    if not street:
        street = str(raw.get("full_street") or "").strip()
    ref: Dict[str, str] = {"id": str(property_id).strip(), "street": street}
    if city:
        ref["city"] = city
    if state:
        ref["state"] = state
    if postal:
        ref["zip"] = postal
    return ref


def house_address(ref: Dict[str, Any]) -> str:
    """Property street line. Owner names are not part of this string."""
    street = str(ref.get("street") or "").strip()
    city = str(ref.get("city") or "").strip()
    state = str(ref.get("state") or "").strip()
    postal = str(ref.get("zip") or "").strip()
    locality = ", ".join(part for part in (city, state) if part)
    if postal:
        locality = f"{locality} {postal}".strip()
    if street and locality:
        return f"{street}, {locality}"
    return street or locality


def _auth_failure(exc: BaseException) -> bool:
    message = str(exc)
    return (
        "Login failed" in message
        or "sessionid" in message
        or "could not be refreshed" in message
    )


def _request_json(
    session: Any,
    creds: Optional[Dict[str, str]],
    url: str,
    params: Optional[Dict[str, Any]],
    *,
    request_fn: Optional[RequestFn] = None,
) -> Any:
    if request_fn is not None:
        try:
            return request_fn(url, params)
        except MembersRequestError:
            raise
        except Exception:
            raise MembersRequestError("request failed") from None
    try:
        response = _authed_request(
            session,
            "GET",
            url,
            creds=creds or {},
            login_fn=login,
            headers={
                "Accept": "application/json",
                "Referer": f"{BASE_URL}/host/",
            },
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
    except RuntimeError as exc:
        if _auth_failure(exc):
            raise MembersRequestError("auth failed") from None
        raise MembersRequestError("request failed") from None
    except Exception:
        raise MembersRequestError("request failed") from None
    status = int(getattr(response, "status_code", 0) or 0)
    if status in (401, 403):
        raise MembersRequestError("auth failed")
    if status < 200 or status >= 300:
        raise MembersRequestError("request failed")
    try:
        return response.json()
    except Exception:
        raise MembersRequestError("request failed") from None


def _page_parts(payload: Any) -> Tuple[List[Dict[str, Any]], Set[str], bool]:
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows, set(), False
    if not isinstance(payload, dict):
        raise MembersRequestError("request failed")
    response_fields = {str(key) for key in payload.keys()}
    results = payload.get("results")
    if results is None:
        results = []
    if not isinstance(results, list):
        raise MembersRequestError("request failed")
    rows = [row for row in results if isinstance(row, dict)]
    has_next = bool(payload.get("next"))
    page = payload.get("page")
    num_pages = payload.get("num_pages")
    if not has_next and page not in (None, "") and num_pages not in (None, ""):
        try:
            has_next = int(page) < int(num_pages)
        except (TypeError, ValueError):
            has_next = False
    return rows, response_fields, has_next


def fetch_member_pages(
    session: Any,
    creds: Optional[Dict[str, str]],
    property_id: str,
    *,
    request_fn: Optional[RequestFn] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_balance: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str], List[str], int]:
    """Every parsed row for one property, plus raw field names and page count.

    Field-name lists are for the probe. They are names only. Parsed rows do
    not include names. Balance is included only when ``include_balance`` is
    set, and that path is for the evictions module. A truncated page walk
    fails closed.
    """
    key = str(property_id).strip()
    if not key:
        raise MembersRequestError("request failed")
    parsed: List[Dict[str, Any]] = []
    row_fields: Set[str] = set()
    response_fields: Set[str] = set()
    page = 1
    pages_fetched = 0
    while page <= MAX_PAGES:
        params = {
            "property_id": key,
            "page": page,
            "page_size": page_size,
        }
        payload = _request_json(session, creds, MEMBERS_URL, params, request_fn=request_fn)
        raw_rows, page_fields, has_next = _page_parts(payload)
        pages_fetched += 1
        response_fields.update(page_fields)
        if not raw_rows:
            break
        parser = eviction_member_row if include_balance else parse_member_row
        for raw in raw_rows:
            row_fields.update(str(name) for name in raw.keys())
            parsed.append(parser(raw))
        if not has_next:
            break
        page += 1
    else:
        # The page cap was still followed by another page. Partial rows are not returned.
        raise MembersRequestError("request failed")
    return parsed, sorted(row_fields), sorted(response_fields), pages_fetched


def fetch_property_members(
    session: Any,
    creds: Optional[Dict[str, str]],
    property_id: str,
    *,
    request_fn: Optional[RequestFn] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> List[Dict[str, Any]]:
    """All parsed member rows for one property. No names or balance."""
    rows, _row_fields, _response_fields, _pages = fetch_member_pages(
        session,
        creds,
        property_id,
        request_fn=request_fn,
        page_size=page_size,
        include_balance=False,
    )
    return rows


def fetch_eviction_members(
    session: Any,
    creds: Optional[Dict[str, str]],
    property_id: str,
    *,
    request_fn: Optional[RequestFn] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> List[Dict[str, Any]]:
    """Member rows including balance. Only the evictions module should call this.

    Do not log the returned rows or write them to latest.json, docs/, Firestore,
    or Discord.
    """
    rows, _row_fields, _response_fields, _pages = fetch_member_pages(
        session,
        creds,
        property_id,
        request_fn=request_fn,
        page_size=page_size,
        include_balance=True,
    )
    return rows


def fetch_property_index(
    session: Any,
    creds: Optional[Dict[str, str]],
    *,
    request_fn: Optional[RequestFn] = None,
) -> List[Dict[str, str]]:
    """Id and street for each partner property. Names and money are dropped."""
    payload = _request_json(session, creds, PROPERTIES_URL, None, request_fn=request_fn)
    if isinstance(payload, dict):
        raw_rows = payload.get("results")
    else:
        raw_rows = payload
    if not isinstance(raw_rows, list):
        raise MembersRequestError("request failed")
    refs: List[Dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        ref = parse_property_ref(raw)
        if ref is not None:
            refs.append(ref)
    return refs


def probe_property(
    session: Any,
    creds: Optional[Dict[str, str]],
    property_id: str,
    *,
    request_fn: Optional[RequestFn] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Counts and field names only. No row values, including balance."""
    _rows, row_fields, response_fields, pages = fetch_member_pages(
        session,
        creds,
        property_id,
        request_fn=request_fn,
        page_size=page_size,
    )
    return {
        "pages": pages,
        "rows": len(_rows),
        "response_fields": response_fields,
        "fields": row_fields,
    }


def format_probe(summary: Dict[str, Any]) -> str:
    response_fields = ", ".join(str(name) for name in (summary.get("response_fields") or []))
    fields = ", ".join(str(name) for name in (summary.get("fields") or []))
    return (
        f"pages: {int(summary.get('pages') or 0)}\n"
        f"rows: {int(summary.get('rows') or 0)}\n"
        f"response_fields: {response_fields}\n"
        f"fields: {fields}\n"
    )


class MemberDirectory:
    """Per-run cache of member rows and the property index."""

    def __init__(
        self,
        session: Any,
        creds: Optional[Dict[str, str]] = None,
        *,
        request_fn: Optional[RequestFn] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.session = session
        self.creds = creds or {}
        self.request_fn = request_fn
        self.page_size = page_size
        self._members: Dict[str, List[Dict[str, Any]]] = {}
        self._member_errors: Dict[str, MembersRequestError] = {}
        self._properties: Optional[List[Dict[str, str]]] = None
        self._properties_error: Optional[MembersRequestError] = None

    def members_for(self, property_id: str) -> List[Dict[str, Any]]:
        key = str(property_id).strip()
        if not key:
            raise MembersRequestError("request failed")
        if key in self._member_errors:
            raise self._member_errors[key]
        if key not in self._members:
            try:
                self._members[key] = fetch_property_members(
                    self.session,
                    self.creds,
                    key,
                    request_fn=self.request_fn,
                    page_size=self.page_size,
                )
            except MembersRequestError as exc:
                self._member_errors[key] = exc
                raise
        return self._members[key]

    def property_index(self) -> List[Dict[str, str]]:
        if self._properties_error is not None:
            raise self._properties_error
        if self._properties is None:
            try:
                self._properties = fetch_property_index(
                    self.session,
                    self.creds,
                    request_fn=self.request_fn,
                )
            except MembersRequestError as exc:
                self._properties_error = exc
                raise
        return self._properties


def occupancy_ids_match(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    left_pk = occupancy_pk_from_gid(a)
    right_pk = occupancy_pk_from_gid(b)
    return left_pk is not None and left_pk == right_pk


def rooms_match(left: Any, right: Any) -> bool:
    def norm(value: Any) -> str:
        text = str(value or "").strip()
        if text.isdigit():
            return str(int(text))
        return text.lower()

    left_norm = norm(left)
    return bool(left_norm) and left_norm == norm(right)


def _as_date(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def move_out_is_open(value: Any, now: datetime) -> bool:
    """Empty move-out is open. A date today or later is still open.

    An unparseable move-out is not open (fail closed for room matching).
    """
    if value in (None, ""):
        return True
    parsed = _as_date(value)
    if parsed is None:
        return False
    today = now.astimezone(timezone.utc).date() if now.tzinfo else now.date()
    return parsed.date() >= today


def select_member(
    rows: Sequence[Dict[str, Any]],
    *,
    occupancy_id: str = "",
    room_number: str = "",
    now: datetime,
) -> MemberMatch:
    """Exactly one row, or a fail-closed reason.

    Occupancy id wins when the caller has one. Otherwise match room number
    among rows whose move-out is empty or not in the past. The caller passes
    rows for one property (or an explicit cross-property occupancy-id scan).
    """
    if occupancy_id:
        matches = [
            row
            for row in rows
            if isinstance(row, dict) and occupancy_ids_match(row.get("occupancy_id"), occupancy_id)
        ]
    else:
        if not str(room_number or "").strip():
            return MemberMatch(False, "no match")
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and rooms_match(row.get("room_number"), room_number)
            and move_out_is_open(row.get("move_out_date"), now)
        ]
    if len(matches) == 0:
        return MemberMatch(False, "no match")
    if len(matches) > 1:
        return MemberMatch(False, "multiple matches")
    flag = matches[0].get("is_terminated")
    if flag is True:
        return MemberMatch(False, "terminated", terminal=True)
    if flag is False:
        return MemberMatch(True, "ok")
    return MemberMatch(False, "is_terminated missing")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe PadSplit partner members. Prints counts and field names only."
    )
    parser.add_argument("--probe", required=True, metavar="property_id")
    args = parser.parse_args(list(argv) if argv is not None else None)
    property_id = str(args.probe).strip()
    if not property_id:
        sys.stderr.write("probe failed (request failed)\n")
        return 1
    try:
        creds = load_credentials()
        with create_session() as session:
            login(session, creds["email"], creds["password"], force=False)
            summary = probe_property(session, creds, property_id)
    except MembersRequestError as exc:
        sys.stderr.write(f"probe failed ({exc.detail})\n")
        return 1
    except SystemExit:
        raise
    except Exception as exc:
        detail = "auth failed" if _auth_failure(exc) else "request failed"
        sys.stderr.write(f"probe failed ({detail})\n")
        return 1
    sys.stdout.write(format_probe(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
