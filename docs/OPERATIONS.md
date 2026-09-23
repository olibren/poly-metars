# Operating Poly METARs on Cloudflare

## Components and ownership

All runtime resources belong to one Cloudflare account. `wrangler.collector.jsonc`
defines the Python collector, one-minute Cron Trigger, D1 database, R2 archive and
two queues. `wrangler.site.jsonc` defines the static website and read-only file
server. It has only an R2 binding: no D1, queue producer or collector binding.

The collector has no public URL and its HTTP handler always returns 404. There is
no observation upload, correction, manual temperature override, or public trigger.
Owner deployments can change policy; every revision includes the exact policy,
registry and engine hashes used. Do not change policy mid-market without an explicit,
publicly documented process. Locking is disabled in v7 during development; see
"V7 development mode" below. V4–v6 days locked on this site’s first eligible
next-day publication or the following date’s 23:59:00 America/New_York deadline.

## Install and deploy to a new account

Use an owner-controlled Cloudflare account with Workers Paid and R2 enabled. Install
Node 22.13+, Python 3.12+ and uv. Clone the repository, then:

```sh
npm ci
uv sync --frozen
npx wrangler login
npx wrangler whoami
npx wrangler d1 create poly-metars
npx wrangler r2 bucket create poly-metars
npx wrangler queues create poly-metars-live
npx wrangler queues create poly-metars-recovery
```

Put the returned D1 database ID into `wrangler.collector.jsonc`. Names can be changed
in both config files if the receiving account already uses them. Resource creation
is a one-time operation. No government API keys or runtime secrets are needed.

```sh
npx wrangler d1 migrations apply poly-metars --remote --config wrangler.collector.jsonc
make check build
npm run cf:dry-run
# Replay a real downloaded day as described in AUDIT.md before publishing changes.
npm run cf:deploy
# After deploying the collector's shared-evidence refresh, apply the R2 policy:
python3 scripts/configure_retention.py
```

Deploy from a clean, reviewed commit. The command deploys both Workers, their queue
consumers and the cron schedule. Cron changes can take time to propagate. On a new
account, wait for the first successful publication before announcing its URL.
GitHub CI checks source changes; releases are explicit owner-run deployments.
No developer-owned CI token is required for handover.

The frontend is a static export; its build deliberately does not run a server-side
Cloudflare Vite plugin. Collector and site configurations have explicit filenames
so the framework does not mistake the collector for its own server runtime.
`cf:sync` briefly exposes the collector configuration at the default path required
by workers-py 1.17, then removes that temporary copy. A clean checkout is tested
in CI, including dependency synchronization and both deployment dry runs.

## Local runtime

For frontend work, run `npm run dev` and open
`http://localhost:3000/?airport=EGLC&date=2026-09-22` (choose the desired date).
The local frontend hot-reloads edits and proxies `/data/` to the read-only site
in `deploy/cloudflare.json`. No local collector, credentials or deployment is
needed. Data is real published evidence, so this mode needs internet access;
frontend changes do not change collection or production data.

To preview from another device on your Tailscale network, bind to IPv4 loopback
and allow the serving machine's exact Tailscale DNS hostname:

```sh
__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=your-machine.your-tailnet.ts.net npm run dev -- --hostname 127.0.0.1
# One-time setup, only if Serve is not already configured for this port:
tailscale serve --bg http://127.0.0.1:3000
```

Check `tailscale serve status` first to preserve any existing services. Open the
HTTPS URL it prints on the other device, with Tailscale connected to the same
tailnet. The development machine must stay awake with the dev server running.
Serve proxies hot-reload WebSockets as well as page/data requests. This uses
private Tailscale Serve, not public Funnel; do not disable Vite's host checks.

To use a local backend instead, start the local Workers below, then run
`METAR_DATA_ORIGIN=http://127.0.0.1:8793 npm run dev`. A separately served offline
fixture directory with a `/data/` tree works with the same override. Backend and
locking changes require the local Workers or offline tests, not just UI hot reload.

Full local Cloudflare runtime:

```sh
npm run cf:prepare
npx wrangler d1 migrations apply poly-metars --local --config wrangler.collector.jsonc
npm run cf:sync
npx wrangler dev --config wrangler.collector.jsonc --test-scheduled --port 8792
# Separate terminal, after npm run build:
npx wrangler dev --config wrangler.site.jsonc --port 8793
# Manually trigger a local collection/publication cycle:
curl 'http://localhost:8792/cdn-cgi/local/scheduled?format=json'
```

Both processes share the local R2 state. Local testing fetches real public sources,
but writes only to local storage. Visit localhost:8793. The old `ledger.http` process
on port 8001 may serve existing historical fixtures for frontend development. The
legacy local publisher rejects automatic locking policies; use the Cloudflare runtime
for current collection and locking.

## Collection, failure and publication

The planner defines every source URL, interval, overlap and expiry in one small
module. Live and recovery queues have separate consumer concurrency (16 and 12).
Each message performs one bounded government HTTP fetch, with a 20-second timeout
and 8 MB response limit. Only allowlisted HTTPS hosts are accepted; redirects are
recorded and rejected. ECCC has a documented alternative HTTPS hostname, treated
as the same source. Missing reception directories are recorded as 404/no data.

D1 contains due times, queue reservations and execution leases. Queue deliveries
are idempotent. Messages carry their reservation so stale deliveries are ignored.
Claims are atomic, reservations last 15 minutes and execution leases
last 180 seconds. A failed send releases its reservation; lost messages and exhausted
error retries recover automatically through cron. Outstanding recovery messages are
bounded separately for planning, AWC, TGFTP, ECCC directories and ECCC files. Large
backlogs in one class cannot monopolize dispatch. Unchecked, earliest-expiring work
comes first within each class; TGFTP's rotating listing precedes its files.

A new account automatically starts the full retained-window backfill on its first
cron. Small durable planning jobs run in the same recovery queue, expanding one UTC
day of ECCC directories per job and all airport/local-day AWC queries in another.
Planning normally completes within several minutes, independently of the longer
retrieval pass. Failed planning can safely repeat without duplicating tasks. No
bootstrap command, separate importer, developer machine, additional service, schema
migration or government API credential is required. Old recovery cursors are ignored.
Task inserts are batched below D1's statement binding limit.

Request clocks in D1 pace both queues, retries and alternate hosts together: at least
1 second between AWC and between MET Norway request slots, 1.1 seconds for ECCC
and 0.25 seconds for TGFTP.
These are shared clocks, not independent per-consumer sleeps. Live work has priority
access to future slots; recovery waits only briefly and otherwise sends a delayed
replacement message. Normal pacing does not consume the queue's error retry budget.
Changing consumer concurrency does not multiply the provider request rate.

ECCC's current reception hour is checked every minute. The previous hour is checked
every ten minutes for late files, and older hours leave the live queue. Recovery
revisits recent reception directories; after three days a successful complete listing
finishes that directory. This is a reception-time boundary, not an observation-time
boundary: newly received corrections still arrive through current reception hours.
Timestamped bulletin files are fetched once successfully. Failed listings/files retry;
a 404 directory is recorded as a successful no-data check, not proof of completeness.
AWC rechecks recent local days every 15 minutes and older days daily. TGFTP recovery
covers its available rotating files only; it cannot reconstruct overwritten history.
The original report receipt time is never replaced by a backfill's retrieval time.

The `recovery` arrays in `/data/index.json` and `/data/health.json` expose each source
and task kind's task count, unchecked count, rechecks due, outstanding messages,
errors and latest successful check (Unix seconds). Planning tasks must finish before
the task counts describe the entire window; file discovery can increase the counts.
These are processing counters, not a completeness percentage. Airport-day missing
slots and evidence remain the coverage record. All four active sources may share upstream
observations; the deployment is independently operable by its owner but depends on
Cloudflare and government availability.

Raw bytes and their receipt are written to R2 before a normalized D1 insert. Distinct
reports have deterministic identities and keep their original receipt time. Pending
normalized archive writes resume from D1, without needing another upstream copy.
Only archived reports enter publication. Raw responses are stored once by SHA-256.

Publication writes an immutable manifest, CSV and day JSON, then conditionally
replaces the current index. A failed or overlapping publisher cannot advertise
half-written days or overwrite a newer index. Repeating an interrupted publication
reuses the original manifest timestamp. Source conflicts and missing readings are
handled by the shared policy, never by interpolation or majority voting.

The `routine-metar-v2` policy preserves AWC's per-report `receiptTime` separately
from the collector's `fetched_at`. New AWC records include this source timing in
their deterministic identity; re-fetching a legacy report can add a timed copy
without altering the original row or receipt. Live, recovery and offline replay
use the same decoder. No database migration is required. TGFTP/ECCC file dates
are not used to order individual report versions. Missing or invalid timing stays
unordered, and unresolved selection is reported automatically without a manual
review workflow. NWS API observation timestamps are not receipt times. V3 retains the old midnight
boundary; v4 adds the publication/deadline boundary below.

`config/retention.json` is the storage policy. D1 and the current index keep complete
airport local days intersecting the last 30 days. Bounded pruning runs after
publication; referenced receipts survive for as long as their retained reports.
Unreferenced receipts and rejected records are pruned after 30 days. Old content
returned by an upstream cannot reintroduce an expired observation day.

The dedicated `poly-metars` R2 bucket expires objects 32 days after upload, including
legacy migration snapshots. This small buffer protects complete local days. Shared
raw bodies reused after 12 hours are refreshed with identical bytes, keeping their
hashes and original receipt times intact. Newly backfilled evidence may consequently
remain longer than 30 days from observation time. Lifecycle deletion is asynchronous
(normally within a day of expiration); this is a minimum retention window, not an
exact erasure deadline. Account owners can still alter/delete R2 data.

`scripts/configure_retention.py` reads the policy and the dedicated bucket name in
`deploy/cloudflare.json`; update that deployment file when handing over accounts.
It replaces the bucket lifecycle rules and preserves seven-day incomplete-upload
cleanup. Always deploy the collector's evidence refresh before applying these rules.
Review with `npx wrangler r2 bucket lifecycle list poly-metars --config wrangler.collector.jsonc`.
Do not put unrelated data in this bucket. Download audit bundles before expiry if
they are needed for longer disputes or external records.

## Read traffic and costs

HTML, JavaScript and CSS are static assets. Only `/data/*` invokes the small file
server. It never queries the database or contacts a government source. Immutable
files cache for a day; the current index caches at the edge for 10 seconds. Query
strings and client cache-busting headers do not create extra cache keys. Visitors
cannot change collection work. Unknown paths are rejected before accessing storage.

Workers Paid has a $5/month base plus usage. Static asset requests are free; data
requests still incur Workers request charges even on cache hits. R2 storage and
operations, Queues operations, D1 usage and logs are additional. This is not a fixed
$5 service. Collection itself generates millions of checks/receipts per month.
Review measured usage after the first full day and set account billing notifications.

For reference, published Workers rates include 10 million requests and 30 million
CPU milliseconds monthly, then $0.30/million requests and $0.02/million CPU ms.
100 million data requests would add $27 in request charges alone, before collector,
storage, CPU and other charges. Queues normally uses three operations per message.
See current [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/),
[R2 pricing](https://developers.cloudflare.com/r2/pricing/),
[Queues pricing](https://developers.cloudflare.com/queues/platform/pricing/) and
[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/).

A custom domain allows the owner to add zone traffic controls and, if justified by
volume, serve published R2 objects through a direct CDN-backed bucket domain. The
initial workers.dev site works without owning or purchasing a domain. It does not
have an application-level hard spending cap. Cloudflare recommends custom domains
for production rather than workers.dev. During verification, workers.dev returned
HTTP 403 / error 1010 for the default Python-urllib user agent, while browsers and
the identifying PolyMETARs-Audit agent succeeded. Before inviting bot traffic, use
a custom domain and configure its browser-integrity/bot rules to permit deliberate
read-only data access; test representative clients. The bundled downloader already
sends an identifying user agent. See [workers.dev guidance](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/).

## Monitor and recover

- `/data/index.json`: publication time, revisions, source route freshness and errors.
- `/data/health.json`: the latest published collection summary. It is a static JSON
  file, so HTTP 200 alone is **not** a health check. Alert on its timestamp being
  older than 180 seconds and on missing/stale `last_success_at` for live sources.
- Cloudflare dashboard: failed cron/queue invocations, queue age/backlog, D1 capacity,
  R2 growth and account usage. Worker logs and sampled traces are enabled.
- The page shows current-clock missing slots and stale source/publication warnings.
  A successful route check says nothing about the completeness of its observations.

Inspect collector logs with:

```sh
npx wrangler tail poly-metars-collector --config wrangler.collector.jsonc
npx wrangler d1 export poly-metars --remote --config wrangler.collector.jsonc --output work/d1-backup.sql
```

Preserve a D1 export before schema changes. D1 also provides managed Time Travel;
consult the [current recovery guide](https://developers.cloudflare.com/d1/reference/time-travel/)
for retention and restore commands. Pause the two queue consumers and remove the
cron trigger before restoring a database; restore into an isolated database first,
then verify an offline day before switching the binding. Never delete the R2 bucket
as part of a database recovery. A database-only code rollback does not undo data.

If D1 is lost, R2 still holds the public history. `scripts/restore_cloudflare.py`
reconstructs report/receipt rows from locally downloaded and verified airport-day
bundles into an SQL file for a fresh D1 database. This restores the supplied days;
it is not an automatic whole-bucket disaster-recovery service. Restore all affected
recent days before restarting publication, or the new index could show fewer reports.
The owner should separately export the bucket for protection from account loss.

## Legacy deployment

The prior EC2/Vercel deployment is documented in [LEGACY_AWS.md](LEGACY_AWS.md) solely
for rollback and historical evidence recovery. Its public revisions and off-host
backups must be preserved during cutover. It is not part of the Cloudflare runtime.

## Midnight-lock rollout and recovery

Apply `0003_midnight_lock.sql` before deploying the v3 collector. It adds durable
acceptance timestamps and a one-time activation boundary; existing archived rows
are stamped as known present now, never at an invented historical cutoff. A bounded
repair stamps rows archived by an overlapping old deployment. Deploy the collector
and frontend together after `make check build`, a Cloudflare dry run and offline
replay. No remote deployment or migration occurs merely by building locally.

The finalizer runs in the existing scheduled collector. It admits only reports
accepted strictly before the airport's next local midnight, then conditionally
creates `locks/YYYY-MM-DD/ICAO.json` after its revision artifacts exist. The index's
`locks` map preserves these pointers. Finalization retries recover incomplete
writes, including a crash after lock creation but before index publication. An
outage does not extend the cutoff; retained unfinalized days are revisited even
when no reports have marked them dirty. At most two unindexed closing days per
airport are processed per tick, newest first; remaining dirty work is retained.
Completed days avoid repeated evidence
queries. Post-cutoff collection may preserve late evidence but never changes a lock.

`locking.json` preserves deployment activation alongside the D1 state. During D1
recovery, preserve the original R2 bucket, lock pointers, activation and revisions.
Restoring SQL evidence alone is not a restoration of resolution locks. Never delete
or recreate lock pointers to force a new result. If the R2 archive is also lost,
restore its lock records and dependencies from an owner backup before resuming
publication; the evidence-only SQL utility cannot reconstruct lock provenance.

The cutoff is exact but scheduled execution/publication is asynchronous. The UI
shows Finalizing until the final record is available. Check after each local
midnight that expected airport/day keys enter `index.locks`; alert on prolonged
pending publication independently of collection health. Locked pages keep stable
coverage diagnostics and do not inherit current live-source freshness warnings.
The 30-day retention window and buffered R2 expiry still apply to locked records.


## V4 next-day-publication rollout and recovery

Run `0004_next_day_lock.sql` before deploying the v4 collector. It records a new
activation time; no reports or old locks are rewritten. Deploy after the required
checks, offline replay and Cloudflare dry run. Days ending after v4 activation use
the new policy; already-ended v3 days still use their original midnight cutoff.
The existing section above describes recovery of those v3 days.

NWS API collection was introduced in v4 and retired in v5; the current planner does
not schedule observation queries or pagination. See the retirement section below.

V4 publication receipts live at `first-publications/YYYY-MM-DD/ICAO.json`. An index
commit first advertises a selected next-day reading. Its R2 upload timestamp,
recorded to whole seconds, supplies the cutoff. A conditional immutable receipt
pins that first revision/report. The publisher immediately follows with finalization;
if interrupted, the next tick first persists pending receipts from the existing
index's own upload time before replacing it. A failed/superseded draft never starts
a cutoff. Browser caching does not affect it.

Keep `next-day-locking.json`, `first-publications/`, `locking.json`, `locks/` and all
referenced revisions/evidence during backup or recovery. Losing both an uncheckpointed
index and its first-publication receipt destroys that publication provenance; do
not invent a replacement timestamp. First-publication receipts are assertions by
the source operator, not independently signed timestamps.

The finalizer uses the earlier of the saved publication time and the fixed ET
deadline. Data accepted at or after the recorded cutoff second stay outside the
locked input set, even if fetched earlier. A next-day fallback-source reading can
trigger; NIL, SPECI, unknown classification and unresolved selections cannot.
The locked audit bundle embeds the triggering day's original manifest and all its
raw evidence, so downloading one bundle is sufficient for offline replay.

Monitor finalization after the next-day reading appears and at the ET deadline,
not just at local midnight. No-next-day-report days stay live until that deadline.
The UI distinguishes Live, Finalizing and Locked. Late corrections cannot change
a lock; no usable readings still produces null high/low.

## MET Norway fallback

MET Norway follows ECCC in v4. Eight-station XML batches run in the live queue
every minute, with a shared one-second request clock and identifying User-Agent.
Each response covers available last-24-hour history, so recent recovery needs no
separate jobs. The `met_no_cache:` D1 state entries retain the original receipt ID,
Last-Modified and Expires. Do not refetch before Expires; send the exact previous
Last-Modified as If-Modified-Since afterwards. A 304 is archived separately and
replays the original 200 receipt, including after interrupted ingestion. No new
schema is needed for this cache. Removing cache state only forces a fresh fetch.

Inspect `met_no` route errors, especially HTTP 203 (provider deprecation).
[MET Norway](https://api.met.no/weatherapi/tafmetar/1.0/documentation) has announced
a Norway-only successor and retains international data for only 24 hours.
Do not treat this path as guaranteed coverage or a 30-day archive. Preserve
CC BY 4.0 attribution in the site, policy, source registry and handover.

## V5 NWS retirement

No schema migration is needed. The first new cron expires tasks whose source is no
longer in the active policy. New task insertion filters inactive sources, and queued
pre-upgrade messages are acknowledged without fetching or planning retired sources.
A fetch already running on the previous Worker may finish during rollout; its
original evidence is preserved. Expired task metadata follows normal pruning.
Do not delete NWS reports, raw responses or receipts as part of this removal.

V5 removes NWS from unlocked next-day-governed revisions and from live health and
source lists. Existing locks, first-publication receipts and embedded v4 policies
stay unchanged. No finality activation is reset. The historical NWS evidence decoder
is retained for offline verification. Confirm zero active NWS tasks, four current
source columns, unchanged old locks and a successful offline audit after deployment.
See WEATHER_GOV_COMPATIBILITY.md for schema, format and coverage findings.

## V6 placeholder NIL rule

No schema migration or activation reset is needed. V6 changes only per-source
selection: a NIL without `COR` or a `CCx` bulletin no longer counts as a version,
so it cannot withdraw or block a routine report from the same source. A corrected
NIL still withdraws. NIL rows stay stored and visible as excluded evidence.

Unlocked next-day-governed days are recomputed under v6 on the next tick; existing
locks, first-publication receipts and embedded v5 manifests stay unchanged and
replay with their pinned policy. Before deployment, replay retained days under v5
and v6 and confirm that every difference is a blocked or disagreement row becoming
selected or clean. A changed temperature on an already selected row means the rule
is broader than intended; do not deploy. Expect additional fallback selections,
which can trigger next-day locks earlier.

## V7 development mode: locking disabled

`lock_mode: "disabled"` in `config/policy.json` turns locking off. The publisher then
applies the current policy to every retained day, ignores activation markers, applies
no cutoff, writes no lock pointers or first-publication receipts, and advertises
empty `locks` and `first_publications` in the index. No migration is needed.

Existing objects under `locks/`, `first-publications/`, `locking.json` and
`next-day-locking.json` are left untouched in R2 but are not read. Their revisions
remain replayable with their pinned policies.

Any change of policy, registry or engine hash enqueues every retained, unlocked
airport-day in the `dirty` table. The publisher drains 100 per tick, newest first,
so a full 31-day backfill of 50 airports completes in roughly 16 minutes. Confirm
the backlog has drained with:

```sh
npx wrangler d1 execute poly-metars --remote --config wrangler.collector.jsonc \
  --command "SELECT COUNT(*) AS n FROM dirty"
```

### Re-enabling locking

Do this only once the policy is agreed; it is not automatic.

1. Create a new policy version with the intended `lock_mode` and archive v7.
2. Move the old `locks/` and `first-publications/` objects to a backup prefix, or wait
   until they have left the retention window. Otherwise, once locking is re-enabled,
   the publisher would honour those old v3–v6 lock pointers again.
3. Rewrite `locking.json` and `next-day-locking.json`, and the matching D1 `state`
   rows, to the re-enable time. Otherwise every day since the original 2026-09-22
   activation that is past its cutoff would lock immediately, with cutoff filtering
   applied retroactively.
4. Run the usual checks, offline replay and dry run, then deploy. Days ending after
   the new activation lock under the new version.
