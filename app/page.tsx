'use client';
import { Fragment, useEffect, useState } from 'react';
import Link from 'next/link';
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
import {
  ArrowDownToLine,
  ChevronDown,
  FileText,
  ArrowUpRight,
} from 'lucide-react';

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
};
type Index = {
  mode?: string;
  audit_download?: string;
  revisions?: Record<string, string>;
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
export default function Home() {
  const [index, setIndex] = useState<Index | null>(null);
  const [icao, setIcao] = useState('ZSQD');
  const [date, setDate] = useState('');
  const [day, setDay] = useState<Day | null>(null);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [method, setMethod] = useState(false);
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
        setDate(
          (current) =>
            current ||
            new Intl.DateTimeFormat('en-CA', {
              timeZone:
                data.airports.find((a) => a.icao === 'ZSQD')?.timezone || 'UTC',
              year: 'numeric',
              month: '2-digit',
              day: '2-digit',
            }).format(new Date()),
        );
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
    const clock = setInterval(() => setNow(Date.now()), 15000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(poll);
      clearInterval(clock);
    };
  }, []);
  const revision = index?.revisions?.[`${date}/${icao}`];
  const dayUrl =
    index?.mode === 'live'
      ? revision
        ? `/data/revisions/${revision}/day.json`
        : ''
      : `${index?.base_path || '/data'}/days/${date}/${icao}.json`;
  useEffect(() => {
    if (!date || !icao || !dayUrl) return;
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
        setDay(data);
        setError('');
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => controller.abort();
  }, [icao, date, dayUrl]);
  const displayRows =
    day?.rows.map((row) => ({
      ...row,
      status:
        !row.selected && row.status !== 'blocked'
          ? new Date(row.observed_at).getTime() > now
            ? 'pending'
            : 'missing'
          : row.status,
    })) || [];
  const missingNow = displayRows.filter(
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
  const airport = index?.airports.find((a) => a.icao === icao);
  const sources = index?.sources || [];
  return (
    <main>
      <header className="masthead">
        <Link className="brand" href="/">
          <span className="brand-mark">PM</span>Poly METARs
        </Link>
        <span className="edition">OPEN OBSERVATION RECORD</span>
        <a
          download
          href="https://github.com/olibren/poly-metars"
          className="header-link"
        >
          Review the project <ArrowUpRight size={16} />
        </a>
      </header>
      <div className="page">
        <div className="intro">
          <div>
            <div className="eyebrow">
              GOVERNMENT WEATHER REPORTS · ONE AUDITABLE RECORD
            </div>
            <h1>Every reading. Every source.</h1>
            <p>
              Compare routine METARs and see exactly which reading the published
              hierarchy selects.
            </p>
          </div>
          <span className="review-badge">PROPOSED RESOLUTION SOURCE</span>
        </div>
        <div className="notice">
          This is a review candidate, not an adopted Polymarket resolution
          source. Daily extremes remain provisional.
        </div>
        <section
          className={`freshness ${stale ? 'freshness-stale' : ''}`}
          aria-live="polite"
        >
          <b>
            {!index
              ? 'Connecting to collector…'
              : stale
                ? 'Data freshness needs attention'
                : 'Collector active'}
          </b>
          <span>
            {index
              ? `Published ${publicationAge}s ago · Checks target every 60s · Page refreshes every 15s`
              : 'Loading the latest government observations.'}
          </span>
          {staleSources.length > 0 && (
            <span>
              Overdue or unsuccessful checks:{' '}
              {staleSources.map((s) => s.label).join(', ')}
            </span>
          )}
          {(index?.disk_used_fraction || 0) >= 0.8 && (
            <span>
              Collector storage is above 80% capacity; maintenance is needed.
            </span>
          )}
          {(refreshError || error) && <span>{refreshError || error}</span>}
        </section>
        <section className="toolbar" aria-label="Observation controls">
          <div className="control">
            <label htmlFor="airport-select" id="airport-label">
              Airport
            </label>
            <Select
              value={icao}
              onValueChange={(v) => {
                if (v) {
                  setIcao(v);
                  setDay(null);
                  setError('');
                  setExpanded(null);
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
              Local observation date
            </label>
            <Select
              value={date}
              onValueChange={(v) => {
                if (v) {
                  setDate(v);
                  setDay(null);
                  setError('');
                  setExpanded(null);
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
                {index?.dates.map((d) => (
                  <SelectItem key={d} value={d}>
                    {d}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="toolbar-note">
            {airport?.name}
            <br />
            <span>
              {airport?.timezone} · Market unit °{airport?.unit}
            </span>
          </div>
          {date && (
            <a
              className="download"
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
        <section className="hierarchy">
          <span className="eyebrow">SOURCE PRIORITY</span>
          <div>
            {sources.map((s, i) => (
              <Fragment key={s.id}>
                {i > 0 && <span className="priority-arrow">→</span>}
                <span>
                  <b>{i + 1}</b> {s.label}
                </span>
              </Fragment>
            ))}
          </div>
          <button onClick={() => setMethod(!method)} aria-expanded={method}>
            How selection works <ChevronDown size={16} />
          </button>
        </section>
        {method && (
          <section className="method">
            <h2>A rule anyone can reproduce</h2>
            <p>
              For each airport and observation time, use the first source in the
              published hierarchy with an eligible routine METAR. Keep every
              other source alongside it. A missing higher-priority report
              permits fallback; a disagreement is flagged and the hierarchy
              still determines the selected reading.
            </p>
            <p>
              Explicit corrections supersede originals within a source.{' '}
              {day?.policy_version === 'routine-metar-v2'
                ? 'Within a correction rank, the latest supported per-report source receipt time wins. Equal-time or unorderable conflicts block selection; download order never breaks a tie.'
                : 'Equally ranked, conflicting revisions from the highest available source block selection.'}{' '}
              SPECI, unclassified reports, missing temperatures
              and malformed reports do not enter the routine-only calculation.
              Daily highs and lows use the airport’s local calendar day.
            </p>
            <p>
              NOAA AWC and TGFTP are two delivery paths from one agency. ECCC is
              a second agency. No majority vote or temperature averaging is
              used. Historical data may change when backfill or corrections
              arrive; all stored versions remain auditable.
            </p>
            <a download href="/POLICY.md">
              Read the full policy
            </a>{' '}
            · <a href={`${dataRoot}/policy.json`}>Machine-readable policy</a> ·{' '}
            <a
              href={
                index?.mode === 'live'
                  ? `${revisionRoot}/audit.json`
                  : `${dataRoot}/manifest.json`
              }
            >
              Evidence manifest
            </a>
          </section>
        )}
        {day && (
          <>
            <section className="summary" aria-label="Daily summary">
              <div>
                <span>Observed high</span>
                <strong>{temperature(day.summary.high, airport?.unit)}</strong>
                <small>From selected routine readings</small>
              </div>
              <div>
                <span>Observed low</span>
                <strong>{temperature(day.summary.low, airport?.unit)}</strong>
                <small>From selected routine readings</small>
              </div>
              <div>
                <span>Expected slots received</span>
                <strong>
                  {day.summary.received}
                  <em> / {day.summary.expected}</em>
                </strong>
                <small>
                  {missingNow} elapsed slots without a selected reading
                </small>
              </div>
              <div>
                <span>Source disagreements</span>
                <strong className={day.summary.conflicts ? 'warning-text' : ''}>
                  {day.summary.conflicts}
                </strong>
                <small>
                  Provisional ·{' '}
                  {stale
                    ? 'collection stale'
                    : missingNow
                      ? 'incomplete'
                      : day.summary.status === 'unresolved'
                        ? 'unresolved observations'
                        : 'no settlement cutoff configured'}
                </small>
              </div>
            </section>
            <div className="table-caption">
              <div>
                <h2>
                  {airport?.city} <span>{icao}</span>
                </h2>
                <p>All routine observation times · source temperatures in °C</p>
                <p>
                  {airport?.market_urls?.slice(0, 2).map((url, i) => (
                    <a key={url} href={url} target="_blank" rel="noreferrer">
                      {i ? ' · ' : ''}Market rules ↗
                    </a>
                  ))}
                </p>
              </div>
              <span className="secondary">
                Expand a row to inspect the original reports.
              </span>
            </div>
            <div className="ledger-table">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      Local time<small>UTC below</small>
                    </TableHead>
                    {sources.map((s, i) => (
                      <TableHead key={s.id}>
                        <span className="rank">{i + 1}</span>
                        {s.label}
                        <small>{s.agency}</small>
                      </TableHead>
                    ))}
                    <TableHead className="selected-column">
                      Selected reading
                      <small>Proposed resolution input · °C</small>
                    </TableHead>
                    <TableHead>Record</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {displayRows.map((row) => (
                    <Fragment key={row.observed_at}>
                      <TableRow className={row.conflict ? 'conflict-row' : ''}>
                        <TableCell>
                          <b className="time">{row.local_time}</b>
                          <small>{row.observed_at.slice(11, 16)} UTC</small>
                        </TableCell>
                        {sources.map((s) => {
                          const v = row.sources[s.id];
                          return (
                            <TableCell key={s.id}>
                              <span className="reading">
                                {temperature(v?.report?.temperature_c)}
                              </span>
                              <small>
                                {v?.ambiguous
                                  ? 'Conflicting revisions'
                                  : v?.report
                                    ? v.report.correction
                                      ? 'Corrected report'
                                      : 'Routine METAR'
                                    : row.status === 'pending'
                                      ? 'Scheduled later'
                                      : 'Not captured'}
                              </small>
                            </TableCell>
                          );
                        })}
                        <TableCell className="selected-column">
                          <strong className="selected-reading">
                            {temperature(row.selected?.temperature_c)}
                          </strong>
                          <small>
                            {row.selected
                              ? sources.find(
                                  (s) => s.id === row.selected?.source,
                                )?.label
                              : row.status === 'pending'
                                ? 'Scheduled later'
                                : row.status === 'blocked'
                                  ? 'Selection blocked'
                                  : 'No eligible report'}
                          </small>
                        </TableCell>
                        <TableCell>
                          <button
                            className={`record-button ${row.conflict || row.status === 'blocked' ? 'warning-text' : ''}`}
                            aria-expanded={expanded === row.observed_at}
                            onClick={() =>
                              setExpanded(
                                expanded === row.observed_at
                                  ? null
                                  : row.observed_at,
                              )
                            }
                          >
                            {row.conflict
                              ? 'Disagreement'
                              : row.status === 'missing'
                                ? 'Not captured'
                                : row.status.replaceAll('_', ' ')}
                            <ChevronDown size={15} />
                          </button>
                        </TableCell>
                      </TableRow>
                      {expanded === row.observed_at && (
                        <TableRow>
                          <TableCell
                            colSpan={sources.length + 3}
                            className="evidence-cell"
                          >
                            <div className="evidence">
                              <h3>Original reports · {row.observed_at}</h3>
                              {sources.map((s) => (
                                <div className="source-evidence" key={s.id}>
                                  <h4>{s.label}</h4>
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
            <details className="excluded">
              <summary>
                Excluded reports ({day.excluded.length}) — retained for audit
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
        {!day && (
          <div className="empty-state" aria-live="polite">
            <FileText size={28} />
            <h2>{error || 'Loading the observation record…'}</h2>
            {error && (
              <p>
                The ledger does not substitute sample values for missing data.
              </p>
            )}
          </div>
        )}
        <section className="collection">
          <h2>Collection record</h2>
          <p>
            Published {index?.generated_at || '—'}. Live checks and historical
            recovery run independently. Missing cells mean we have not captured
            a report; they do not prove a source never published it.
          </p>
          <div className="collection-grid">
            {index?.collection.map((s, i) => (
              <div key={`${s.source}-${i}`}>
                <b>{sources.find((source) => source.id === s.source)?.label || s.source}</b>
                <span>
                  {s.status} · {s.scope ? 'Source retrieval checks' : `${s.reports} new reports in latest sweep`}
                </span>
                <small>
                  {s.scope || `${s.airports?.length || '—'} airports · ${s.dates?.join(', ') || ''}`}
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
                {index.rejected_count} report parsing failures retained for
                review
              </a>
              . These reports cannot set an extreme.
            </p>
          )}
        </section>
        <footer>
          <span>Poly METARs · {index?.policy.version || 'policy-v1'}</span>
          <div>
            <a download href="/POLICY.md">
              Resolution policy
            </a>
            <a download href="/data/index.json">
              JSON data
            </a>
            <a
              href={
                index?.mode === 'live'
                  ? `${revisionRoot}/audit.json`
                  : `${dataRoot}/manifest.json`
              }
            >
              Audit manifest
            </a>
            <a
              download={index?.audit_download !== 'manifest-and-evidence'}
              href={
                index?.audit_download === 'manifest-and-evidence'
                  ? '/AUDIT.md'
                  : index?.mode === 'live'
                  ? `${revisionRoot}/bundle.zip`
                  : `${dataRoot}/bundle.zip`
              }
            >
              {index?.audit_download === 'manifest-and-evidence'
                ? 'Download and verify evidence'
                : 'Download audit bundle'}
            </a>
            <a href="/source.zip" download>
              Download source
            </a>
            <a download href="/README.md">
              Repository guide
            </a>
          </div>
        </footer>
      </div>
    </main>
  );
}
