# Changelog

## 2026-09-22 — Automatic local-midnight locking (routine-metar-v3)

- Freeze each governed airport/day at its next local midnight from the best available selected observations, without incomplete/unresolved day status, review or adjudication.
- Record database acceptance separately from retrieval and source receipt; exclude reports accepted at or after cutoff, including late archival of pre-cutoff fetches.
- Pin complete day JSON, CSV and audit artifacts through a conditional immutable lock pointer; preserve it through backfills, corrections, retries, policy changes and index recovery.
- Display Live, Finalizing and Locked; keep gap/conflict counts as diagnostics and never invent a temperature when no usable readings exist.
- Add a prospective activation migration and v2 locked-manifest replay while preserving historical v1/v2 policy and evidence. Retention remains unchanged.
- Test strict boundaries, DST, delayed publication, archival races, failures, overlapping publishers and offline replay.

## 2026-09-22 — Airport and day resolution pages

- Give every airport/local date a shareable `/?airport=ICAO&date=YYYY-MM-DD` URL, with selection changes, reloads and browser history keeping the same market context.
- Pin the initial date in the airport timezone; reject unknown airports and invalid dates instead of substituting another market. Explain unavailable and expired days explicitly.
- Lead with airport, date and provisional daily temperatures; retain the side-by-side source table and exact METAR evidence, and collapse collection details.
- Keep day-specific downloads tied to the displayed immutable revision and remove registry example market links that were not matched to the displayed day.
- Document the Kalshi/NWS comparison and deterministic airport/day pairing. Selection, retention and finality policy remain unchanged.

## 2026-09-22 — Automatic recovery scheduling

- Keep backfill inside the existing collector and two queues; a fresh deployment automatically plans the full retained window using resumable queue jobs.
- Increase recovery concurrency from 2 to 12 with shared provider request pacing, fair bounded dispatch and atomic queue reservations.
- Batch task planning, avoid repeated closed ECCC directory scans, and retire obsolete live reception hours while preserving current-hour checks.
- Delay paced work without exhausting error retries; expose durable recovery counters in the public health/index data.
- Preserve the deployed source-selection policy, government sources, raw evidence, receipt times and thirty-day retention.

## 2026-09-22 — US source temperature display

- Display all source columns and selected readings for Fahrenheit markets in whole degrees Fahrenheit, converting before rounding under the existing policy.
- Keep original Celsius values and raw METAR evidence intact for audit replay; daily highs and lows already use whole market degrees.

## 2026-09-22 — Source receipt ordering (routine-metar-v2)

- Preserve AWC per-report receipt times, including subsecond precision, in report identities and raw-evidence replay across live, recovery and Cloudflare collection.
- Within source priority and the highest explicit correction rank, select the latest supported source receipt time; never use download order, file age or bulletin position.
- Retain equal-time and unorderable conflicts as automatic blocked observations, including ambiguous withdrawals. Show resolved disagreements without a human-review status.
- Preserve the v1 policy and exact old-snapshot replay; expose source receipt times in the evidence view. Finalization and settlement remain unconfigured.

## 2026-09-22 — Thirty-day history and retention

- Added a machine-readable 30-day retention policy, keeping complete airport local boundary days and removing expired days from the current index.
- Extended background AWC/ECCC recovery to the retained window, with bounded durable planning, oldest-history priority and daily rechecks of older directories.
- Avoid repeated downloads of successfully archived timestamped ECCC files; preserve source selection and actual receipt times.
- Added bounded D1 cleanup and a reproducible 32-day R2 lifecycle configuration, including shared-evidence refresh to prevent premature audit dependency expiry.
- Reduced immutable file caching to one day and documented that public audit URLs expire.
- Added tests for historical planning, expiry, stale upstream copies, DST boundaries, shared evidence and uninterrupted live dispatch.

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
