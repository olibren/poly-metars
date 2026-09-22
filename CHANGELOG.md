# Changelog

## 2026-09-22 — Source receipt ordering (routine-metar-v2)

- Preserve AWC per-report receipt times, including subsecond precision, in report identities and raw-evidence replay across live, recovery and Cloudflare collection.
- Within source priority and the highest explicit correction rank, select the latest supported source receipt time; never use download order, file age or bulletin position.
- Retain equal-time and unorderable conflicts as automatic blocked observations, including ambiguous withdrawals. Show resolved disagreements without a human-review status.
- Preserve the v1 policy and exact old-snapshot replay; expose source receipt times in the evidence view. Finalization and settlement remain unconfigured.

## 2026-09-22 — Cloudflare migration

- Added a Cloudflare-only runtime: scheduled Python Worker, independent live/recovery queues, D1 working index and R2 evidence archive.
- Reused the existing METAR parser, source decoders and selection policy in the cloud runtime and offline verifier.
- Added durable task leases, redispatch, recovery priorities and interrupted archive/publication recovery.
- Added a read-only static site Worker with bounded edge caching; public traffic cannot trigger collection or query D1.
- Published immutable audit manifests with shared evidence downloads, operating instructions and an ownership handover guide.
- Added crash, duplicate-delivery, queue-loss, publication-race and recovery-priority tests.
- Verified clean-checkout Worker packaging in CI, including the Python SDK default-config compatibility step.
- Redirected the former Vercel address to Cloudflare, verified all 18,196 legacy reports, preserved historical evidence, and stopped the old EC2 collector after its final backup.

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
