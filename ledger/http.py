"""Read-only public data server. Never exposes the database or collection controls."""

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import csv
import io
import json
import re
import signal
import threading

from .archive import atomic_write
from .live import audit_bundle, run_service
from .metar import UTC, parse_time

HASH = r'[a-f0-9]{64}'


def handler_for(root, config):
    root = Path(root)
    bundle_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type='application/json', immutable=False):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if immutable else 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path in ('/health', '/data/health.json'):
                    index = json.loads((root / 'public/index.json').read_text())
                    now = datetime.now(UTC)
                    age = (now - parse_time(index['generated_at'])).total_seconds()
                    sources = {j['source']: j for j in index['collection']}
                    stale = [s['id'] for s in index['sources'] if not sources.get(s['id'], {}).get('last_success_at') or
                             (now - parse_time(sources[s['id']]['last_success_at'])).total_seconds() > index['stale_after_seconds']]
                    self.respond(200 if age <= 180 and not stale else 503,
                                 json.dumps({'publication_age_seconds': int(age), 'stale_sources': stale, 'generated_at': index['generated_at']}).encode())
                elif path == '/data/index.json':
                    self.respond(200, (root / 'public/index.json').read_bytes())
                elif path == '/data/policy.json':
                    self.respond(200, (Path(config) / 'policy.json').read_bytes())
                elif match := re.fullmatch(r'/data/evidence/(' + HASH + r')\.txt', path):
                    self.respond(200, (root / 'archive/objects' / f'{match[1]}.txt').read_bytes(), 'text/plain; charset=utf-8', True)
                elif match := re.fullmatch(r'/data/revisions/(' + HASH + r')/(day\.json|audit\.json|day\.csv|bundle\.zip)', path):
                    revision, filename = match.groups()
                    directory = root / 'public/revisions' / revision
                    if filename == 'bundle.zip':
                        target = directory / filename
                        with bundle_lock:
                            if not target.exists():
                                atomic_write(target, audit_bundle(root / 'public', root / 'archive/objects', revision))
                        self.respond(200, target.read_bytes(), 'application/zip', True)
                    elif filename == 'day.csv':
                        day = json.loads((directory / 'day.json').read_text())
                        buffer = io.StringIO()
                        writer = csv.writer(buffer)
                        order = json.loads((directory / 'audit.json').read_text())['policy']['source_order']
                        writer.writerow(['observation_utc', 'local_time', 'selected_c', 'selected_source', *order])
                        for row in day['rows']:
                            selected = row['selected'] or {}
                            writer.writerow([row['observed_at'], row['local_time'], selected.get('temperature_c', ''), selected.get('source', ''),
                                             *[(row['sources'][s]['report'] or {}).get('temperature_c', '') for s in order]])
                        self.respond(200, buffer.getvalue().encode(), 'text/csv; charset=utf-8', True)
                    else:
                        self.respond(200, (directory / filename).read_bytes(), immutable=True)
                else:
                    self.respond(404, b'{"error":"Not found"}')
            except FileNotFoundError:
                self.respond(404, b'{"error":"No published record at this path"}')
            except (ValueError, KeyError):
                self.respond(503, b'{"error":"Record unavailable or failed integrity validation"}')
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='work/live')
    parser.add_argument('--config', default='config')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--serve-only', action='store_true')
    args = parser.parse_args()
    stop = threading.Event()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(args.root, args.config))
    if not args.serve_only:
        thread = threading.Thread(target=run_service, args=(args.root, args.config, 60, stop), daemon=True)
        thread.start()
    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
