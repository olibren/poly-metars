"""Pure selection rules. No networking, storage or market-specific exceptions."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from zoneinfo import ZoneInfo
from .metar import UTC, iso, parse_time


def _reading(report):
    return report["temperature_c"] if report["eligible"] else None


def _version(report):
    """Match legacy copies to timed copies without conflating distinct reports."""
    return tuple(report.get(k) for k in (
        "raw", "report_type", "correction", "eligible", "reason", "temperature_c", "precision",
    ))


def _latest_source_versions(reports):
    timed, untimed = [], []
    for report in reports:
        value = report.get("source_received_at")
        if report["source"] == "noaa_awc" and isinstance(value, str):
            try:
                stamp = parse_time(value)
                if stamp >= parse_time(report["observed_at"]):
                    timed.append((stamp, report))
                    continue
            except ValueError:
                pass
        untimed.append(report)
    if not timed:
        return reports, False
    latest = max(stamp for stamp, _ in timed)
    known = {_version(report) for _, report in timed}
    # A re-fetched legacy copy has no independent unknown version order. Distinct
    # untimed versions remain candidates; never assume they precede timed ones.
    candidates = [report for stamp, report in timed if stamp == latest]
    candidates.extend(report for report in untimed if _version(report) not in known)
    return sorted(candidates, key=lambda r: r["id"]), True


NIL_WITHDRAWAL_MODES = (None, "explicit_correction_only")


def _placeholder_nil(report):
    """A NIL without COR/CCx says no report was relayed; it does not retract one."""
    return report.get("reason") == "nil_report" and report["correction"] == 0


def source_choice(reports, revision_order=None, nil_withdrawal=None):
    variants = sorted(reports, key=lambda r: (r["correction"], r["id"]))
    if not variants:
        return {"report": None, "ambiguous": False, "variants": variants}
    rank = max(r["correction"] for r in variants)
    if revision_order == "source_receipt_time":
        ranked = [r for r in variants if r["correction"] == rank]
        if nil_withdrawal == "explicit_correction_only":
            # Placeholder NILs stay in variants as evidence but are not versions.
            ranked = [r for r in ranked if not _placeholder_nil(r)]
        candidates, ordered = _latest_source_versions(ranked)
        readings = {_reading(r) for r in candidates}
        ambiguous = len(readings) > 1
        eligible = [r for r in candidates if r["eligible"]]
        return {
            "report": eligible[0] if eligible and not ambiguous else None,
            "ambiguous": ambiguous,
            "variants": variants,
            "selection_basis": "source_receipt_time" if ordered else "correction_rank",
            "revision_disagreement": len({_reading(r) for r in ranked}) > 1,
        }
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


def resolve(reports, source_order, *, revision_order=None, nil_withdrawal=None):
    if revision_order not in (None, "source_receipt_time"):
        raise ValueError("Unsupported revision order: " + str(revision_order))
    if nil_withdrawal not in NIL_WITHDRAWAL_MODES:
        raise ValueError("Unsupported NIL withdrawal: " + str(nil_withdrawal))
    if nil_withdrawal and revision_order != "source_receipt_time":
        raise ValueError("NIL withdrawal mode requires source receipt time ordering")
    sources = {
        source: source_choice([r for r in reports if r["source"] == source], revision_order,
                              nil_withdrawal)
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
    conflict = len(values) > 1 or any(
        c["ambiguous"] or c.get("revision_disagreement", False) for c in sources.values()
    )
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


def market_value(temp_c, unit, rounding=None):
    value = Decimal(str(temp_c))
    if unit == "F":
        value = value * 9 / 5 + 32
    if rounding == "half_toward_positive":
        return int((value+Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def day_bounds(day, timezone):
    local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC), (local + timedelta(days=1)).astimezone(UTC)


def resolution_deadline(day):
    """23:59 ET on the next calendar date; ET follows New York DST rules."""
    following = datetime.fromisoformat(day)+timedelta(days=1)
    return following.replace(hour=23, minute=59, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)


def lock_cutoff(airport, date, policy, now, finalization):
    end = day_bounds(date, airport["timezone"])[1]
    cutoff = parse_time(finalization["cutoff_at"])
    if policy.get("lock_mode") == "local_midnight":
        if cutoff != end or now < end:
            raise ValueError("Invalid midnight lock")
    elif policy.get("lock_mode") == "next_day_publication":
        deadline = resolution_deadline(date)
        trigger = finalization.get("trigger")
        if finalization.get("deadline_at") != iso(deadline) or now < cutoff:
            raise ValueError("Invalid next-day lock deadline")
        if trigger is None:
            if finalization.get("reason") != "deadline" or cutoff != deadline:
                raise ValueError("Invalid deadline lock")
        else:
            next_date = (datetime.fromisoformat(date)+timedelta(days=1)).date().isoformat()
            _, next_end = day_bounds(next_date, airport["timezone"])
            observed = parse_time(trigger["observed_at"])
            published = parse_time(trigger["published_at"])
            if (finalization.get("reason") != "next_day_publication" or trigger["icao"] != airport["icao"]
                    or trigger["date"] != next_date or not end <= observed < next_end
                    or observed > published or cutoff != published or cutoff >= deadline):
                raise ValueError("Invalid next-day publication trigger")
    else:
        raise ValueError("Unsupported lock policy")
    return cutoff


LOCK_MODES = (None, "local_midnight", "next_day_publication", "disabled")


def daily(airport, date, reports, policy, now, finalization=None):
    if policy.get("lock_mode") not in LOCK_MODES:
        raise ValueError("Unsupported lock mode: " + str(policy.get("lock_mode")))
    if policy.get("lock_mode") == "disabled" and finalization is not None:
        raise ValueError("Locking is disabled for this policy")
    start, end = day_bounds(date, airport["timezone"])
    if finalization is not None:
        cutoff = lock_cutoff(airport, date, policy, now, finalization)
        accepted = finalization.get("accepted_at", {})
        if set(accepted) != {r["id"] for r in reports}:
            raise ValueError("Lock acceptance set mismatch")
        for report in reports:
            stamp = accepted[report["id"]]
            if (not isinstance(stamp, int) or isinstance(stamp, bool) or stamp >= cutoff.timestamp()
                    or parse_time(report["fetched_at"]) >= cutoff
                    or stamp < int(parse_time(report["fetched_at"]).timestamp())):
                raise ValueError("Report not durably accepted before midnight" if policy.get("lock_mode") == "local_midnight"
                                 else "Report not durably accepted before cutoff")
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
        result = resolve(groups.get(timestamp, []), policy["source_order"],
                         revision_order=policy.get("revision_order"),
                         nil_withdrawal=policy.get("nil_withdrawal"))
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
        market_value(row["selected"]["temperature_c"], airport["unit"], policy.get("rounding_mode"))
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
    if policy.get("revision_order") == "source_receipt_time":
        # Disagreements are diagnostic. Only unresolved selection/coverage affects
        # availability; there is no human-review gate or automatic finalization.
        blocked = sum(r["status"] == "blocked" for r in rows)
        summary["blocked"] = blocked
        summary["status"] = (
            "unresolved" if blocked else "incomplete" if missing
            else "day_in_progress" if now < end else "provisional"
        )
    result = {
        "airport": airport,
        "date": date,
        "rows": rows,
        "summary": summary,
        "excluded": [r for r in in_day if not r["eligible"]],
    }
    if policy.get("revision_order") == "source_receipt_time":
        result["policy_version"] = policy["version"]
    if policy.get("rounding_mode"):
        result["rounding_mode"] = policy["rounding_mode"]
    if policy.get("lock_mode") in ("local_midnight", "next_day_publication"):
        # Coverage/conflicts remain in the evidence, never a human-review gate.
        boundary = end if policy["lock_mode"] == "local_midnight" else resolution_deadline(date)
        summary["status"] = "locked" if finalization is not None else "finalization_pending" if now >= boundary else "live"
        if finalization is not None:
            summary["missing"] = sum(r["expected"] and not r["selected"] for r in rows)
        result["source_order"] = policy["source_order"]
        result["cutoff_at"] = finalization["cutoff_at"] if finalization is not None else iso(boundary)
        if policy["lock_mode"] == "next_day_publication":
            result["deadline_at"] = iso(boundary)
            result["lock_mode"] = policy["lock_mode"]
        result["finalization"] = finalization
    elif policy.get("lock_mode") == "disabled":
        # Development: no cutoff, no finalization; results stay revisable.
        result["lock_mode"] = "disabled"
    return result
