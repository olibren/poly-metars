"""Read public market rules; never silently invent or change airport mappings."""

from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
import json
import re
from .archive import Archive, write_json
from .metar import UTC, iso
from .sources import Client


def station_for_event(event):
    candidates = set()
    texts = [event.get("resolutionSource") or "", event.get("description") or ""]
    for market in event.get("markets", []):
        texts.extend(
            [market.get("resolutionSource") or "", market.get("description") or ""]
        )
    for text in texts:
        candidates.update(
            x.upper()
            for x in re.findall(
                r"(?:[?&]site=|/history/daily/[^\s/]+/[^\s/]+/)([A-Za-z]{4})\b", text
            )
        )
    return next(iter(candidates)) if len(candidates) == 1 else None


def refresh(config="config", archive_root="catalog-archive"):
    path = Path(config)
    archive = Archive(archive_root)
    client = Client(archive)
    with archive.lock():
        events = []
        offset = 0
        # The tag ID is discovered from the public tag endpoint; never inferred from a title.
        data, _ = client.fetch(
            "polymarket_catalog",
            "https://gamma-api.polymarket.com/tags/slug/daily-temperature",
        )
        tag = json.loads(data)
        after = (
            datetime.now(UTC) - timedelta(days=2)
        ).date().isoformat() + "T00:00:00Z"
        while True:
            query = urlencode(
                {
                    "tag_id": tag["id"],
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                    "end_date_min": after,
                }
            )
            body, _ = client.fetch(
                "polymarket_catalog", "https://gamma-api.polymarket.com/events?" + query
            )
            batch = json.loads(body)
            if not isinstance(batch, list):
                raise ValueError("Expected events array")
            events.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 10000:
                raise ValueError("Catalog pagination exceeded bound")
        airports = json.loads((path / "airports.json").read_text())
        by_icao = {a["icao"]: a for a in airports}
        seen = {}
        unresolved = []
        markets = []
        for event in events:
            title = event.get("title", "")
            if not re.match(r"^(Highest|Lowest) temperature in ", title):
                continue
            icao = station_for_event(event)
            url = "https://polymarket.com/event/" + event["slug"]
            record = {
                "id": event["id"],
                "title": title,
                "url": url,
                "icao": icao,
                "resolution_source": event.get("resolutionSource"),
                "rules": event.get("description"),
                "end_date": event.get("endDate"),
            }
            markets.append(record)
            if icao is None:
                unresolved.append(
                    {
                        "title": title,
                        "url": url,
                        "reason": "No unique airport station in published source URLs; non-airport or manual review required.",
                    }
                )
                continue
            seen.setdefault(icao, []).append(url)
            if icao not in by_icao:
                unresolved.append(
                    {
                        "title": title,
                        "url": url,
                        "icao": icao,
                        "reason": "Airport absent from reviewed registry; add timezone, unit and bulletin routes explicitly.",
                    }
                )
        for airport in airports:
            airport["registry_status"] = (
                "current_market_verified"
                if airport["icao"] in seen
                else "historical_market_link"
            )
            if airport["icao"] in seen:
                airport["market_urls"] = sorted(set(seen[airport["icao"]]))
            airport["market_checked_at"] = iso(datetime.now(UTC))
        write_json(path / "airports.json", airports)
        write_json(
            path / "markets.json",
            {
                "checked_at": iso(datetime.now(UTC)),
                "markets": markets,
                "unresolved": unresolved,
                "scope": "Open Daily Temperature events ending after " + after,
            },
        )
        return {
            "events": len(markets),
            "current_airports": len(seen),
            "unregistered_airports": sorted(set(seen) - set(by_icao)),
            "unresolved_count": len(unresolved),
        }


if __name__ == "__main__":
    print(json.dumps(refresh()))
