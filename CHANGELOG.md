# Changelog

## 2026-09-23 — Disable locking during development (routine-metar-v7)

- Add `lock_mode: "disabled"`: every retained day follows the current policy and stays revisable, with no cutoff, finalization, lock pointers or first-publication receipts. Existing lock objects are left in R2 but not read or advertised.
- Enqueue every retained airport-day through the bounded dirty queue after any policy, registry or engine change, so the whole window backfills under the current policy without one oversized publish.
- Show "Locking is not active yet" on live days. Document how to re-enable locking from a new activation time.
- Archive v6; v7 keeps v6 selection, NIL handling and rounding.

## 2026-09-23 — Placeholder NILs no longer block selection (routine-metar-v6)

- Treat a NIL without `COR` or a `CCx` bulletin as a relay placeholder: it is retained and shown, but neither withdraws nor competes with a routine report from the same source. An explicitly corrected NIL still withdraws.
- Fixes TGFTP/ECCC showing an ambiguous source when a compiling centre sends a NIL followed by a delayed (`RRx`) bulletin, which blocked fallback selection whenever AWC lacked the report.
- A newer plain NIL at AWC no longer withdraws an older AWC reading. Reports with missing temperatures keep their existing withdrawal and blocking behaviour.
- Archive v5 policy; locked days and pinned v5 manifests replay unchanged. No schema migration or activation reset.

## 2026-09-22 — Compact selected-reading table

- Show local time, selected temperature, selected source and full METAR text in compact rows.
- Use a chevron to expand the original reports, source hierarchy, per-source temperatures, selected-source marker and audit links.
- Keep long METARs readable with wrapping; retain missing/ambiguous states and existing selection and finality rules.

## 2026-09-22 — Retire unusable NWS observations source (routine-metar-v5)

- Verify the NWS observation schema, JSON-LD and XML formats, station provider metadata and alternate MTR products; none provides reliable current routine-only coverage for this registry.
- Remove NWS from active planning, source priority, health and current table columns. Expire retired tasks and discard queued deliveries without fetching; retain raw evidence and historical replay support.
- Archive v4 policy and preserve all locks, publication triggers, rounding and cutoff rules. No schema migration or activation reset.
- Explain missing source-cell values on hover and document why successful API requests did not supply eligible observations.

## 2026-09-22 — Next-day publication locking, NWS API and MET Norway (routine-metar-v4)

- Keep revisions open after local midnight until this site first publishes an eligible selected routine METAR for the next local date, capped at 23:59:00 America/New_York on the following calendar date.
- Pin the successful index publication, triggering revision and raw evidence; replay the trigger and cutoff offline, including interrupted publication/finalization. Preserve all existing v3 locks and historical policy replay.
- Add independently paced NWS API live collection and bounded seven-day recovery after TGFTP and before ECCC. Preserve GeoJSON; exclude missing raw messages and unclassified reports.
- Match weather.gov WRH table rounding for exact negative halves in v4 while retaining tenth-degree raw temperatures. Document its Synoptic backend and broader observation coverage; do not claim exact market equivalence.
- Add MET Norway after ECCC, with original XML evidence, explicit routine/special/correction metadata, last-24-hour gap recovery, cache expiry and conditional requests. Preserve original receipts across 304s and interrupted ingestion; include CC BY attribution and document the announced international-service withdrawal.
- Add prospective migration 0004; no deployment is performed by local checks or builds.

## 2026-09-22 — Simpler daily temperature viewer

- Focus the page on airport/date selection, daily high and low, a single lock status, and the multi-source readings table.
- Keep exact METAR reports expandable by row; move methodology, coverage and collection diagnostics, and evidence links into optional details.
- Use a quieter white layout, compact table labels, and a responsive two-card temperature summary; preserve airport-day URLs and resolution calculations.

## 2026-09-22 — Faster local frontend preview

- Make `npm run dev` read published evidence through the development proxy, so local UI edits hot-reload without running a collector or deploying.
- Allow `METAR_DATA_ORIGIN` to select a local Wrangler site or offline fixture server; production routing is unchanged.
- Document private Tailscale preview with an exact allowed hostname and IPv4 loopback binding for Serve.

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
