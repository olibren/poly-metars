"""routine-metar-v7: locking disabled during development; all retained days backfill."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest

import test_cloudflare as support
from ledger.archive import canonical
from ledger.metar import UTC, iso
from ledger.policy import daily

ROOT = Path(__file__).resolve().parents[1]
CURRENT = json.loads((ROOT / "config/policy.json").read_text())
DAY = "2026-09-22/EGLC"


class LockingDisabledTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await support.CloudflareTests.asyncSetUp(self)
        self.module.SETTINGS["policy"] = CURRENT
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    async def collect(self, temperature=23, received=None):
        self.body = json.dumps([{
            "icaoId": "EGLC", "obsTime": datetime(2026, 9, 22, 12, 50, tzinfo=UTC).timestamp(),
            "receiptTime": received or iso(self.now), "metarType": "METAR",
            "rawOb": f"EGLC 221250Z 25005KT 9999 FEW020 {temperature}/12 Q1013",
        }]).encode()
        await self.worker.collect(json.loads(self.task["payload"]), "live")

    async def index(self):
        return json.loads(await (await self.worker.env.ARCHIVE.get("index.json")).text())

    async def day(self, index):
        revision = index["revisions"][DAY]
        return json.loads(await (await self.worker.env.ARCHIVE.get(f"revisions/{revision}/day.json")).text())

    def test_current_policy_disables_locking(self):
        self.assertEqual(CURRENT["lock_mode"], "disabled")

    async def test_days_past_every_cutoff_stay_live_and_accept_late_corrections(self):
        await self.collect(23)
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)  # Well past the v6 ET deadline.
        await self.worker.publish(self.now)
        index = await self.index()
        self.assertEqual(index["locks"], {})
        self.assertEqual(index["first_publications"], {})
        day = await self.day(index)
        self.assertEqual(day["lock_mode"], "disabled")
        self.assertNotIn("cutoff_at", day)
        self.assertNotEqual(day["summary"]["status"], "locked")
        self.assertEqual(day["summary"]["high"], 23)
        # A later-received version still changes the day, days after midnight.
        await self.collect(25, received=iso(self.now))
        await self.worker.publish(self.now)
        self.assertEqual((await self.day(await self.index()))["summary"]["high"], 25)
        bucket = self.worker.env.ARCHIVE.objects
        self.assertFalse([k for k in bucket if k.startswith(("locks/", "first-publications/"))])
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_existing_lock_records_and_pre_activation_days_use_current_policy(self):
        await self.collect(23)
        bucket = self.worker.env.ARCHIVE
        lock = canonical({"revision": "0"*64, "cutoff_at": "2026-09-22T23:00:00Z",
                          "published_at": "2026-09-22T23:00:05Z"})
        await bucket.put("locks/2026-09-22/EGLC.json", lock, {})
        await bucket.put("index.json", canonical({"locks": {DAY: json.loads(lock)}, "revisions": {DAY: "0"*64},
                                                  "first_publications": {DAY: {"published_at": "x"}}}), {})
        # Activation after this day would otherwise pin legacy v2 history.
        await bucket.put("locking.json", canonical({"started_at": int(self.now.timestamp()) + 10*86400}), {})
        self.now += timedelta(days=2)
        await self.worker.publish(self.now)
        index = await self.index()
        self.assertEqual(index["locks"], {})
        self.assertNotEqual(index["revisions"][DAY], "0"*64)
        manifest = json.loads(await (await bucket.get(f"revisions/{index['revisions'][DAY]}/audit.json")).text())
        self.assertEqual(manifest["policy"]["version"], CURRENT["version"])
        self.assertNotIn("finalization", manifest)
        self.assertEqual(bucket.objects["locks/2026-09-22/EGLC.json"].body, lock)  # Left untouched.

    async def test_policy_change_backfills_every_retained_day_through_the_bounded_queue(self):
        await self.collect(23)
        await self.worker.publish(self.now)
        queued = await self.worker.rows("SELECT date FROM dirty WHERE icao='EGLC'")
        retained = self.module.retained_dates(self.airport, self.now, self.module.SETTINGS["retention"]["days"])
        self.assertEqual(sorted(r["date"] for r in queued), sorted(retained))
        await self.worker.publish(self.now)
        self.assertEqual(await self.worker.rows("SELECT date FROM dirty"), [])
        index = await self.index()
        self.assertEqual(set(index["revisions"]), {f"{d}/EGLC" for d in retained})
        # An unchanged engine does not requeue the whole window.
        await self.worker.publish(self.now)
        self.assertEqual(await self.worker.rows("SELECT date FROM dirty"), [])

    def test_disabled_policy_cannot_be_finalized_and_unknown_modes_fail(self):
        airport = {"icao": "EGLC", "timezone": "Europe/London", "unit": "C", "routine_minutes_utc": [20, 50]}
        now = datetime(2026, 9, 25, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "Locking is disabled"):
            daily(airport, "2026-09-22", [], CURRENT, now, {"cutoff_at": "2026-09-22T23:00:00Z"})
        with self.assertRaisesRegex(ValueError, "Unsupported lock mode"):
            daily(airport, "2026-09-22", [], {**CURRENT, "lock_mode": "someday"}, now)


if __name__ == "__main__":
    unittest.main()
