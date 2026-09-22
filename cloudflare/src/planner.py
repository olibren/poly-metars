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
            yield job("eccc", "live", "directory", eccc_directory(center, hour), 60,
                      hour + timedelta(hours=3), prefixes=prefixes, recent=True)


def recovery_jobs(airports, now, since):
    # AWC has a limited upstream window; older ECCC data remains worth requesting.
    for airport in airports:
        today = now.astimezone(ZoneInfo(airport["timezone"])).date()
        for offset in range(3):
            date = (today - timedelta(days=offset)).isoformat()
            start, end = day_bounds(date, airport["timezone"])
            query = urlencode({"ids": airport["icao"], "format": "json", "date": iso(min(end, now)),
                               "hours": int((end-start).total_seconds()/3600)})
            yield job("noaa_awc", "recovery", "report", "https://aviationweather.gov/api/data/metar?"+query,
                      900, now+timedelta(hours=1))
    yield job("noaa_tgftp", "recovery", "collective-directory", TGFTP_HISTORY, 300, now+timedelta(hours=1))
    for center, prefixes in centers(airports).items():
        hour = now.replace(minute=0, second=0, microsecond=0)
        while hour >= since:
            yield job("eccc", "recovery", "directory", eccc_directory(center, hour),
                      1800 if now-hour < timedelta(hours=6) else 21600,
                      hour+timedelta(days=31), prefixes=prefixes, recent=False)
            hour -= timedelta(hours=1)


def recent_dates(airports, now):
    return sorted({(now.astimezone(ZoneInfo(a["timezone"])).date()-timedelta(days=d)).isoformat()
                   for a in airports for d in range(3)}, reverse=True)
