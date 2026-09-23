"""Local HTTP capture helpers for registry review and audit fixtures.

Production collection uses cloudflare/src/entry.py and its durable queues.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
import json
import math
import threading
import time
from ledger.metar import UTC, iso, parse_time, parse_awc, parse_bulletin
from ledger.policy import day_bounds
from ledger.sources import Links, eccc_live_links

ALLOWED_HOSTS = {
    "aviationweather.gov", "tgftp.nws.noaa.gov", "dd.weather.gc.ca",
    "dd.meteo.gc.ca", "gamma-api.polymarket.com",
}


class Client:
    def __init__(self, archive, stop=None):
        self.stop = stop
        self.archive = archive
        self.gates = {host: threading.Lock() for host in ALLOWED_HOSTS}
        self.last = {}

    def fetch(self, source, url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(
                "Only configured HTTPS government / market endpoints are allowed"
            )
        for attempt in range(3):
            if self.stop is not None and self.stop.is_set():
                raise RuntimeError("Collector stopping")
            with self.gates[parsed.hostname]:
                # Keep AWC below its documented 100 requests/minute limit.
                interval = 0.7 if parsed.hostname == "aviationweather.gov" else 0.25
                wait = max(
                    0, interval - (time.monotonic() - self.last.get(parsed.hostname, 0))
                )
                if wait:
                    time.sleep(wait)
                self.last[parsed.hostname] = time.monotonic()
            try:
                req = Request(
                    url,
                    headers={
                        "User-Agent": "PolyMETARs/0.2 (+https://github.com/olibren/poly-metars)",
                        "Accept": "*/*",
                    },
                )
                with urlopen(req, timeout=20) as response:
                    final = urlparse(response.url)
                    if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                        raise ValueError("Unapproved redirect")
                    body = response.read(8_000_001)
                    if len(body) > 8_000_000:
                        raise ValueError("Response exceeds 8 MB safety limit")
                    headers = {
                        k.lower(): v
                        for k, v in response.headers.items()
                        if k.lower()
                        in ("date", "last-modified", "etag", "content-type")
                    }
                    receipt = self.archive.response(
                        source, url, body, response.status, headers
                    )
                    return body, receipt
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                status = getattr(error, "code", 0)
                body = error.read(100000) if isinstance(error, HTTPError) else b""
                self.archive.response(source, url, body, status, error=str(error))
                if attempt == 2 or status in (400, 401, 403, 404):
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("Unreachable")

    def links(self, source, url):
        body, _ = self.fetch(source, url)
        parser = Links()
        parser.feed(body.decode("utf-8", errors="replace"))
        return parser.links

def parallel(jobs, task, workers=4):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(task, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                yield future.result(), None
            except Exception as error:
                yield None, f"{futures[future]}: {type(error).__name__}: {error}"

def collect_awc(client, airports, dates, workers=4):
    source = "noaa_awc"
    total = 0
    errors = []
    jobs = [(a, day) for a in airports for day in dates]

    def fetch(job):
        airport, day = job
        start, end = day_bounds(day, airport["timezone"])
        query = urlencode(
            {
                "ids": airport["icao"],
                "format": "json",
                "date": iso(end),
                "hours": math.ceil((end - start).total_seconds() / 3600),
            }
        )
        body, receipt = client.fetch(
            source, "https://aviationweather.gov/api/data/metar?" + query
        )
        payload = json.loads(body) if body else []
        if not isinstance(payload, list):
            raise ValueError("Expected METAR array")
        if len(payload) >= 400:
            raise ValueError(
                "Possible AWC result truncation; narrow the query before accepting this run"
            )
        rows = []
        for item in payload:
            if item.get("icaoId") != airport["icao"]:
                continue
            try:
                row = parse_awc(item)
            except (ValueError, KeyError) as error:
                row = {
                    "icao": airport["icao"],
                    "raw": item.get("rawOb"),
                    "parse_error": str(error),
                }
            rows.append(row)
        return rows, receipt

    for result, error in parallel(jobs, fetch, workers):
        if error:
            errors.append(error)
            continue
        rows, receipt = result
        for row in rows:
            total += client.archive.report(row, receipt)
    return {
        "source": source,
        "status": "partial" if errors else "ok",
        "reports": total,
        "errors": errors,
    }

def collect_eccc(client, airports, dates, workers=4, recent_hours=None):
    source = "eccc"
    total = 0
    errors = []
    allowed = {a["icao"] for a in airports}
    center_prefixes = {}
    center_airports = {}
    for airport in airports:
        for bulletin in airport["eccc_bulletins"]:
            prefix, center = bulletin.split("_")
            center_prefixes.setdefault(center, set()).add(prefix + "_")
            center_airports.setdefault(center, []).append(airport)
    # Use UTC reception hours covering local dates, plus 6 hours for delayed delivery.
    now = datetime.now(UTC)
    jobs = []
    for center, stations in sorted(center_airports.items()):
        hours = set()
        for airport in stations:
            for date in dates:
                start, end = day_bounds(date, airport["timezone"])
                cursor = start.replace(minute=0)
                if recent_hours is not None:
                    cursor = max(cursor, (now - timedelta(hours=recent_hours)).replace(minute=0, second=0, microsecond=0))
                while cursor < min(end + timedelta(hours=6), now):
                    hours.add(cursor)
                    cursor += timedelta(hours=1)
        jobs.extend((center, hour) for hour in sorted(hours))
    # Prioritize current reception hours. Ingest each file immediately so a large
    # US collective route cannot hold newer readings until its whole hour finishes.
    jobs.sort(key=lambda job: job[1], reverse=True)
    # Successful historical immutable file URLs need not be downloaded on every sweep.
    retained = {
        r["url"]: r
        for r in (client.archive.latest_receipts(source) if hasattr(client.archive, "latest_receipts") else client.archive.read("receipts"))
        if r["source"] == source and r["status"] == 200 and not r["url"].endswith("/")
    }

    def fetch(job):
        center, hour = job
        day = hour.strftime("%Y%m%d")
        path = f"/{day}/WXO-DD/bulletins/alphanumeric/{day}/SA/{center}/{hour:%H}/"
        failures = []
        for host in ("dd.weather.gc.ca", "dd.meteo.gc.ca"):
            directory = "https://" + host + path
            try:
                links = client.links(source, directory)
                if recent_hours is not None:
                    links = eccc_live_links(links, center_prefixes[center], hour)
                added = 0
                links.sort(key=lambda name: name.split("_")[2] if len(name.split("_")) > 2 else name, reverse=True)
                for name in links:
                    if not any(
                        name.startswith(prefix) for prefix in center_prefixes[center]
                    ):
                        continue
                    url = urljoin(directory, name)
                    previous = retained.get(url)
                    if previous and now - parse_time(
                        previous["fetched_at"]
                    ) < timedelta(hours=6):
                        receipt = previous
                        body = (
                            client.archive.root
                            / "objects"
                            / f"{receipt['body_sha256']}.txt"
                        ).read_bytes()
                    else:
                        body, receipt = client.fetch(source, url)
                    rows = [
                        r
                        for r in parse_bulletin(
                            body.decode("utf-8", errors="replace"), hour
                        )
                        if r["icao"] in allowed
                    ]
                    for row in rows:
                        added += client.archive.report(row, receipt)
                return added
            except HTTPError as error:
                if error.code == 404 and error.url == directory:
                    # Directories exist only when products have been issued.
                    # Keep the 404 receipt and revisit next sweep; this says
                    # nothing about observation completeness.
                    if failures:
                        raise RuntimeError("; ".join(failures)) from error
                    return 0
                failures.append(str(error))
            except (URLError, TimeoutError, OSError, ValueError) as error:
                failures.append(str(error))
        raise RuntimeError("; ".join(failures))

    for result, error in parallel(jobs, fetch, workers):
        if error:
            errors.append(error)
            continue
        total += result
    return {
        "source": source,
        "status": "partial" if errors else "ok",
        "reports": total,
        "errors": errors,
        "scope": ("Two newest bulletin times per configured route in recent reception hours; all versions at those times. Full history is recovered separately." if recent_hours is not None else "Configured SA bulletin routes, local-day coverage plus six receipt hours. Missing directories are retained in receipts."),
    }

def collect_awc_recent(client, airports, dates=None, workers=4):
    """Small station batches retrieve three overlapping hours within AWC's result cap."""
    total, errors = 0, []
    allowed = {a["icao"] for a in airports}
    stations = sorted(allowed)
    jobs = [stations[i:i + 8] for i in range(0, len(stations), 8)]

    def fetch(stations):
        url = "https://aviationweather.gov/api/data/metar?" + urlencode(
            {"ids": ",".join(stations), "format": "json", "hours": 3})
        body, receipt = client.fetch("noaa_awc", url)
        payload = json.loads(body) if body else []
        if not isinstance(payload, list) or len(payload) >= 400:
            raise ValueError("Invalid or possibly truncated AWC response")
        rows = []
        for item in payload:
            if item.get("icaoId") not in allowed:
                continue
            try:
                row = parse_awc(item)
            except (ValueError, KeyError) as error:
                row = {"icao": item["icaoId"], "raw": item.get("rawOb"), "parse_error": str(error)}
            rows.append(row)
        return rows, receipt

    for result, error in parallel(jobs, fetch, workers):
        if error:
            errors.append(error)
        else:
            rows, receipt = result
            for row in rows:
                total += client.archive.report(row, receipt)
    return {"source": "noaa_awc", "status": "partial" if errors else "ok", "reports": total, "errors": errors,
            "scope": "All configured airports, overlapping last three hours."}
