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

from ledger.audit import verify_export
from ledger.metar import UTC, iso

ROOT = Path(__file__).resolve().parents[1]


class Statement:
    def __init__(self, db, sql, values=()):
        self.db, self.sql, self.values = db, sql, values

    def bind(self, *values):
        return Statement(self.db, self.sql, values)

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
        self.connection.executescript((ROOT / "cloudflare/migrations/0001_ledger.sql").read_text())
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
        return self.objects[key]


class Queue:
    def __init__(self):
        self.messages = []

    async def sendBatch(self, messages):
        self.messages.extend(messages)


class CloudflareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
        self.http_status = 200
        airports = json.loads((ROOT / "config/airports.json").read_text())
        self.airport = next(a for a in airports if a["icao"] == "EGLC")
        settings = {"airports": [self.airport], "policy": json.loads((ROOT / "config/policy.json").read_text()),
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
        self.now += timedelta(seconds=121)
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

    async def test_recovery_listing_cannot_starve_behind_historical_directories(self):
        jobs = list(self.module.recovery_jobs([self.airport], self.now, self.now-timedelta(days=3)))
        await self.worker.upsert_tasks(jobs)
        await self.worker.dispatch("recovery", 1)
        task_id = self.worker.env.RECOVERY.messages[0]["body"]["task"]
        task = await self.worker.statement("SELECT kind FROM tasks WHERE id=?", task_id).first()
        self.assertEqual(task["kind"], "collective-directory")

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


if __name__ == "__main__":
    unittest.main()
