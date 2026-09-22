# Resolution policy: routine-metar-v1

This document describes a proposed temperature resolution policy. The machine-readable
version is `config/policy.json`. It is not the current rulebook of an existing market.

## The selected reading

For one registered airport and exact UTC observation time:

1. Examine NOAA/AWC, then NOAA/TGFTP, then ECCC.
2. Within each source, find the highest explicit correction rank for this observation.
   `COR` and WMO `CCA` have rank 1; `CCB` has rank 2, and so on. Arrival order does
   not establish which report is correct. All previous versions are retained.
3. Use an eligible routine METAR from that revision. A corrected NIL or report without
   a valid temperature withdraws the older value from that source.
4. If equally ranked eligible revisions from this source disagree on temperature,
   stop: selection is blocked. Do not conceal the ambiguity by falling back.
5. If there is no eligible temperature from this source, proceed to the next source.
6. Select the first unambiguous eligible reading. Show its provider and temperature
   in the bold selected-reading column. Other providers' readings remain visible.
7. Mark any cross-source temperature disagreement. The published priority determines
   the selected input even during a disagreement; the day's result remains provisional.

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

Continuous collection repeats an overlapping local-day history window. Restarts repeat
that window. Explicit historical dates allow recovery further back within upstream
retention. A newly obtained higher-priority report or correction may change an earlier
selection. Every received version stays in the evidence archive.

Each published export pins its policy hash, airport-registry hash, generation time and
input report IDs. The index changes only after the immutable export is complete. Old
snapshots and original reports are retained. A frontend follows one export's immutable
URLs so that a refresh cannot mix reports from different publication versions.

## Finality and adoption

Every daily result is **provisional**. This implementation intentionally has no automatic
settlement submission or finalization. An adopter must agree the finality window,
handling of outstanding gaps and disputes, and whether this routine-only observation
universe is appropriate. Those rules must be published before trading, not chosen
after seeing a result. New policy versions must retain previous versions and may not
silently redefine an already published market.
