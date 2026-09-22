"""Durable live collection: SQLite is an index; original government bytes are retained."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import io
import json
import math
import shutil
import sqlite3
import threading
import time
import zipfile

from .archive import Archive, atomic_write, canonical, digest, write_json
from .metar import UTC, iso, parse_time
from .policy import daily
from .sources import Client, collect_awc, collect_awc_recent, collect_tgftp, collect_tgftp_history, collect_eccc


class Store(Archive):
    """All normalized versions and receipts are insert-only; commits are synchronous."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self.db = sqlite3.connect(self.root / 'ledger.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, source TEXT, url TEXT, fetched_at TEXT,
                status INTEGER, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS receipt_url ON receipts(source, url, fetched_at);
            CREATE TABLE IF NOT EXISTS latest (source TEXT, url TEXT, payload TEXT NOT NULL, PRIMARY KEY(source, url));
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, icao TEXT, observed_at TEXT, receipt_id TEXT,
                payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS report_day ON reports(icao, observed_at);
            CREATE TABLE IF NOT EXISTS rejected (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        self.db.commit()

    def append(self, name, value):
        with self.mutex, self.db:
            if name == 'receipts':
                self.db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?,?,?,?,?)',
                                (*[value[k] for k in ('id', 'source', 'url', 'fetched_at', 'status')], canonical(value).decode()))
                if value['status'] == 200:
                    self.db.execute('INSERT OR REPLACE INTO latest VALUES (?,?,?)', (value['source'], value['url'], canonical(value).decode()))
            elif name == 'rejected':
                self.db.execute('INSERT OR IGNORE INTO rejected VALUES (?,?)', (digest(canonical(value)), canonical(value).decode()))
            else:
                raise ValueError('Unsupported live journal: ' + name)

    def report(self, parsed, receipt):
        if 'parse_error' in parsed:
            self.append('rejected', {**parsed, 'receipt_id': receipt['id']})
            return False
        identity = {**parsed, 'source': receipt['source']}
        record_id = digest(canonical(identity))
        record = {**identity, 'id': record_id, **{k: receipt[k] for k in ('url', 'fetched_at', 'body_sha256')}, 'receipt_id': receipt['id']}
        with self.mutex, self.db:
            result = self.db.execute('INSERT OR IGNORE INTO reports VALUES (?,?,?,?,?)',
                                    (record_id, parsed['icao'], parsed['observed_at'], receipt['id'], canonical(record).decode()))
            return result.rowcount == 1

    def read(self, name):
        if name not in ('receipts', 'reports', 'rejected', 'jobs'):
            raise ValueError('Unsupported table')
        with self.mutex:
            return [json.loads(row[0]) for row in self.db.execute(f'SELECT payload FROM {name}')]

    def latest_receipts(self, source):
        with self.mutex:
            rows = self.db.execute('SELECT payload FROM latest WHERE source=?', (source,))
            return [json.loads(row[0]) for row in rows]

    def job(self, name, value):
        with self.mutex, self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (name, canonical(value).decode()))

    def records_since(self, since):
        with self.mutex:
            return [json.loads(row[0]) for row in self.db.execute(
                'SELECT payload FROM reports WHERE observed_at >= ? ORDER BY observed_at, id', (since,))]

    def receipts_for(self, reports):
        with self.mutex:
            return [json.loads(self.db.execute('SELECT payload FROM receipts WHERE id=?', (rid,)).fetchone()[0])
                    for rid in sorted({r['receipt_id'] for r in reports})]


def recent_dates(airports, days=3):
    return sorted({(datetime.now(ZoneInfo(a['timezone'])).date() - timedelta(days=d)).isoformat()
                   for a in airports for d in range(days)}, reverse=True)


def source_receipts(archive, source):
    if hasattr(archive, 'latest_receipts'):
        return archive.latest_receipts(source)
    return archive.read('receipts')


def publish(store, config, output, now=None):
    """Small immutable airport-day revisions; no rebuilding or copying the archive."""
    now = now or datetime.now(UTC)
    output, config = Path(output), Path(config)
    airports = json.loads((config / 'airports.json').read_text())
    policy = json.loads((config / 'policy.json').read_text())
    sources = json.loads((config / 'sources.json').read_text())
    dates = recent_dates(airports)
    records = store.records_since(iso(now - timedelta(days=5)))
    previous = json.loads((output / 'index.json').read_text()) if (output / 'index.json').exists() else {}
    revisions = dict(previous.get('revisions', {}))
    policy_hash, registry_hash = digest(canonical(policy)), digest(canonical(airports))
    engine_hashes = {p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py'))}
    for airport in airports:
        station = [r for r in records if r['icao'] == airport['icao']]
        for date in dates:
            # Revision identity changes only when evidence or policy changes, not with every poll.
            reports = [r for r in station if parse_time(r['observed_at']).astimezone(ZoneInfo(airport['timezone'])).date().isoformat() == date]
            identity = {'date': date, 'icao': airport['icao'], 'report_ids': sorted(r['id'] for r in reports),
                        'policy_sha256': policy_hash, 'registry_sha256': registry_hash, 'engine_sha256': engine_hashes}
            revision = digest(canonical(identity))
            key = f'{date}/{airport["icao"]}'
            path = output / 'revisions' / revision
            if not (path / 'day.json').exists():
                result = daily(airport, date, reports, policy, now)
                result.update({'generated_at': iso(now), 'policy_sha256': policy_hash,
                               'registry_sha256': registry_hash, 'snapshot_id': revision})
                receipts = store.receipts_for(reports)
                manifest = {**identity, 'schema': 'poly-metars-day-v1', 'revision': revision,
                            'generated_at': iso(now), 'airport': airport, 'registry': airports, 'policy': policy,
                            'reports': reports, 'receipts': receipts,
                            'evidence': sorted({r['body_sha256'] for r in receipts})}
                # Publish the record last; readers never see an incomplete revision.
                write_json(path / 'audit.json', manifest)
                write_json(path / 'day.json', result)
            revisions[key] = revision
    usage = shutil.disk_usage(store.root)
    jobs = store.read('jobs')
    live_jobs = {j['source']: j for j in jobs if j.get('mode') == 'live'}
    index = {'mode': 'live', 'generated_at': iso(now), 'snapshot_id': digest(canonical(revisions)),
             'base_path': '/data', 'airports': airports, 'dates': sorted({k.split('/')[0] for k in revisions}, reverse=True),
             'sources': sources, 'policy': policy, 'collection': list(live_jobs.values()), 'jobs': jobs,
             'revisions': revisions, 'poll_seconds': 60, 'browser_refresh_seconds': 15,
             'stale_after_seconds': 180, 'disk_free_bytes': usage.free, 'disk_used_fraction': (usage.total - usage.free) / usage.total, 'rejected_count': len(store.read('rejected')),
             'catalog_note': 'Airport coverage follows the reviewed registry. Expected slots are cadence assumptions, not proof that a report was issued.'}
    write_json(output / 'index.json', index)
    return index


def audit_bundle(output, objects, revision):
    """Package the exact immutable day revision, including original source responses."""
    manifest = json.loads((Path(output) / 'revisions' / revision / 'audit.json').read_text())
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('audit.json', canonical(manifest))
        bundle.write(Path(output) / 'revisions' / revision / 'day.json', 'day.json')
        for body_hash in manifest['evidence']:
            body = (Path(objects) / f'{body_hash}.txt').read_bytes()
            if digest(body) != body_hash:
                raise ValueError('Evidence is corrupt')
            bundle.writestr(f'evidence/{body_hash}.txt', body)
    return result.getvalue()


def run_service(root, config='config', live_interval=60, stop=None):
    """Each source and recovery worker has its own cadence and cannot block another."""
    stop = stop or threading.Event()
    root = Path(root)
    store = Store(root / 'archive')
    airports = json.loads((Path(config) / 'airports.json').read_text())
    # One instance per persistent volume. Multiple replicas must use separate volumes.
    with store.lock():
        client = Client(store)
        jobs = [
            ('awc-live', 'noaa_awc', 'live', collect_awc_recent, live_interval),
            ('tgftp-live', 'noaa_tgftp', 'live', collect_tgftp, live_interval),
            ('eccc-live', 'eccc', 'live', lambda c, a, d, workers: collect_eccc(c, a, d, workers, recent_hours=1), live_interval),
            ('awc-recovery', 'noaa_awc', 'recovery', collect_awc, 900),
            ('tgftp-recovery', 'noaa_tgftp', 'recovery', collect_tgftp_history, 300),
            ('eccc-recovery', 'eccc', 'recovery', collect_eccc, 1800),
        ]

        def worker(job):
            name, source, mode, adapter, interval = job
            last_success = None
            prior = next((j for j in store.read('jobs') if j['name'] == name), {})
            last_success = prior.get('last_success_at')
            while not stop.is_set():
                start = time.monotonic()
                started = iso(datetime.now(UTC))
                recovery_days = min(30, max(3, math.ceil((datetime.now(UTC) - parse_time(last_success)).total_seconds() / 86400) + 1)) if last_success else 3
                dates = recent_dates(airports, 1 if mode == 'live' else recovery_days)
                try:
                    result = adapter(client, airports, dates, workers=4)
                except Exception as error:
                    result = {'source': source, 'status': 'error', 'reports': 0, 'errors': [f'{type(error).__name__}: {error}']}
                finished = iso(datetime.now(UTC))
                if result['status'] == 'ok':
                    last_success = finished
                state = {**result, 'name': name, 'mode': mode, 'started_at': started, 'finished_at': finished,
                         'last_success_at': last_success, 'interval_seconds': interval, 'airports': [a['icao'] for a in airports], 'dates': dates}
                store.job(name, state)
                print(json.dumps({'job': name, 'finished_at': finished, 'status': result['status'], 'reports': result['reports'], 'errors': result['errors'][:3]}), flush=True)
                stop.wait(max(1, interval - (time.monotonic() - start)))

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            for job in jobs:
                pool.submit(worker, job)
            while not stop.is_set():
                try:
                    publish(store, config, root / 'public')
                except Exception as error:
                    print(json.dumps({'publisher_error': str(error)}), flush=True)
                stop.wait(15)
