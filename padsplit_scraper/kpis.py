"""Pure PadSplit KPI calculation and payload-normalization helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional


def compute_monthly_kpis(month_data: Dict[str, Any]) -> Dict[str, Any]:
    """Approximate monthly bonuses/penalties using historical metrics that are available."""

    occupancy_pct = round(_to_num(month_data.get("occupancy_pct")), 1)
    avg_flip_days = round(_to_num(month_data.get("avg_flip_days")), 1)
    avg_tenure_days = round(_to_num(month_data.get("avg_tenure_days")), 1)

    bonus_items: List[Dict[str, Any]] = []
    penalty_items: List[Dict[str, Any]] = []
    score = 80

    if occupancy_pct >= 90:
        score += 20
        bonus_items.append({"label": "occupancy >= 90%", "points": 20})
    elif occupancy_pct >= 80:
        score += 10
        bonus_items.append({"label": "occupancy 80-89%", "points": 10})

    # Historical endpoint currently exposes only a portfolio tenure summary, not true per-month tenure.
    if avg_tenure_days >= 180:
        score += 20
        bonus_items.append({"label": "avg tenure >= 180d", "points": 20})
    elif avg_tenure_days >= 160:
        score += 10
        bonus_items.append({"label": "avg tenure 160-179d", "points": 10})
    elif avg_tenure_days >= 152:
        pass
    elif avg_tenure_days >= 140:
        score -= 10
        penalty_items.append({"label": "avg tenure 140-151d", "points": -10, "count": 1})
    else:
        score -= 20
        penalty_items.append({"label": "avg tenure < 140d", "points": -20, "count": 1})

    if occupancy_pct < 70:
        score -= 20
        penalty_items.append({"label": "occupancy < 70%", "points": -20, "count": 1})
    elif occupancy_pct < 75:
        score -= 10
        penalty_items.append({"label": "occupancy 70-74%", "points": -10, "count": 1})

    # Approximation: if monthly average is above threshold, count as one trigger.
    if avg_flip_days > 5:
        score -= 2
        penalty_items.append({"label": "flip rooms > 5d (approx)", "points": -2, "count": 1})

    score = max(0, min(125, int(round(score))))
    return {
        "score": score,
        "base_score": 80,
        "bonuses": bonus_items,
        "penalties": penalty_items,
        "partial": True,
        "note": "ticket penalties not available for historical months",
    }


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_num(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    return 0.0


def _find_property_key(room: Dict[str, Any]) -> Optional[str]:
    candidates = [
        room.get("psproperty_id"),
        room.get("property_id"),
        room.get("property_pk"),
        room.get("property"),
        (room.get("property_data") or {}).get("id") if isinstance(room.get("property_data"), dict) else None,
    ]
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        return str(candidate)
    address = room.get("property_address") or room.get("property_name")
    if address:
        return str(address)
    return None


def _room_property_label(room: Dict[str, Any]) -> str:
    address_obj = room.get("address")
    if isinstance(address_obj, dict):
        street = str(address_obj.get("full_street") or "").strip()
        city = str(address_obj.get("city") or "").strip()
        state = str(address_obj.get("state") or "").strip()
        if street and city and state:
            return f"{street}, {city}, {state}"
        if street:
            return street

    parts = []
    address = room.get("property_address")
    location = room.get("property_location")
    if address:
        parts.append(str(address))
    if location:
        parts.append(str(location))
    if parts:
        return ", ".join(parts)
    prop = room.get("property_data")
    if isinstance(prop, dict):
        maybe_address = prop.get("address") or prop.get("display_address") or prop.get("name")
        if maybe_address:
            return str(maybe_address)
    return "Unknown Property"


def _extract_earnings_rows(earnings_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("earnings", "results", "data", "items"):
        val = earnings_payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def _find_net_revenue(row: Dict[str, Any]) -> float:
    for key in ("net_revenue", "netRevenue", "net", "host_net", "hostNet", "owner_net"):
        if key in row:
            return _to_num(row.get(key))
    return 0.0


def _flatten_tasks(tasks_by_bucket: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    tickets: List[Dict[str, Any]] = []
    for ticket_list in tasks_by_bucket.values():
        if not isinstance(ticket_list, list):
            continue
        for ticket in ticket_list:
            if isinstance(ticket, dict):
                tickets.append(ticket)
    return tickets


def compute_kpis(
    rooms: List[Dict[str, Any]],
    properties: List[Dict[str, Any]],
    earnings_payload: Dict[str, Any],
    tasks_by_bucket: Dict[str, List[Dict[str, Any]]],
    now: datetime,
    subjective_score: int = 0,
) -> Dict[str, Any]:
    listed_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "listed"]
    occupied_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "occupied"]
    flip_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "flip-room"]
    move_in_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "move-in"]

    listed_days = [_to_num(r.get("days_in_current_status")) for r in listed_rooms]
    occupied_days = [_to_num(r.get("days_in_current_status")) for r in occupied_rooms]
    flip_days = [_to_num(r.get("days_in_current_status")) for r in flip_rooms]

    listed_over_14 = [r for r in listed_rooms if _to_num(r.get("days_in_current_status")) > 14]
    listed_over_21 = [r for r in listed_rooms if _to_num(r.get("days_in_current_status")) > 21]
    listed_over_30 = [r for r in listed_rooms if _to_num(r.get("days_in_current_status")) > 30]
    flip_over_5 = [r for r in flip_rooms if _to_num(r.get("days_in_current_status")) > 5]

    total_rooms = sum(len(p.get("rooms", [])) for p in properties if isinstance(p.get("rooms"), list))
    if total_rooms == 0:
        total_rooms = len(rooms)
    occupied_from_properties = int(sum(_to_num(p.get("occupied")) for p in properties))
    inactive_from_properties = int(sum(_to_num(p.get("inactive")) for p in properties))
    listed_count = len(listed_rooms)
    flip_count = len(flip_rooms)
    occupied = occupied_from_properties if occupied_from_properties > 0 else len(occupied_rooms)

    effective_total = max(total_rooms - inactive_from_properties, 0)
    occupancy_pct = round((occupied / effective_total) * 100, 2) if effective_total else 0.0

    tickets = _flatten_tasks(tasks_by_bucket)
    open_statuses = {"submitted", "accepted", "in_progress", "on_hold", "eviction"}
    open_tickets: List[Dict[str, Any]] = []
    open_ticket_ages: List[float] = []
    tickets_over_7d = 0
    tickets_over_14d = 0
    for ticket in tickets:
        status = str(ticket.get("status", "")).lower()
        if status not in open_statuses:
            continue
        created_dt = _parse_iso(ticket.get("created"))
        age_days = 0.0
        if created_dt:
            age_days = max((now - created_dt).total_seconds() / 86400.0, 0.0)
        if age_days > 7:
            tickets_over_7d += 1
        if age_days > 14:
            tickets_over_14d += 1
        open_ticket_ages.append(age_days)
        open_tickets.append(
            {
                "id": ticket.get("id"),
                "status": ticket.get("status"),
                "category": ticket.get("category"),
                "location": ticket.get("location"),
                "room_number": ticket.get("room_number"),
                "details": ticket.get("details"),
                "property_id": ticket.get("property_id"),
                "property_address": (ticket.get("property_address") or {}).get("street1")
                if isinstance(ticket.get("property_address"), dict)
                else None,
                "created": ticket.get("created"),
                "age_days": round(age_days, 1),
            }
        )
    open_tickets.sort(key=lambda t: _to_num(t.get("age_days")), reverse=True)

    earnings_rows = _extract_earnings_rows(earnings_payload)
    earnings_rows_sorted = sorted(
        earnings_rows,
        key=lambda r: (_parse_iso(r.get("month")) or _parse_iso(r.get("date")) or datetime.min.replace(tzinfo=timezone.utc)),
    )
    monthly_revenue_series = []
    for row in earnings_rows_sorted:
        net = _find_net_revenue(row)
        label = str(row.get("month") or row.get("date") or row.get("label") or "")
        monthly_revenue_series.append({"label": label, "net_revenue": round(net, 2)})
    monthly_net_revenue = monthly_revenue_series[-1]["net_revenue"] if monthly_revenue_series else 0.0
    revenue_per_room = round(monthly_net_revenue / total_rooms, 2) if total_rooms else 0.0

    property_room_map: Dict[str, Dict[str, Any]] = {}
    for p in properties:
        pid = str(p.get("id", ""))
        label = ", ".join([str(p.get("address") or "").strip(), str(p.get("location") or "").strip()]).strip(", ")
        property_room_map[pid] = {
            "property_id": p.get("id"),
            "property": label or str(p.get("address") or p.get("location") or "Unknown Property"),
            "rooms": len(p.get("rooms", [])) if isinstance(p.get("rooms"), list) else int(_to_num(p.get("rooms"))),
            "occupied": int(_to_num(p.get("occupied"))),
            "vacant": int(_to_num(p.get("vacant"))),
            "inactive": int(_to_num(p.get("inactive"))),
            "needs_flip": int(_to_num(p.get("needs_flip"))),
            "move_in": int(_to_num(p.get("move_in"))),
            "listed": 0,
            "flip": 0,
        }
    for room in rooms:
        key = _find_property_key(room)
        if not key:
            continue
        if key not in property_room_map:
            property_room_map[key] = {
                "property_id": key,
                "property": _room_property_label(room),
                "rooms": 0,
                "occupied": 0,
                "vacant": 0,
                "inactive": 0,
                "needs_flip": 0,
                "move_in": 0,
                "listed": 0,
                "flip": 0,
            }
        property_room_map[key]["rooms"] += 1
        status = str(room.get("detailed_status", "")).lower()
        if status == "listed":
            property_room_map[key]["listed"] += 1
        if status == "flip-room":
            property_room_map[key]["flip"] += 1
    per_property = []
    for item in property_room_map.values():
        denom = max(item["rooms"] - item["inactive"], 0)
        item["occupancy_pct"] = round((item["occupied"] / denom) * 100, 2) if denom else 0.0
        item["mini_score"] = max(
            0,
            min(
                100,
                round(
                    item["occupancy_pct"]
                    - (item["listed"] * 2.0)
                    - (item["flip"] * 3.0),
                    1,
                ),
            ),
        )
        per_property.append(item)
    per_property.sort(key=lambda p: p.get("occupancy_pct", 0.0))

    avg_listed_days = round(sum(listed_days) / len(listed_days), 1) if listed_days else 0.0
    max_listed_days = int(max(listed_days)) if listed_days else 0
    avg_tenure_days = round(sum(occupied_days) / len(occupied_days), 1) if occupied_days else 0.0
    median_tenure_days = int(round(median(occupied_days))) if occupied_days else 0
    avg_flip_days = round(sum(flip_days) / len(flip_days), 1) if flip_days else 0.0
    max_flip_days = int(max(flip_days)) if flip_days else 0
    avg_ticket_age_days = round(sum(open_ticket_ages) / len(open_ticket_ages), 1) if open_ticket_ages else 0.0

    score = 80
    bonus_items = []
    penalty_items = []

    if occupancy_pct >= 90:
        score += 20
        bonus_items.append({"label": "occupancy >= 90%", "points": 20})
    elif occupancy_pct >= 80:
        score += 10
        bonus_items.append({"label": "occupancy 80-89%", "points": 10})
    elif occupancy_pct >= 75:
        pass
    elif occupancy_pct >= 70:
        score -= 10
        penalty_items.append({"label": "occupancy 70-74%", "points": -10, "count": 1})
    else:
        score -= 20
        penalty_items.append({"label": "occupancy < 70%", "points": -20, "count": 1})

    if avg_tenure_days >= 180:
        score += 20
        bonus_items.append({"label": "avg tenure >= 180d", "points": 20})
    elif avg_tenure_days >= 160:
        score += 10
        bonus_items.append({"label": "avg tenure 160-179d", "points": 10})
    elif avg_tenure_days >= 152:
        pass
    elif avg_tenure_days >= 140:
        score -= 10
        penalty_items.append({"label": "avg tenure 140-151d", "points": -10, "count": 1})
    else:
        score -= 20
        penalty_items.append({"label": "avg tenure < 140d", "points": -20, "count": 1})

    clamped_subj = max(0, min(20, int(round(_to_num(subjective_score)))))
    if clamped_subj:
        score += clamped_subj
        bonus_items.append({"label": "daily hustle (subjective)", "points": clamped_subj})

    listed_penalty = len(listed_over_21) * 1
    if listed_penalty:
        score -= listed_penalty
        penalty_items.append(
            {"label": "rooms listed > 21d", "points": -listed_penalty, "count": len(listed_over_21)}
        )

    flip_penalty = len(flip_over_5) * 2
    if flip_penalty:
        score -= flip_penalty
        penalty_items.append(
            {"label": "flip rooms > 5d", "points": -flip_penalty, "count": len(flip_over_5)}
        )

    ticket_penalty = tickets_over_14d * 2
    if ticket_penalty:
        score -= ticket_penalty
        penalty_items.append(
            {
                "label": "open tickets > 14d",
                "points": -ticket_penalty,
                "count": tickets_over_14d,
            }
        )

    score = max(0, min(125, int(round(score))))

    vacancy_rooms = [
        {
            "id": r.get("id"),
            "property": _room_property_label(r),
            "room_number": r.get("room_number"),
            "days_listed": round(_to_num(r.get("days_in_current_status")), 1),
            "base_price": _to_num(r.get("base_price")),
            "last_room_price": _to_num(r.get("last_room_price")),
            "latest_occupancy_move_out_date": r.get("latest_occupancy_move_out_date"),
        }
        for r in listed_rooms
    ]
    vacancy_rooms.sort(key=lambda x: _to_num(x.get("days_listed")), reverse=True)

    flip_room_rows = [
        {
            "id": r.get("id"),
            "property": _room_property_label(r),
            "room_number": r.get("room_number"),
            "days_in_flip": round(_to_num(r.get("days_in_current_status")), 1),
        }
        for r in flip_rooms
    ]
    flip_room_rows.sort(key=lambda x: _to_num(x.get("days_in_flip")), reverse=True)

    retention_histogram = {"0-30": 0, "31-90": 0, "91-180": 0, "181+": 0}
    for days in occupied_days:
        if days <= 30:
            retention_histogram["0-30"] += 1
        elif days <= 90:
            retention_histogram["31-90"] += 1
        elif days <= 180:
            retention_histogram["91-180"] += 1
        else:
            retention_histogram["181+"] += 1

    return {
        "score": score,
        "base_score": 80,
        "bonus_points": sum(item["points"] for item in bonus_items),
        "penalty_points": -sum(abs(item["points"]) for item in penalty_items),
        "subjective_score": clamped_subj,
        "bonuses": bonus_items,
        "penalties": penalty_items,
        "occupancy_pct": occupancy_pct,
        "total_rooms": total_rooms,
        "occupied": occupied,
        "listed": listed_count,
        "flip": flip_count,
        "move_in": len(move_in_rooms),
        "avg_listed_days": avg_listed_days,
        "max_listed_days": max_listed_days,
        "rooms_over_14d": len(listed_over_14),
        "rooms_over_30d": len(listed_over_30),
        "avg_tenure_days": avg_tenure_days,
        "median_tenure_days": median_tenure_days,
        "avg_flip_days": avg_flip_days,
        "max_flip_days": max_flip_days,
        "open_tickets": len(open_tickets),
        "tickets_over_7d": tickets_over_7d,
        "tickets_over_14d": tickets_over_14d,
        "avg_ticket_age_days": avg_ticket_age_days,
        "monthly_net_revenue": monthly_net_revenue,
        "revenue_per_room": revenue_per_room,
        "per_property": per_property,
        "vacancy_rooms": vacancy_rooms,
        "flip_rooms": flip_room_rows,
        "open_ticket_items": open_tickets,
        "monthly_revenue_series": monthly_revenue_series,
        "retention_histogram": retention_histogram,
    }
