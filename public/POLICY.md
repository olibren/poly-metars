# Resolution policy: routine-metar-v3

This document describes a proposed temperature resolution policy. The machine-readable
version is `config/policy.json`. It is not the current rulebook of an existing market.

## The selected reading

For one registered airport and exact UTC observation time:

1. Examine NOAA/AWC, then NOAA/TGFTP, then ECCC.
2. Within each source, find the highest explicit correction rank for this observation.
   `COR` and WMO `CCA` have rank 1; `CCB` has rank 2, and so on. An explicitly
   corrected version takes precedence over a later-received lower-ranked version.
3. Within that rank, use the latest supported **per-report source receipt time**.
   Currently this is AWC's `receiptTime` from its original JSON, preserved as
   `source_received_at` with subsecond precision. It orders AWC's receipt of versions;
   it is not proof of the airport's original issuance order or sensor correctness.
   TGFTP and ECCC have no supported per-report timestamp for this tie-break.
4. Never substitute our retrieval time, HTTP Date/Last-Modified, a filename, array
   position or bulletin position. Missing, malformed, timezone-free or pre-observation
   source receipt times are unordered. An untimed legacy copy of exactly the same
   parsed report as a timed copy adds no independent version. A distinct untimed
   version remains a candidate alongside the latest timed versions.
5. Use an eligible routine METAR from those candidates. A newer NIL or report without
   a valid temperature withdraws the older reading from that source, even at the
   same correction rank. If the remaining candidates disagree on temperature, or
   disagree on whether a temperature is available, selection is blocked. Do not
   hide an unresolved higher-priority ambiguity by falling back.
6. If the source has no eligible reading and no unresolved ambiguity, proceed to
   the next source. Select the first unambiguous eligible reading. Equivalent
   temperatures may have different winds/clouds; the report ID deterministically
   chooses the displayed representative without choosing a temperature by hash.
7. Show cross-source disagreements and competing temperatures within the highest
   correction rank, including versions superseded by source receipt time. These
   flags do not require human adjudication when a reading has been selected.
   Rows with no determinable reading are excluded automatically from the daily
   extrema. Gaps and conflicts remain diagnostic evidence; they do not prevent
   automatic resolution or require human review. Governed days are `live`,
   `finalization_pending`, or `locked`.

No average, majority vote, hottest-reading preference or coldest-reading preference
is used. Multiple copies of an airport's report are not independent measurements.

## Eligible observations

Routine METAR classification must come from an explicit `METAR` token, AWC's reported
message type, or an SA routine-observation bulletin header. An explicit `SPECI` always
remains special even if delivered in an SA bulletin. Unclassified messages are excluded.

NIL reports, absent temperatures, malformed timestamps, contradictory timestamp
metadata, and temperatures outside −90°C to +60°C are ineligible. Parse failures and
excluded reports are retained. Plausibility checks cannot prove a sensor was correct.

Use the main METAR air-temperature group. When a valid tenth-degree `T` group appears
in remarks, use that precision provided it agrees with the whole-degree group within
0.6°C. Never use dew point, a forecast, a heat index or a neighboring station.

Include every identified routine report, even outside the expected publication slots.
Expected slots exist to reveal possible gaps; they are not a whitelist of eligible times.

## Daily high and low

The interval is midnight inclusive to the next local midnight exclusive in the airport's
configured IANA timezone. Observation time determines the day, not retrieval time.
Daylight-saving transitions can produce 23-hour or 25-hour days.

Convert each selected Celsius value to the market's configured unit, then round to the
nearest whole degree, with exact halves away from zero. For Fahrenheit, conversion
is C × 9/5 + 32 before rounding. The maximum is the daily observed high and the minimum
is the daily observed low. The same observations and selection rules apply to both.

These are extrema among routine reports. They are not necessarily the continuous
24-hour instrumental extreme. No available readings produces **no result**, never 0°C,
an inferred temperature, or an arbitrary lowest market bracket.

## Gaps, recovery and publication

An expected slot more than 15 minutes old without a selected value is shown as missing.
Future slots are pending. Schedule expectations do not prove every expected report
was actually issued, nor do filled slots prove no additional report was missed.

Continuous collection and a separate recovery queue cover the last 30 days, within
upstream availability. AWC and ECCC offer up to 30 days; TGFTP's rotating files do
not guarantee that history. Restarts resume bounded historical planning. Before cutoff, a newly
obtained higher-priority report or correction may change a selection. After locking,
late reports and corrections are retained separately and cannot change the day.
Missing history remains explicitly missing; successful retrieval is not proof of
completeness. Backfilled reports keep their actual retrieval times.

Each published export pins its policy hash, airport-registry hash, generation time and
input report IDs. The index changes only after the immutable export is complete. Old
snapshots and original reports are retained within the retention window. A frontend follows one export's immutable
URLs so that a refresh cannot mix reports from different publication versions.

## Retention

`config/retention.json` defines a rolling **30-day minimum retention window**, based
on observation time. Keep complete airport local days intersecting that window so
expiry cannot silently turn a daily high or low into a partial-day result. This
usually means today and the previous 30 dates. Older days leave the current index
and their working records are removed in bounded batches.

Keep original evidence, receipt times, excluded reports and every received revision
for retained observations. R2 archive objects expire 32 days after upload, with a
buffer for local-day boundaries and shared evidence. Identical raw bodies reused
after 12 hours have their storage age refreshed without changing their bytes, IDs
or recorded receipt times. Backfilled evidence and old snapshots can therefore
outlive their observation window; they are not a permanent archive. Lifecycle
deletion is asynchronous, and cached copies can outlive origin deletion.

Download and retain an audit bundle before expiry if it is needed for a longer
dispute or recordkeeping period. This retention change does not change observation
eligibility, source priority, rounding or the separate locking contract.

## Midnight cutoff and automatic locking

For days governed by v3, the cutoff is the next local midnight in the airport's
pinned timezone: the exclusive end of the observation day. There is no grace
period after midnight and no human review or adjudication. Resolve from the best
available readings selected by the published hierarchy, even with missing slots
or excluded ambiguous observations. These are diagnostics, not `incomplete` or
`unresolved` day statuses. No selected values means a locked record with null high
and low, never an invented temperature.

Only reports durably archived and accepted by the database strictly before cutoff
may enter the result. Database acceptance time is recorded separately from our
HTTP retrieval time and the upstream source receipt time. A report observed before
midnight but first accepted at or after midnight is excluded from the locked input
set. A response retrieved before cutoff but archived afterward is also too late.
Never backdate acceptance during repair or recovery.

The finalizer pins the report IDs, acceptance timestamps, cutoff, policy, airport
registry, engine hashes and immutable day artifacts. An atomically created lock
pointer selects one completed revision. Later source corrections, backfills,
retries, index rebuilds and policy/engine changes cannot replace it. The live
index follows that pointer throughout retention. There is no settlement submission
integration; the locked public record is the proposed resolution source.

The effective cutoff and actual publication time are separate. Scheduled jobs may
run after midnight; the page says `Finalizing` until the immutable record is
published, without admitting later reports. Completed records say `Locked`.
Before cutoff the page says `Live`; missing slots and source differences do not
introduce a manual gate.

## Policy versions and adoption

V1 and v2 policies and documents remain in `config/policies/` and `docs/policies/`.
Their existing manifests replay without changes. A new v2 day-manifest schema pins
v3 lock metadata in its revision identity; the verifier validates the strict
cutoff and recomputes the result from the exact evidence.

The migration records the activation time and stamps already archived reports as
known present at migration. The first v3 publication preserves that activation in
R2. Days ending after activation use midnight locking; days that had already ended
retain the v2 historical policy. They are not falsely labeled as locked at their
past midnight. Rollout to an adopted market must be publicly agreed before trading;
this repository remains a proposed source. The current user-authorized rollout
also covers days open at activation.

Locking does not extend retention. Frozen pages and their evidence remain available
within the existing 30-day minimum retention window, then expire rather than
recompute. Download an audit bundle for longer recordkeeping. Application-level
immutability does not remove the account owner's ability to alter or delete storage.
