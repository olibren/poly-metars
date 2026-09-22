'use client';
import { Fragment, useEffect, useState } from 'react';
import Link from 'next/link';
import { resolutionUrl, resolveSelection } from '@/lib/resolution-url';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { ArrowDownToLine, ChevronDown, FileText } from 'lucide-react';

type Report = {
  id: string;
  source: string;
  raw: string;
  temperature_c: number | null;
  precision: string;
  report_type: string;
  observed_at: string;
  fetched_at: string;
  source_received_at?: string | null;
  url: string;
  body_sha256: string;
  correction: number;
  eligible: boolean;
  reason: string | null;
};
type Choice = { report: Report | null; ambiguous: boolean; variants: Report[] };
type Row = {
  observed_at: string;
  local_time: string;
  expected: boolean;
  status: string;
  selected: Report | null;
  sources: Record<string, Choice>;
  conflict: boolean;
};
type Airport = {
  icao: string;
  city: string;
  name: string;
  timezone: string;
  unit: string;
  market_urls: string[];
  registry_status: string;
};
type Day = {
  airport: Airport;
  date: string;
  generated_at: string;
  rows: Row[];
  summary: {
    high: number | null;
    low: number | null;
    expected: number;
    received: number;
    missing: number;
    conflicts: number;
    status: string;
  };
  excluded: Report[];
  policy_sha256: string;
  policy_version?: string;
  cutoff_at?: string;
  deadline_at?: string;
  lock_mode?: string;
  rounding_mode?: string;
  source_order?: string[];
  finalization?: {
    cutoff_at: string;
    accepted_at: Record<string, number>;
    reason?: string;
  } | null;
};
type Index = {
  mode?: string;
  audit_download?: string;
  revisions?: Record<string, string>;
  first_publications?: Record<string, { published_at?: string }>;
  stale_after_seconds?: number;
  disk_used_fraction?: number;
  base_path: string;
  generated_at: string;
  airports: Airport[];
  dates: string[];
  sources: { id: string; label: string; agency: string }[];
  policy: {
    version: string;
    source_order: string[];
    delivery_grace_minutes: number;
  };
  collection: {
    source: string;
    status: string;
    reports: number;
    errors: string[];
    finished_at: string;
    last_success_at?: string;
    scope?: string;
    airports: string[];
    dates: string[];
  }[];
  catalog_note: string;
  rejected_count: number;
};
const temperature = (value: number | null | undefined, unit = 'C') =>
  value == null ? '—' : `${value}°${unit}`;
const sourceTemperature = (
  value: number | null | undefined,
  unit = 'C',
  rounding?: string,
) => {
  if (value == null) return '—';
  if (unit === 'F') {
    const fahrenheit = (value * 9) / 5 + 32;
    // Preserve the displayed day's pinned rounding policy.
    return temperature(
      rounding === 'half_toward_positive'
        ? Math.round(fahrenheit)
        : Math.sign(fahrenheit) * Math.round(Math.abs(fahrenheit)),
      unit,
    );
  }
  return temperature(value, unit);
};
export default function Home() {
  const [index, setIndex] = useState<Index | null>(null);
  const [icao, setIcao] = useState('');
  const [date, setDate] = useState('');
  const [loaded, setLoaded] = useState<{ day: Day; url: string } | null>(null);
  const [routeError, setRouteError] = useState('');
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [refreshError, setRefreshError] = useState('');
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch('/data/index.json', {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok)
          throw Error(
            'The collector is temporarily unreachable. Showing the last received data.',
          );
        const data = (await response.json()) as Index;
        if (!active) return;
        setIndex(data);
        setRefreshError('');
      } catch (error) {
        if (active && error instanceof Error && error.name !== 'AbortError')
          setRefreshError(error.message);
      }
    }
    void refresh();
    const poll = setInterval(() => {
      void refresh();
    }, 15000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(poll);
      clearInterval(clock);
    };
  }, []);
  useEffect(() => {
    if (!index) return;
    function readUrl() {
      try {
        const selected = resolveSelection(
          window.location.search,
          index!.airports,
        );
        setIcao(selected.icao);
        setDate(selected.date);
        setRouteError('');
        // Pin the initial local day, so a saved URL never rolls over at midnight.
        window.history.replaceState(
          null,
          '',
          resolutionUrl(selected.icao, selected.date) + window.location.hash,
        );
      } catch (error) {
        setRouteError(
          error instanceof Error ? error.message : 'Invalid page URL.',
        );
        setIcao('');
        setDate('');
      }
    }
    readUrl();
    function onPopState() {
      setError('');
      setExpanded(null);
      readUrl();
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [index]);
  function navigate(nextIcao: string, nextDate: string) {
    window.history.pushState(null, '', resolutionUrl(nextIcao, nextDate));
    setIcao(nextIcao);
    setDate(nextDate);
    setLoaded(null);
    setError('');
    setRouteError('');
    setExpanded(null);
  }
  const revision = index?.revisions?.[`${date}/${icao}`];
  const dayUrl =
    index?.mode === 'live'
      ? revision
        ? `/data/revisions/${revision}/day.json`
        : ''
      : `${index?.base_path || '/data'}/days/${date}/${icao}.json`;
  useEffect(() => {
    if (!date || !icao || !dayUrl || routeError) return;
    const controller = new AbortController();
    fetch(dayUrl, { signal: controller.signal })
      .then((r) => {
        if (!r.ok)
          throw Error(
            'No collection has been published for this airport and date.',
          );
        return r.json() as Promise<Day>;
      })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.airport.icao !== icao || data.date !== date)
          throw Error('Published data does not match this airport and date.');
        setLoaded({ day: data, url: dayUrl });
        setError('');
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => controller.abort();
  }, [icao, date, dayUrl, routeError]);
  const day =
    !routeError &&
    loaded?.url === dayUrl &&
    loaded.day.airport.icao === icao &&
    loaded.day.date === date
      ? loaded.day
      : null;
  const unavailable = index?.mode === 'live' && icao && date && !revision;
  const pageError =
    routeError ||
    error ||
    (unavailable
      ? `No published observations for ${icao} on ${date}. This date may be outside the retained history or not yet collected.`
      : '');
  useEffect(() => {
    if (icao && date) document.title = `${icao} · ${date} — Poly METARs`;
    else document.title = 'Poly METARs — daily airport temperatures';
  }, [icao, date]);
  const locked = day?.summary.status === 'locked';
  const nextDate = day
    ? new Date(Date.parse(`${day.date}T00:00:00Z`) + 86400000)
        .toISOString()
        .slice(0, 10)
    : '';
  const publicationTriggered =
    day?.lock_mode === 'next_day_publication' &&
    !!index?.first_publications?.[`${nextDate}/${icao}`];
  const finalizing =
    !locked &&
    (!!publicationTriggered ||
      (!!day?.cutoff_at && now >= Date.parse(day.cutoff_at)));
  const dayStatus = locked
    ? 'Locked'
    : finalizing
      ? 'Finalizing'
      : day?.cutoff_at
        ? 'Live'
        : 'Historical';
  const displayRows =
    day?.rows.map((row) => ({
      ...row,
      status:
        !locked && !row.selected && row.status !== 'blocked'
          ? new Date(row.observed_at).getTime() > now
            ? 'pending'
            : 'missing'
          : row.status,
    })) || [];
  const missingNow = locked
    ? day.summary.missing
    : displayRows.filter(
        (row) =>
          row.expected &&
          !row.selected &&
          new Date(row.observed_at).getTime() <=
            now - (index?.policy.delivery_grace_minutes || 15) * 60000,
      ).length;
  const publicationAge = index
    ? Math.max(
        0,
        Math.floor((now - new Date(index.generated_at).getTime()) / 1000),
      )
    : 0;
  const staleSources =
    index?.sources.filter((source) => {
      const job = index.collection.find((job) => job.source === source.id);
      const last = job?.last_success_at;
      return (
        job?.status !== 'ok' ||
        !last ||
        now - new Date(last).getTime() >
          (index.stale_after_seconds || 180) * 1000
      );
    }) || [];
  const stale =
    !!refreshError ||
    publicationAge > 180 ||
    (index?.disk_used_fraction || 0) >= 0.8 ||
    staleSources.length > 0;
  const revisionRoot = revision
    ? `/data/revisions/${revision}`
    : index?.base_path || '/data';
  const dataRoot = index?.base_path || '/data';
  const airport = day?.airport || index?.airports.find((a) => a.icao === icao);
  const sources = day?.source_order
    ? day.source_order.map(
        (id) =>
          index?.sources.find((s) => s.id === id) || {
            id,
            label: id,
            agency: '',
          },
      )
    : index?.sources || [];
  return (
    <main>
      <header className="masthead">
        <Link className="brand" href="/">
          <span className="brand-mark">PM</span>Poly METARs
        </Link>
        <span className="header-note">Daily airport temperatures</span>
      </header>
      <div className="page">
        <div className="intro">
          <div>
            <h1>
              {airport ? `${airport.city} · ${icao}` : 'Airport observations'}
            </h1>
            <p>
              {airport
                ? `${airport.name} · ${airport.timezone}`
                : 'Select an airport and date to view its readings.'}
            </p>
          </div>
        </div>
        {!locked && stale && index && (
          <output className="freshness freshness-stale">
            Updates are delayed. Showing the latest available readings.
          </output>
        )}
        <section className="toolbar" aria-label="Observation controls">
          <div className="control">
            <label htmlFor="airport-select" id="airport-label">
              Airport
            </label>
            <Select
              value={icao}
              onValueChange={(v) => {
                if (v) {
                  const nextDate =
                    date ||
                    resolveSelection(`?airport=${v}`, index?.airports || [])
                      .date;
                  navigate(v, nextDate);
                }
              }}
              items={
                index?.airports.map((a) => ({
                  value: a.icao,
                  label: `${a.city} · ${a.icao}`,
                })) || []
              }
            >
              <SelectTrigger
                id="airport-select"
                aria-labelledby="airport-label"
                className="airport-select"
              >
                <SelectValue placeholder="Select airport" />
              </SelectTrigger>
              <SelectContent>
                {index?.airports.map((a) => (
                  <SelectItem key={a.icao} value={a.icao}>
                    {a.city} · {a.icao}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="control">
            <label htmlFor="date-select" id="date-label">
              Date (local)
            </label>
            <Select
              value={date}
              onValueChange={(v) => {
                if (v) {
                  if (icao) navigate(icao, v);
                }
              }}
            >
              <SelectTrigger
                id="date-select"
                aria-labelledby="date-label"
                className="date-select"
              >
                <SelectValue placeholder="Select date" />
              </SelectTrigger>
              <SelectContent>
                {[...new Set([date, ...(index?.dates || [])])]
                  .filter(Boolean)
                  .sort()
                  .reverse()
                  .map((d) => (
                    <SelectItem key={d} value={d}>
                      {d}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
          {day && (
            <a
              className="download toolbar-download"
              href={
                index?.mode === 'live'
                  ? `${revisionRoot}/day.csv`
                  : `${dataRoot}/days/${date}/${icao}.csv`
              }
              download
            >
              <ArrowDownToLine size={17} /> Download CSV
            </a>
          )}
        </section>
        {day && (
          <>
            <output className={`day-status ${locked ? 'day-locked' : ''}`}>
              <strong>{dayStatus}</strong>
              <span>
                {locked
                  ? day.lock_mode === 'next_day_publication'
                    ? 'Final result · revisions are closed.'
                    : 'Final result · frozen under the midnight policy.'
                  : finalizing
                    ? 'Cutoff reached · publishing the fixed result.'
                    : day.cutoff_at
                      ? day.lock_mode === 'next_day_publication'
                        ? 'Updates until the first eligible next-day reading, or 11:59 PM ET the following date.'
                        : 'Updates throughout the day · freezes at local midnight.'
                      : 'Recorded before midnight locking was introduced.'}
              </span>
              {!locked && !finalizing && index && (
                <span className="updated-time">
                  Updated{' '}
                  {publicationAge < 60
                    ? 'just now'
                    : `${Math.floor(publicationAge / 60)} min ago`}
                </span>
              )}
            </output>
            {locked && day.summary.high == null && (
              <p>
                No usable temperature readings were available at cutoff. No
                numeric result.
              </p>
            )}
            <section className="summary" aria-label="Daily summary">
              <div>
                <span>Day’s high</span>
                <strong>{temperature(day.summary.high, airport?.unit)}</strong>
              </div>
              <div>
                <span>Day’s low</span>
                <strong>{temperature(day.summary.low, airport?.unit)}</strong>
              </div>
            </section>
            <div className="table-caption">
              <div>
                <h2>Temperature readings</h2>
                <p>
                  Local time · {date} ·{' '}
                  {airport?.unit === 'F' ? 'whole degrees °F' : '°C'}
                </p>
              </div>
              <span className="secondary">
                Expand a row to compare sources in priority order.
              </span>
            </div>
            <div className="ledger-table">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="time-column">Local time</TableHead>
                    <TableHead className="temperature-column">Temperature</TableHead>
                    <TableHead className="source-column">Source</TableHead>
                    <TableHead>METAR</TableHead>
                    <TableHead className="expand-column">
                      <span className="sr-only">Compare sources</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {displayRows.map((row) => (
                    <Fragment key={row.observed_at}>
                      <TableRow className={row.conflict ? 'conflict-row' : ''}>
                        <TableCell>
                          <time className="time" dateTime={row.observed_at} title={row.observed_at}>{row.local_time}</time>
                        </TableCell>
                        <TableCell>
                          <strong className="selected-reading">
                            {sourceTemperature(
                              row.selected?.temperature_c,
                              airport?.unit,
                              day.rounding_mode,
                            )}
                          </strong>
                        </TableCell>
                        <TableCell className="selected-source">
                          {row.selected
                            ? sources.find((s) => s.id === row.selected?.source)?.label || row.selected.source
                            : '—'}
                        </TableCell>
                        <TableCell className="metar-cell">
                          {row.selected ? (
                            <code className="selected-metar">{row.selected.raw}</code>
                          ) : (
                            <span className="secondary">
                              {row.status === 'pending'
                                ? 'Scheduled later'
                                : row.status === 'blocked'
                                  ? 'Ambiguous reading excluded'
                                  : 'No eligible report'}
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <button
                            className={`record-button ${row.conflict || row.status === 'blocked' ? 'warning-text' : ''}`}
                            aria-label={`Compare sources at ${row.local_time}: ${row.status.replaceAll('_', ' ')}`}
                            aria-expanded={expanded === row.observed_at}
                            aria-controls={`evidence-${row.observed_at}`}
                            onClick={() =>
                              setExpanded(
                                expanded === row.observed_at
                                  ? null
                                  : row.observed_at,
                              )
                            }
                          >
                            <ChevronDown size={16} aria-hidden="true" />
                          </button>
                        </TableCell>
                      </TableRow>
                      {expanded === row.observed_at && (
                        <TableRow>
                          <TableCell
                            colSpan={5}
                            className="evidence-cell"
                          >
                            <div className="evidence" id={`evidence-${row.observed_at}`}>
                              <h3>Original reports · {row.observed_at}</h3>
                              <p className="source-hierarchy">
                                Source priority: {sources.map((s) => s.label).join(' → ')}
                              </p>
                              {row.conflict && (
                                <p className="warning-text">
                                  {row.selected
                                    ? 'Reports disagree. The selected reading follows the source hierarchy and correction rules.'
                                    : 'Conflicting revisions prevent a selection at this time.'}
                                </p>
                              )}
                              {sources.map((s, rank) => (
                                <div className="source-evidence" key={s.id}>
                                  <h4 className="source-evidence-heading">
                                    <span>{rank + 1}. {s.label}</span>
                                    <span>{sourceTemperature(row.sources[s.id]?.report?.temperature_c, airport?.unit, day.rounding_mode)}</span>
                                    {row.selected?.source === s.id && <span className="chosen-source">Selected</span>}
                                  </h4>
                                  {row.sources[s.id]?.variants.length ? (
                                    row.sources[s.id].variants.map((r) => (
                                      <div key={r.id} className="report">
                                        <code>{r.raw}</code>
                                        <p>
                                          {r.eligible
                                            ? `${r.precision} · correction rank ${r.correction}`
                                            : `Excluded: ${r.reason}`}{' '}
                                          · Retrieved {r.fetched_at}
                                          {r.source_received_at &&
                                            ` · Source received ${r.source_received_at}`}
                                        </p>
                                        <a
                                          href={r.url}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          Source URL ↗
                                        </a>{' '}
                                        ·{' '}
                                        <a
                                          href={`${dataRoot}/evidence/${r.body_sha256}.txt`}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          Stored response
                                        </a>
                                        <span className="hash">
                                          SHA-256 {r.body_sha256}
                                        </span>
                                      </div>
                                    ))
                                  ) : (
                                    <p>
                                      No report retained from this source for
                                      this time.
                                    </p>
                                  )}
                                </div>
                              ))}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
        {!day && (
          <div className="empty-state" aria-live="polite">
            <FileText size={28} />
            <h2>
              {pageError || refreshError || 'Loading the observation record…'}
            </h2>
            {pageError && (
              <p>
                No readings from another airport or date are substituted. Choose
                an airport and date above.
              </p>
            )}
          </div>
        )}
        <div className="supporting-details">
          <details className="method">
            <summary>How temperatures are determined</summary>
            <p>
              Source priority:{' '}
              {sources.map((source) => source.label).join(' → ')}.
            </p>
            <p>
              For each airport and observation time, use the first source in the
              published hierarchy with an eligible routine METAR. Keep every
              other source alongside it. A missing higher-priority report
              permits fallback; a disagreement is flagged and the hierarchy
              still determines the selected reading.
            </p>
            <p>
              Explicit corrections supersede originals within a source.{' '}
              {day?.policy_version !== 'routine-metar-v1'
                ? 'Within a correction rank, the latest supported per-report source receipt time wins. Equal-time or unorderable conflicts block selection; download order never breaks a tie.'
                : 'Equally ranked, conflicting revisions from the highest available source block selection.'}{' '}
              SPECI, unclassified reports, missing temperatures and malformed
              reports do not enter the routine-only calculation. Daily highs and
              lows use the airport’s local calendar day.
            </p>
            <p>
              NOAA AWC and TGFTP are delivery paths from one agency.
              ECCC and MET Norway add other distribution paths. No majority vote or temperature averaging
              is used. Current-policy days freeze when this site first publishes
              an eligible selected routine reading for the next local date, or
              at 11:59 PM ET on that following calendar date, whichever comes
              first. Reports must be durably accepted before cutoff. Earlier
              days retain their original policy. Missing slots do not require
              review. Later corrections cannot change a locked day. NWS API
              collection was retired because its observations do not reliably
              identify routine METARs. Archived evidence remains auditable.
            </p>
            <p>
              MET Norway data: <a href="https://api.met.no/">Norwegian Meteorological Institute</a>,{' '}
              <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.
              We parse, filter and round these reports under this site’s policy.
            </p>
            <a download href="/POLICY.md">
              Read the full policy
            </a>{' '}
            {index?.mode !== 'live' && (
              <>
                {' '}
                ·{' '}
                <a href={`${dataRoot}/policy.json`}>Machine-readable policy</a>
              </>
            )}{' '}
            ·{' '}
            {day && (
              <a
                href={
                  index?.mode === 'live'
                    ? `${revisionRoot}/audit.json`
                    : `${dataRoot}/manifest.json`
                }
              >
                Evidence manifest
              </a>
            )}
          </details>
          <details className="audit-details">
            <summary>Data &amp; audit details</summary>
            {day && (
              <p>
                {day.summary.received} of {day.summary.expected} expected
                readings received · {missingNow} elapsed slots missing ·{' '}
                {day.summary.conflicts} source disagreements. Daily results use
                the best available selected readings.
              </p>
            )}
            {day?.cutoff_at && (
              <p>
                {locked
                  ? 'Recorded cutoff'
                  : day.lock_mode === 'next_day_publication'
                    ? 'Latest cutoff (ET deadline)'
                    : 'Midnight cutoff'}
                : {day.cutoff_at}
              </p>
            )}
            {day && (
              <>
                <details className="excluded">
                  <summary>
                    Excluded reports ({day.excluded.length}) — retained for
                    audit
                  </summary>
                  {day.excluded.map((r) => (
                    <div className="report" key={r.id}>
                      <b>
                        {r.source} · {r.observed_at} · {r.reason}
                      </b>
                      <code>{r.raw}</code>
                      <a href={`${dataRoot}/evidence/${r.body_sha256}.txt`}>
                        Stored response
                      </a>
                    </div>
                  ))}
                </details>
              </>
            )}
            {!locked && (
              <details className="collection">
                <summary>Source availability</summary>
                <p>
                  Published {index?.generated_at || '—'}. Live checks and
                  historical recovery run independently. Missing cells mean we
                  have not captured a report; they do not prove a source never
                  published it.
                </p>
                <div className="collection-grid">
                  {index?.collection.map((s, i) => (
                    <div key={`${s.source}-${i}`}>
                      <b>
                        {sources.find((source) => source.id === s.source)
                          ?.label || s.source}
                      </b>
                      <span>
                        {s.status} ·{' '}
                        {s.scope
                          ? 'Source retrieval checks'
                          : `${s.reports} new reports in latest sweep`}
                      </span>
                      <small>
                        {s.scope ||
                          `${s.airports?.length || '—'} airports · ${s.dates?.join(', ') || ''}`}
                        <br />
                        Last attempt {s.finished_at || '—'}
                        <br />
                        Last successful check{' '}
                        {s.last_success_at || 'Not yet completed'}
                      </small>
                      {s.errors.length > 0 && (
                        <details>
                          <summary>{s.errors.length} collection issues</summary>
                          {s.errors.map((e, j) => (
                            <p key={j}>{e}</p>
                          ))}
                        </details>
                      )}
                    </div>
                  ))}
                </div>
                <p>{index?.catalog_note}</p>
                {!!index?.rejected_count && (
                  <p>
                    <a download href={`${dataRoot}/rejected.jsonl`}>
                      {index.rejected_count} report parsing failures retained
                      for review
                    </a>
                    . These reports cannot set an extreme.
                  </p>
                )}
              </details>
            )}
            <div className="audit-links">
              <a href={day ? dayUrl : '/data/index.json'}>JSON data</a>
              {day && (
                <a
                  href={
                    index?.mode === 'live'
                      ? `${revisionRoot}/audit.json`
                      : `${dataRoot}/manifest.json`
                  }
                >
                  Evidence manifest
                </a>
              )}
              <a download href="/AUDIT.md">
                Download and verify evidence
              </a>
              <a href="https://github.com/olibren/poly-metars">Source code</a>
              {icao && (
                <a
                  href={`https://www.weather.gov/wrh/timeseries?site=${icao.toLowerCase()}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  NWS station viewer ↗
                </a>
              )}
            </div>
          </details>
        </div>
        <footer>
          <span>Proposed resolution source · not adopted by Polymarket</span>
          <a download href="/POLICY.md">
            Resolution policy
          </a>
        </footer>
      </div>
    </main>
  );
}
