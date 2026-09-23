"""Scheduled collection only. Public traffic has no route to this Worker.

Each queue message performs one bounded fetch. D1 leases suppress duplicates;
expired leases and due tasks are redispatched even after queue retries exhaust.
Raw R2 objects and receipts are written before normalized records are committed.
"""
import asyncio
import csv
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import io
import json
from urllib.parse import urljoin, urlparse
import uuid
from zoneinfo import ZoneInfo

import js
from pyodide.ffi import to_js
from workers import WorkerEntrypoint, Response

from ledger.metar import UTC, iso, parse_time, parse_awc
from ledger.evidence import decode, bulletin_reference
from ledger.sources import Links, collective_listing, eccc_live_links
from ledger.policy import daily, day_bounds, resolution_deadline
from planner import canonical, digest, job, live_jobs, recovery_jobs, planning_jobs, recent_dates, retained_dates, retention_start, TGFTP_HISTORY
from settings import SETTINGS

MAX_BODY = 8_000_000
GOVERNMENT_HOSTS = {"aviationweather.gov", "tgftp.nws.noaa.gov", "dd.weather.gc.ca", "dd.meteo.gc.ca", "api.met.no"}
IMMUTABLE = "public, max-age=86400, immutable"
LATEST = "public, max-age=0, s-maxage=10, must-revalidate"
RESERVATION_SECONDS = 900
# Shared across both queues and all isolates, including retries and alternate hosts.
REQUEST_SPACING_MS = {"noaa_awc": 1000, "eccc": 1100, "noaa_tgftp": 250, "met_no": 1000}
# Outstanding messages per class, not an unbounded number added every minute.
RECOVERY_LANES = (("kind='plan'", 8),
                  ("source='noaa_awc' AND kind!='plan'", 60),
                  ("source='noaa_tgftp' AND kind!='plan'", 20),
                  ("source='eccc' AND kind='directory'", 20),
                  ("source='eccc' AND kind='report'", 40))


class Deferred(Exception):
    def __init__(self, seconds):
        self.seconds = seconds



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

    async def upsert_tasks(self, tasks, refresh_live=False):
        tasks = [task for task in tasks if task["source"] in SETTINGS["policy"]["source_order"]]
        statements = []
        if refresh_live:
            # Expire reception hours no longer in the live plan, in the same batch
            # transaction as their replacements. Recovery owns older hours.
            statements.append(self.statement("UPDATE tasks SET expires_at=MIN(expires_at,?) WHERE mode='live' AND kind='directory'",
                                             int(now_utc().timestamp())))
        # Seven bindings per row; stay below D1's 100-variable statement limit.
        fields = ("id", "source", "mode", "kind", "payload", "interval_seconds", "expires_at")
        for offset in range(0, len(tasks), 12):
            chunk = tasks[offset:offset+12]
            statements.append(self.statement("""
              INSERT INTO tasks(id,source,mode,kind,payload,interval_seconds,expires_at)
              VALUES """+",".join("(?,?,?,?,?,?,?)" for _ in chunk)+"""
              ON CONFLICT(id) DO UPDATE SET
              expires_at=MAX(tasks.expires_at,excluded.expires_at),
              mode=CASE WHEN excluded.mode='live' THEN 'live' ELSE tasks.mode END,
              interval_seconds=CASE WHEN tasks.mode='live' AND excluded.mode!='live'
                THEN tasks.interval_seconds ELSE excluded.interval_seconds END
            """, *[item[k] for item in chunk for k in fields]))
        await self.batch(statements)

    async def dispatch(self, mode, limit):
        now = int(now_utc().timestamp())
        queue = self.env.LIVE if mode == "live" else self.env.RECOVERY
        lanes = RECOVERY_LANES if mode == "recovery" else (("1=1", limit),)
        sent = 0
        for predicate, capacity in lanes:
            count = limit-sent
            if count <= 0:
                break
            # Claim and select atomically. Concurrent cron/directory dispatches
            # cannot reserve the same task; queued work gets time to finish.
            tasks = await self.rows("""UPDATE tasks SET queued_until=? WHERE id IN (
              SELECT id FROM tasks WHERE mode=? AND next_due<=? AND queued_until<=?
              AND lease_until<=? AND expires_at>? AND """+predicate+"""
              ORDER BY CASE WHEN kind='collective-directory' THEN 0 ELSE 1 END,
                CASE WHEN last_success IS NULL THEN 0 ELSE 1 END, expires_at, next_due,id
              LIMIT MIN(?,MAX(0,?-(SELECT COUNT(*) FROM tasks WHERE mode=? AND expires_at>?
                AND (queued_until>? OR lease_until>?) AND """+predicate+"""))))
              RETURNING id""", now+RESERVATION_SECONDS, mode, now, now, now, now,
              count, capacity, mode, now, now, now)
            for offset in range(0, len(tasks), 100):
                chunk = tasks[offset:offset+100]
                try:
                    await queue.sendBatch([{"body": {"task": t["id"], "reservation": now+RESERVATION_SECONDS}, "contentType": "json"} for t in chunk])
                except Exception:
                    await self.batch([self.statement("UPDATE tasks SET queued_until=0 WHERE id=? AND queued_until=?",
                                                     t["id"], now+RESERVATION_SECONDS) for t in tasks[offset:]])
                    raise
            sent += len(tasks)
        return sent

    async def retire_inactive_tasks(self, now):
        sources = SETTINGS["policy"]["source_order"]
        await self.statement("UPDATE tasks SET expires_at=MIN(expires_at,?),queued_until=0 WHERE source NOT IN ("
                             + ",".join("?" for _ in sources) + ") AND (expires_at>? OR queued_until>0)",
                             int(now.timestamp()), *sources, int(now.timestamp())).run()

    async def scheduled(self, controller, env=None, ctx=None):
        now = now_utc()
        airports = SETTINGS["airports"]
        await self.retire_inactive_tasks(now)
        await self.upsert_tasks(live_jobs(airports, now), refresh_live=True)
        await self.dispatch("live", 300)
        # Planning itself is bounded queue work: a fresh owner deployment starts
        # automatically, and every window can resume without an operator/script.
        await self.upsert_tasks(planning_jobs(now, SETTINGS["retention"]["days"]))
        await self.dispatch("recovery", 148)
        # A crash after the normalized insert must not require the source to repeat it.
        await self.archive_reports(await self.rows("SELECT id,payload FROM reports WHERE archived=0 LIMIT 100"))
        # Mixed-version rollout or restored legacy rows: timestamp when this
        # deployment first knows they are durable, never backdate acceptance.
        await self.statement("UPDATE reports SET accepted_at=unixepoch('now') WHERE id IN (SELECT id FROM reports WHERE archived=1 AND accepted_at IS NULL LIMIT 1000)").run()
        await self.publish(now)
        await self.prune(now)
        print(json.dumps({"event": "scheduled", "finished_at": iso(now_utc())}))

    async def prune(self, now):
        # Keep the full boundary day for each airport. Delete only bounded batches
        # after publication; receipts referenced by retained reports remain intact.
        days = SETTINGS["retention"]["days"]
        for airport in SETTINGS["airports"]:
            cutoff = iso(retention_start(airport, now, days))
            await self.statement("DELETE FROM reports WHERE id IN (SELECT id FROM reports WHERE icao=? AND observed_at<? LIMIT 1000)", airport["icao"], cutoff).run()
        cutoff = iso(now-timedelta(days=days))
        await self.statement("DELETE FROM tasks WHERE id IN (SELECT id FROM tasks WHERE expires_at<? LIMIT 1000)", int(now.timestamp())-3600).run()
        await self.statement("DELETE FROM receipts WHERE id IN (SELECT id FROM receipts WHERE fetched_at<? AND NOT EXISTS (SELECT 1 FROM reports WHERE reports.receipt_id=receipts.id) LIMIT 5000)", cutoff).run()
        await self.statement("DELETE FROM rejected WHERE id IN (SELECT id FROM rejected WHERE fetched_at<? LIMIT 1000)", cutoff).run()

    async def queue(self, batch, env=None, ctx=None):
        for message in batch.messages:
            task_id = message.body["task"]
            reservation = message.body.get("reservation")
            if reservation is None:
                # Pre-upgrade messages have short-lived D1 reservations. Cron
                # replaces them automatically; do not drain a legacy duplicate storm.
                message.ack()
                continue
            try:
                delay = await self.perform(task_id, reservation)
                if delay:
                    mode, seconds, replacement = delay
                    queue = self.env.LIVE if mode == "live" else self.env.RECOVERY
                    await queue.sendBatch([{"body": {"task": task_id, "reservation": replacement}, "contentType": "json"}], delaySeconds=seconds)
                message.ack()
            except Exception as error:
                print(json.dumps({"event": "task_error", "task": task_id, "error": str(error)}))
                message.retry({"delaySeconds": min(300, 30 * message.attempts)})

    async def perform(self, task_id, reservation=None):
        now = int(now_utc().timestamp())
        token = str(uuid.uuid4())
        rows = await self.rows("""UPDATE tasks SET lease_until=?,lease_token=?,last_attempt=?
          WHERE id=? AND lease_until<=? AND next_due<=? AND expires_at>?
          AND (? IS NULL OR queued_until=?)
          RETURNING *""", now+180, token, now, task_id, now, now, now, reservation, reservation)
        if not rows:
            return
        task = rows[0]
        if task["source"] not in SETTINGS["policy"]["source_order"]:
            # A queued pre-upgrade message must not revive retired collection.
            await self.statement("UPDATE tasks SET expires_at=MIN(expires_at,?),lease_until=0,queued_until=0 WHERE id=? AND lease_token=?",
                                 now, task_id, token).run()
            return
        payload = json.loads(task["payload"])
        try:
            count = await self.collect(payload, task["mode"])
            finished = int(now_utc().timestamp())
            closed_directory = (payload["kind"] == "directory" and task["mode"] == "recovery"
                                and now_utc()-bulletin_reference({"source": "eccc", "url": payload["url"],
                                                               "fetched_at": iso(now_utc())}) >= timedelta(days=3))
            await self.statement("""UPDATE tasks SET lease_until=0,queued_until=0,
              next_due=?,last_success=?,last_error=NULL,reports=reports+?
              WHERE id=? AND lease_token=?""",
              task["expires_at"] if payload.get("once") or closed_directory else (finished//task["interval_seconds"]+1)*task["interval_seconds"], finished, count, task_id, token).run()
            # Directory-discovered live reports need not wait for the next cron.
            if task["mode"] == "live" and payload["kind"] == "directory":
                await self.dispatch("live", 100)
        except Deferred as deferred:
            # A delayed replacement message keeps normal pacing out of the error
            # retry budget. Lost replacements still recover through the D1 lease.
            stamp = int(now_utc().timestamp())
            await self.statement("UPDATE tasks SET lease_until=0,queued_until=?,next_due=? WHERE id=? AND lease_token=?",
                                 stamp+RESERVATION_SECONDS, stamp+deferred.seconds, task_id, token).run()
            return task["mode"], deferred.seconds, stamp+RESERVATION_SECONDS
        except Exception as error:
            await self.statement("UPDATE tasks SET lease_until=0,last_error=? WHERE id=? AND lease_token=?",
                                 str(error)[:1000], task_id, token).run()
            raise

    async def pace(self, source, mode):
        # SQLite serializes this single UPSERT across all Worker instances. Reserve
        # short future slots, never a whole queue's worth of sleeping consumers.
        now = int(now_utc().timestamp()*1000)
        spacing = REQUEST_SPACING_MS[source]
        horizon = 40000 if mode == "live" else 8000
        row = await self.statement("""INSERT INTO state(name,value) VALUES(?,?)
          ON CONFLICT(name) DO UPDATE SET value=MAX(CAST(state.value AS INTEGER),?)+?
          WHERE CAST(state.value AS INTEGER)<=? RETURNING CAST(value AS INTEGER) AS slot""",
          "request_clock:"+source, str(now+spacing), now, spacing, now+horizon).first()
        if not row:
            raise Deferred(30)
        wait = max(0, (int(row["slot"])-spacing-now)/1000)
        if wait:
            await asyncio.sleep(wait)

    async def response(self, source, url, mode="live"):
        check_url(url)
        request_headers = {"User-Agent": "PolyMETARs/0.3 (+https://github.com/olibren/poly-metars)", "Accept": "*/*"}
        cache, cached = None, None
        cache_key = "met_no_cache:"+digest(url.encode())
        if source == "met_no":
            value = await self.statement("SELECT value FROM state WHERE name=?", cache_key).first("value")
            if value:
                cache = json.loads(value)
                saved = await self.env.ARCHIVE.get(f"receipts/{cache['receipt_id']}.json")
                if saved:
                    original = json.loads(await saved.text())
                    obj = await self.env.ARCHIVE.get(f"evidence/{original['body_sha256']}.txt")
                    if obj:
                        saved_body = (await obj.text()).encode()
                        if digest(saved_body) == original["body_sha256"]:
                            cached = (saved_body, original)
            if cached:
                if cache["expires_at"] > now_utc().timestamp():
                    return cached
                if cache.get("last_modified"):
                    request_headers["If-Modified-Since"] = cache["last_modified"]
        await self.pace(source, mode)
        status, headers, body, error = 0, {}, b"", None
        try:
            response = await js.fetch(url, js_object({"redirect": "manual",
              "headers": request_headers,
              "signal": js.AbortSignal.timeout(20000)}))
            status = int(response.status)
            for name in ("date", "last-modified", "etag", "content-type", "expires"):
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
            if status not in (200, 204, 404) and not (source == "met_no" and status == 304 and cached):
                error = f"HTTP {status}"
        except Exception as exc:
            status, body, error = 0, b"", str(exc)
        body_hash = digest(body)
        receipt = {"source": source, "url": url, "fetched_at": iso(now_utc()), "status": status,
                   "body_sha256": body_hash, "bytes": len(body), "headers": headers, "error": error}
        receipt["id"] = digest(canonical(receipt))
        key = f"evidence/{body_hash}.txt"
        existing = await self.env.ARCHIVE.head(key)
        if not existing:
            await self.put(key, body, "text/plain; charset=utf-8")
        elif existing.uploaded.timestamp() < now_utc().timestamp()-SETTINGS["retention"]["evidence_refresh_hours"]*3600:
            # Same bytes and hash, refreshed storage age. A reused global bulletin
            # must not expire before a newer receipt's retained observations.
            await self.put(key, body, "text/plain; charset=utf-8", condition={"etagMatches": existing.etag})
        await self.put(f"receipts/{receipt['id']}.json", canonical(receipt))
        await self.statement("INSERT OR IGNORE INTO receipts VALUES(?,?,?)",
                             receipt["id"], receipt["fetched_at"], canonical(receipt).decode()).run()
        if error:
            raise RuntimeError(error)
        if source == "met_no" and status in (200, 304):
            expires = now_utc().timestamp()
            if headers.get("expires"):
                try:
                    expires = parsedate_to_datetime(headers["expires"]).timestamp()
                except (ValueError, TypeError, OverflowError):
                    pass
            # Keep the original 200 receipt on 304, including after an ingest
            # failure. Never invent a new first receipt for cached observations.
            state = {"receipt_id": cached[1]["id"] if status == 304 else receipt["id"],
                     "expires_at": expires,
                     "last_modified": headers.get("last-modified") or (cache.get("last_modified") if status == 304 else None)}
            await self.statement("INSERT INTO state(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                                 cache_key, canonical(state).decode()).run()
            if status == 304:
                return cached
        return body, receipt

    async def collect(self, task, mode):
        source, url = task["source"], task["url"]
        if source not in SETTINGS["policy"]["source_order"]:
            return 0
        now = now_utc()
        if task["kind"] == "plan":
            if source == "noaa_awc":
                jobs = recovery_jobs(SETTINGS["airports"], now, now+timedelta(hours=1),
                                     awc_offsets=range(SETTINGS["retention"]["days"]+2))
                await self.upsert_tasks(j for j in jobs if j["source"] == "noaa_awc")
            else:
                await self.upsert_tasks(recovery_jobs(SETTINGS["airports"], now, parse_time(task["since"]),
                                                     parse_time(task["until"]), awc_offsets=()))
            return 0
        try:
            body, receipt = await self.response(source, url, mode)
        except Deferred:
            raise
        except Exception:
            if source != "eccc" or "dd.weather.gc.ca" not in url:
                raise
            # The alternative host is the same agency, never an independent vote.
            url = url.replace("dd.weather.gc.ca", "dd.meteo.gc.ca")
            body, receipt = await self.response(source, url, mode)
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
            expiry = bulletin_reference(receipt)+timedelta(days=31)
            await self.upsert_tasks(job(source, mode, "report", urljoin(url, name), 21600,
                                        expiry, once=True) for name in links)
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
        cutoffs = {a["icao"]: iso(retention_start(a, now, SETTINGS["retention"]["days"])) for a in SETTINGS["airports"]}
        if source == "noaa_awc":
            payload = json.loads(body)
            if not isinstance(payload, list) or len(payload) >= 400:
                raise ValueError("Invalid or possibly truncated AWC response")
            parsed = []
            for item in payload:
                if item.get("icaoId") not in allowed:
                    continue
                try:
                    parsed.append(parse_awc(item))
                except (ValueError, KeyError) as error:
                    parsed.append({"icao": item.get("icaoId"), "raw": item.get("rawOb"), "parse_error": str(error)})
        else:
            parsed = list(decode(body, receipt))
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
            if row["observed_at"] < cutoffs[row["icao"]]:
                continue  # Preserve raw receipt, but never resurrect an expired day.
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
              self.statement("UPDATE reports SET archived=1,accepted_at=COALESCE(accepted_at,unixepoch('now')) WHERE id=?", row["id"])])
        await self.batch(statements)

    async def persist_first_publications(self, first, published_at):
        """Run only for a successfully committed index (or its recovery).

        The index is the publication commit. Saving its first-reading receipts
        before replacing it makes interrupted finalization restartable.
        """
        for key, item in list(first.items()):
            if "published_at" not in item:
                item = {**item, "published_at": iso(published_at)}
                path = f"first-publications/{key}.json"
                await self.put(path, canonical(item))
                stored = await self.env.ARCHIVE.get(path)
                first[key] = json.loads(await stored.text())

    async def publish(self, now, followup=False):
        now = max(now, now_utc())
        previous_object = await self.env.ARCHIVE.get("index.json")
        previous = json.loads(await previous_object.text()) if previous_object else {}
        condition = {"etagMatches": previous_object.etag} if previous_object else {"etagDoesNotMatch": "*"}
        airports, policy = SETTINGS["airports"], SETTINGS["policy"]
        policy_hash, registry_hash = digest(canonical(policy)), digest(canonical(airports))
        # lock_mode "disabled" (development): every retained day follows the current
        # policy and stays revisable. Existing lock and first-publication objects are
        # left untouched in R2 but neither read nor advertised.
        locking = policy.get("lock_mode") != "disabled"
        activation_object = await self.env.ARCHIVE.get("locking.json") if locking else None
        if locking and not activation_object:
            activated = await self.statement("SELECT value FROM state WHERE name='midnight_lock_started_at'").first("value")
            if activated is None:
                raise ValueError("Apply the midnight-lock migration before publishing")
            await self.put("locking.json", canonical({"started_at": int(activated)}))
            activation_object = await self.env.ARCHIVE.get("locking.json")
        activation = json.loads(await activation_object.text())["started_at"] if locking else None
        next_activation = None
        if policy.get("lock_mode") == "next_day_publication":
            next_object = await self.env.ARCHIVE.get("next-day-locking.json")
            if not next_object:
                value = await self.statement("SELECT value FROM state WHERE name='next_day_lock_started_at'").first("value")
                if value is None:
                    raise ValueError("Apply the next-day-lock migration before publishing")
                await self.put("next-day-locking.json", canonical({"started_at": int(value)}))
                next_object = await self.env.ARCHIVE.get("next-day-locking.json")
            next_activation = json.loads(await next_object.text())["started_at"]
        retained = {a["icao"]: set(retained_dates(a, now, SETTINGS["retention"]["days"])) for a in airports}
        revisions = {k: v for k, v in previous.get("revisions", {}).items()
                     if k.split("/")[0] in retained.get(k.split("/")[1], set())}
        locks = {k: v for k, v in previous.get("locks", {}).items()
                 if locking and k.split("/")[0] in retained.get(k.split("/")[1], set())}
        first = {k: v for k, v in previous.get("first_publications", {}).items()
                 if locking and k.split("/")[0] in retained.get(k.split("/")[1], set())}
        if previous_object:
            await self.persist_first_publications(first, previous_object.uploaded)
        new_first = False
        dates = recent_dates(airports, now)
        # Drain live changes first, then a bounded batch of recovered days.
        dirty = await self.rows("SELECT * FROM dirty ORDER BY date DESC,icao LIMIT 100")
        # Bound concurrent airport work; avoid hundreds of serial storage round trips.
        changed = {(d["icao"], d["date"]) for d in dirty}
        same_engine = (previous.get("engine_sha256") == SETTINGS["engine_hashes"]
                       and previous.get("policy_sha256") == policy_hash
                       and previous.get("registry_sha256") == registry_hash)
        gate = asyncio.Semaphore(4)
        handled = {(d["icao"], d["date"]) for d in dirty
                   if d["date"] not in retained.get(d["icao"], set())}

        async def publish_airport(airport):
            nonlocal new_first
            async with gate:
                # Include retained prior days after outages, even if they have no dirty reports.
                # Completed lock pointers are small; no evidence scan is needed for them.
                candidates = set(dates) | {d["date"] for d in dirty if d["icao"] == airport["icao"]}
                if locking:
                    candidates |= {d for d in retained[airport["icao"]]
                                   if activation < day_bounds(d, airport["timezone"])[1].timestamp() <= now.timestamp()}
                closing_work = 0
                for date in sorted(candidates, reverse=True):
                    if date not in retained[airport["icao"]]:
                        continue
                    day_key = f"{date}/{airport['icao']}"
                    if day_key in locks:
                        revisions[day_key] = locks[day_key]["revision"]
                        handled.add((airport["icao"], date))
                        continue
                    start, end = day_bounds(date, airport["timezone"])
                    day_now = max(now, now_utc())
                    governed = locking and end.timestamp() > activation
                    next_governed = next_activation is not None and end.timestamp() > next_activation
                    day_policy = (policy if not locking or next_governed or (governed and next_activation is None)
                                  else SETTINGS["midnight_policy"] if governed else SETTINGS["legacy_policy"])
                    cutoff, trigger, trigger_manifest = end, None, None
                    if next_governed:
                        cutoff = resolution_deadline(date)
                        next_date = (datetime.fromisoformat(date)+timedelta(days=1)).date().isoformat()
                        next_key = f"{next_date}/{airport['icao']}"
                        candidate = first.get(next_key)
                        if not candidate or "published_at" not in candidate:
                            saved = await self.env.ARCHIVE.get(f"first-publications/{next_key}.json")
                            candidate = json.loads(await saved.text()) if saved else None
                        if candidate and parse_time(candidate["published_at"]) < cutoff:
                            trigger = candidate
                            cutoff = parse_time(trigger["published_at"])
                    closing = governed and day_now >= cutoff
                    if closing:
                        if closing_work >= 2:
                            continue  # Resume the backlog next tick without delaying today's publication.
                        closing_work += 1
                    handled.add((airport["icao"], date))
                    lock_key = f"locks/{date}/{airport['icao']}.json"
                    existing_lock = await self.env.ARCHIVE.get(lock_key) if locking else None
                    if existing_lock:
                        locks[day_key] = json.loads(await existing_lock.text())
                        revisions[day_key] = locks[day_key]["revision"]
                        continue
                    day_policy_hash = digest(canonical(day_policy))
                    needs_first = next_activation is not None and start.timestamp() > next_activation and day_key not in first
                    if not closing and not needs_first and same_engine and day_key in revisions and (airport["icao"], date) not in changed:
                        continue
                    rows = await self.rows("SELECT payload,accepted_at FROM reports WHERE archived=1 AND icao=? AND observed_at>=? AND observed_at<? ORDER BY observed_at,id",
                                           airport["icao"], iso(start), iso(end))
                    if governed:
                        rows = [r for r in rows if r["accepted_at"] is not None and r["accepted_at"] < cutoff.timestamp()
                                and parse_time(json.loads(r["payload"])["fetched_at"]) < cutoff]
                    reports = [json.loads(r["payload"]) for r in rows]
                    finalization = {"cutoff_at": iso(cutoff),
                                    "accepted_at": {json.loads(r["payload"])["id"]: int(r["accepted_at"]) for r in rows}} if closing else None
                    if closing and next_governed:
                        finalization.update({"deadline_at": iso(resolution_deadline(date)),
                                             "reason": "next_day_publication" if trigger else "deadline", "trigger": trigger})
                        if trigger:
                            obj = await self.env.ARCHIVE.get(f"revisions/{trigger['revision']}/audit.json")
                            if not obj:
                                raise ValueError("Missing first-publication evidence")
                            trigger_manifest = json.loads(await obj.text())
                    identity = {"date": date, "icao": airport["icao"], "report_ids": sorted(r["id"] for r in reports),
                                "policy_sha256": day_policy_hash, "registry_sha256": registry_hash,
                                "engine_sha256": SETTINGS["engine_hashes"]}
                    if closing:
                        identity["finalization"] = finalization
                    revision = digest(canonical(identity))
                    day_key = f"{date}/{airport['icao']}"
                    if not closing and not needs_first and revisions.get(day_key) == revision:
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
                        manifest = {**identity, "schema": "poly-metars-day-v2" if closing else "poly-metars-day-v1", "revision": revision,
                                    "generated_at": iso(day_now), "airport": airport, "registry": airports,
                                    "policy": day_policy, "reports": reports, "receipts": sorted(receipts, key=lambda r:r["id"]),
                                    "evidence": sorted({r["body_sha256"] for r in receipts})}
                        if trigger_manifest:
                            manifest["trigger_manifest"] = trigger_manifest
                            manifest["evidence"] = sorted(set(manifest["evidence"]) | set(trigger_manifest["evidence"]))
                        written = await self.put(prefix+"audit.json", canonical(manifest))
                        if not written:
                            existing = await self.env.ARCHIVE.get(prefix+"audit.json")
                            manifest = json.loads(await existing.text())
                    result = daily(airport, date, manifest["reports"], day_policy, parse_time(manifest["generated_at"]), manifest.get("finalization"))
                    result.update({"generated_at": manifest["generated_at"], "policy_sha256": day_policy_hash,
                                   "registry_sha256": registry_hash, "snapshot_id": revision})
                    buffer = io.StringIO()
                    writer = csv.writer(buffer)
                    writer.writerow(["observation_utc", "local_time", "selected_c", "selected_source", "status", *day_policy["source_order"]])
                    for row in result["rows"]:
                        selected = row["selected"] or {}
                        writer.writerow([row["observed_at"], row["local_time"], selected.get("temperature_c", ""),
                          selected.get("source", ""), row["status"],
                          *[(row["sources"][s]["report"] or {}).get("temperature_c", "") for s in day_policy["source_order"]]])
                    await self.put(prefix+"day.csv", buffer.getvalue(), "text/csv; charset=utf-8")
                    await self.put(prefix+"day.json", canonical(result))
                    if closing:
                        # Commit the lock only after all of the immutable files exist.
                        # A competing finalizer may win; always follow the winning pointer.
                        await self.put(lock_key, canonical({"revision": revision, "cutoff_at": iso(cutoff),
                                                           "published_at": iso(now_utc())}))
                    committed_lock = await self.env.ARCHIVE.get(lock_key) if locking else None
                    if committed_lock:
                        locks[day_key] = json.loads(await committed_lock.text())
                    revisions[day_key] = locks[day_key]["revision"] if day_key in locks else revision
                    # A first reading becomes public only if the conditional index
                    # write below succeeds. Failed/superseded drafts never trigger.
                    if next_activation is not None and start.timestamp() > next_activation and day_key not in first:
                        selected = [r["selected"] for r in result["rows"]
                                    if r["selected"] and parse_time(r["observed_at"]) <= day_now]
                        if selected:
                            row = selected[0]
                            first[day_key] = {"date": date, "icao": airport["icao"], "revision": revision,
                                              "report_id": row["id"], "observed_at": row["observed_at"]}
                            new_first = True

        await asyncio.gather(*(publish_airport(airport) for airport in airports))
        stamp = int(now.timestamp())
        task_states = await self.rows("""SELECT source,mode,kind,last_success,last_attempt,last_error,interval_seconds FROM tasks
          WHERE mode='live' AND expires_at>? AND (source!='eccc' OR kind!='report' OR last_success IS NULL)""", stamp)
        collection = []
        for source in policy["source_order"]:
            active = [t for t in task_states if t["source"] == source and t["mode"] == "live"]
            errors = [t["last_error"] for t in active if t["last_error"]]
            attempted = [t["last_attempt"] for t in active if t["last_attempt"]]
            # Freshness refers to the oldest live route check, not the newest lucky response.
            roots = [t for t in active if source != "eccc" or (t["kind"] == "directory" and t["interval_seconds"] == 60)]
            oldest = min((t["last_success"] or 0 for t in roots), default=0)
            collection.append({"source": source, "name": source+"-live", "mode": "live",
              "status": "partial" if errors or not oldest else "ok", "errors": errors[:10],
              "finished_at": iso(datetime.fromtimestamp(max(attempted), UTC)) if attempted else None,
              "last_success_at": iso(datetime.fromtimestamp(oldest, UTC)) if oldest else None,
              "interval_seconds": max((t["interval_seconds"] for t in roots), default=60), "reports": 0,
              "scope": "Per-route retrieval checks; observation completeness is shown separately.",
              "pending_tasks": sum(not t["last_success"] for t in active)})
        recovery = await self.rows("""SELECT source,kind,COUNT(*) AS tasks,
          SUM(last_success IS NULL) AS unchecked,
          SUM(next_due<=? AND last_success IS NOT NULL) AS rechecks_due,
          SUM(queued_until>? OR lease_until>?) AS outstanding,
          SUM(last_error IS NOT NULL) AS errors,MAX(last_success) AS last_success
          FROM tasks WHERE mode='recovery' AND expires_at>? GROUP BY source,kind""",
          stamp, stamp, stamp, stamp)
        index = {"mode": "live", "platform": "cloudflare", "generated_at": iso(now_utc()),
                 "snapshot_id": digest(canonical(revisions)), "base_path": "/data",
                 "engine_sha256": SETTINGS["engine_hashes"], "policy_sha256": policy_hash, "registry_sha256": registry_hash,
                 "airports": airports, "dates": sorted({k.split('/')[0] for k in revisions}, reverse=True),
                 "sources": SETTINGS["sources"], "policy": policy, "retention": SETTINGS["retention"], "collection": collection,
                 "jobs": collection, "recovery": recovery, "revisions": revisions, "locks": locks,
                 "first_publications": first, "poll_seconds": 60,
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
        await self.persist_first_publications(first, published.uploaded)
        await self.put("health.json", canonical({"generated_at": index["generated_at"], "collection": collection, "recovery": recovery}), immutable=False)
        await self.batch([self.statement("DELETE FROM dirty WHERE icao=? AND date=? AND generation=?", d["icao"], d["date"], d["generation"]) for d in dirty if (d["icao"], d["date"]) in handled])
        if not same_engine:
            # Rebuild every retained, unlocked day under the new engine/policy through
            # the bounded dirty queue, 100 days per tick, instead of in one run.
            await self.batch([self.statement("INSERT INTO dirty(icao,date) VALUES(?,?) ON CONFLICT(icao,date) DO NOTHING", icao, date)
                              for icao, dates_ in retained.items() for date in sorted(dates_)
                              if f"{date}/{icao}" not in locks])
        if new_first and not followup:
            await self.publish(now_utc(), followup=True)
