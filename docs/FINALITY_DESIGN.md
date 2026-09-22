# Automatic local-midnight locking

Implemented by `routine-metar-v3`; see [the resolution policy](../POLICY.md).

- The airport's next local midnight is the strict cutoff, including DST changes.
- Reports must be durably archived and database-accepted before cutoff. Source
  receipt, HTTP retrieval and durable acceptance remain separate timestamps.
- The selected observations determine the daily high/low automatically. Coverage
  gaps and excluded ambiguous observations are diagnostics, never a review gate.
- No usable readings produces null extrema in a locked record, never a temperature
  inferred from a neighboring airport or an arbitrary market bracket.
- A conditional R2 lock pointer pins complete immutable JSON, CSV and audit artifacts.
  Backfills, corrections, retries, engine upgrades and index rebuilds honor it.
- A delayed job publishes later but does not move the cutoff. The UI distinguishes
  Live, Finalizing and Locked and stops recalculating a locked day's gap counts.
- Days ending before deployment activation remain honest v2 historical records.
- Existing evidence retention still applies. This is not a permanent archive.

`tests/test_midnight_lock.py` covers cutoff exclusion, missing/ambiguous/no-data
inputs, archival crossing midnight, delayed publication, interrupted writes,
overlapping publishers, lost-index recovery, version changes, tampered acceptance
metadata and offline replay, DST and fractional-offset timezones.
