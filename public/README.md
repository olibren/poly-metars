# Poly METARs

A public, auditable ledger of government METAR reports for airport-based temperature
markets. Source priority: **NOAA/AWC → NOAA/TGFTP → ECCC**. Each row shows every
captured source and the selected reading. This is an independent proposal; it is not
an adopted Polymarket resolution source.

## Live operation

The Vercel website refreshes data every **15 seconds**. A separate collector checks
each source on a **60-second target cadence**. Jobs never overlap with themselves;
a slow upstream response can extend a cycle. The page prominently warns when the
publisher or a source has not succeeded for three minutes. Missing/future slots are
calculated against the current clock, even if collection stops.

Historical recovery is independent of live checks:

| Path | Target interval | Recovery |
|---|---:|---|
| AWC live | 60 seconds | Previous 3 hours, small batches |
| TGFTP live | 60 seconds | Latest configured SA bulletins |
| ECCC live | 60 seconds | Current and previous reception hour, two distribution hosts |
| AWC recovery | 15 minutes | Previous 3 local dates, extended after downtime |
| TGFTP recovery | 5 minutes | All still-retained WMO collective files covering the requested dates |
| ECCC recovery | 30 minutes | Previous 3 local dates, extended after downtime |

Intervals run start-to-start when a sweep fits inside its interval. The next sweep
starts after completion when it does not. Recovery expands to at most 30 days based
on the last successful recovery; actual upstream retention still limits recovery.
No architecture can retrieve a report that no retained source ever received.

NOAA AWC and TGFTP are two NOAA delivery paths, not two independent agencies.
ECCC supplies a second agency's distribution path. They may share the originating
airport and WMO transport. Only explicit routine METARs enter this policy; SPECI
and ambiguous classifications are retained and excluded. Read [POLICY.md](POLICY.md).

## Run locally

Requires Python 3.11+ and Node 22.13+. The collector and audit tools use only the
Python standard library.

```sh
npm ci
python3 -m ledger.http --root work/live --port 8001
# In another terminal; /data is proxied to the collector:
npm run dev
```

The collector preserves exact response bytes under `archive/objects/<sha256>.txt`.
SQLite stores receipts and every distinct normalized report. It has no report or
receipt update/delete path. Configuration, source URLs, receipt times, explicit
corrections and excluded reports are public. The read-only HTTP service exposes
only published records and evidence, never the database or write controls.

Each airport-day revision is immutable. A late higher-priority report can change
the current selection; the previous published revision remains addressable.
Download that day's audit bundle from the site, unzip it, and replay offline:

```sh
python3 -m ledger.audit /path/to/unpacked-bundle
```

The verifier checks original response hashes, receipt/report identity, classification,
policy, station registry, and selected readings. Hashes detect changes relative to
a manifest; they are not government signatures or independent timestamp witnesses.

## Deployment and development

Source: https://github.com/olibren/poly-metars

The static viewer deploys to Vercel. Its `/data/*` rewrite reaches the separate
collector through HTTPS. Observation updates do not trigger Git commits or site
builds. The collector runs under systemd with persistent disk and hourly off-host
backups. See [docs/OPERATIONS.md](docs/OPERATIONS.md) and `deploy/`.

```sh
make check build
```

This repository contains no trading code, credentials or order execution. Runtime
data and secrets are excluded from Git. The original batch collector/exporter
(`python3 -m ledger`) remains available for self-contained research snapshots.

## Current limits

One collector is not geographic failover. Source redundancy, historical recovery,
restart recovery and off-host backups improve availability, but do not establish an
SLA or settlement readiness. Daily results remain provisional. Station schedules
and market mapping require review; non-airport markets are excluded. The site does
not claim to reproduce every current market's resolution rule.

MIT licensed. Government-source documentation is linked in `config/sources.json`.
