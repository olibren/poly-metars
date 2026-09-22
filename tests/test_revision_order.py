"""Version ordering, migration and source-timing evidence, independent of arrival."""
import copy
from datetime import datetime
from itertools import permutations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from ledger.archive import Archive, canonical, digest, write_json
from ledger.audit import verify_export
from ledger.evidence import decode
from ledger.export import export
from ledger.metar import UTC, parse_awc, parse_report
from ledger.policy import daily, resolve
from ledger.sources import collect_awc, collect_awc_recent

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "config/policies/routine-metar-v2.json").read_text())
ORDER = POLICY["source_order"]
NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
OBS = datetime(2026, 9, 20, 6, tzinfo=UTC)
AIRPORT = {"icao": "ZSQD", "timezone": "Asia/Shanghai", "unit": "C",
           "routine_minutes_utc": [0]}


def item(temp=31, received="2026-09-20T06:05:00Z", corrected=False):
    return {"icaoId": "ZSQD", "obsTime": OBS.timestamp(), "metarType": "METAR",
            "receiptTime": received,
            "rawOb": f"METAR ZSQD 200600Z {'COR ' if corrected else ''}00000KT CAVOK {temp:02}/20 Q1010"}


def report(payload, source="noaa_awc"):
    row = {**parse_awc(payload), "source": source}
    return {**row, "id": digest(canonical(row)), "fetched_at": "2026-09-22T12:00:00Z"}


def select(reports):
    return resolve(reports, ORDER, revision_order=POLICY["revision_order"])


class RevisionOrderTests(unittest.TestCase):
    def test_latest_source_receipt_wins_every_delivery_order_and_keeps_disagreement(self):
        old = report(item(31, "2026-09-20T06:05:00.100Z"))
        new = report(item(32, "2026-09-20T06:05:00.200Z"))
        old["fetched_at"] = "2026-09-22T12:01:00Z"  # Recovery arrives later.
        for rows in permutations([old, new]):
            result = select(rows)
            self.assertEqual(result["selected"]["id"], new["id"])
            self.assertTrue(result["conflict"])
            self.assertEqual(len(result["sources"]["noaa_awc"]["variants"]), 2)
            self.assertEqual(result["sources"]["noaa_awc"]["selection_basis"], "source_receipt_time")

    def test_explicit_correction_precedes_newer_original_and_source_priority_precedes_time(self):
        correction = report(item(30, "2026-09-20T06:06:00Z", corrected=True))
        delayed_original = report(item(31, "2026-09-20T06:10:00Z"))
        fallback = report(item(32, "2026-09-20T06:20:00Z", corrected=True), "noaa_tgftp")
        for rows in permutations([correction, delayed_original, fallback]):
            self.assertEqual(select(rows)["selected"]["id"], correction["id"])

    def test_equal_or_unknown_conflicting_times_block_without_fallback(self):
        new = report(item(32))
        fallback = report(item(32), "eccc")
        for stamp in [None, "", "bad", "2026-09-20T06:06:00", "2026-09-20T05:59:59Z",
                      "2026-09-20T06:05:00Z"]:
            with self.subTest(stamp=stamp):
                other = report(item(31, stamp))
                result = select([new, other, fallback])
                self.assertIsNone(result["selected"])
                self.assertEqual(result["status"], "blocked")

    def test_latest_nil_or_missing_temperature_withdraws_same_rank_reading(self):
        old = report(item(31))
        fallback = report(item(28), "eccc")
        for raw in ["METAR ZSQD 200600Z NIL", "METAR ZSQD 200600Z 00000KT CAVOK Q1010"]:
            payload = item(received="2026-09-20T06:06:00Z")
            payload["rawOb"] = raw
            withdrawal = report(payload)
            for rows in permutations([old, withdrawal, fallback]):
                self.assertEqual(select(rows)["selected"]["id"], fallback["id"])
            self.assertIsNone(select([old, withdrawal])["selected"])

    def test_equal_time_or_untimed_withdrawal_is_ambiguous(self):
        old = report(item(31))
        for stamp in [None, "2026-09-20T06:05:00Z"]:
            payload = item(received=stamp)
            payload["rawOb"] = "METAR ZSQD 200600Z NIL"
            self.assertEqual(select([old, report(payload)])["status"], "blocked")

    def test_latest_valid_reading_can_replace_same_rank_nil(self):
        payload = item()
        payload["rawOb"] = "METAR ZSQD 200600Z NIL"
        latest = report(item(32, "2026-09-20T06:06:00Z"))
        self.assertEqual(select([report(payload), latest])["selected"]["id"], latest["id"])

    def test_only_matching_legacy_copy_is_superseded(self):
        old, new = report(item(31)), report(item(32, "2026-09-20T06:06:00Z"))
        legacy = {k: v for k, v in old.items() if k != "source_received_at"}
        legacy["id"] = "legacy"
        for rows in permutations([legacy, old, new]):
            self.assertEqual(select(rows)["selected"]["id"], new["id"])
        different = copy.deepcopy(legacy)
        different["raw"] += " NOSIG"
        self.assertEqual(select([different, old, new])["status"], "blocked")

    def test_same_temperature_ties_and_non_awc_sources(self):
        same = report(item(32))
        variant = copy.deepcopy(same)
        variant.update(id="other", raw=same["raw"] + " NOSIG", source_received_at=None)
        self.assertEqual(select([same, variant])["selected"]["temperature_c"], 32)
        for source in ["noaa_tgftp", "eccc"]:
            older = report(item(31), source)
            newer = report(item(32, "2026-09-20T06:06:00Z"), source)
            # Even an accidentally attached field cannot invent a supported clock.
            self.assertEqual(select([older, newer])["status"], "blocked")

    def test_legacy_policy_keeps_old_conflict_behavior(self):
        rows = [report(item(31)), report(item(32, "2026-09-20T06:06:00Z"))]
        self.assertEqual(resolve(rows, ORDER)["status"], "blocked")
        old_policy = json.loads((ROOT / "config/policies/routine-metar-v1.json").read_text())
        legacy = daily(AIRPORT, "2026-09-20", rows, old_policy, NOW)
        current = daily(AIRPORT, "2026-09-20", rows, POLICY, NOW)
        self.assertEqual(legacy["summary"]["status"], "review_required")
        self.assertNotIn("policy_version", legacy)
        self.assertEqual(current["summary"]["high"], 32)
        self.assertEqual(current["summary"]["blocked"], 0)
        self.assertEqual(current["summary"]["status"], "incomplete")
        self.assertEqual(current["policy_version"], "routine-metar-v2")
        blocked = daily(AIRPORT, "2026-09-20", [report(item(31)), report(item(32))], POLICY, NOW)
        self.assertEqual(blocked["summary"]["status"], "unresolved")
        self.assertEqual(blocked["summary"]["blocked"], 1)

    def test_unknown_ordering_policy_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Unsupported revision"):
            resolve([], ORDER, revision_order="local_download_time")


class SourceTimingEvidenceTests(unittest.TestCase):
    def test_awc_normalizes_offsets_and_preserves_fractional_seconds(self):
        row = parse_awc(item(received="2026-09-20 07:05:00.123456+01:00"))
        self.assertEqual(row["source_received_at"], "2026-09-20T06:05:00.123456Z")
        for invalid in [None, "", 123, {}, "bad", "2026-09-20T06:05:00", "2026-09-19T06:05:00Z"]:
            with self.subTest(invalid=invalid):
                row = parse_awc(item(received=invalid))
                self.assertIsNone(row["source_received_at"])
                self.assertTrue(row["eligible"])

    def test_both_collectors_preserve_source_time_and_duplicate_identity(self):
        for collector in [collect_awc, collect_awc_recent]:
            with self.subTest(collector=collector.__name__), tempfile.TemporaryDirectory() as directory:
                archive = Archive(directory)
                payload = item(received="2026-09-20T06:05:00.123Z")
                body = json.dumps([payload]).encode()
                receipt = archive.response("noaa_awc", "https://aviationweather.gov/api/data/metar", body, 200)
                client = MagicMock(archive=archive)
                client.fetch.return_value = body, receipt
                collector(client, [AIRPORT], ["2026-09-20"], workers=1)
                collector(client, [AIRPORT], ["2026-09-20"], workers=1)
                rows = archive.read("reports")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["source_received_at"], "2026-09-20T06:05:00.123000Z")
                self.assertTrue(archive.verify()["verified"])

    def test_forged_source_time_fails_even_with_recomputed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            payload = item()
            receipt = archive.response("noaa_awc", "https://aviationweather.gov/api/data/metar",
                                       json.dumps([payload]).encode(), 200)
            fake = parse_awc(payload)
            fake["source_received_at"] = "2026-09-20T06:59:00.000000Z"
            archive.report(fake, receipt)
            with self.assertRaisesRegex(ValueError, "not supported"):
                archive.verify()

    def test_replay_old_and_new_exports_with_mixed_legacy_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = Archive(root / "archive")
            config = root / "config"
            write_json(config / "airports.json", [AIRPORT])
            write_json(config / "sources.json", [{"id": s} for s in ORDER])
            old_policy = json.loads((ROOT / "config/policies/routine-metar-v1.json").read_text())
            write_json(config / "policy.json", old_policy)
            # The older legacy representation is retained before timing is added.
            old, new = item(31), item(32, "2026-09-20T06:06:00Z")
            receipt = archive.response("noaa_awc", "https://aviationweather.gov/api/data/metar",
                                       json.dumps([old, new]).encode(), 200)
            for payload in [old, new]:
                archive.report(parse_report(payload["rawOb"], OBS, kind="METAR"), receipt)
            before = export(archive, config, root / "public", ["2026-09-20"], NOW)
            earlier = root / "public/snapshots" / before["snapshot_id"]
            original = (earlier / "days/2026-09-20/ZSQD.json").read_bytes()
            for row in decode(json.dumps([old, new]).encode(), receipt):
                archive.report(row, receipt)
            write_json(config / "policy.json", POLICY)
            after = export(archive, config, root / "public", ["2026-09-20"], NOW)
            latest = root / "public/snapshots" / after["snapshot_id"]
            self.assertEqual((earlier / "days/2026-09-20/ZSQD.json").read_bytes(), original)
            self.assertTrue(verify_export(earlier)["verified"])
            self.assertTrue(verify_export(latest)["verified"])
            result = json.loads((latest / "days/2026-09-20/ZSQD.json").read_text())
            self.assertEqual(result["summary"]["high"], 32)
            self.assertEqual(result["summary"]["blocked"], 0)
            observed = next(row for row in result["rows"] if row["observed_at"] == "2026-09-20T06:00:00Z")
            self.assertEqual(len(observed["sources"]["noaa_awc"]["variants"]), 4)


if __name__ == "__main__":
    unittest.main()
