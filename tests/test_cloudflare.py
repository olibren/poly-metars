"""Crash/retry and publication contracts, using SQLite and an R2 conditional-write model.

The actual Python/JavaScript binding boundary is additionally exercised with Wrangler.
These tests deliberately run without Cloudflare packages or credentials.
"""
import asyncio
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from ledger.audit import verify_export
from ledger.metar import UTC, iso

ROOT = Path(__file__).resolve().parents[1]


class Statement:
    def __init__(self, db, sql, values=()):
        self.db, self.sql, self.values = db, sql, values

    def bind(self, *values):
        return Statement(self.db, self.sql, tuple(float(v) if isinstance(v, int) else v for v in values))

    async def all(self):
        if self.db.fail_reports and "INSERT OR IGNORE INTO reports" in self.sql:
            self.db.fail_reports = False
            raise RuntimeError("Injected D1 write failure")
        cursor = self.db.connection.execute(self.sql, self.values)
        rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
        return {"results": rows, "meta": {"changes": max(cursor.rowcount, 0)}}

    run = all

    async def first(self, column=None):
        rows = (await self.all())["results"]
        return (rows[0][column] if column else rows[0]) if rows else None


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        for migration in sorted((ROOT / "cloudflare/migrations").glob("*.sql")):
            self.connection.executescript(migration.read_text())
        self.fail_reports = False

    def prepare(self, sql):
        return Statement(self, sql)

    async def batch(self, statements):
        self.connection.execute("BEGIN")
        try:
            result = [await statement.all() for statement in statements]
            self.connection.execute("COMMIT")
            return result
        except Exception:
            self.connection.execute("ROLLBACK")
            raise


class StoredObject:
    def __init__(self, body, etag):
        self.body, self.etag = body, etag
        self.uploaded = datetime(2026, 9, 22, 13, tzinfo=UTC)

    async def text(self):
        return self.body.decode()


class Bucket:
    def __init__(self):
        self.objects, self.counter, self.fail_key, self.before_index = {}, 0, None, None

    async def get(self, key):
        return self.objects.get(key)

    head = get

    async def put(self, key, body, options):
        if self.fail_key and key.endswith(self.fail_key):
            self.fail_key = None
            raise RuntimeError("Injected R2 write failure")
        if key == "index.json" and self.before_index:
            callback, self.before_index = self.before_index, None
            await callback()
        old = self.objects.get(key)
        condition = options.get("onlyIf", {})
        if condition.get("etagDoesNotMatch") == "*" and old:
            return None
        if "etagMatches" in condition and (not old or old.etag != condition["etagMatches"]):
            return None
        self.counter += 1
        self.objects[key] = StoredObject(body.encode() if isinstance(body, str) else body, str(self.counter))
        if hasattr(self, "clock"):
            self.objects[key].uploaded = self.clock()
        return self.objects[key]


class Queue:
    def __init__(self):
        self.messages = []

    async def sendBatch(self, messages, **options):
        self.messages.extend({**message, "delaySeconds": options.get("delaySeconds", 0)} for message in messages)


class CloudflareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
        self.http_status = 200
        airports = json.loads((ROOT / "config/airports.json").read_text())
        self.airport = next(a for a in airports if a["icao"] == "EGLC")
        settings = {"airports": [self.airport], "policy": json.loads((ROOT / "config/policy.json").read_text()),
                    "midnight_policy": json.loads((ROOT / "config/policies/routine-metar-v3.json").read_text()),
                    "legacy_policy": json.loads((ROOT / "config/policies/routine-metar-v2.json").read_text()),
                    "retention": json.loads((ROOT / "config/retention.json").read_text()),
                    "sources": json.loads((ROOT / "config/sources.json").read_text()), "engine_hashes": {}}
        self.body = json.dumps([{"icaoId": "EGLC", "obsTime": self.now.timestamp()-600,
                                "metarType": "METAR", "rawOb": "EGLC 221250Z 25005KT 9999 FEW020 23/12 Q1013"}]).encode()

        async def fetch(url, options):
            pieces = [self.body]

            async def read():
                return SimpleNamespace(done=False, value=SimpleNamespace(to_py=lambda:pieces.pop())) if pieces else SimpleNamespace(done=True)

            return SimpleNamespace(status=self.http_status,
              headers=SimpleNamespace(get=lambda name:None),
              body=None if self.http_status == 204 else SimpleNamespace(getReader=lambda:SimpleNamespace(read=read, cancel=AsyncMock())))

        js = ModuleType("js")
        js.fetch, js.AbortSignal, js.Object = fetch, SimpleNamespace(timeout=lambda ms:None), SimpleNamespace(fromEntries=None)
        workers = ModuleType("workers")
        workers.WorkerEntrypoint, workers.Response = object, object
        ffi = ModuleType("pyodide.ffi")
        ffi.to_js = lambda value, **kw:value
        generated = ModuleType("settings")
        generated.SETTINGS = settings
        sys.path.insert(0, str(ROOT / "cloudflare/src"))
        self.import_patch = patch.dict(sys.modules, {"js":js, "workers":workers, "pyodide.ffi":ffi, "settings":generated})
        self.import_patch.start()
        spec = importlib.util.spec_from_file_location("cloudflare_under_test", ROOT / "cloudflare/src/entry.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.now_utc = lambda:self.now
        self.worker = self.module.Default()
        self.worker.env = SimpleNamespace(DB=Database(), ARCHIVE=Bucket(), LIVE=Queue(), RECOVERY=Queue())
        self.worker.env.ARCHIVE.clock = lambda:self.now
        self.worker.env.DB.connection.create_function("unixepoch", 1, lambda _: int(self.now.timestamp()))
        await self.worker.statement("UPDATE state SET value=? WHERE name='midnight_lock_started_at'", str(int(self.now.timestamp()))).run()
        await self.worker.statement("UPDATE state SET value=? WHERE name='next_day_lock_started_at'", str(int(self.now.timestamp()))).run()
        self.sleep_patch = patch.object(self.module.asyncio, "sleep", new=AsyncMock())
        self.sleep_patch.start()
        self.task = self.module.job("noaa_awc", "live", "report",
          "https://aviationweather.gov/api/data/metar?ids=EGLC&format=json&hours=3", 60, self.now+timedelta(hours=3))
        await self.worker.upsert_tasks([self.task])

    async def asyncTearDown(self):
        self.sleep_patch.stop()
        self.import_patch.stop()
        sys.path.remove(str(ROOT / "cloudflare/src"))
        self.worker.env.DB.connection.close()

    async def verify_published_day(self):
        bucket = self.worker.env.ARCHIVE
        index = json.loads(await (await bucket.get("index.json")).text())
        revision = index["revisions"]["2026-09-22/EGLC"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("day.json", "audit.json"):
                (root/name).write_bytes(bucket.objects[f"revisions/{revision}/{name}"].body)
            manifest = json.loads((root/"audit.json").read_text())
            (root/"evidence").mkdir()
            for body_hash in manifest["evidence"]:
                (root/"evidence"/f"{body_hash}.txt").write_bytes(bucket.objects[f"evidence/{body_hash}.txt"].body)
            return verify_export(root)

    async def test_retry_after_raw_evidence_written_replays_exactly(self):
        self.worker.env.DB.fail_reports = True
        with self.assertRaisesRegex(RuntimeError, "D1 write failure"):
            await self.worker.perform(self.task["id"])
        self.assertTrue(any(k.startswith("evidence/") for k in self.worker.env.ARCHIVE.objects))
        task = await self.worker.statement("SELECT * FROM tasks").first()
        self.assertIsNone(task["last_success"])
        await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        result = await self.verify_published_day()
        self.assertEqual(result["reports"], 1)
        self.assertTrue(result["verified"])

    async def test_duplicate_delivery_and_repeated_fetch_keep_first_provenance(self):
        await self.worker.perform(self.task["id"])
        before = await self.worker.statement("SELECT payload FROM reports").first("payload")
        await self.worker.perform(self.task["id"])
        self.now += timedelta(minutes=2)
        await self.worker.perform(self.task["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM reports").first("n"), 1)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM receipts").first("n"), 2)
        self.assertEqual(await self.worker.statement("SELECT payload FROM reports").first("payload"), before)

    async def test_source_receipt_order_survives_late_recovery_and_offline_replay(self):
        newer = json.loads(self.body)[0]
        newer["receiptTime"] = "2026-09-22T12:55:00.200Z"
        self.body = json.dumps([newer]).encode()
        await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        before = await self.worker.env.ARCHIVE.get("index.json")
        old_revision = json.loads(await before.text())["revisions"]["2026-09-22/EGLC"]
        old_day = self.worker.env.ARCHIVE.objects[f"revisions/{old_revision}/day.json"].body
        # An earlier provider version arrives through recovery after publication.
        older = {**newer, "receiptTime": "2026-09-22T12:55:00.100Z",
                 "rawOb": newer["rawOb"].replace("23/12", "24/12")}
        self.now += timedelta(minutes=2)
        self.body = json.dumps([older]).encode()
        await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        result = await self.verify_published_day()
        self.assertTrue(result["verified"])
        self.assertEqual(result["reports"], 2)
        index = json.loads(await (await self.worker.env.ARCHIVE.get("index.json")).text())
        revision = index["revisions"]["2026-09-22/EGLC"]
        day = json.loads(self.worker.env.ARCHIVE.objects[f"revisions/{revision}/day.json"].body)
        row = next(r for r in day["rows"] if r["observed_at"] == "2026-09-22T12:50:00Z")
        self.assertEqual(row["selected"]["temperature_c"], 23)
        self.assertEqual(row["selected"]["source_received_at"], "2026-09-22T12:55:00.200000Z")
        self.assertTrue(row["conflict"])
        self.assertEqual(day["summary"]["blocked"], 0)
        self.assertNotEqual(day["summary"]["status"], "review_required")
        self.assertEqual(self.worker.env.ARCHIVE.objects[f"revisions/{old_revision}/day.json"].body, old_day)

    async def test_partial_publication_is_not_advertised_and_can_resume(self):
        await self.worker.perform(self.task["id"])
        self.worker.env.ARCHIVE.fail_key = "day.json"
        with self.assertRaisesRegex(RuntimeError, "R2 write failure"):
            await self.worker.publish(self.now)
        self.assertIsNone(await self.worker.env.ARCHIVE.get("index.json"))
        self.now += timedelta(minutes=2)
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_older_publisher_cannot_overwrite_newer_index(self):
        await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        newer = self.now+timedelta(minutes=2)
        async def publish_newer():
            self.now = newer
            await self.worker.publish(newer)
        self.worker.env.ARCHIVE.before_index = publish_newer
        await self.worker.publish(self.now+timedelta(minutes=1))
        index = json.loads(await (await self.worker.env.ARCHIVE.get("index.json")).text())
        self.assertEqual(index["generated_at"], iso(newer))

    async def test_lost_queue_message_is_redispatched_after_reservation_expires(self):
        await self.worker.dispatch("live", 10)
        self.assertEqual(len(self.worker.env.LIVE.messages), 1)
        await self.worker.dispatch("live", 10)
        self.assertEqual(len(self.worker.env.LIVE.messages), 1)
        self.now += timedelta(seconds=self.module.RESERVATION_SECONDS+1)
        await self.worker.dispatch("live", 10)
        self.assertEqual(len(self.worker.env.LIVE.messages), 2)

    async def test_redirect_never_becomes_an_accepted_observation(self):
        self.http_status = 302
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            await self.worker.perform(self.task["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM reports").first("n"), 0)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM receipts").first("n"), 1)

    async def test_awc_empty_response_has_no_stream_and_remains_a_successful_check(self):
        self.http_status = 204
        await self.worker.perform(self.task["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM reports").first("n"), 0)
        receipt = json.loads(await self.worker.statement("SELECT payload FROM receipts").first("payload"))
        self.assertEqual((receipt["status"], receipt["bytes"], receipt["error"]), (204, 0, None))
        self.assertIsNotNone(await self.worker.statement("SELECT last_success FROM tasks").first("last_success"))

    async def test_recovery_classes_all_progress_with_bounded_outstanding_messages(self):
        jobs = list(self.module.recovery_jobs([self.airport], self.now, self.now-timedelta(days=30)))
        jobs.extend(self.module.job("eccc", "recovery", "report", "https://dd.weather.gc.ca/test/"+str(i),
                                   86400, self.now+timedelta(days=1), once=True) for i in range(100))
        await self.worker.upsert_tasks(jobs)
        await asyncio.gather(self.worker.dispatch("recovery", 148), self.worker.dispatch("recovery", 148))
        ids = [m["body"]["task"] for m in self.worker.env.RECOVERY.messages]
        self.assertEqual(len(ids), len(set(ids)))
        states = await self.worker.rows("SELECT source,kind,COUNT(*) n FROM tasks WHERE mode='recovery' AND queued_until>0 GROUP BY source,kind")
        counts = {(r["source"], r["kind"]): r["n"] for r in states}
        self.assertGreater(counts[("noaa_awc", "report")], 0)
        self.assertEqual(counts[("noaa_tgftp", "collective-directory")], 1)
        self.assertEqual(counts[("eccc", "directory")], 20)
        self.assertEqual(counts[("eccc", "report")], 40)
        self.now += timedelta(minutes=3)
        before = len(ids)
        await self.worker.dispatch("recovery", 148)
        self.assertEqual(len(self.worker.env.RECOVERY.messages), before)

    async def test_failed_queue_send_releases_claim_for_next_cron(self):
        with patch.object(self.worker.env.LIVE, "sendBatch", side_effect=RuntimeError("Queue unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Queue unavailable"):
                await self.worker.dispatch("live", 300)
        self.assertEqual(await self.worker.statement("SELECT queued_until FROM tasks WHERE id=?", self.task["id"]).first("queued_until"), 0)
        await self.worker.dispatch("live", 300)
        self.assertEqual(len(self.worker.env.LIVE.messages), 1)

    async def test_request_pacing_is_shared_across_modes_and_independent_by_source(self):
        await self.worker.pace("noaa_awc", "recovery")
        await self.worker.pace("noaa_awc", "live")
        self.assertEqual(self.module.asyncio.sleep.call_args.args, (1.0,))
        await self.worker.pace("eccc", "recovery")
        clock = await self.worker.statement("SELECT value FROM state WHERE name='request_clock:noaa_awc'").first("value")
        self.assertEqual(int(float(clock)), int(self.now.timestamp()*1000)+2000)
        for _ in range(7):
            await self.worker.pace("noaa_awc", "recovery")
        with self.assertRaises(self.module.Deferred):
            await self.worker.pace("noaa_awc", "recovery")
        # Live keeps access to a bounded future slot even under a recovery backlog.
        await self.worker.pace("noaa_awc", "live")
        self.assertEqual(self.module.asyncio.sleep.call_args.args, (9.0,))

    async def test_pacing_requeues_without_source_errors_or_queue_retry_exhaustion(self):
        await self.worker.statement("INSERT INTO state VALUES(?,?)", "request_clock:noaa_awc", str(int(self.now.timestamp()*1000)+60000)).run()
        await self.worker.dispatch("live", 300)
        message = SimpleNamespace(body=self.worker.env.LIVE.messages[-1]["body"], attempts=99, ack=unittest.mock.Mock(), retry=unittest.mock.Mock())
        await self.worker.queue(SimpleNamespace(messages=[message]))
        message.ack.assert_called_once()
        message.retry.assert_not_called()
        replacement = self.worker.env.LIVE.messages[-1]
        self.assertEqual(replacement["delaySeconds"], 30)
        state = await self.worker.statement("SELECT * FROM tasks WHERE id=?", self.task["id"]).first()
        self.assertIsNone(state["last_error"])
        self.assertIsNone(state["last_success"])
        self.assertGreater(state["queued_until"], self.now.timestamp())
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n"), 0)
        self.now += timedelta(seconds=61)
        message.body = replacement["body"]
        await self.worker.queue(SimpleNamespace(messages=[message]))
        self.assertIsNotNone(await self.worker.statement("SELECT last_success FROM tasks WHERE id=?", self.task["id"]).first("last_success"))

    async def test_stale_and_legacy_queue_messages_do_not_repeat_fetches(self):
        await self.worker.dispatch("live", 300)
        old = self.worker.env.LIVE.messages[-1]["body"]
        self.now += timedelta(seconds=self.module.RESERVATION_SECONDS+1)
        await self.worker.dispatch("live", 300)
        for body in ({"task": self.task["id"]}, old):
            message = SimpleNamespace(body=body, attempts=1, ack=unittest.mock.Mock(), retry=unittest.mock.Mock())
            await self.worker.queue(SimpleNamespace(messages=[message]))
            message.ack.assert_called_once()
            message.retry.assert_not_called()
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n"), 0)
        message.body = self.worker.env.LIVE.messages[-1]["body"]
        await self.worker.queue(SimpleNamespace(messages=[message]))
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n"), 1)

    async def test_closed_eccc_directory_is_scanned_once_but_failures_retry(self):
        jobs = self.module.recovery_jobs([self.airport], self.now, self.now-timedelta(days=10),
                                         self.now-timedelta(days=10)+timedelta(hours=1), awc_offsets=())
        task = next(j for j in jobs if j["kind"] == "directory")
        await self.worker.upsert_tasks([task])
        self.http_status = 500
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            await self.worker.perform(task["id"])
        self.http_status = 200
        self.body = b'<a href="ignored.txt">Unrelated bulletin</a>'
        await self.worker.perform(task["id"])
        count = await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n")
        self.now += timedelta(days=1)
        await self.worker.upsert_tasks([task])
        await self.worker.perform(task["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n"), count)

    async def test_unarchived_insert_is_repaired_without_refetching_source(self):
        # Fail specifically at normalized archival, after receipt and D1 insertion.
        original = self.worker.env.ARCHIVE.put
        async def fail_report(key, body, options):
            if key.startswith("reports/"):
                raise RuntimeError("Archive temporarily unavailable")
            return await original(key, body, options)
        with patch.object(self.worker.env.ARCHIVE, "put", side_effect=fail_report):
            with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
                await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        self.assertEqual((await self.verify_published_day())["reports"], 0)
        await self.worker.archive_reports(await self.worker.rows("SELECT id,payload FROM reports WHERE archived=0"))
        await self.worker.publish(self.now)
        self.assertEqual((await self.verify_published_day())["reports"], 1)

    async def test_backfill_covers_30_days_with_bounded_queries_and_oldest_first(self):
        jobs = list(self.module.recovery_jobs([self.airport], self.now, self.now-timedelta(days=30)))
        awc = [j for j in jobs if j["source"] == "noaa_awc"]
        self.assertEqual(len(awc), 31)
        for task in awc:
            query = parse_qs(urlparse(json.loads(task["payload"])["url"]).query)
            self.assertEqual(query["ids"], ["EGLC"])
            self.assertEqual(query["hours"], ["24"])
        self.assertIn("2026-08-23", json.loads(awc[-1]["payload"])["url"])
        await self.worker.upsert_tasks(awc)
        await self.worker.dispatch("recovery", 2)
        # Today's short-lived overlapping query precedes the oldest historical day.
        self.assertEqual(self.worker.env.RECOVERY.messages[1]["body"]["task"], awc[-1]["id"])

    async def test_retention_keeps_whole_boundary_day_and_expires_old_index_entries(self):
        await self.worker.perform(self.task["id"])
        await self.worker.publish(self.now)
        original = await self.worker.statement("SELECT payload FROM reports").first("payload")
        self.now += timedelta(days=30)
        await self.worker.publish(self.now)
        await self.worker.prune(self.now)
        self.assertEqual(await self.worker.statement("SELECT payload FROM reports").first("payload"), original)
        self.assertEqual((await self.verify_published_day())["reports"], 1)
        # The first receipt is older than 30 days but still supports the boundary day.
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM receipts").first("n"), 1)
        self.now += timedelta(days=1)
        await self.worker.publish(self.now)
        await self.worker.prune(self.now)
        index = json.loads(await (await self.worker.env.ARCHIVE.get("index.json")).text())
        self.assertNotIn("2026-09-22/EGLC", index["revisions"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM reports").first("n"), 0)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM receipts").first("n"), 0)
        # Refetching stale upstream content cannot bring the expired day back.
        await self.worker.collect(json.loads(self.task["payload"]), "recovery")
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM reports").first("n"), 0)

    async def test_local_retention_boundary_handles_dst_and_utc_offsets(self):
        london = {**self.airport, "timezone": "Europe/London"}
        now = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
        self.assertEqual(self.module.retention_start(london, now), datetime(2026, 9, 25, 23, tzinfo=UTC))
        airport = {**self.airport, "timezone": "Asia/Kolkata"}
        self.assertEqual(self.module.retention_start(airport, self.now), datetime(2026, 8, 22, 18, 30, tzinfo=UTC))

    async def test_reused_evidence_refreshes_storage_age_without_changing_provenance(self):
        await self.worker.perform(self.task["id"])
        original = await self.worker.statement("SELECT payload FROM reports").first("payload")
        key = next(k for k in self.worker.env.ARCHIVE.objects if k.startswith("evidence/"))
        old = await self.worker.env.ARCHIVE.head(key)
        old.uploaded = self.now-timedelta(days=31)
        self.now += timedelta(minutes=2)
        await self.worker.perform(self.task["id"])
        new = await self.worker.env.ARCHIVE.head(key)
        self.assertNotEqual(old.etag, new.etag)
        self.assertEqual(old.body, new.body)
        self.assertEqual(await self.worker.statement("SELECT payload FROM reports").first("payload"), original)
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())["verified"])

    async def test_immutable_history_files_are_not_redownloaded_on_every_listing(self):
        task = self.module.job("noaa_awc", "recovery", "report", json.loads(self.task["payload"])["url"],
                               60, self.now+timedelta(days=2), once=True)
        await self.worker.upsert_tasks([task])
        await self.worker.perform(task["id"])
        self.now += timedelta(hours=12)
        await self.worker.upsert_tasks([task])
        await self.worker.perform(task["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) AS n FROM receipts").first("n"), 1)

    async def test_history_rechecks_slow_down_as_days_age_without_slowing_live_tasks(self):
        tasks = list(self.module.recovery_jobs([self.airport], self.now, self.now, awc_offsets=(1,)))
        recent = next(t for t in tasks if t["source"] == "noaa_awc")
        await self.worker.upsert_tasks([recent])
        later = self.now+timedelta(days=3)
        tasks = list(self.module.recovery_jobs([self.airport], later, later, awc_offsets=(4,)))
        older = next(t for t in tasks if t["source"] == "noaa_awc")
        self.assertEqual(recent["id"], older["id"])
        await self.worker.upsert_tasks([older])
        self.assertEqual(await self.worker.statement("SELECT interval_seconds FROM tasks WHERE id=?", older["id"]).first("interval_seconds"), 86400)
        await self.worker.upsert_tasks([{**self.task, "mode": "recovery", "interval_seconds": 86400}])
        self.assertEqual(await self.worker.statement("SELECT interval_seconds FROM tasks WHERE id=?", self.task["id"]).first("interval_seconds"), 60)

    async def test_fresh_account_automatically_plans_all_airports_and_recovers_interruption(self):
        self.module.SETTINGS["airports"] = json.loads((ROOT/"config/airports.json").read_text())
        with patch.object(self.worker, "publish", new=AsyncMock()), patch.object(self.worker, "prune", new=AsyncMock()):
            for _ in range(5):
                await self.worker.scheduled(None)
                self.assertTrue(self.worker.env.LIVE.messages)
                plans = await self.worker.rows("SELECT id FROM tasks WHERE kind='plan' AND last_success IS NULL AND queued_until>0")
                for plan in plans:
                    await self.worker.perform(plan["id"])
                self.now += timedelta(minutes=1)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM tasks WHERE kind='plan' AND last_success IS NULL").first("n"), 0)
        self.assertGreater(await self.worker.statement("SELECT COUNT(*) n FROM tasks WHERE source='eccc' AND kind='directory' AND mode='recovery'").first("n"), 24000)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM tasks WHERE source='noaa_awc' AND kind='report' AND mode='recovery'").first("n"), 50*32)
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM receipts").first("n"), 0)
        # Re-expanding an already planned window is idempotent.
        plan = await self.worker.statement("SELECT payload FROM tasks WHERE kind='plan' AND source='noaa_awc'").first("payload")
        before = await self.worker.statement("SELECT COUNT(*) n FROM tasks").first("n")
        await self.worker.collect(json.loads(plan), "recovery")
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM tasks").first("n"), before)

    async def test_interrupted_planning_resumes_after_partially_committed_batch(self):
        self.module.SETTINGS["airports"] = json.loads((ROOT/"config/airports.json").read_text())
        plan = next(self.module.planning_jobs(self.now))
        await self.worker.upsert_tasks([plan])
        original = self.worker.env.DB.batch
        calls = 0
        async def fail_second_batch(statements):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Planning interrupted")
            return await original(statements)
        with patch.object(self.worker.env.DB, "batch", side_effect=fail_second_batch):
            with self.assertRaisesRegex(RuntimeError, "Planning interrupted"):
                await self.worker.perform(plan["id"])
        partial = await self.worker.statement("SELECT COUNT(*) n FROM tasks WHERE mode='recovery' AND kind='report'").first("n")
        self.assertGreater(partial, 0)
        self.assertLess(partial, 50*32)
        await self.worker.perform(plan["id"])
        self.assertEqual(await self.worker.statement("SELECT COUNT(*) n FROM tasks WHERE mode='recovery' AND kind='report'").first("n"), 50*32)
        self.assertIsNotNone(await self.worker.statement("SELECT last_success FROM tasks WHERE id=?", plan["id"]).first("last_success"))

    async def test_live_rollover_retires_old_hours_and_keeps_current_minute_checks(self):
        await self.worker.upsert_tasks(self.module.live_jobs([self.airport], self.now), refresh_live=True)
        self.now += timedelta(hours=1)
        await self.worker.upsert_tasks(self.module.live_jobs([self.airport], self.now), refresh_live=True)
        current = await self.worker.rows("SELECT interval_seconds,payload FROM tasks WHERE mode='live' AND kind='directory' AND expires_at>?", int(self.now.timestamp()))
        self.assertEqual(sorted(t["interval_seconds"] for t in current), [60, 60, 600, 600])
        self.assertTrue(all("/12/" not in t["payload"] for t in current))


if __name__ == "__main__":
    unittest.main()
