"""Pure, reviewable collection jobs. No cloud credentials, networking, or selection rules."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import hashlib
import json

from ledger.metar import UTC, iso
from ledger.policy import day_bounds


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


TGFTP_HISTORY = "https://tgftp.nws.noaa.gov/SL.us008001/DF.an/DC.sflnd/DS.metar/"


def job(source, mode, kind, url, interval, expires, **extra):
    payload = {"source": source, "kind": kind, "url": url, **extra}
    return {"id": digest(canonical(payload)), "source": source, "mode": mode, "kind": kind,
            "payload": canonical(payload).decode(), "interval_seconds": interval,
            "expires_at": int(expires.timestamp())}


def centers(airports):
    result = {}
    for airport in airports:
        for bulletin in airport["eccc_bulletins"]:
            prefix, center = bulletin.split("_")
            result.setdefault(center, set()).add(prefix + "_")
    return {k: sorted(v) for k, v in result.items()}


def eccc_directory(center, hour):
    day = hour.strftime("%Y%m%d")
    return f"https://dd.weather.gc.ca/{day}/WXO-DD/bulletins/alphanumeric/{day}/SA/{center}/{hour:%H}/"


def live_jobs(airports, now):
    expiry = now + timedelta(hours=3)
    stations = sorted(a["icao"] for a in airports)
    for offset in range(0, len(stations), 8):
        query = urlencode({"ids": ",".join(stations[offset:offset+8]), "format": "json", "hours": 3})
        yield job("noaa_awc", "live", "report", "https://aviationweather.gov/api/data/metar?" + query, 60, expiry)
    for name in sorted({n for a in airports for n in a["tgftp_files"]}):
        yield job("noaa_tgftp", "live", "report", "https://tgftp.nws.noaa.gov/data/raw/sa/" + name, 60, expiry)
    for center, prefixes in centers(airports).items():
        for offset in (0, 1):
            hour = (now - timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
            yield job("eccc", "live", "directory", eccc_directory(center, hour), 60 if offset == 0 else 600,
                      hour + timedelta(hours=2), prefixes=prefixes, recent=True)


def planning_jobs(now, days=30):
    """Small durable planning messages; the existing recovery queue expands them."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yield job("noaa_awc", "recovery", "plan", "https://aviationweather.gov/api/data/metar",
              3600, today+timedelta(days=1), date=today.date().isoformat())
    for offset in range(days+2):
        start = today-timedelta(days=offset)
        yield job("eccc", "recovery", "plan", "https://dd.weather.gc.ca/"+start.strftime("%Y%m%d")+"/",
                  1800 if offset == 0 else 86400, start+timedelta(days=days+2),
                  since=iso(start), until=iso(start+timedelta(days=1)))


def retained_dates(airport, now, days=30):
    """Full local days intersecting a rolling window, never a partial day's extrema."""
    zone = ZoneInfo(airport["timezone"])
    first = (now-timedelta(days=days)).astimezone(zone).date()
    today = now.astimezone(zone).date()
    return [(today-timedelta(days=d)).isoformat() for d in range((today-first).days+1)]


def retention_start(airport, now, days=30):
    return day_bounds(retained_dates(airport, now, days)[-1], airport["timezone"])[0]


def recovery_jobs(airports, now, since, until=None, awc_offsets=None):
    # Stable completed-day URLs; small individual station queries avoid result caps.
    for airport in airports:
        today = now.astimezone(ZoneInfo(airport["timezone"])).date()
        for offset in (awc_offsets if awc_offsets is not None else range(31)):
            date = (today - timedelta(days=offset)).isoformat()
            start, end = day_bounds(date, airport["timezone"])
            anchor = min(end, now.replace(minute=0, second=0, microsecond=0))
            query = urlencode({"ids": airport["icao"], "format": "json", "date": iso(anchor),
                               "hours": int((end-start).total_seconds()/3600)})
            yield job("noaa_awc", "recovery", "report", "https://aviationweather.gov/api/data/metar?"+query,
                      900 if offset < 3 else 86400,
                      now+timedelta(hours=1) if offset == 0 else end+timedelta(days=30))
    yield job("noaa_tgftp", "recovery", "collective-directory", TGFTP_HISTORY, 300, now+timedelta(hours=1))
    for center, prefixes in centers(airports).items():
        hour = min(now, until-timedelta(hours=1) if until else now).replace(minute=0, second=0, microsecond=0)
        while hour >= since:
            yield job("eccc", "recovery", "directory", eccc_directory(center, hour),
                      1800 if now-hour < timedelta(hours=6) else (21600 if now-hour < timedelta(days=3) else 86400),
                      hour+timedelta(days=31), prefixes=prefixes, recent=False)
            hour -= timedelta(hours=1)


def recent_dates(airports, now):
    return sorted({(now.astimezone(ZoneInfo(a["timezone"])).date()-timedelta(days=d)).isoformat()
                   for a in airports for d in range(3)}, reverse=True)
