import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from scripts.offline.archive import Archive
from ledger.archive import write_json
from ledger.audit import verify_export
from scripts.offline.export import export
from ledger.metar import UTC, parse_report, parse_bulletin
from ledger.policy import resolve, daily, day_bounds, market_value
from scripts.offline.catalog import station_for_event
from scripts.offline.sources import Client, collect_awc
from ledger.evidence import decode

ORDER = ["noaa_awc", "noaa_tgftp", "eccc"]
NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
POLICY = {"version": "test", "source_order": ORDER, "delivery_grace_minutes": 15}
AIRPORT = {
    "icao": "ZSQD",
    "city": "Qingdao",
    "name": "Qingdao",
    "timezone": "Asia/Shanghai",
    "unit": "C",
    "routine_minutes_utc": [0],
    "market_urls": [],
}


def record(source, temp=30, raw=None, rank=0):
    raw = raw or f"METAR ZSQD 200600Z 32004MPS CAVOK {temp:02}/16 Q1016 NOSIG"
    parsed = parse_report(raw, datetime(2026, 9, 20, 6, tzinfo=UTC), correction=rank)
    return {
        **parsed,
        "source": source,
        "id": source + raw,
        "fetched_at": "2026-09-22T12:00:00Z",
    }


class ParserTests(unittest.TestCase):
    def test_missing_routine_label_is_not_guessed(self):
        self.assertFalse(
            parse_report("ZSQD 200600Z 00000KT CAVOK 30/16 Q1016", NOW)["eligible"]
        )

    def test_routine_bulletin_provides_classification(self):
        rows = list(
            parse_bulletin(
                "SACI32 ZBBB 200600\nZSQD 200600Z 00000KT CAVOK 30/16 Q1016=", NOW
            )
        )
        self.assertTrue(rows[0]["eligible"])

    def test_explicit_speci_never_becomes_routine(self):
        row = list(
            parse_bulletin(
                "SACI32 ZBBB 200600\nSPECI ZSQD 200600Z 00000KT CAVOK 30/16 Q1016=", NOW
            )
        )[0]
        self.assertEqual(row["reason"], "special_report")

    def test_t_group_signed_temperature(self):
        r = parse_report(
            "METAR KORD 200651Z 00000KT CLR M01/M02 A3000 RMK T10061022", NOW
        )
        self.assertEqual(r["temperature_c"], -0.6)

    def test_corrupt_precise_temperature_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_report(
                "METAR KORD 200651Z 00000KT CLR 10/02 A3000 RMK T02001020", NOW
            )

    def test_month_rollover(self):
        r = parse_report(
            "METAR ZSQD 312300Z 00000KT CAVOK 20/10 Q1016",
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        self.assertEqual(r["observed_at"], "2026-08-31T23:00:00Z")

    def test_year_rollover(self):
        r = parse_report(
            "METAR ZSQD 312300Z 00000KT CAVOK 20/10 Q1016",
            datetime(2027, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(r["observed_at"], "2026-12-31T23:00:00Z")

    def test_provider_timestamp_must_agree(self):
        with self.assertRaises(ValueError):
            parse_report(
                "METAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016",
                NOW,
                observed_at="2026-09-20T07:00:00Z",
            )

    def test_nil_retained_but_excluded(self):
        self.assertEqual(
            parse_report("METAR ZSQD 200600Z NIL", NOW)["reason"], "nil_report"
        )

    def test_multiline_and_multiple_reports(self):
        rows = list(
            parse_bulletin(
                "SACI31 ZBBB 200600 CCB\nMETAR ZBAA 200600Z 00000KT\nCAVOK 28/15 Q1019=\nMETAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016=",
                NOW,
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["correction"], 2)


class PolicyTests(unittest.TestCase):
    def test_priority_not_maximum_or_majority(self):
        r = resolve(
            [record("noaa_awc", 28), record("noaa_tgftp", 30), record("eccc", 30)],
            ORDER,
        )
        self.assertEqual(r["selected"]["temperature_c"], 28)
        self.assertTrue(r["conflict"])

    def test_missing_primary_falls_back_per_observation(self):
        r = resolve([record("noaa_tgftp", 30), record("eccc", 30)], ORDER)
        self.assertEqual(r["selected"]["source"], "noaa_tgftp")

    def test_correction_replaces_original_even_if_original_arrives_later(self):
        a = record("noaa_awc", 28)
        b = record("noaa_awc", 30, rank=1)
        self.assertEqual(resolve([b, a], ORDER)["selected"]["temperature_c"], 30)

    def test_same_source_ambiguity_blocks_fallback(self):
        r = resolve(
            [
                record("noaa_awc", 28, rank=1),
                record("noaa_awc", 30, rank=1),
                record("eccc", 30),
            ],
            ORDER,
        )
        self.assertIsNone(r["selected"])
        self.assertEqual(r["status"], "blocked")

    def test_duplicate_same_temperature_does_not_block(self):
        a = record("noaa_awc")
        b = copy.deepcopy(a)
        b["id"] = "other"
        b["raw"] += " NOSIG"
        self.assertIsNotNone(resolve([a, b], ORDER)["selected"])

    def test_special_extreme_not_counted(self):
        ordinary = record("noaa_awc", 30)
        special = record("eccc", raw="SPECI ZSQD 200630Z 00000KT CAVOK 40/20 Q1016")
        result = daily(AIRPORT, "2026-09-20", [ordinary, special], POLICY, NOW)
        self.assertEqual(result["summary"]["high"], 30)
        self.assertEqual(len(result["excluded"]), 1)

    def test_local_calendar_day(self):
        r = record("eccc", raw="METAR ZSQD 191700Z 00000KT CAVOK 31/20 Q1016")
        self.assertEqual(
            daily(AIRPORT, "2026-09-20", [r], POLICY, NOW)["summary"]["high"], 31
        )

    def test_dst_day_length(self):
        spring = day_bounds("2026-03-08", "America/New_York")
        fall = day_bounds("2026-11-01", "America/New_York")
        self.assertEqual((spring[1] - spring[0]).total_seconds() / 3600, 23)
        self.assertEqual((fall[1] - fall[0]).total_seconds() / 3600, 25)

    def test_us_conversion_and_negative_rounding(self):
        self.assertEqual(market_value(20, "F"), 68)
        self.assertEqual(market_value(-0.5, "C"), -1)

    def test_pending_slots_not_missing(self):
        result = daily(AIRPORT, "2026-09-23", [], POLICY, NOW)
        self.assertEqual(result["summary"]["missing"], 0)
        self.assertTrue(all(r["status"] == "pending" for r in result["rows"]))

    def test_out_of_schedule_routine_is_not_dropped(self):
        r = record("eccc", raw="METAR ZSQD 200615Z 00000KT CAVOK 31/20 Q1016")
        result = daily(AIRPORT, "2026-09-20", [r], POLICY, NOW)
        self.assertEqual(result["summary"]["high"], 31)
        self.assertEqual(len(result["rows"]), 25)


class AuditTests(unittest.TestCase):
    def test_stale_tgftp_file_keeps_its_original_month(self):
        receipt = {
            "source": "noaa_tgftp",
            "headers": {"last-modified": "Sun, 30 Aug 2026 23:01:00 GMT"},
            "fetched_at": "2026-09-22T12:00:00Z",
        }
        row = list(
            decode(
                b"SACI32 ZBBB 302300\nMETAR ZSQD 302300Z 00000KT CAVOK 30/16 Q1016=",
                receipt,
            )
        )[0]
        self.assertEqual(row["observed_at"], "2026-08-30T23:00:00Z")

    def test_transient_outage_is_retained_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            response = MagicMock()
            response.__enter__.return_value = response
            response.url = "https://aviationweather.gov/api/data/metar"
            response.status = 200
            response.headers = {}
            response.read.return_value = b"[]"
            with (
                patch(
                    "scripts.offline.sources.urlopen",
                    side_effect=[URLError("offline"), response],
                ),
                patch("scripts.offline.sources.time.sleep"),
            ):
                body, receipt = Client(archive).fetch("noaa_awc", response.url)
            self.assertEqual(body, b"[]")
            self.assertEqual(receipt["status"], 200)
            attempts = archive.read("receipts")
            self.assertEqual([r["status"] for r in attempts], [0, 200])
            self.assertIn("offline", attempts[0]["error"])
            self.assertEqual(archive.read("reports"), [])

    def test_awc_timestamp_contradiction_is_retained_as_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            payload = [
                {
                    "icaoId": "ZSQD",
                    "rawOb": "METAR ZSQD 200700Z 00000KT CAVOK 30/16 Q1016",
                    "obsTime": 1789884000,
                    "metarType": "METAR",
                }
            ]
            body = json.dumps(payload).encode()
            receipt = archive.response(
                "noaa_awc", "https://aviationweather.gov/api/data/metar", body, 200
            )
            client = MagicMock()
            client.archive = archive
            client.fetch.return_value = (body, receipt)
            collect_awc(client, [AIRPORT], ["2026-09-20"], workers=1)
            self.assertEqual(archive.read("reports"), [])
            self.assertIn("disagrees", archive.read("rejected")[0]["parse_error"])

    def test_corrected_nil_withdraws_original_reading(self):
        original = record("noaa_awc", 30)
        correction = record("noaa_awc", raw="METAR ZSQD 200600Z COR NIL")
        fallback = record("eccc", 28)
        selected = resolve([original, correction, fallback], ORDER)["selected"]
        self.assertEqual(selected["source"], "eccc")
        self.assertEqual(selected["temperature_c"], 28)

    def test_unsupported_reading_fails_even_with_a_consistent_record_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            raw = "METAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016"
            receipt = archive.response(
                "eccc",
                "https://dd.weather.gc.ca/20260920/WXO-DD/bulletins/alphanumeric/20260920/SA/ZBBB/06/fixture",
                raw.encode(),
                200,
            )
            fake = parse_report(raw, NOW)
            fake["temperature_c"] = 45
            archive.report(fake, receipt)
            with self.assertRaisesRegex(ValueError, "not supported"):
                archive.verify()

    def test_backfill_replaces_fallback_and_preserves_published_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            write_json(config / "airports.json", [AIRPORT])
            write_json(config / "policy.json", POLICY)
            write_json(config / "sources.json", [{"id": x} for x in ORDER])
            archive = Archive(root / "archive")
            raw = "METAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016"
            receipt = archive.response(
                "eccc",
                "https://dd.weather.gc.ca/20260920/WXO-DD/bulletins/alphanumeric/20260920/SA/ZBBB/06/fixture",
                raw.encode(),
                200,
            )
            archive.report(parse_report(raw, NOW), receipt)
            before = export(archive, config, root / "public", ["2026-09-20"], NOW)
            earlier = root / "public/snapshots" / before["snapshot_id"]
            path = earlier / "days/2026-09-20/ZSQD.json"
            retained = path.read_bytes()
            replacement = raw.replace("30/16", "28/16")
            payload = [
                {"rawOb": replacement, "obsTime": 1789884000, "metarType": "METAR"}
            ]
            receipt = archive.response(
                "noaa_awc",
                "https://aviationweather.gov/api/data/metar",
                json.dumps(payload).encode(),
                200,
            )
            archive.report(parse_report(replacement, NOW), receipt)
            after = export(archive, config, root / "public", ["2026-09-20"], NOW)
            self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])
            self.assertEqual(path.read_bytes(), retained)
            self.assertTrue(verify_export(earlier)["verified"])
            self.assertTrue(verify_export(root / "public")["verified"])
            current = (
                root
                / "public/snapshots"
                / after["snapshot_id"]
                / "days/2026-09-20/ZSQD.json"
            )
            result = json.loads(current.read_text())
            self.assertEqual(result["summary"]["high"], 28)
            self.assertEqual(result["summary"]["conflicts"], 1)

    def test_idempotence_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            body = b"METAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016"
            receipt = archive.response(
                "eccc",
                "https://dd.weather.gc.ca/20260920/WXO-DD/bulletins/alphanumeric/20260920/SA/ZBBB/06/fixture",
                body,
                200,
            )
            parsed = parse_report(body.decode(), NOW)
            self.assertTrue(archive.report(parsed, receipt))
            self.assertFalse(archive.report(parsed, receipt))
            self.assertTrue(archive.verify()["verified"])
            (Path(directory) / "objects" / f"{receipt['body_sha256']}.txt").write_bytes(
                b"changed"
            )
            with self.assertRaises(ValueError):
                archive.verify()

    def test_export_is_reproducible_and_csv_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            archive = Archive(root / "archive")
            write_json(config / "airports.json", [AIRPORT])
            write_json(config / "policy.json", POLICY)
            write_json(
                config / "sources.json",
                [{"id": x, "label": x, "agency": x} for x in ORDER],
            )
            raw = "METAR ZSQD 200600Z 00000KT CAVOK 30/16 Q1016"
            receipt = archive.response(
                "eccc",
                "https://dd.weather.gc.ca/20260920/WXO-DD/bulletins/alphanumeric/20260920/SA/ZBBB/06/fixture",
                raw.encode(),
                200,
            )
            archive.report(parse_report(raw, NOW), receipt)
            export(archive, config, root / "export", ["2026-09-20"], NOW)
            self.assertEqual(verify_export(root / "export")["replayed_airport_days"], 1)
            index = json.loads((root / "export/index.json").read_text())
            target = (
                root
                / "export/snapshots"
                / index["snapshot_id"]
                / "days/2026-09-20/ZSQD.json"
            )
            bundle_path = target.parents[2] / "bundle.zip"
            with zipfile.ZipFile(bundle_path) as bundle:
                bundle.extractall(root / "portable")
            self.assertTrue(verify_export(root / "portable")["verified"])
            data = json.loads(target.read_text())
            data["summary"]["high"] = 99
            write_json(target, data)
            with self.assertRaises(ValueError):
                verify_export(root / "export")

    def test_catalog_does_not_guess_multiple_stations(self):
        self.assertIsNone(
            station_for_event(
                {
                    "description": "https://www.weather.gov/wrh/timeseries?site=klga or https://www.weather.gov/wrh/timeseries?site=kjfk"
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
