# Changelog

## 2026-09-22 — Poly METARs live service

- Created a standalone public source repository and Vercel viewer.
- Added independent 60-second source checks, separate historical recovery workers,
  bounded retries and recovery-window extension after outages.
- Added TGFTP rotating WMO collective recovery with per-bulletin classification
  and correction boundaries.
- Added durable SQLite receipts/report versions and content-addressed evidence,
  immutable airport-day revisions, CSV exports and offline-replayable day bundles.
- Added 15-second browser refresh, current-clock gap labels, source freshness and
  prominent stale/error indicators.
- Added dedicated systemd deployment, read-only serving and hourly off-host backups.
- Added per-host pacing so one provider cannot hold another provider's request gate,
  and cooperative shutdown during long recovery sweeps.
- Connected Vercel to the dedicated HTTPS data origin and added public health,
  disk-capacity warning, parsing-failure downloads and source-failure banners.
- Prioritized recent ECCC reception hours and ingest reports as each file arrives,
  so a large collective cannot hold back other reports in its hour.
- Treat absent ECCC reception directories as recorded no-data responses, and use
  the documented alternate HTTPS hostname for actual request failures.
- Exercise the collector's deployed Python version in CI as well as frontend checks.
- Preserved the original routine-METAR policy and source order.
