"""Cutoff, failure and replay contracts for automatic midnight locking."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_cloudflare as support
from ledger.archive import canonical, digest
from ledger.audit import verify_export
from ledger.metar import UTC, iso
from ledger.policy import daily, day_bounds


class MidnightLockTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.CloudflareTests.asyncSetUp
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    async def collect(self, temperature=23):
        self.body = json.dumps([{
            "icaoId": "EGLC", "obsTime": datetime(2026, 9, 22, 12, 50, tzinfo=UTC).timestamp(),
            "receiptTime": iso(self.now), "metarType": "METAR",
            "rawOb": f"EGLC 221250Z 25005KT 9999 FEW020 {temperature}/12 Q1013",
        }]).encode()
        await self.worker.collect(json.loads(self.task["payload"]), "live")

    async def published(self):
        index = json.loads(await (await self.worker.env.ARCHIVE.get("index.json")).text())
        revision = index["revisions"]["2026-09-22/EGLC"]
        day = json.loads(await (await self.worker.env.ARCHIVE.get(f"revisions/{revision}/day.json")).text())
        return index, revision, day

    async def test_midnight_locks_best_available_even_with_gaps_and_never_changes(self):
        await self.collect()
        await self.worker.publish(self.now)
        _, live_revision, live = await self.published()
        self.assertEqual(live["summary"]["status"], "live")
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)  # London midnight in BST
        await self.worker.publish(self.now)
        index, revision, day = await self.published()
        self.assertNotEqual(revision, live_revision)
        self.assertEqual(day["summary"]["status"], "locked")
        self.assertEqual(day["summary"]["high"], 23)
        self.assertGreater(day["summary"]["missing"], 0)
        self.assertEqual(day["cutoff_at"], "2026-09-22T23:00:00Z")
        self.assertEqual(index["locks"]["2026-09-22/EGLC"]["revision"], revision)
        frozen = {k: v.body for k, v in self.worker.env.ARCHIVE.objects.items() if revision in k}
        self.now += timedelta(minutes=1)
        await self.collect(39)
        self.module.SETTINGS["engine_hashes"] = {"new-engine": "changed"}
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[1:], (revision, day))
        self.assertEqual(frozen, {k: v.body for k, v in self.worker.env.ARCHIVE.objects.items() if revision in k})
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_exact_cutoff_excludes_new_reports_and_delayed_cron_does_not_move_it(self):
        self.now = datetime(2026, 9, 22, 22, 59, 59, 900000, tzinfo=UTC)
        await self.collect(24)
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.collect(40)
        self.now += timedelta(hours=4)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["summary"]["high"], 24)
        self.assertEqual(len(day["finalization"]["accepted_at"]), 1)
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_fetch_before_midnight_but_durable_acceptance_after_is_excluded(self):
        self.now = datetime(2026, 9, 22, 22, 59, 59, tzinfo=UTC)
        with patch.object(self.worker, "archive_reports", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                await self.collect()
        self.now += timedelta(seconds=1)
        await self.worker.archive_reports(await self.worker.rows("SELECT id,payload FROM reports WHERE archived=0"))
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["summary"]["status"], "locked")
        self.assertIsNone(day["summary"]["high"])
        self.assertEqual(day["finalization"]["accepted_at"], {})

    async def test_no_reports_still_locks_automatically_without_inventing_temperature(self):
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["summary"]["status"], "locked")
        self.assertIsNone(day["summary"]["high"])
        self.assertIsNone(day["summary"]["low"])
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_conflicting_versions_are_diagnostic_and_do_not_prevent_locking(self):
        await self.collect(23)
        await self.collect(25)  # same source receipt time: ambiguous observation
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["summary"]["status"], "locked")
        self.assertEqual(day["summary"]["blocked"], 1)
        self.assertIsNone(day["summary"]["high"])
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_failed_artifact_write_does_not_advertise_lock_and_retry_ignores_late_data(self):
        await self.collect()
        await self.worker.publish(self.now)
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        self.worker.env.ARCHIVE.fail_key = "day.csv"
        with self.assertRaisesRegex(RuntimeError, "R2 write failure"):
            await self.worker.publish(self.now)
        self.assertIsNone(await self.worker.env.ARCHIVE.get("locks/2026-09-22/EGLC.json"))
        self.assertNotEqual((await self.published())[2]["summary"]["status"], "locked")
        self.now += timedelta(minutes=1)
        await self.collect(40)
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[2]["summary"]["high"], 23)
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_old_inflight_publisher_cannot_revert_new_lock(self):
        await self.collect()
        await self.worker.publish(self.now)
        self.now = datetime(2026, 9, 22, 22, 59, tzinfo=UTC)
        await self.collect(24)
        async def finish_at_midnight():
            self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
            await self.worker.publish(self.now)
        self.worker.env.ARCHIVE.before_index = finish_at_midnight
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["summary"]["status"], "locked")
        self.assertEqual(day["summary"]["high"], 24)

    async def test_lock_survives_lost_index_and_engine_or_policy_change(self):
        await self.collect()
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.worker.publish(self.now)
        _, revision, day = await self.published()
        del self.worker.env.ARCHIVE.objects["index.json"]
        self.module.SETTINGS["policy"]["version"] = "future-policy"
        self.module.SETTINGS["engine_hashes"] = {"future": "engine"}
        self.now += timedelta(days=5)
        await self.collect(40)
        await self.worker.publish(self.now)
        self.assertIsNotNone(await self.worker.statement("SELECT date FROM dirty WHERE icao='EGLC' AND date='2026-09-22'").first())
        for _ in range(3):
            await self.worker.publish(self.now)
        self.assertEqual((await self.published())[1:], (revision, day))

    async def test_pre_activation_history_is_not_claimed_to_have_locked_at_midnight(self):
        self.now = datetime(2026, 9, 23, 1, tzinfo=UTC)
        await self.worker.statement("UPDATE state SET value=? WHERE name='midnight_lock_started_at'", str(int(self.now.timestamp()))).run()
        await self.collect()
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day["policy_version"], "routine-metar-v2")
        self.assertNotEqual(day["summary"]["status"], "locked")
        self.assertIsNone(await self.worker.env.ARCHIVE.get("locks/2026-09-22/EGLC.json"))

    async def test_replay_rejects_late_acceptance_even_with_rehashed_manifest(self):
        await self.collect()
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.worker.publish(self.now)
        _, revision, _ = await self.published()
        bucket = self.worker.env.ARCHIVE
        manifest = json.loads(await (await bucket.get(f"revisions/{revision}/audit.json")).text())
        rid = manifest["report_ids"][0]
        manifest["finalization"]["accepted_at"][rid] = int(self.now.timestamp())
        identity = {k: manifest[k] for k in ("date", "icao", "report_ids", "policy_sha256", "registry_sha256", "engine_sha256", "finalization")}
        manifest["revision"] = digest(canonical(identity))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"audit.json").write_bytes(canonical(manifest))
            (root/"day.json").write_bytes(bucket.objects[f"revisions/{revision}/day.json"].body)
            (root/"evidence").mkdir()
            for body_hash in manifest["evidence"]:
                (root/"evidence"/f"{body_hash}.txt").write_bytes(bucket.objects[f"evidence/{body_hash}.txt"].body)
            with self.assertRaisesRegex(ValueError, "accepted before midnight"):
                verify_export(root)

    async def test_dst_days_and_fractional_timezone_lock_at_local_midnight(self):
        for date, zone, hours in [("2026-03-29", "Europe/London", 23),
                                  ("2026-10-25", "Europe/London", 25),
                                  ("2026-09-22", "Asia/Kathmandu", 24)]:
            airport = {**self.airport, "timezone": zone}
            start, end = day_bounds(date, zone)
            self.assertEqual((end-start).total_seconds()/3600, hours)
            result = daily(airport, date, [], self.module.SETTINGS["policy"], end,
                           {"cutoff_at": iso(end), "accepted_at": {}})
            self.assertEqual(result["summary"]["status"], "locked")
            self.assertEqual(result["summary"]["expected"], hours*2)
