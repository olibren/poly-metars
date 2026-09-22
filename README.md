# Poly METARs

A public, auditable ledger of government METAR reports for airport-based temperature
markets. Source priority: **NOAA/AWC → NOAA/TGFTP → ECCC → MET Norway**. Each row shows the
captured sources and the selected reading. This is an independent proposal, not
an adopted Polymarket resolution source. Governed daily results lock automatically
when this site first publishes an eligible reading for the following local date,
or at 11:59 PM ET the following calendar date, whichever comes first.

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
| MET Norway live + recent recovery | 60 seconds, respecting upstream cache expiry | Batches of eight stations; available last 24 hours |
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

AWC and TGFTP are two NOAA delivery paths, not independent agencies. ECCC and MET Norway add
other distribution paths; all can share the originating airport and WMO
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
not a frozen publication: it follows the latest retained revision while live, then the immutable day lock. The audit
manifest identifies the exact displayed snapshot. Unsupported, uncollected and
expired days do not fall back to another day. Data remains subject to the existing
30-day retention policy; download evidence for longer recordkeeping.

See [the interface comparison](docs/INTERFACE_REVIEW.md) for the design rationale.

V5 retains the v4 revision window: it stays open until the first eligible next-day reading is
published here, capped at 23:59:00 America/New_York on the following calendar date.
V3 locks and pre-activation history retain their original policy. New day rounding
matches weather.gov's whole-degree display tie rule; raw temperature precision stays
unchanged. NWS API collection is retired: its available observations could not
reliably establish routine report type. Archived evidence remains replayable. See [weather.gov compatibility](docs/WEATHER_GOV_COMPATIBILITY.md).

MET Norway data are provided by the [Norwegian Meteorological Institute](https://api.met.no/)
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). We parse, filter
and round reports under this site’s policy. Its [Tafmetar API](https://api.met.no/weatherapi/tafmetar/1.0/documentation)
retains international observations for 24 hours and has an announced Norway-only
successor. It is an additional fallback, not a long-term international archive.
