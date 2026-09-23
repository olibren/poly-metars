"""AMSC raw evidence, station isolation, source priority and offline replay."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import test_cloudflare as support
from ledger.evidence import decode
from ledger.metar import UTC
from ledger.policy import resolve

ROOT = Path(__file__).resolve().parents[1]
URL = ('https://www.amsc.net.cn/gateway/api/saas/rest/common/'
       'ReportController/messageRetrieval?isCCCC=cccc&cccc=EGLC&nearest=72&tt=SA%2CSP')
RAW = 'METAR EGLC 221250Z 25005KT 9999 FEW020 23/12 Q1013='


def body(*reports):
    return json.dumps({'code': 200, 'data': list(reports)}).encode()


def rows(payload, fetched_at='2026-09-22T13:00:00Z'):
    return list(decode(payload, {'source': 'amsc', 'url': URL, 'fetched_at': fetched_at}))


class AmscParsingTests(unittest.TestCase):
    def test_classification_correction_nil_and_precision(self):
        values = rows(body(RAW, RAW.replace('METAR', 'SPECI'),
                           RAW.replace('METAR ', ''), RAW.replace('METAR ', 'METAR COR '),
                           'METAR EGLC 221250Z NIL=',
                           RAW.replace('=', ' RMK T02330120=')))
        self.assertTrue(values[0]['eligible'])
        self.assertEqual(values[1]['reason'], 'special_report')
        self.assertEqual(values[2]['reason'], 'unclassified_report')
        self.assertEqual(values[3]['correction'], 1)
        self.assertEqual(values[4]['reason'], 'nil_report')
        self.assertEqual(values[5]['temperature_c'], 23.3)
        self.assertTrue(all('source_received_at' not in row for row in values))

    def test_invalid_envelope_and_empty_response(self):
        for value in [{}, [], {'code': 500, 'data': []}, {'code': 200, 'data': None}]:
            with self.assertRaises(ValueError):
                rows(json.dumps(value).encode())
        self.assertEqual(rows(body()), [])

    def test_invalid_entries_preserved_without_poisoning_valid_reports(self):
        result = rows(body(RAW, RAW.replace('EGLC', 'ZSJN'), None, {}, 'broken', RAW+' '+RAW,
                           RAW.replace('221250Z', '221400Z')))
        self.assertTrue(result[0]['eligible'])
        self.assertTrue(all('parse_error' in r for r in result[1:]))
        self.assertTrue(all(r['icao'] == 'EGLC' for r in result))

    def test_month_rollover_uses_retained_receipt_not_replay_date(self):
        row = rows(body(RAW.replace('221250Z', '302350Z')), '2026-10-01T00:10:00Z')[0]
        self.assertEqual(row['observed_at'], '2026-09-30T23:50:00Z')

    def test_priority_and_array_order_never_resolve_ambiguous_revisions(self):
        policy = json.loads((ROOT/'config/policy.json').read_text())
        self.assertEqual(policy['source_order'], ['noaa_awc', 'noaa_tgftp', 'eccc', 'met_no', 'amsc'])
        first, second = rows(body(RAW, RAW.replace('23/12', '24/12')))
        reports = [{**r, 'source': 'amsc', 'id': str(i)} for i, r in enumerate([first, second])]
        for ordered in [reports, reports[::-1]]:
            result = resolve(ordered, policy['source_order'], revision_order='source_receipt_time')
            self.assertIsNone(result['selected'])
        self.assertEqual(resolve(reports[:1], policy['source_order'])['selected']['source'], 'amsc')
        met = {**reports[0], 'id': 'met', 'source': 'met_no'}
        self.assertEqual(resolve([*reports, met], policy['source_order'])['selected']['source'], 'met_no')
        old = json.loads((ROOT/'config/policies/routine-metar-v7.json').read_text())
        self.assertIsNone(resolve(reports[:1], old['source_order'])['selected'])


class AmscCollectionTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    async def asyncSetUp(self):
        await support.CloudflareTests.asyncSetUp(self)
        self.module.SETTINGS['policy'] = json.loads((ROOT/'config/policy.json').read_text())

    async def test_retry_preserves_json_and_selected_fallback_replays(self):
        self.body = body(RAW, RAW.replace('METAR', 'SPECI'), 'broken')
        task = {'source': 'amsc', 'kind': 'report', 'url': URL}
        self.worker.env.DB.fail_reports = True
        with self.assertRaisesRegex(RuntimeError, 'D1 write failure'):
            await self.worker.collect(task, 'live')
        await self.worker.collect(task, 'live')
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM reports').first('n'), 2)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM rejected').first('n'), 1)
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())['verified'])
        archive = self.worker.env.ARCHIVE
        index = json.loads(await (await archive.get('index.json')).text())
        revision = index['revisions']['2026-09-22/EGLC']
        day = json.loads(await (await archive.get(f'revisions/{revision}/day.json')).text())
        selected = [r['selected'] for r in day['rows'] if r['selected']]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['source'], 'amsc')

    async def test_api_error_is_not_success_and_empty_result_keeps_receipt(self):
        self.body = b'{"code":500,"data":[]}'
        task = {'source': 'amsc', 'kind': 'report', 'url': URL}
        with self.assertRaises(ValueError):
            await self.worker.collect(task, 'live')
        self.body = body()
        self.assertEqual(await self.worker.collect(task, 'live'), 0)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM reports').first('n'), 0)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM receipts').first('n'), 2)

    async def test_global_plan_pacing_and_host_allowlist(self):
        import planner
        airports = json.loads((ROOT/'config/airports.json').read_text())
        tasks = [t for t in planner.live_jobs(airports, self.now) if t['source'] == 'amsc']
        self.assertEqual(len(tasks), len(airports))
        stations = set()
        for task in tasks:
            url = json.loads(task['payload'])['url']
            self.module.check_url(url)
            query = parse_qs(urlparse(url).query)
            stations.add(query['cccc'][0])
            self.assertEqual(query['nearest'], ['72'])
            self.assertNotIn('count', query)
            self.assertEqual(query['tt'], ['SA,SP'])
            self.assertEqual(task['interval_seconds'], 300)
        self.assertEqual(stations, {a['icao'] for a in airports})
        later = [t for t in planner.live_jobs(airports, self.now.replace(hour=14)) if t['source'] == 'amsc']
        self.assertEqual([t['id'] for t in tasks], [t['id'] for t in later])
        self.assertEqual(self.module.REQUEST_SPACING_MS['amsc'], 1000)
        recovery = planner.recovery_jobs(airports, self.now, datetime(2026, 9, 22, tzinfo=UTC))
        self.assertFalse(any(t['source'] == 'amsc' for t in recovery))
        with self.assertRaises(ValueError):
            self.module.check_url(URL.replace('www.amsc.net.cn', 'www.aamets.com'))
