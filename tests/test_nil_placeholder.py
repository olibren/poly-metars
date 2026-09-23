"""routine-metar-v6: a plain NIL is a relay placeholder, not a withdrawal."""
from datetime import datetime
import json
from pathlib import Path
import unittest

from ledger.archive import canonical, digest
from ledger.metar import UTC, parse_awc, parse_bulletin, parse_report
from ledger.policy import daily, resolve

ROOT = Path(__file__).resolve().parents[1]
V5 = json.loads((ROOT / "config/policies/routine-metar-v5.json").read_text())
V6 = json.loads((ROOT / "config/policies/routine-metar-v6.json").read_text())
CURRENT = json.loads((ROOT / "config/policy.json").read_text())
ORDER = V6["source_order"]
OBS = datetime(2026, 9, 22, 0, 25, tzinfo=UTC)
NOW = datetime(2026, 9, 22, 16, 40, tzinfo=UTC)
AIRPORT = {"icao": "EHAM", "timezone": "Europe/Amsterdam", "unit": "C",
           "routine_minutes_utc": [25, 55]}

# Verbatim bulletins from the TGFTP collective retrieved 2026-09-22T16:37:39Z
# (SHA-256 2047c785...): two placeholder NILs, then the delayed (RRA) report.
TGFTP_BULLETINS = [
    "SANL31 EHDB 220025\nMETAR EHAM 220025Z NIL=",
    "SANL31 KWBC 220020 RRA\nMETAR\nEHAM 220025Z NIL=",
    "SANL31 EHDB 220025 RRA\nMETAR EHAM 220025Z 12002KT 9999 SCT040 11/11 Q1030 NOSIG=",
]
FULL = "METAR EHAM 220025Z 12002KT 9999 SCT040 11/11 Q1030 NOSIG"


def stamp(row, source):
    row = {**row, "source": source}
    return {**row, "id": digest(canonical(row)), "fetched_at": "2026-09-22T16:37:39Z"}


def bulletin(text, source="noaa_tgftp"):
    return [stamp(row, source) for row in parse_bulletin(text, OBS)]


def tgftp():
    return [row for text in TGFTP_BULLETINS for row in bulletin(text)]


def awc(raw=FULL, received="2026-09-22T00:30:16.189Z"):
    return stamp(parse_awc({"obsTime": OBS.timestamp(), "metarType": "METAR",
                            "receiptTime": received, "rawOb": raw}), "noaa_awc")


def plain(source, raw=FULL):
    return stamp(parse_report(raw, OBS), source)


def select(reports, policy=V6):
    return resolve(reports, policy["source_order"], revision_order=policy["revision_order"],
                   nil_withdrawal=policy.get("nil_withdrawal"))


class PlaceholderNilTests(unittest.TestCase):
    def test_eham_delayed_bulletin_agrees_with_awc(self):
        rows = [awc(), *tgftp()]
        self.assertEqual([r["reason"] for r in rows[1:]], ["nil_report", "nil_report", None])
        result = select(rows)
        source = result["sources"]["noaa_tgftp"]
        self.assertEqual(source["report"]["temperature_c"], 11)
        self.assertFalse(source["ambiguous"])
        self.assertFalse(source["revision_disagreement"])
        self.assertEqual(len(source["variants"]), 3)  # NIL evidence stays visible.
        self.assertEqual(result["selected"]["source"], "noaa_awc")
        self.assertEqual(result["status"], "selected")
        # v5 treated the placeholders as unordered competing versions.
        old = select(rows, V5)
        self.assertTrue(old["sources"]["noaa_tgftp"]["ambiguous"])
        self.assertEqual(old["status"], "disagreement")
        self.assertEqual(old["selected"]["id"], result["selected"]["id"])

    def test_fallback_is_not_blocked_when_awc_is_missing(self):
        result = select(tgftp())
        self.assertEqual(result["selected"]["source"], "noaa_tgftp")
        self.assertEqual(result["selected"]["temperature_c"], 11)
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(select(tgftp(), V5)["status"], "blocked")

    def test_nil_only_source_falls_through_without_a_reading(self):
        nil_only = [r for r in tgftp() if r["reason"] == "nil_report"]
        result = select([*nil_only, plain("eccc")])
        self.assertIsNone(result["sources"]["noaa_tgftp"]["report"])
        self.assertFalse(result["sources"]["noaa_tgftp"]["ambiguous"])
        self.assertEqual(result["selected"]["source"], "eccc")
        missing = select(nil_only)
        self.assertEqual(missing["status"], "missing")
        self.assertIsNone(missing["selected"])

    def test_explicitly_corrected_nil_still_withdraws(self):
        for correction in [plain("noaa_tgftp", "METAR EHAM 220025Z COR NIL"),
                           *bulletin("SANL31 EHDB 220025 CCA\nMETAR EHAM 220025Z NIL=")]:
            self.assertEqual(correction["correction"], 1)
            result = select([plain("noaa_tgftp"), correction, plain("eccc", FULL.replace("11/11", "12/11"))])
            self.assertIsNone(result["sources"]["noaa_tgftp"]["report"])
            self.assertEqual(result["selected"]["source"], "eccc")

    def test_newer_plain_nil_no_longer_withdraws_timed_awc_reading(self):
        rows = [awc(), awc("METAR EHAM 220025Z NIL", "2026-09-22T00:40:00Z")]
        self.assertEqual(select(rows)["selected"]["temperature_c"], 11)
        self.assertEqual(select(rows)["status"], "selected")
        self.assertIsNone(select(rows, V5)["selected"])

    def test_competing_missing_temperature_still_blocks(self):
        rows = [plain("noaa_tgftp"), plain("noaa_tgftp", "METAR EHAM 220025Z 12002KT 9999 SCT040 Q1030")]
        self.assertEqual(select([*rows, plain("eccc")])["status"], "blocked")

    def test_daily_uses_the_pinned_policy(self):
        rows = tgftp()
        current = daily(AIRPORT, "2026-09-22", rows, V6, NOW)
        pinned = daily(AIRPORT, "2026-09-22", rows, V5, NOW)
        slot = "2026-09-22T00:25:00Z"
        self.assertEqual(next(r for r in current["rows"] if r["observed_at"] == slot)["status"], "fallback")
        self.assertEqual(next(r for r in pinned["rows"] if r["observed_at"] == slot)["status"], "blocked")
        self.assertEqual(current["summary"]["high"], 11)
        self.assertEqual(current["policy_version"], "routine-metar-v6")

    def test_current_policy_keeps_v6_selection_with_amsc_last(self):
        self.assertEqual(CURRENT["source_order"], V6["source_order"] + ["amsc"])
        for key in ("revision_order", "nil_withdrawal", "rounding_mode"):
            self.assertEqual(CURRENT[key], V6[key])

    def test_unsupported_modes_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Unsupported NIL"):
            resolve([], ORDER, revision_order="source_receipt_time", nil_withdrawal="never")
        with self.assertRaisesRegex(ValueError, "requires source receipt"):
            resolve([], ORDER, nil_withdrawal="explicit_correction_only")


if __name__ == "__main__":
    unittest.main()
