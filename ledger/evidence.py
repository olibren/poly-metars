"""Reparse retained provider bytes to check that normalized reports have evidence."""

from datetime import datetime
from email.utils import parsedate_to_datetime
import json
import re

from .metar import UTC, parse_bulletin, parse_awc, parse_time, parse_collective


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
    if receipt["source"] == "noaa_awc":
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
