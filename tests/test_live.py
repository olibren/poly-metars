from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import threading
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
import zipfile

from ledger.archive import Archive, write_json
from ledger.audit import verify_export
from ledger.evidence import decode
from ledger.http import handler_for, ThreadingHTTPServer
from ledger.live import Store, publish, audit_bundle
from ledger.metar import UTC, parse_collective, parse_report
from ledger.sources import collective_listing, collect_tgftp_history, collect_eccc, eccc_live_links

NOW = datetime.now(UTC).replace(second=0, microsecond=0)
AIRPORT = {'icao': 'EGLC', 'city': 'London', 'timezone': 'Europe/London', 'unit': 'C', 'routine_minutes_utc': [20,50], 'market_urls': []}
POLICY = {'version':'test', 'source_order':['noaa_awc','noaa_tgftp','eccc'], 'delivery_grace_minutes':15}


class CollectiveTests(unittest.TestCase):
    def test_each_bulletin_preserves_type_and_correction(self):
        text = '''####018000123####
SAUK32 EGGY 221250 CCB
EGLC 221250Z 00000KT CAVOK 24/15 Q1029=
####018000123####
SPUK32 EGGY 221251
EGLC 221251Z 00000KT CAVOK 25/15 Q1029=
####018000123####
SAUK32 EGGY 221320
EGLC 221320Z 00000KT CAVOK 25/15 Q1029=
'''
        rows = list(parse_collective(text, datetime(2026,9,22,tzinfo=UTC)))
        self.assertEqual([r['report_type'] for r in rows], ['METAR','SPECI','METAR'])
        self.assertEqual([r['correction'] for r in rows], [2,0,0])
        self.assertFalse(rows[1]['eligible'])
        self.assertNotIn('####', rows[0]['raw'])
        receipt={'source':'noaa_tgftp','url':'https://tgftp.nws.noaa.gov/SL.us008001/DF.an/DC.sflnd/DS.metar/sn.0001.txt', 'headers':{'last-modified':'Tue, 22 Sep 2026 13:25:00 GMT'}}
        self.assertEqual(rows, list(decode(text.encode(), receipt)))

    def test_eccc_live_preserves_every_version_at_recent_times(self):
        names = ['SAUK32_EGGY_221300___1', 'SAUK32_EGGY_221320___2',
                 'SAUK32_EGGY_221350___3', 'SAUK32_EGGY_221350_CCA_4',
                 'SAUK32_EGGY_221350___5', 'OTHER_EGGY_221350___6']
        chosen = eccc_live_links(names, {'SAUK32_'})
        self.assertEqual(set(chosen), set(names[1:5]))
        rollover = ['SAUK32_EGGY_312320___1', 'SAUK32_EGGY_312350___2', 'SAUK32_EGGY_010020___3']
        self.assertEqual(set(eccc_live_links(rollover, {'SAUK32_'}, datetime(2026,9,1,tzinfo=UTC))), set(rollover[1:]))

    def test_rotation_uses_dates_not_filenames(self):
        entries=collective_listing('<tr><td><a href="sn.0750.txt">x</a></td><td>21-Sep-2026 23:10</td></tr><tr><td><a href="sn.0000.txt">x</a></td><td>21-Sep-2026 23:15</td></tr>')
        self.assertEqual(entries[0][0], 'sn.0000.txt')
        with self.assertRaises(ValueError): collective_listing('<html>upstream error</html>')

    def test_eccc_retains_first_file_even_if_later_download_fails(self):
        from unittest.mock import MagicMock
        from urllib.error import URLError
        with TemporaryDirectory() as temp:
            archive = Archive(temp)
            client = MagicMock()
            client.archive = archive
            stamp = NOW.strftime('%d%H%M')
            client.links.return_value = [f'SAUK32_EGGY_{stamp}___2', f'SAUK32_EGGY_{stamp}___1']
            body = f'SAUK32 EGGY {stamp}\nMETAR EGLC {stamp}Z 00000KT CAVOK 24/15 Q1029='.encode()
            calls = []
            def fetch(source, url):
                calls.append(url)
                if len(calls) > 1:
                    raise URLError('simulated second-file outage')
                return body, archive.response(source, url, body, 200)
            client.fetch.side_effect = fetch
            airport = {**AIRPORT, 'timezone':'UTC', 'eccc_bulletins':['SAUK32_EGGY']}
            result = collect_eccc(client, [airport], [NOW.date().isoformat()], recent_hours=0)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(len(archive.read('reports')), 1)
            self.assertTrue(archive.verify()['verified'])

    def test_eccc_missing_directory_is_recorded_without_mirror_retry(self):
        from unittest.mock import MagicMock
        with TemporaryDirectory() as temp:
            archive = Archive(temp)
            client = MagicMock()
            client.archive = archive
            def missing(source, url):
                archive.response(source, url, b'Not found', 404)
                raise HTTPError(url, 404, 'Not found', {}, io.BytesIO(b'Not found'))
            client.links.side_effect = missing
            airport = {**AIRPORT, 'timezone':'UTC', 'eccc_bulletins':['SAUK32_EGGY']}
            result = collect_eccc(client, [airport], [NOW.date().isoformat()], recent_hours=0)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['reports'], 0)
            self.assertEqual(client.links.call_count, 1)
            self.assertEqual(archive.read('receipts')[0]['status'], 404)
            self.assertTrue(archive.verify()['verified'])

    def test_eccc_server_outage_uses_alternate_https_hostname(self):
        from unittest.mock import MagicMock
        with TemporaryDirectory() as temp:
            archive = Archive(temp)
            client = MagicMock()
            client.archive = archive
            stamp = NOW.strftime('%d%H%M')
            name = f'SAUK32_EGGY_{stamp}___1'
            body = f'SAUK32 EGGY {stamp}\nMETAR EGLC {stamp}Z 00000KT CAVOK 24/15 Q1029='.encode()
            def listing(source, url):
                if 'dd.weather.gc.ca' in url:
                    raise HTTPError(url, 503, 'Unavailable', {}, io.BytesIO(b'Unavailable'))
                self.assertTrue(url.startswith('https://dd.meteo.gc.ca/'))
                return [name]
            client.links.side_effect = listing
            client.fetch.side_effect = lambda source, url: (body, archive.response(source, url, body, 200))
            airport = {**AIRPORT, 'timezone':'UTC', 'eccc_bulletins':['SAUK32_EGGY']}
            result = collect_eccc(client, [airport], [NOW.date().isoformat()], recent_hours=0)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['reports'], 1)
            self.assertEqual(client.links.call_count, 2)
            self.assertTrue(archive.read('reports')[0]['url'].startswith('https://dd.meteo.gc.ca/'))
            self.assertTrue(archive.verify()['verified'])


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'archive')
        self.config=self.root/'config'
        write_json(self.config/'airports.json', [AIRPORT])
        write_json(self.config/'sources.json', [{'id':s} for s in POLICY['source_order']])
        write_json(self.config/'policy.json', POLICY)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def add(self, source='noaa_awc', temp=24):
        raw=f'METAR EGLC {NOW:%d%H%M}Z 00000KT CAVOK {temp}/15 Q1029'
        if source=='noaa_awc':
            body=json.dumps([{'icaoId':'EGLC','obsTime':NOW.timestamp(),'metarType':'METAR','rawOb':raw}]).encode()
        else: body=raw.encode()
        receipt=self.store.response(source, 'https://aviationweather.gov/api/data/metar', body, 200)
        parsed=parse_report(raw,NOW)
        self.assertTrue(self.store.report(parsed,receipt))
        self.assertFalse(self.store.report(parsed,receipt))
        return receipt

    def test_restart_retains_versions_and_receipts(self):
        receipt=self.add()
        self.store.db.close()
        self.store=Store(self.root/'archive')
        self.assertEqual(len(self.store.read('reports')),1)
        self.assertEqual(self.store.latest_receipts('noaa_awc')[0]['id'],receipt['id'])
        self.store.verify()

    def test_immutable_revisions_and_offline_replay(self):
        self.add()
        first=publish(self.store,self.config,self.root/'public',now=NOW)
        key=f'{NOW.astimezone(__import__("zoneinfo").ZoneInfo("Europe/London")).date()}/EGLC'
        revision=first['revisions'][key]
        second=publish(self.store,self.config,self.root/'public',now=NOW)
        self.assertEqual(revision,second['revisions'][key])
        self.add(temp=25)
        third=publish(self.store,self.config,self.root/'public',now=NOW)
        self.assertNotEqual(revision,third['revisions'][key])
        bundle=audit_bundle(self.root/'public',self.root/'archive/objects',revision)
        target=self.root/'unpacked'
        zipfile.ZipFile(io.BytesIO(bundle)).extractall(target)
        self.assertTrue(verify_export(target)['verified'])
        (target/'day.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'replay'): verify_export(target)

    def test_public_http_cannot_expose_database(self):
        self.add()
        publish(self.store,self.config,self.root/'public',now=NOW)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.root,self.config))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/data/index.json') as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store')
            for path in ['/archive/ledger.sqlite3','/data/../archive/ledger.sqlite3','/data/evidence/../../ledger.sqlite3']:
                with self.assertRaises(HTTPError) as error: urlopen(base+path)
                self.assertEqual(error.exception.code,404)
            with self.assertRaises(HTTPError) as error: urlopen(base+'/health')
            self.assertEqual(error.exception.code,503) # no successful live checks
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
