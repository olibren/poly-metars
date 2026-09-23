# Resolution policy: routine-metar-v7

This document describes a proposed temperature resolution policy. The machine-readable
version is `config/policy.json`. It is not the current rulebook of an existing market.

## The selected reading

For one registered airport and exact UTC observation time:

1. Examine NOAA/AWC, then NOAA/TGFTP, then ECCC, then MET Norway.
2. Within each source, find the highest explicit correction rank for this observation.
   `COR` and WMO `CCA` have rank 1; `CCB` has rank 2, and so on. An explicitly
   corrected version takes precedence over a later-received lower-ranked version.
3. Within that rank, use the latest supported **per-report source receipt time**.
   Currently this is AWC's `receiptTime` from its original JSON, preserved as
   `source_received_at` with subsecond precision. It orders AWC's receipt of versions;
   it is not proof of the airport's original issuance order or sensor correctness.
   TGFTP, ECCC and MET Norway have no supported per-report timestamp for this tie-break.
4. Never substitute our retrieval time, HTTP Date/Last-Modified, a filename, array
   position or bulletin position. Missing, malformed, timezone-free or pre-observation
   source receipt times are unordered. An untimed legacy copy of exactly the same
   parsed report as a timed copy adds no independent version. A distinct untimed
   version remains a candidate alongside the latest timed versions.
5. Use an eligible routine METAR from those candidates. A plain NIL, without `COR`
   or a `CCx` bulletin, is not a version: it records that a compiling centre had no
   report to relay, not that the airport withdrew one. It is retained and shown but
   neither withdraws nor competes with a report. An explicitly corrected NIL does
   withdraw the reading at its rank. A newer report without a valid temperature
   withdraws the older reading from that source, even at the same correction rank.
   If the remaining candidates disagree on temperature, or disagree on whether a
   temperature is available, selection is blocked. Do not hide an unresolved
   higher-priority ambiguity by falling back.
6. If the source has no eligible reading and no unresolved ambiguity, proceed to
   the next source. Select the first unambiguous eligible reading. Equivalent
   temperatures may have different winds/clouds; the report ID deterministically
   chooses the displayed representative without choosing a temperature by hash.
7. Show cross-source disagreements and competing temperatures within the highest
   correction rank, including versions superseded by source receipt time. These
   flags do not require human adjudication when a reading has been selected.
   Rows with no determinable reading are excluded automatically from the daily
   extrema. Gaps and conflicts remain diagnostic evidence; they do not prevent
   automatic resolution or require human review. While locking is disabled
   (v7), every retained day is `live` and stays revisable.

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
nearest whole degree, with exact halves toward positive infinity (the weather.gov
WRH table’s `Math.round` convention: −2.5 becomes −2; +2.5 becomes +3). For Fahrenheit, conversion
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
upstream availability. AWC and ECCC offer up to 30 days; MET Norway provides the available last 24 hours. TGFTP's rotating files do
not guarantee that history. Restarts resume bounded historical planning. A newly
obtained higher-priority report or correction may change a selection. In v7 no cutoff
applies, so recovered history can change any retained day. When locking is enabled,
late reports and corrections after cutoff are retained separately and cannot change the day.
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

## Automatic locking (not active in v7)

Locking is disabled while the site and policy are under development. V7 applies the
current selection rules to every retained day, with no cutoff, finalization or lock,
and publishes no first-publication receipts. Results change whenever reports,
corrections or recovered history arrive, or when the policy changes. Lock records
created under v3–v6 remain in storage but are neither honoured nor advertised.

Locking will be enabled by a later policy version from a new activation time; it is
never applied retroactively to days recomputed during development. The rule below
is the next-day publication cutoff used by v4–v6 and is the intended starting point.

### Next-day publication cutoff (v4–v6)

For v4–v6 days, the cutoff is the earlier of:

- The first successful publication in **this site's public index** of an eligible,
  selected routine METAR for the same airport's immediately following local date.
- **23:59:00 America/New_York on the calendar date following the observation date.**
  This is Eastern Time, including daylight saving time, not a fixed UTC offset.

Local midnight ends the observation interval but does not itself close the revision
window. A correction or recovered report for the observation day can enter the
result after midnight if it is durably accepted before cutoff. SPECI, NIL, unknown
classification, missing temperature, future observations, and blocked/ambiguous
selections do not trigger a next-day lock. A report from a later date does not stand
in for the immediately following date. Source priority does not delay the trigger:
an eligible selected fallback reading can trigger it.

The first-publication receipt pins the next-day revision and selected report. Its
time comes from the successful R2 index write's upload time, recorded to whole
seconds; admission is strictly before that recorded second. Failed artifact writes
and failed conditional index updates do not publish a reading or start a cutoff.
Evidence stored at an immutable URL alone is not the publication trigger. A browser's
cache or refresh time does not define publication. Finalization can run after the
trigger, but it uses the original recorded cutoff, never its retry time.

Only reports durably archived and accepted by the database strictly before cutoff
may enter the result. Database acceptance is separate from HTTP retrieval and any
upstream source receipt time. Fetches before cutoff archived afterward are too late.
Never backdate acceptance during recovery. Late versions remain audit evidence but
cannot change the locked result. Coverage gaps and conflicts are diagnostics; they
do not require review or prevent automatic resolution. No eligible values produces
null high/low, never an invented temperature or arbitrary lowest market bracket.

The audit manifest pins acceptance times, cutoff reason, deadline, policy, registry,
engine hashes and exact report inputs. Publication-triggered locks embed the first
next-day revision's audit manifest and raw evidence as well. Offline replay checks
that the trigger was an eligible selected reading for the correct airport/date.
These records are operator assertions, not independent or government-signed proof
of the first publication time or of completeness.

A conditional immutable lock pointer selects a completed day revision. Subsequent
corrections, retries, index recovery and policy changes cannot replace it. The site
shows Live while revisions are allowed, Finalizing while a triggered/deadline lock
is being published, and Locked afterward. There is no settlement submission service.
The existing rolling retention window applies to locked records and trigger evidence.

## Retired NWS observations API

V5 removes NWS API from active collection and source selection. Its observation
schema has no routine/SPECI discriminator, and received raw messages did not carry
explicit classification. EGLC returned no observations. Station network/provider
metadata, reporting minute and QC flags do not establish report type. Alternative
text products did not provide current coverage for the configured stations.
See [the investigation](docs/WEATHER_GOV_COMPATIBILITY.md#nws-retirement-investigation).

Original NWS GeoJSON, normalized reports, receipts and historical revisions remain
subject to the existing retention policy. The decoder remains available for offline
replay; no reports are relabeled or inferred. Existing locks are immutable.

## Policy versions and adoption

V1–v6 documents/configuration are preserved under `docs/policies/` and
`config/policies/`. Their evidence continues to replay with its original rounding,
source order and finality rules. While v7 is active, existing lock records are not
honoured; every retained day is republished under v7.

The v4 migration records a prospective activation time, also retained in
`next-day-locking.json`. Days whose observation interval ends after that activation
use v4. Already-ended v3 days retain their original midnight cutoff, even if their
finalizer had not run. Days before the original v3 activation retain v2 history.
V5 changes only the active source hierarchy for unlocked v4-governed days. It
retains v4 rounding, eligibility and publication-triggered finality, including
original activation and first-publication receipts. No migration or cutoff reset
is required; existing v4 locks and trigger manifests keep their exact policy.
V6 stops plain NIL placeholders from withdrawing or blocking a reading. V7 keeps v6
selection and disables locking for development. Building does not publish or deploy
a change.

This remains a proposed alternate resolution source, not an adopted Polymarket
service or exact reproduction of the weather.gov viewer. The viewer displays a
broader observation set and uses Synoptic-supplied values. See
[the compatibility review](docs/WEATHER_GOV_COMPATIBILITY.md). Any adoption must
publicly agree the source, observation eligibility, cutoff and no-data rules before
trading. Administrator access can still alter or delete storage; application-level
immutability is not external certification.

## MET Norway eligibility and attribution

MET Norway is the final fallback after ECCC. Original Tafmetar XML is retained.
The `meteorologicalAerodromeReport` envelope and required `metarType` field identify
routine reports (empty type or AUTO), special reports (SPECI), and corrections (COR).
Unknown flags, missing metadata, and station/timestamp contradictions are rejected;
SPECI is excluded. XML validTime anchors the UTC observation date, never revision
receipt order. Raw METAR temperature parsing is unchanged.

The live batch includes the available last 24 hours and automatically recovers
recent gaps. International records are discarded upstream after 24 hours; their
announced successor covers Norway only. Cached responses preserve the original
receipt and bytes, including on HTTP 304. HTTP 203 deprecation is surfaced as a
source error rather than silently changing the source contract.

Data: [Norwegian Meteorological Institute](https://api.met.no/),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This site parses, filters
and rounds those data under this policy. No provider endorsement is implied.
