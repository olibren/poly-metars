# Poly METARs

A public, auditable ledger of government METAR reports for airport-based temperature
markets. Source priority: **NOAA/AWC → NOAA/TGFTP → ECCC**. Each row shows the
captured sources and the selected reading. This is an independent proposal, not
an adopted Polymarket resolution source. Governed daily results lock automatically
at local midnight from the best available selected observations.

Site: https://poly-metars.olibren.workers.dev

Source: https://github.com/olibren/poly-metars

## One Cloudflare account, one repository

```text
Every-minute schedule → live queue ────→ government sources
                      → recovery queue → government archives
                                   ↓
                     original bytes + receipts → R2
                     normalized working index → D1
                                   ↓
                     immutable airport-day revisions → R2
                                   ↓
                     cached files + static website → readers
```

The collector and the website are separate Workers. Website requests cannot trigger
collection, query D1, or submit observations. There are no servers to maintain,
no AWS dependency in the Cloudflare runtime, and no observation commits or rebuilds.
The collector uses the same Python parser and selection code as the offline verifier.

The schedule targets **60-second source checks** and publication. The browser checks
for updates every **15 seconds**. Upstream delays, queue backlog or failed checks can
extend these intervals; the page warns when publication or source checks become stale.
Future and missing slots use the reader's current clock, even if collection stops.

| Path | Target interval | Recovery |
|---|---:|---|
| AWC live | 60 seconds | Previous 3 hours, batches of 8 airports |
| TGFTP live | 60 seconds | Latest configured SA bulletin files |
| ECCC live | 60 seconds | Recent reception directories; latest two bulletin times per route |
| AWC recovery | 15 minutes recent / daily older | Last 30 days and complete local boundary days, within upstream retention |
| TGFTP recovery | 5 minutes | Timestamped rotating global collectives |
| ECCC recovery | 30 minutes recent / 6 hours / daily older | Hourly directories covering the last 30 days |

Live collection has its own queue and concurrency. Historical scans cannot occupy
its consumers. Persistent tasks, expiring leases and retries recover interrupted
work. Failed requests, raw bytes, receipt times, rejected reports and distinct
corrections are retained. Publication changes the current index only after every
referenced file is stored. Previous revisions remain addressable within retention.
The current index keeps complete local days covering the last 30 days. Older data
is pruned; R2 evidence and revisions expire 32 days after upload, with shared raw
files refreshed on reuse. Backfill is limited to history still available upstream;
TGFTP's rotating files do not guarantee a full month. See [POLICY.md](POLICY.md).

AWC and TGFTP are two NOAA delivery paths, not independent agencies. ECCC adds a
second agency's distribution path; all can share the originating airport and WMO
transport. Only explicitly classified routine METARs enter selection. SPECI and
ambiguous reports are retained and excluded. Read [POLICY.md](POLICY.md).

## Audit a reading

Open a row to inspect the original reports. Download the day's **Audit manifest**
and its referenced evidence with the standard-library verifier:

```sh
python3 scripts/download_audit.py 'https://SITE/data/revisions/HASH/audit.json' work/audit
python3 -m ledger.audit work/audit
```

The second command works offline. It checks response hashes and provenance,
reparses the reports and recomputes the selections and extrema. See
[docs/AUDIT.md](docs/AUDIT.md). Hashes are not government signatures, independent
timestamps, or proof that no report was omitted.

## Develop and deploy

Requires Node 22.13+, Python 3.12+ and uv for Cloudflare tooling. Offline verification
also runs on Python 3.9+. Dependencies and runtime dates are pinned.

```sh
npm ci
uv sync --frozen
make check build
npm run cf:dry-run
```

[docs/OPERATIONS.md](docs/OPERATIONS.md) describes account setup, local execution,
deployment, monitoring and recovery. [docs/HANDOVER.md](docs/HANDOVER.md) describes
adoption into Polymarket-controlled accounts. The legacy standalone Python collector
remains available for offline research and rollback; it is not required by Cloudflare.

## Limits and ownership

A single Cloudflare account is an operational dependency, not decentralized consensus.
The operator controls deployment and stored data. The application never overwrites
published evidence, but account administrators retain that technical ability.
Adoption requires Polymarket to own the account, repository, domain and deployment
permissions, with the original developers' access removed.

No architecture can recover a report that all retained upstream sources missed.
Station schedules and market mappings require review. This project does not claim
an SLA, settlement readiness, or exact equivalence to every market's existing rules.

MIT licensed. No trading code, credentials or order execution. Government-source
documentation is linked in `config/sources.json`.

## Airport/day links

Use `/?airport=EGLC&date=2026-09-22` for a specific airport and its local calendar
day. The airport identifier is the registered ICAO
code; the date is `YYYY-MM-DD` in that airport's configured timezone. Changing the
selectors updates the URL, and opening or reloading that URL restores the same
selection. The default page pins its initial date rather than rolling over at
midnight. Both daily high and low markets share this airport/day evidence page.

Programmatic pairing requires the market's actual airport and local observation
date from its rules; do not infer a station from the city name alone. Construct
`origin + '/?airport=' + ICAO + '&date=' + localDate`. This is a stable page address,
not a frozen publication: it follows the latest retained revision while live, then the immutable midnight lock. The audit
manifest identifies the exact displayed snapshot. Unsupported, uncollected and
expired days do not fall back to another day. Data remains subject to the existing
30-day retention policy; download evidence for longer recordkeeping.

See [the interface comparison](docs/INTERFACE_REVIEW.md) for the design rationale.

V3 automatically locks days ending after deployment activation at their next local
midnight, using the best available selected data without review or adjudication.
Gaps and conflicts remain visible diagnostics and do not delay resolution. Reports
accepted after cutoff cannot change a locked result. Historical days predating
activation remain identified as historical; see [the lock contract](docs/FINALITY_DESIGN.md).
