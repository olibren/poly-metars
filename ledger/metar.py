"""Small, conservative parser. Never infer a routine report from its clock minute."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

UTC = timezone.utc
START = re.compile(
    r"(?<!\w)(?:(METAR|SPECI)\s+)?(?:(COR)\s+)?([A-Z][A-Z0-9]{3})\s+(\d{6})Z\b"
)
TEMP = re.compile(r"(?<!\S)(M?\d{2})/(?:M?\d{2}|//)(?!\S)")
PRECISE = re.compile(r"(?<!\S)T([01])(\d{3})([01])(\d{3})(?!\S)")
HEADER = re.compile(
    r"\b(S[AP][A-Z]{2}\d{2})\s+([A-Z]{4})\s+(\d{6})(?:\s+([ACR]{2}[A-Z]))?\b"
)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone is required")
    return dt.astimezone(UTC)


def report_time(ddhhmm, reference):
    """Choose nearest valid month using a source/query timestamp, never today's month."""
    candidates = []
    for offset in (-1, 0, 1):
        month_index = reference.year * 12 + reference.month - 1 + offset
        year, month = divmod(month_index, 12)
        try:
            candidates.append(
                datetime(
                    year,
                    month + 1,
                    int(ddhhmm[:2]),
                    int(ddhhmm[2:4]),
                    int(ddhhmm[4:]),
                    tzinfo=UTC,
                )
            )
        except ValueError:
            pass
    if not candidates:
        raise ValueError("Invalid observation time")
    closest = min(candidates, key=lambda d: abs(d - reference))
    if abs(closest - reference) > timedelta(days=16):
        raise ValueError("Ambiguous observation month")
    return closest


def parse_report(raw, reference, *, kind=None, correction=0, observed_at=None):
    normalized = " ".join(raw.strip().rstrip("=").split())
    match = START.match(normalized)
    if not match:
        raise ValueError("Missing station or observation timestamp")
    explicit, prefix_cor, icao, timestamp = match.groups()
    obs = report_time(timestamp, reference)
    if observed_at is not None:
        declared = parse_time(observed_at)
        if declared != obs:
            raise ValueError("Raw timestamp disagrees with provider timestamp")
    report_type = explicit or kind or "UNKNOWN"
    if kind in ("METAR", "SPECI") and explicit and explicit != kind:
        # An explicit SPECI in an SA bulletin remains a SPECI, never routine.
        report_type = explicit
    before_remarks = normalized.split(" RMK ", 1)[0]
    temperature = None
    precision = "whole-degree Celsius"
    token = TEMP.search(before_remarks)
    if token:
        temperature = Decimal(token[1].replace("M", "-"))
    precise = (
        PRECISE.search(normalized.split(" RMK ", 1)[1])
        if " RMK " in normalized
        else None
    )
    if precise and temperature is not None:
        value = Decimal(precise[2]) / 10 * (-1 if precise[1] == "1" else 1)
        if abs(value - temperature) > Decimal("0.6"):
            raise ValueError("Precise and whole-degree temperatures disagree")
        temperature = value
        precision = "tenth-degree Celsius (T group)"
    is_corrected = bool(prefix_cor or re.search(r"\bCOR\b", before_remarks))
    reason = None
    if report_type != "METAR":
        reason = "special_report" if report_type == "SPECI" else "unclassified_report"
    elif re.search(r"\bNIL\b", before_remarks):
        reason = "nil_report"
    elif temperature is None:
        reason = "missing_temperature"
    elif not Decimal("-90") <= temperature <= Decimal("60"):
        reason = "temperature_outside_policy_range"
    return {
        "icao": icao,
        "observed_at": iso(obs),
        "raw": normalized,
        "report_type": report_type,
        "temperature_c": float(temperature) if temperature is not None else None,
        "precision": precision,
        "correction": max(correction, int(is_corrected)),
        "eligible": reason is None,
        "reason": reason,
    }


def parse_bulletin(text, reference):
    """Retain a bulletin's SA/SP classification and explicit correction sequence."""
    header = HEADER.search(text)
    kind, correction = None, 0
    if header:
        kind = "METAR" if header[1].startswith("SA") else "SPECI"
        reference = report_time(header[3], reference)
        if header[4] and header[4].startswith("CC"):
            correction = ord(header[4][-1]) - ord("A") + 1
        text = text[header.end() :]
    matches = list(START.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[match.start() : end].split("=", 1)[0].strip("\x03\x01\n\r ")
        try:
            yield parse_report(raw, reference, kind=kind, correction=correction)
        except ValueError as error:
            yield {"icao": match[3], "raw": raw, "parse_error": str(error)}


def parse_collective(text, reference):
    """Decode each WMO bulletin separately; its SA/SP and COR never leak to neighbors."""
    headers = list(HEADER.finditer(text))
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        segment = text[header.start():end]
        segment = re.split(r"####\d{9}####", segment, maxsplit=1)[0]
        yield from parse_bulletin(segment, reference)
