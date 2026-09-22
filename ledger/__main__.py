"""Run with python3 -m ledger. The collector uses only the Python standard library."""

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import json
import time
from zoneinfo import ZoneInfo
from .archive import Archive
from .export import export
from .metar import UTC, iso
from .sources import Client, collect_awc, collect_tgftp, collect_eccc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "export", "verify", "run"])
    parser.add_argument("--archive", default="archive")
    parser.add_argument("--config", default="config")
    parser.add_argument("--output", default="public/data")
    parser.add_argument(
        "--date",
        action="append",
        dest="dates",
        help="Local observation date; repeat for multiple dates",
    )
    parser.add_argument(
        "--airports", help="Comma-separated ICAO codes; default: entire registry"
    )
    parser.add_argument("--sources", default="noaa_awc,noaa_tgftp,eccc")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--lookback-days", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("workers must be 1..8")
    if not 1 <= args.lookback_days <= 30:
        parser.error("lookback-days must be 1..30")
    if args.interval < 60:
        parser.error("interval must be at least 60 seconds")
    archive = Archive(args.archive)
    if args.command == "verify":
        print(json.dumps(archive.verify()))
        return
    with archive.lock():
        archive.ids = {r["id"] for r in archive.read("reports")}
        if args.command == "export":
            print(json.dumps(export(archive, args.config, args.output, args.dates)))
            return
        airports = json.loads((Path(args.config) / "airports.json").read_text())
        if args.airports:
            wanted = set(args.airports.upper().split(","))
            unknown = wanted - {a["icao"] for a in airports}
            if unknown:
                parser.error("Unknown airports: " + ",".join(sorted(unknown)))
            airports = [a for a in airports if a["icao"] in wanted]
        adapters = {
            "noaa_awc": collect_awc,
            "noaa_tgftp": collect_tgftp,
            "eccc": collect_eccc,
        }
        selected = args.sources.split(",")
        if any(s not in adapters for s in selected):
            parser.error("Unknown source")
        client = Client(archive)
        while True:
            dates = args.dates or sorted(
                {
                    (
                        datetime.now(ZoneInfo(a["timezone"])).date() - timedelta(days=d)
                    ).isoformat()
                    for a in airports
                    for d in range(args.lookback_days)
                },
                reverse=True,
            )
            results = []
            started = iso(datetime.now(UTC))
            for source in selected:
                result = adapters[source](client, airports, dates, args.workers)
                results.append(result)
                print(json.dumps(result), flush=True)
            archive.append(
                "runs",
                {
                    "started_at": started,
                    "finished_at": iso(datetime.now(UTC)),
                    "dates": dates,
                    "airports": [a["icao"] for a in airports],
                    "results": results,
                },
            )
            print(json.dumps(export(archive, args.config, args.output)), flush=True)
            if args.command != "run":
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
