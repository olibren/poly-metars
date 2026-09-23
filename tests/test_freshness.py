"""Freshness follows the actual live AWC batch, independently of other routes."""
from datetime import datetime
import unittest

from cloudflare.src.planner import awc_airport_checks, live_jobs
from ledger.metar import UTC


class AirportChecksTests(unittest.TestCase):
    def test_actual_planned_batches_and_recovery_isolation(self):
        airports = [{"icao": f"TEST{i}", "eccc_bulletins": [], "tgftp_files": []}
                    for i in range(10)]
        now = datetime(2026, 9, 23, 10, tzinfo=UTC)
        tasks = list(live_jobs(airports, now))
        for task in tasks:
            task["last_success"] = int(now.timestamp())
        awc = [task for task in tasks if task["source"] == "noaa_awc"]
        awc[1]["last_success"] = None
        tasks.append({**awc[1], "mode": "recovery", "last_success": int(now.timestamp())})
        checks = awc_airport_checks(tasks)
        self.assertEqual(set(checks), {a["icao"] for a in airports})
        self.assertEqual(checks["TEST0"], "2026-09-23T10:00:00Z")
        self.assertEqual(checks["TEST7"], "2026-09-23T10:00:00Z")
        self.assertIsNone(checks["TEST8"])
        self.assertIsNone(checks["TEST9"])
