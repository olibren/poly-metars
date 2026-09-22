"""Pure selection rules. No networking, storage or market-specific exceptions."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from .metar import UTC, iso, parse_time


def source_choice(reports):
    variants = sorted(reports, key=lambda r: (r["correction"], r["id"]))
    if not variants:
        return {"report": None, "ambiguous": False, "variants": variants}
    rank = max(r["correction"] for r in variants)
    newest = [r for r in variants if r["correction"] == rank and r["eligible"]]
    if not newest:
        return {"report": None, "ambiguous": False, "variants": variants}
    temperatures = {r["temperature_c"] for r in newest}
    # Equivalent temperatures can differ in wind/clouds. Preserve all raw variants.
    ambiguous = len(temperatures) > 1
    return {
        "report": None if ambiguous else newest[0],
        "ambiguous": ambiguous,
        "variants": variants,
    }


def resolve(reports, source_order):
    sources = {
        source: source_choice([r for r in reports if r["source"] == source])
        for source in source_order
    }
    selected, blocked = None, False
    for source in source_order:
        choice = sources[source]
        if choice["ambiguous"]:
            blocked = True
            break
        if choice["report"]:
            selected = choice["report"]
            break
    values = {
        choice["report"]["temperature_c"]
        for choice in sources.values()
        if choice["report"]
    }
    conflict = len(values) > 1 or any(c["ambiguous"] for c in sources.values())
    return {
        "sources": sources,
        "selected": selected,
        "conflict": conflict,
        "status": "blocked"
        if blocked
        else "disagreement"
        if conflict
        else "fallback"
        if selected and selected["source"] != source_order[0]
        else "selected"
        if selected
        else "missing",
    }


def market_value(temp_c, unit):
    value = Decimal(str(temp_c))
    if unit == "F":
        value = value * 9 / 5 + 32
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def day_bounds(day, timezone):
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC), (local + timedelta(days=1)).astimezone(UTC)


def daily(airport, date, reports, policy, now):
    start, end = day_bounds(date, airport["timezone"])
    in_day = [
        r
        for r in reports
        if r["icao"] == airport["icao"] and start <= parse_time(r["observed_at"]) < end
    ]
    routine = [r for r in in_day if r["report_type"] == "METAR"]
    groups = {}
    for report in routine:
        groups.setdefault(report["observed_at"], []).append(report)
    expected = set()
    cursor = start.replace(second=0, microsecond=0)
    while cursor < end:
        if cursor.minute in airport["routine_minutes_utc"]:
            expected.add(iso(cursor))
        cursor += timedelta(minutes=1)
    rows = []
    for timestamp in sorted(expected | set(groups)):
        result = resolve(groups.get(timestamp, []), policy["source_order"])
        if parse_time(timestamp) > now and not result["selected"]:
            result["status"] = "pending"
        rows.append(
            {
                "observed_at": timestamp,
                "local_time": parse_time(timestamp)
                .astimezone(ZoneInfo(airport["timezone"]))
                .strftime("%H:%M"),
                "expected": timestamp in expected,
                **result,
            }
        )
    values = [
        market_value(row["selected"]["temperature_c"], airport["unit"])
        for row in rows
        if row["selected"]
    ]
    elapsed = [
        r
        for r in rows
        if r["expected"]
        and parse_time(r["observed_at"])
        <= now - timedelta(minutes=policy["delivery_grace_minutes"])
    ]
    missing = sum(not r["selected"] for r in elapsed)
    conflicts = sum(r["conflict"] for r in rows)
    summary = {
        "high": max(values) if values else None,
        "low": min(values) if values else None,
        "expected": len(expected),
        "received": sum(r["expected"] and r["selected"] is not None for r in rows),
        "missing": missing,
        "conflicts": conflicts,
        "status": "review_required"
        if conflicts
        else "incomplete"
        if missing
        else "day_in_progress"
        if now < end
        else "provisional",
    }
    return {
        "airport": airport,
        "date": date,
        "rows": rows,
        "summary": summary,
        "excluded": [r for r in in_day if not r["eligible"]],
    }
