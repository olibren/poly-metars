# Finalization contract

The current policy disables backend locking during development. Every retained day
can change as reports, corrections and recovered history arrive. The frontend's
Locked preview is presentation only and does not establish an immutable result.
See [POLICY.md](../POLICY.md) for current selection rules and
[OPERATIONS.md](OPERATIONS.md#finalization) for the re-enable procedure.

The implemented next-day locking mode uses the first successful public-index
publication of an eligible selected routine METAR for the following airport-local
date, capped at 23:59:00 America/New_York on that following date. The committed
index upload time is checkpointed in an immutable first-publication receipt before
a subsequent index can replace it. Failed drafts do not trigger a cutoff.

Reports must be durably archived and database-accepted strictly before cutoff.
Source receipt, HTTP retrieval and durable acceptance are separate timestamps.
Locked manifests embed the trigger revision and its evidence. No usable readings
produce null extrema, never an inferred temperature. Delayed execution does not
extend the cutoff. The retention window still applies.

When operating with locking enabled, preserve `locking.json`,
`next-day-locking.json`, `locks/`, `first-publications/` and all referenced revisions
and evidence during backup or recovery. Evidence-only SQL restoration cannot
reconstruct lock provenance. Never recreate timestamps or lock pointers to force a
new result. Restore original lock metadata before resuming publication.

Tests cover publication triggers, cutoff exclusion, interrupted writes, retries,
acceptance metadata, DST and offline replay. Earlier policy versions and the
midnight-lock verifier remain because published bundles pin those contracts.
