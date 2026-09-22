"""Scheduled collection only. Public traffic has no route to this Worker.

Each queue message performs one bounded fetch. D1 leases suppress duplicates;
expired leases and due tasks are redispatched even after queue retries exhaust.
Raw R2 objects and receipts are written before normalized records are committed.
"""
import asyncio
import csv
from datetime import datetime, timedelta
import io
import json
from urllib.parse import urljoin, urlparse
import uuid
from zoneinfo import ZoneInfo

import js
from pyodide.ffi import to_js
from workers import WorkerEntrypoint, Response

from ledger.metar import UTC, iso, parse_time, parse_report
from ledger.evidence import decode
from ledger.sources import Links, collective_listing, eccc_live_links
from ledger.policy import daily, day_bounds
from planner import canonical, digest, job, live_jobs, recovery_jobs, recent_dates, TGFTP_HISTORY
from settings import SETTINGS

MAX_BODY = 8_000_000
GOVERNMENT_HOSTS = {"aviationweather.gov", "tgftp.nws.noaa.gov", "dd.weather.gc.ca", "dd.meteo.gc.ca"}
IMMUTABLE = "public, max-age=31536000, immutable"
LATEST = "public, max-age=0, s-maxage=10, must-revalidate"


def now_utc():
    return datetime.now(UTC)


def js_object(value):
    return to_js(value, dict_converter=js.Object.fromEntries)


def check_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in GOVERNMENT_HOSTS or parsed.username or parsed.port not in (None, 443):
        raise ValueError("Unapproved source URL")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # No public administrative endpoint, trigger, or visitor-driven collection.
        return Response("Collector accepts only scheduled and queue events.\n", status=404)

    def statement(self, sql, *values):
        return self.env.DB.prepare(sql).bind(*values)

    async def rows(self, sql, *values):
        result = await self.statement(sql, *values).all()
        return result["results"]

    async def batch(self, statements):
        results = []
        for offset in range(0, len(statements), 50):
            results.extend(await self.env.DB.batch(statements[offset:offset+50]))
        return results

    async def put(self, key, body, content_type="application/json", immutable=True, condition=None):
        options = {"httpMetadata": {"contentType": content_type,
                                    "cacheControl": IMMUTABLE if immutable else LATEST}}
        if immutable:
            options["onlyIf"] = {"etagDoesNotMatch": "*"}
        if condition is not None:
            options["onlyIf"] = condition
        return await self.env.ARCHIVE.put(key, body, options)

    async def upsert_tasks(self, tasks):
        statements = []
        for item in tasks:
            statements.append(self.statement("""
              INSERT INTO tasks(id,source,mode,kind,payload,interval_seconds,expires_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              expires_at=MAX(tasks.expires_at,excluded.expires_at),
              mode=CASE WHEN excluded.mode='live' THEN 'live' ELSE tasks.mode END,
              interval_seconds=MIN(tasks.interval_seconds,excluded.interval_seconds)
            """, *[item[k] for k in ("id", "source", "mode", "kind", "payload", "interval_seconds", "expires_at")]))
        await self.batch(statements)

    async def dispatch(self, mode, limit):
        now = int(now_utc().timestamp())
        tasks = await self.rows("""SELECT id FROM tasks WHERE mode=? AND next_due<=?
          AND queued_until<=? AND lease_until<=? AND expires_at>?
          ORDER BY CASE WHEN kind='collective-directory' THEN 0
            WHEN source='noaa_awc' THEN 1 WHEN kind='report' THEN 2 ELSE 3 END, next_due,
          expires_at DESC LIMIT ?""", mode, now, now, now, now, limit)
        queue = self.env.LIVE if mode == "live" else self.env.RECOVERY
        for offset in range(0, len(tasks), 100):
            chunk = tasks[offset:offset+100]
            # Reserve before sending. A failed send becomes eligible again in 120 s.
            await self.batch([self.statement("UPDATE tasks SET queued_until=? WHERE id=?", now+120, t["id"]) for t in chunk])
            await queue.sendBatch([{"body": {"task": t["id"]}, "contentType": "json"} for t in chunk])

    async def scheduled(self, controller, env=None, ctx=None):
        now = now_utc()
        airports = SETTINGS["airports"]
        await self.upsert_tasks(live_jobs(airports, now))
        # Live dispatch happens before planning historical work.
        await self.dispatch("live", 300)
        prior = await self.statement("SELECT value FROM state WHERE name='recovery_plan'").first("value")
        if not prior or (now-parse_time(prior)).total_seconds() >= 3600:
            # On restart, extend the overlap to cover downtime, within ECCC retention.
            since = min(now-timedelta(days=3), parse_time(prior) if prior else now-timedelta(days=3))
            since = max(since, now-timedelta(days=29)).replace(minute=0, second=0, microsecond=0)
            await self.upsert_tasks(recovery_jobs(airports, now, since))
            await self.statement("INSERT INTO state VALUES('recovery_plan',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", iso(now)).run()
            # Permanent evidence is already in R2; bound only the working index.
            cutoff = iso(now-timedelta(days=90))
            await self.statement("DELETE FROM tasks WHERE id IN (SELECT id FROM tasks WHERE expires_at<? LIMIT 5000)", int(now.timestamp())-3600).run()
            await self.statement("DELETE FROM reports WHERE id IN (SELECT id FROM reports WHERE observed_at<? AND archived=1 LIMIT 5000)", cutoff).run()
            await self.statement("DELETE FROM receipts WHERE id IN (SELECT id FROM receipts WHERE fetched_at<? AND NOT EXISTS (SELECT 1 FROM reports WHERE reports.receipt_id=receipts.id) LIMIT 50000)", iso(now-timedelta(days=3))).run()
            await self.statement("DELETE FROM rejected WHERE id IN (SELECT id FROM rejected WHERE fetched_at<? LIMIT 5000)", cutoff).run()
        await self.dispatch("recovery", 120)
        # A crash after the normalized insert must not require the source to repeat it.
        await self.archive_reports(await self.rows("SELECT id,payload FROM reports WHERE archived=0 LIMIT 100"))
        await self.publish(now)
        print(json.dumps({"event": "scheduled", "finished_at": iso(now_utc())}))

    async def queue(self, batch, env=None, ctx=None):
        for message in batch.messages:
            task_id = message.body["task"]
            try:
                await self.perform(task_id)
                message.ack()
            except Exception as error:
                print(json.dumps({"event": "task_error", "task": task_id, "error": str(error)}))
                message.retry({"delaySeconds": min(300, 30 * message.attempts)})

    async def perform(self, task_id):
        now = int(now_utc().timestamp())
        token = str(uuid.uuid4())
        rows = await self.rows("""UPDATE tasks SET lease_until=?,lease_token=?,last_attempt=?
          WHERE id=? AND lease_until<=? AND next_due<=? AND expires_at>?
          RETURNING *""", now+180, token, now, task_id, now, now, now)
        if not rows:
            return
        task = rows[0]
        payload = json.loads(task["payload"])
        try:
            count = await self.collect(payload, task["mode"])
            finished = int(now_utc().timestamp())
            await self.statement("""UPDATE tasks SET lease_until=0,queued_until=0,
              next_due=?,last_success=?,last_error=NULL,reports=reports+?
              WHERE id=? AND lease_token=?""",
              (now//task["interval_seconds"]+1)*task["interval_seconds"], finished, count, task_id, token).run()
            # Directory-discovered live reports need not wait for the next cron.
            if task["mode"] == "live" and payload["kind"] == "directory":
                await self.dispatch("live", 100)
        except Exception as error:
            await self.statement("UPDATE tasks SET lease_until=0,last_error=? WHERE id=? AND lease_token=?",
                                 str(error)[:1000], task_id, token).run()
            raise

    async def response(self, source, url):
        check_url(url)
        if source == "noaa_awc":
            # Recovery concurrency is two: <=80 requests/minute plus seven live batches.
            await asyncio.sleep(1.5)
        status, headers, body, error = 0, {}, b"", None
        try:
            response = await js.fetch(url, js_object({"redirect": "manual",
              "headers": {"User-Agent": "PolyMETARs/0.3 (+https://github.com/olibren/poly-metars)", "Accept": "*/*"},
              "signal": js.AbortSignal.timeout(20000)}))
            status = int(response.status)
            for name in ("date", "last-modified", "etag", "content-type"):
                value = response.headers.get(name)
                if value:
                    headers[name] = value
            reader = response.body.getReader() if response.body else None
            chunks, length = [], 0
            while reader is not None:
                part = await reader.read()
                if part.done:
                    break
                chunk = bytes(part.value.to_py())
                length += len(chunk)
                if length > MAX_BODY:
                    await reader.cancel()
                    raise ValueError("Response exceeds 8 MB limit; no partial body accepted")
                chunks.append(chunk)
            body = b"".join(chunks)
            if status not in (200, 204, 404):
                error = f"HTTP {status}"
        except Exception as exc:
            status, body, error = 0, b"", str(exc)
        body_hash = digest(body)
        receipt = {"source": source, "url": url, "fetched_at": iso(now_utc()), "status": status,
                   "body_sha256": body_hash, "bytes": len(body), "headers": headers, "error": error}
        receipt["id"] = digest(canonical(receipt))
        if not await self.env.ARCHIVE.head(f"evidence/{body_hash}.txt"):
            await self.put(f"evidence/{body_hash}.txt", body, "text/plain; charset=utf-8")
        await self.put(f"receipts/{receipt['id']}.json", canonical(receipt))
        await self.statement("INSERT OR IGNORE INTO receipts VALUES(?,?,?)",
                             receipt["id"], receipt["fetched_at"], canonical(receipt).decode()).run()
        if error:
            raise RuntimeError(error)
        return body, receipt

    async def collect(self, task, mode):
        source, url = task["source"], task["url"]
        try:
            body, receipt = await self.response(source, url)
        except Exception:
            if source != "eccc" or "dd.weather.gc.ca" not in url:
                raise
            # The alternative host is the same agency, never an independent vote.
            url = url.replace("dd.weather.gc.ca", "dd.meteo.gc.ca")
            body, receipt = await self.response(source, url)
        now = now_utc()
        if task["kind"] == "directory":
            if receipt["status"] == 404:
                return 0  # A reception directory can legitimately be absent.
            if receipt["status"] != 200:
                raise ValueError("Missing reception directory")
            parser = Links()
            parser.feed(body.decode("utf-8", errors="replace"))
            links = [name for name in parser.links if any(name.startswith(p) for p in task["prefixes"])]
            if task["recent"]:
                links = eccc_live_links(links, task["prefixes"], now)
            await self.upsert_tasks(job(source, mode, "report", urljoin(url, name), 21600,
                                        now+timedelta(days=1)) for name in links)
            return 0
        if task["kind"] == "collective-directory":
            if receipt["status"] != 200:
                raise ValueError("TGFTP collective listing unavailable")
            entries = collective_listing(body.decode("utf-8", errors="replace"))
            # Rotating names are identified by the directory's advertised timestamp.
            await self.upsert_tasks(job(source, "recovery", "report", TGFTP_HISTORY+name,
                                        21600, now+timedelta(hours=6), modified=iso(modified))
                                    for name, modified in entries)
            return 0
        if receipt["status"] == 204 and source == "noaa_awc":
            return 0
        if receipt["status"] != 200:
            raise ValueError(f"Report unavailable: HTTP {receipt['status']}")
        allowed = {a["icao"] for a in SETTINGS["airports"]}
        if source == "noaa_awc":
            payload = json.loads(body)
            if not isinstance(payload, list) or len(payload) >= 400:
                raise ValueError("Invalid or possibly truncated AWC response")
            parsed = []
            for item in payload:
                if item.get("icaoId") not in allowed:
                    continue
                try:
                    stamp = datetime.fromtimestamp(item["obsTime"], UTC)
                    parsed.append(parse_report(item["rawOb"], stamp, kind=item.get("metarType"), observed_at=iso(stamp)))
                except (ValueError, KeyError) as error:
                    parsed.append({"icao": item.get("icaoId"), "raw": item.get("rawOb"), "parse_error": str(error)})
        else:
            parsed = decode(body, receipt)
        statements, record_ids = [], []
        for row in parsed:
            if row["icao"] not in allowed:
                continue
            if "parse_error" in row:
                rejected = {**row, "receipt_id": receipt["id"]}
                rid = digest(canonical(rejected))
                await self.put(f"rejected/{rid}.json", canonical(rejected))
                statements.append(self.statement("INSERT OR IGNORE INTO rejected VALUES(?,?,?)", rid, receipt["fetched_at"], canonical(rejected).decode()))
                continue
            identity = {**row, "source": source}
            record_id = digest(canonical(identity))
            record = {**identity, "id": record_id, "receipt_id": receipt["id"],
                      **{k: receipt[k] for k in ("url", "fetched_at", "body_sha256")}}
            statements.append(self.statement("INSERT OR IGNORE INTO reports(id,icao,observed_at,receipt_id,payload) VALUES(?,?,?,?,?)",
              record_id, row["icao"], row["observed_at"], receipt["id"], canonical(record).decode()))
            record_ids.append(record_id)
        await self.batch(statements)
        # Archive the winning insert's provenance, including when another delivery won.
        for offset in range(0, len(record_ids), 50):
            chunk = record_ids[offset:offset+50]
            rows = await self.rows("SELECT id,payload FROM reports WHERE archived=0 AND id IN ("+",".join("?" for _ in chunk)+")", *chunk)
            await self.archive_reports(rows)
        return len(record_ids)

    async def archive_reports(self, rows):
        gate = asyncio.Semaphore(4)
        async def archive(row):
            async with gate:
                await self.put(f"reports/{row['id']}.json", row["payload"])
        await asyncio.gather(*(archive(row) for row in rows))
        statements = []
        for row in rows:
            record = json.loads(row["payload"])
            airport = next(a for a in SETTINGS["airports"] if a["icao"] == record["icao"])
            date = parse_time(record["observed_at"]).astimezone(ZoneInfo(airport["timezone"])).date().isoformat()
            # Dirty marking and archive acknowledgement are one transaction.
            statements.extend([
              self.statement("INSERT INTO dirty(icao,date) VALUES(?,?) ON CONFLICT(icao,date) DO UPDATE SET generation=generation+1", record["icao"], date),
              self.statement("UPDATE reports SET archived=1 WHERE id=?", row["id"])])
        await self.batch(statements)

    async def publish(self, now):
        previous_object = await self.env.ARCHIVE.get("index.json")
        previous = json.loads(await previous_object.text()) if previous_object else {}
        condition = {"etagMatches": previous_object.etag} if previous_object else {"etagDoesNotMatch": "*"}
        airports, policy = SETTINGS["airports"], SETTINGS["policy"]
        policy_hash, registry_hash = digest(canonical(policy)), digest(canonical(airports))
        revisions = dict(previous.get("revisions", {}))
        dates = recent_dates(airports, now)
        dirty = await self.rows("SELECT * FROM dirty")
        # Bound concurrent airport work; avoid hundreds of serial storage round trips.
        changed = {(d["icao"], d["date"]) for d in dirty}
        same_engine = (previous.get("engine_sha256") == SETTINGS["engine_hashes"]
                       and previous.get("policy_sha256") == policy_hash
                       and previous.get("registry_sha256") == registry_hash)
        gate = asyncio.Semaphore(4)

        async def publish_airport(airport):
            async with gate:
                for date in sorted(set(dates) | {d["date"] for d in dirty if d["icao"] == airport["icao"]}):
                    day_key = f"{date}/{airport['icao']}"
                    if same_engine and day_key in revisions and (airport["icao"], date) not in changed:
                        continue
                    start, end = day_bounds(date, airport["timezone"])
                    rows = await self.rows("SELECT payload FROM reports WHERE archived=1 AND icao=? AND observed_at>=? AND observed_at<? ORDER BY observed_at,id",
                                           airport["icao"], iso(start), iso(end))
                    reports = [json.loads(r["payload"]) for r in rows]
                    identity = {"date": date, "icao": airport["icao"], "report_ids": sorted(r["id"] for r in reports),
                                "policy_sha256": policy_hash, "registry_sha256": registry_hash,
                                "engine_sha256": SETTINGS["engine_hashes"]}
                    revision = digest(canonical(identity))
                    day_key = f"{date}/{airport['icao']}"
                    if revisions.get(day_key) == revision:
                        continue
                    prefix = f"revisions/{revision}/"
                    old_manifest = await self.env.ARCHIVE.get(prefix+"audit.json")
                    if old_manifest:
                        manifest = json.loads(await old_manifest.text())
                    else:
                        receipt_ids = sorted({r["receipt_id"] for r in reports})
                        receipts = []
                        for offset in range(0, len(receipt_ids), 50):
                            chunk = receipt_ids[offset:offset+50]
                            rows = await self.rows("SELECT payload FROM receipts WHERE id IN ("+",".join("?" for _ in chunk)+")", *chunk)
                            receipts.extend(json.loads(row["payload"]) for row in rows)
                        if len(receipts) != len(receipt_ids):
                            raise ValueError("Cannot publish reports without receipts")
                        manifest = {**identity, "schema": "poly-metars-day-v1", "revision": revision,
                                    "generated_at": iso(now), "airport": airport, "registry": airports,
                                    "policy": policy, "reports": reports, "receipts": sorted(receipts, key=lambda r:r["id"]),
                                    "evidence": sorted({r["body_sha256"] for r in receipts})}
                        written = await self.put(prefix+"audit.json", canonical(manifest))
                        if not written:
                            existing = await self.env.ARCHIVE.get(prefix+"audit.json")
                            manifest = json.loads(await existing.text())
                    result = daily(airport, date, manifest["reports"], policy, parse_time(manifest["generated_at"]))
                    result.update({"generated_at": manifest["generated_at"], "policy_sha256": policy_hash,
                                   "registry_sha256": registry_hash, "snapshot_id": revision})
                    buffer = io.StringIO()
                    writer = csv.writer(buffer)
                    writer.writerow(["observation_utc", "local_time", "selected_c", "selected_source", "status", *policy["source_order"]])
                    for row in result["rows"]:
                        selected = row["selected"] or {}
                        writer.writerow([row["observed_at"], row["local_time"], selected.get("temperature_c", ""),
                          selected.get("source", ""), row["status"],
                          *[(row["sources"][s]["report"] or {}).get("temperature_c", "") for s in policy["source_order"]]])
                    await self.put(prefix+"day.csv", buffer.getvalue(), "text/csv; charset=utf-8")
                    await self.put(prefix+"day.json", canonical(result))
                    revisions[day_key] = revision

        await asyncio.gather(*(publish_airport(airport) for airport in airports))
        stamp = int(now.timestamp())
        task_states = await self.rows("SELECT source,mode,kind,last_success,last_attempt,last_error FROM tasks WHERE mode='live' AND expires_at>?", stamp)
        collection = []
        for source in policy["source_order"]:
            active = [t for t in task_states if t["source"] == source and t["mode"] == "live"]
            errors = [t["last_error"] for t in active if t["last_error"]]
            attempted = [t["last_attempt"] for t in active if t["last_attempt"]]
            # Freshness refers to the oldest live route check, not the newest lucky response.
            roots = [t for t in active if t["kind"] == "directory" or source != "eccc"]
            oldest = min((t["last_success"] or 0 for t in roots), default=0)
            collection.append({"source": source, "name": source+"-live", "mode": "live",
              "status": "partial" if errors or not oldest else "ok", "errors": errors[:10],
              "finished_at": iso(datetime.fromtimestamp(max(attempted), UTC)) if attempted else None,
              "last_success_at": iso(datetime.fromtimestamp(oldest, UTC)) if oldest else None,
              "interval_seconds": 60, "reports": 0,
              "scope": "Per-route retrieval checks; observation completeness is shown separately.",
              "pending_tasks": sum(not t["last_success"] for t in active)})
        index = {"mode": "live", "platform": "cloudflare", "generated_at": iso(now_utc()),
                 "snapshot_id": digest(canonical(revisions)), "base_path": "/data",
                 "engine_sha256": SETTINGS["engine_hashes"], "policy_sha256": policy_hash, "registry_sha256": registry_hash,
                 "airports": airports, "dates": sorted({k.split('/')[0] for k in revisions}, reverse=True),
                 "sources": SETTINGS["sources"], "policy": policy, "collection": collection,
                 "jobs": collection, "revisions": revisions, "poll_seconds": 60,
                 "browser_refresh_seconds": 15, "stale_after_seconds": 180,
                 "rejected_count": await self.statement("SELECT COUNT(*) AS n FROM rejected").first("n"),
                 "audit_download": "manifest-and-evidence",
                 "catalog_note": "Expected slots are cadence assumptions, not proof a report was issued."}
        rejected = await self.rows("SELECT payload FROM rejected ORDER BY fetched_at,id")
        await self.put("rejected.jsonl", "".join(r["payload"]+"\n" for r in rejected),
                       "application/x-ndjson", immutable=False)
        # An overlapping older publisher cannot overwrite a newer index.
        published = await self.put("index.json", canonical(index), immutable=False, condition=condition)
        if not published:
            print(json.dumps({"event": "publication_superseded"}))
            return
        await self.put("health.json", canonical({"generated_at": index["generated_at"], "collection": collection}), immutable=False)
        await self.batch([self.statement("DELETE FROM dirty WHERE icao=? AND date=? AND generation=?", d["icao"], d["date"], d["generation"]) for d in dirty])
