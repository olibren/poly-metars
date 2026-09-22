# Resolution page review — 22 September 2026

References: [The Weather Company / Kalshi](https://weather.com/kalshi) and
[NWS London City time series](https://www.weather.gov/wrh/timeseries?site=eglc).

Kalshi's portal leads with station and period controls, compact observed values,
and a data/graph choice. Its hourly view distinguishes preliminary, final and
revised values, and explains missing reports. NWS offers a station-linked time
series, observation table, local/UTC context, units, raw METARs and historical
controls. Its default is a rolling 72-hour period, not an individual market day.

Our page should answer, in order: which airport and day, what are the observed
high and low, and which reports support them? Keep the existing clear table of
AWC, TGFTP, ECCC and the selected reading. Keep original METARs, revisions and
receipt times one row expansion away. Put policy and collection detail behind
progressive disclosure while keeping stale data, gaps and blocked selection visible.
The NWS station link is a comparison tool, not historical evidence for this date.

Each airport/local day has a deterministic URL:
`/?airport=EGLC&date=2026-09-22`. Query parameters give distinct, bookmarkable pages
on the existing static export without a daily rebuild or a dynamic HTML service.
Changing selectors pushes browser history; reload and Back/Forward restore the
selection. Explicit invalid or unavailable selections never become today's page.
A date-only parameter uses the default station; integrations should always provide
both fields. Both high and low use the same policy and evidence, so share this page.

Pair by registered ICAO and market-local date, never by city label or browser date.
A future market registry can store the market identifier with these two fields;
it must verify them against each market's rules. Existing airport-registry market
URLs are examples, not verified links for every selected day, so the day view no
longer presents them as if they were exact matches.

The stable day URL follows the latest publication while live, then its midnight lock. An immutable audit manifest
pins a particular revision, within the retention window. Permanent page identity
does not imply permanent evidence storage. Long-lived resolution records would
require an explicit retention decision, separate from this interface change.

The user subsequently specified automatic local-midnight locking, including days
with gaps or conflicts and with no human review. V3 therefore replaces indefinite
provisional status with Live, Finalizing and Locked. This is an explicit policy
change, not a copy of Kalshi's hourly final/revised semantics. See
[the lock contract](FINALITY_DESIGN.md). The raw multi-source table remains the
direct evidence interface; hourly aggregation must not drop routine observations.
