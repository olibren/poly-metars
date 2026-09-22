# Operating Poly METARs

## Ownership

This is an isolated side project. The collector has its own service account, EC2
instance, security group, persistent volume and backup bucket. It does not use or
restart trading infrastructure. Vercel Hobby serves the static frontend; no Vercel
cron is required. GitHub contains source and CI, not a stream of observation commits.

## Collection and publication

`python3 -m ledger.http` starts six independent source/recovery workers and a
read-only data server. One operating-system lock protects each persistent volume.
Each source has a paced HTTP gate shared by its live and recovery jobs. Requests
have 20-second timeouts and bounded retries. A worker that overruns its interval
finishes before starting again. The publisher writes every 15 seconds.

SQLite uses WAL and FULL synchronous commits. Raw government bytes are durably
written before the associated receipt, then the normalized report is inserted.
Repeated identical reports are deduplicated. Failed requests and rejected reports
are retained. Evidence can be backfilled with its actual receipt time; retrieval
times are never rewritten to appear live.

The current index atomically points to immutable airport-day revisions. Reports
and receipts referenced by a revision are fixed. Existing revisions and evidence
are never deleted by collection. Only small current indexes/job health records
are mutable. The live UI evaluates elapsed time independently of that publication.

TGFTP recovery reads timestamped `DS.metar/sn.NNNN.txt` collectives, which rotate.
Names are not timestamps. Each WMO bulletin retains its own SA/SP classification
and correction sequence. Cached file versions are reused only when directory
metadata matches, with periodic rereads. AWC and ECCC recover older history within
their retention. The recovery window expands after downtime, up to 30 local dates.

## Deploy

The source-only GitHub `main` branch is connected to the dedicated Vercel project.
Run `make check build` before pushing. `vercel.json` contains the public HTTPS
collector origin; no secret is required to read government evidence.

Collector changes are deployed by fetching an exact Git commit on the dedicated
instance, checking the Python suite, then restarting **only** `poly-metars.service`.
Use the instance ID recorded in `deploy/production.json`; do not infer a target
from a trading deployment script. SSM is the administration path; SSH is closed.

The instance boots through `deploy/bootstrap.sh`, and systemd handles restarts.
NGINX fronts the localhost-only Python HTTP server. The origin security group
accepts HTTP only from CloudFront's managed origin-facing address list. CloudFront
provides HTTPS; the browser reaches it through Vercel's same-origin `/data` rewrite.

## Monitor

- `/data/health.json`: non-200 when a live source or publisher is older than 180 seconds.
- `/data/index.json`: publication time, each job's last success, errors, intended
  cadence, recovery window, and disk capacity. A successful HTTP check is not proof
  of complete observation coverage.
- `journalctl -u poly-metars -u poly-metars-backup`: worker and backup failures.
- `systemctl status poly-metars poly-metars-backup.timer`: process/timer state.
- Disk use: alert before 80%. Grow storage or archive it deliberately; never remove
  previously published evidence silently.

The UI displays stale source names and refresh failures prominently. Expected
slots are cadence assumptions, not a guarantee that an airport issued a report.

## Backup and restore

Hourly backup copies raw evidence and published revisions to a private, encrypted,
versioned S3 bucket. It uses SQLite's online backup API for a consistent database
copy. The instance role is limited to its own bucket plus SSM administration.
Backups never use `--delete`. Backups are not an independent collection replica.

To restore: stop the collector, restore the selected SQLite backup to
`archive/ledger.sqlite3` in an empty data directory, sync the matching evidence into
`archive/objects` and published revisions into `public`, and verify every referenced
receipt before starting. Never overwrite a running WAL database. Restart collection
and inspect recovery errors and source health. Upstream retention bounds what can
be recovered from an outage.

## Resource limits

All observation and receipt history is retained. Capacity therefore grows over
time. The initial dedicated collector uses a small ARM instance and encrypted gp3
disk. Only this side project's resource IDs appear in its deployment manifest.
Costs include compute, disk, public IPv4, backup storage and data delivery. No
Vercel plan upgrade is needed. Geographic failover and independent hash witnessing
remain future work; this deployment does not claim either.
