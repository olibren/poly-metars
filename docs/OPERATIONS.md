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
publicly documented process. All current daily results are provisional.

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
on port 8001 remains an alternative for frontend development with `npm run dev`.

## Collection, failure and publication

The planner defines every source URL, interval, overlap and expiry in one small
module. Live and recovery queues have separate consumer concurrency (16 and 2).
Each message performs one bounded government HTTP fetch, with a 20-second timeout
and 8 MB response limit. Only allowlisted HTTPS hosts are accepted; redirects are
recorded and rejected. ECCC has a documented alternative HTTPS hostname, treated
as the same source. Missing reception directories are recorded as 404/no data.

D1 contains due times, queue reservations and execution leases. Queue deliveries
are idempotent. Reservations expire after 120 seconds and leases after 180 seconds;
due work is redispatched by cron even after queue retries are exhausted. Recovery
prioritizes the rotating TGFTP listing, AWC and discovered files before large ECCC
historical directory scans. Initial recovery can take hours while live work continues.

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
review workflow. This change does not configure market finality.

R2 retains evidence, receipts, reports, rejected records and revisions without an
application deletion path. D1 retains 90 days of reports and their referenced
receipts, 3 days of unreferenced receipts, and bounded expired task history.
Its pruning does not remove published R2 history. Account owners can
still alter/delete R2 data: application immutability is not an administrator lock.

## Read traffic and costs

HTML, JavaScript and CSS are static assets. Only `/data/*` invokes the small file
server. It never queries the database or contacts a government source. Immutable
files cache for a year; the current index caches at the edge for 10 seconds. Query
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
