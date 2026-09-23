"""Reparse retained provider bytes to check that normalized reports have evidence."""

from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import re
from urllib.parse import urlparse, parse_qs
import xml.etree.ElementTree as ET

from .metar import UTC, START, parse_bulletin, parse_awc, parse_nws, parse_time, parse_collective, parse_report


def decode_amsc(body, receipt):
    """Raw reports only; API array order and fetch time never order revisions."""
    payload = json.loads(body)
    if (not isinstance(payload, dict) or payload.get("code") != 200
            or not isinstance(payload.get("data"), list)):
        raise ValueError("Expected successful AMSC report array")
    stations = parse_qs(urlparse(receipt["url"]).query).get("cccc", [])
    if len(stations) != 1 or not re.fullmatch(r"[A-Z][A-Z0-9]{3}", stations[0]):
        raise ValueError("Expected one requested AMSC station")
    station = stations[0]
    # The API has no absolute report date. Use the retained retrieval timestamp
    # solely to anchor DDHHMM to the nearest month, as a recent-report feed.
    reference = parse_time(receipt["fetched_at"])
    for raw in payload["data"]:
        try:
            if not isinstance(raw, str) or len(list(START.finditer(raw))) != 1:
                raise ValueError("Expected one raw AMSC report")
            row = parse_report(raw, reference)
            if row["icao"] != station:
                raise ValueError("AMSC report disagrees with requested station")
            if parse_time(row["observed_at"]) > reference:
                raise ValueError("AMSC observation is after retrieval")
            yield row
        except (ValueError, TypeError) as error:
            yield {"icao": station, "raw": raw, "parse_error": str(error)}


def decode_met_no(body, receipt):
    """MET Norway's XML envelope supplies classification and an absolute UTC date."""
    # No DTD/entity expansion is needed for the upstream schema.
    xml = body.decode("utf-8")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise ValueError("Unexpected XML declaration")
    root = ET.fromstring(xml)
    ns = {"m": "http://api.met.no", "g": "http://www.opengis.net/gml/3.2"}
    if root.tag != "{http://api.met.no}aviationProducts":
        raise ValueError("Expected MET Norway aviationProducts")
    stations = set(parse_qs(urlparse(receipt["url"]).query).get("icao", [""])[0].split(","))
    for item in root.findall("m:meteorologicalAerodromeReport", ns):
        station = item.findtext("m:icaoAirportIdentifier", "", ns).strip()
        raw = item.findtext("m:metarText", "", ns).strip()
        try:
            fields = ("m:icaoAirportIdentifier", "m:metarText", "m:metarType",
                      "m:validTime/g:TimeInstant/g:timePosition")
            if any(len(item.findall(field, ns)) != 1 for field in fields):
                raise ValueError("Missing or duplicate MET Norway metadata")
            flags = set(item.findtext("m:metarType", "", ns).split())
            if flags - {"METAR", "SPECI", "AUTO", "COR"} or {"METAR", "SPECI"} <= flags:
                raise ValueError("Unknown or contradictory MET Norway classification")
            stamp = item.findtext(fields[-1], "", ns).strip()
            observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            # This API documents its unqualified XML timestamps as UTC.
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            kind = "SPECI" if "SPECI" in flags else "METAR"
            row = parse_report(raw, observed, kind=kind, correction=int("COR" in flags),
                               observed_at=observed.isoformat())
            if station not in stations or row["icao"] != station:
                raise ValueError("MET Norway report disagrees with requested station")
            if kind == "SPECI" and row["report_type"] != "SPECI":
                raise ValueError("MET Norway special classification contradicts raw report")
            yield row
        except (ValueError, TypeError) as error:
            yield {"icao": station, "raw": raw, "parse_error": str(error)}


def bulletin_reference(receipt):
    """Anchor TGFTP month to file metadata, not the date a stale file was fetched."""
    if receipt["source"] == "noaa_tgftp":
        value = receipt["headers"].get("last-modified")
        if not value:
            raise ValueError(
                "TGFTP bulletin needs Last-Modified to establish its month"
            )
        reference = parsedate_to_datetime(value)
        if reference.tzinfo is None:
            raise ValueError("TGFTP Last-Modified needs a timezone")
        return reference.astimezone(UTC)
    reference = parse_time(receipt["fetched_at"])
    match = re.search(r"/alphanumeric/(\d{8})/SA/[A-Z0-9]{4}/(\d{2})/", receipt["url"])
    if match:
        reference = datetime.strptime("".join(match.groups()), "%Y%m%d%H").replace(
            tzinfo=UTC
        )
    return reference


def decode(body, receipt):
    """Use the source's timestamp context, never the auditor's current date."""
    if receipt["source"] == "amsc":
        yield from decode_amsc(body, receipt)
    elif receipt["source"] == "met_no":
        yield from decode_met_no(body, receipt)
    elif receipt["source"] == "noaa_nws":
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            raise ValueError("Expected NWS observation FeatureCollection")
        for feature in payload["features"]:
            props = feature.get("properties") if isinstance(feature, dict) else None
            if isinstance(props, dict) and not props.get("rawMessage"):
                # Normal subhourly non-METAR observations can vastly outnumber
                # METARs. Preserve them in the original GeoJSON, without creating
                # a fresh per-observation rejection object on every poll.
                continue
            try:
                row = parse_nws(feature)
                match = re.fullmatch(r"/stations/([A-Z0-9]{4})/observations", urlparse(receipt["url"]).path)
                if not match or row["icao"] != match[1]:
                    raise ValueError("NWS report disagrees with requested station")
                yield row
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                props = feature.get("properties", {}) if isinstance(feature, dict) else {}
                props = props if isinstance(props, dict) else {}
                yield {"icao": props.get("stationId") or str(props.get("station", "")).rstrip("/").split("/")[-1],
                       "raw": props.get("rawMessage"), "parse_error": str(error)}
    elif receipt["source"] == "noaa_awc":
        payload = json.loads(body) if body else []
        if not isinstance(payload, list):
            raise ValueError("Expected AWC report array")
        for item in payload:
            try:
                yield parse_awc(item)
            except (ValueError, KeyError):
                continue  # Rejected inputs cannot substantiate an accepted report.
    else:
        reference = bulletin_reference(receipt)
        parser = parse_collective if "/DS.metar/" in receipt.get("url", "") else parse_bulletin
        yield from parser(body.decode("utf-8", errors="replace"), reference)


def verify_evidence(reports, receipts, object_directory):
    from .archive import canonical, digest

    by_id = {r["id"]: r for r in receipts}
    parsed_cache = {}
    for receipt in receipts:
        if (
            digest(canonical({k: v for k, v in receipt.items() if k != "id"}))
            != receipt["id"]
        ):
            raise ValueError("Receipt hash mismatch")
        body = (object_directory / f"{receipt['body_sha256']}.txt").read_bytes()
        if digest(body) != receipt["body_sha256"]:
            raise ValueError("Raw response hash mismatch")
    for report in reports:
        receipt = by_id[report["receipt_id"]]
        for field in ("source", "url", "fetched_at", "body_sha256"):
            if report[field] != receipt[field]:
                raise ValueError(f"Report provenance mismatch: {field}")
        identity = {
            k: v
            for k, v in report.items()
            if k not in ("id", "receipt_id", "url", "fetched_at", "body_sha256")
        }
        if digest(canonical(identity)) != report["id"]:
            raise ValueError("Report hash mismatch")
        if receipt["status"] != 200:
            raise ValueError("Accepted report references an unsuccessful response")
        if receipt["id"] not in parsed_cache:
            body = (object_directory / f"{receipt['body_sha256']}.txt").read_bytes()
            identities = set()
            for row in decode(body, receipt):
                if "parse_error" in row:
                    continue
                identities.add(digest(canonical({**row, "source": receipt["source"]})))
                # Pre-v2 reports omit source timing. Preserve their exact identities
                # and replay, while new timing claims must match the original JSON.
                if "source_received_at" in row:
                    legacy = {k: v for k, v in row.items() if k != "source_received_at"}
                    identities.add(digest(canonical({**legacy, "source": receipt["source"]})))
            parsed_cache[receipt["id"]] = identities
        if report["id"] not in parsed_cache[receipt["id"]]:
            raise ValueError(
                "Normalized report is not supported by its original response"
            )
