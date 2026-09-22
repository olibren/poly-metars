# Comparison with the weather.gov resolution page

Checked 2026-09-22 against the London market's linked source:

- [Polymarket London market](https://polymarket.com/event/highest-temperature-in-london-on-september-22-2026)
- [NOAA WRH time-series viewer](https://www.weather.gov/wrh/timeseries?site=eglc)
- [Viewer JavaScript](https://www.weather.gov/source/wrh/timeseries/obs.js?v202601121730)
- [NWS observations API documentation](https://www.weather.gov/documentation/services-web-api)

The linked weather.gov page is **not the api.weather.gov observations API**. Its
viewer JavaScript requests station time-series data from `api.synopticdata.com`,
with metric units or Fahrenheit requested upstream. It displays `air_temp_set_1`
in the Temp column using `Math.round`. This establishes whole-degree display
rounding, not a rule to discard the METAR tenth-degree T group before conversion.
The code alone does not establish how every underlying Synoptic value was decoded,
filtered, corrected or rounded upstream.

V4 retains the raw METAR precision and converts to the market unit before rounding.
Its exact-half rule now follows the viewer: +2.5 becomes +3 and −2.5 becomes −2.
Prior policies keep half-away-from-zero for historical replay. No guarantee of
identical upstream floating-point or normalization behavior is implied.

There are still material differences:

| Aspect | weather.gov / quoted market | This proposed source |
|---|---|---|
| Observation set | Viewer defaults to all available observations; its help describes METAR, SPECI and subhourly ASOS data. The market refers to all times in the Temp column. | Explicitly classified routine METARs only. |
| Temperature data | Synoptic-supplied temperature in the requested unit, rounded for the table. | Temperature parsed from archived government METAR text. |
| First next-day point | First point published by the designated weather.gov source. | First eligible selected next-day routine reading committed to our public index, as explicitly requested. |
| Final deadline | 11:59 PM ET the following date. | Precisely 23:59:00 America/New_York on that date; admission strictly before cutoff. |
| No NOAA data | Quoted market falls back to Weather Underground; if still absent, lowest bracket. | No numeric result when no eligible reading exists; never estimate or select a bracket. |
| Revisions | Viewer says data are preliminary and subject to QC changes. Market freezes consideration at its trigger. | Immutable policy-governed day revision, with late evidence retained separately. |

The new locking rule and rounding convention improve alignment, but **this is not
an exact replica of existing market resolution**. Matching the full viewer would
require a separately agreed observation set and access/provenance design. Do not
reuse the viewer's embedded third-party access token as this project's credentials.

A bounded live NWS API check found empty observations for EGLC and both blank raw
messages and unlabeled raw reports for KJFK. NWS integration therefore does not
claim global equivalent coverage: raw messages without explicit routine
classification remain excluded under the current policy. NWS's documented MADIS
processing delay and shared NOAA dependencies remain relevant.

## NWS retirement investigation

Checked 2026-09-22 before v5 removal:

- The live [OpenAPI specification](https://api.weather.gov/openapi.json) defines
  Observation with timestamp, rawMessage, weather fields and QC values, but no
  METAR/SPECI report-type field or observation-type filter. GeoJSON and JSON-LD
  reference the same Observation schema. `@type=wx:ObservationStation` is an object
  type, not a routine report classification.
- `obs_station_provider` supplies station-level provider/subProvider, not per-report
  classification. KJFK returned ASOS-HFM; it cannot prove a particular report routine.
- A KJFK hourly report requested as `application/vnd.noaa.uswx+xml` returned
  MesonetSurfaceObservation with ASOS network metadata and decoded weather fields,
  but no routine/SPECI classification. Legacy `application/vnd.noaa.obs+xml` also
  lacked a discriminator; changing formats does not repair the missing provenance.
- [EGLC observations](https://api.weather.gov/stations/EGLC/observations?limit=100)
  returned HTTP 200 with an empty features array. KJFK's latest 100 observations
  included seven raw messages, none with an explicit METAR prefix. At the production
  check, every stored NWS report was excluded as unclassified.
- The alternate [MTR text-product location list](https://api.weather.gov/products/types/MTR/locations)
  matched only KLAX among the configured US K-stations. JFK and EGLC product lists
  were empty. [Latest LAX product](https://api.weather.gov/products/types/MTR/locations/LAX/latest)
  contained an explicit SA bulletin/METAR, but was issued 2026-09-17T12:56Z, five days
  before the check. This route could not establish useful current coverage.

Decision: retire the observations API instead of inferring routine status from
clock minute, station network, QC, or the absence of a SPECI token. V5 keeps the
other four sources and all v4 finality/rounding rules. Preserve historical NWS data
and decoding for audit replay. Source retrieval success must not be described as
usable routine-METAR coverage. Reintroduction needs fresh evidence of a supported
classification contract and useful station coverage.
