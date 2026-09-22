"""MET Norway XML eligibility, fallback and cache provenance contracts."""
from datetime import timedelta
from email.utils import format_datetime
import json
import unittest
from unittest.mock import AsyncMock

import test_cloudflare as support
from ledger.evidence import decode
from ledger.policy import resolve

URL = 'https://api.met.no/weatherapi/tafmetar/1.0/metar.xml?icao=EGLC&extended=true'


def xml(flags='', raw='EGLC 221250Z 25005KT 9999 FEW020 23/12 Q1013', station='EGLC', stamp='2026-09-22T12:50:00'):
    return f'''<metno:aviationProducts xmlns:metno="http://api.met.no" xmlns:gml="http://www.opengis.net/gml/3.2">
    <metno:meteorologicalAerodromeReport><metno:icaoAirportIdentifier>{station}</metno:icaoAirportIdentifier>
    <metno:validTime><gml:TimeInstant><gml:timePosition>{stamp}</gml:timePosition></gml:TimeInstant></metno:validTime>
    <metno:metarType>{flags}</metno:metarType><metno:metarText>{raw}=</metno:metarText>
    </metno:meteorologicalAerodromeReport></metno:aviationProducts>'''.encode()


class MetNoParsingTests(unittest.TestCase):
    def row(self, **args):
        return list(decode(xml(**args), {'source':'met_no','url':URL}))[0]

    def test_routine_auto_corrections_and_specials(self):
        for flags in ['', 'AUTO', 'METAR', 'COR AUTO']:
            row = self.row(flags=flags)
            self.assertTrue(row['eligible'])
            self.assertEqual(row['correction'], int('COR' in flags))
            self.assertEqual(row['observed_at'], '2026-09-22T12:50:00Z')
            self.assertNotIn('source_received_at', row)
        self.assertEqual(self.row(flags='SPECI')['reason'], 'special_report')
        self.assertEqual(self.row(raw='SPECI EGLC 221250Z 25005KT CAVOK 23/12 Q1013')['reason'], 'special_report')

    def test_malformed_unknown_and_contradictory_metadata_fail_closed(self):
        for args in [{'flags':'UNKNOWN'}, {'flags':'METAR SPECI'}, {'station':'KJFK'},
                     {'stamp':'2026-09-22T12:51:00'},
                     {'flags':'SPECI','raw':'METAR EGLC 221250Z 25005KT CAVOK 23/12 Q1013'}]:
            self.assertIn('parse_error', self.row(**args))
        body = xml().replace(b'<metno:metarType></metno:metarType>', b'')
        self.assertIn('parse_error', list(decode(body, {'source':'met_no','url':URL}))[0])
        with self.assertRaises(ValueError):
            list(decode(b'<!DOCTYPE x>'+xml(), {'source':'met_no','url':URL}))


class MetNoCollectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.CloudflareTests.asyncSetUp
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    async def test_last_fallback_preserves_xml_and_replays_offline(self):
        self.body = xml('COR')
        await self.worker.collect({'source':'met_no','kind':'report','url':URL}, 'live')
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())['verified'])
        row = json.loads(await self.worker.statement('SELECT payload FROM reports').first('payload'))
        order = self.module.SETTINGS['policy']['source_order']
        self.assertEqual(order[-2:], ['eccc', 'met_no'])
        self.assertEqual(resolve([row], order)['selected']['source'], 'met_no')
        self.assertEqual(resolve([row, {**row,'id':'eccc','source':'eccc'}],order)['selected']['source'],'eccc')

    async def test_cache_expiry_conditional_fetch_and_retry_keep_original_receipt(self):
        self.body = xml()
        fetch = self.module.js.fetch
        modified = format_datetime(self.now-timedelta(minutes=1), usegmt=True)
        expires = format_datetime(self.now+timedelta(minutes=3), usegmt=True)
        async def response(url, options):
            result = await fetch(url, options)
            result.headers.get = lambda name: {'last-modified':modified, 'expires':expires}.get(name)
            return result
        self.module.js.fetch = AsyncMock(side_effect=response)
        task = {'source':'met_no','kind':'report','url':URL}
        self.worker.env.DB.fail_reports = True
        with self.assertRaisesRegex(RuntimeError, 'D1 write failure'):
            await self.worker.collect(task, 'live')
        receipt = await self.worker.statement('SELECT id FROM receipts').first('id')
        self.now += timedelta(minutes=1)
        await self.worker.collect(task, 'live')
        self.assertEqual(self.module.js.fetch.await_count, 1)
        row = json.loads(await self.worker.statement('SELECT payload FROM reports').first('payload'))
        self.assertEqual(row['receipt_id'], receipt)
        self.now += timedelta(minutes=3)
        self.http_status, self.body = 304, b''
        await self.worker.collect(task, 'live')
        self.assertEqual(self.module.js.fetch.await_count, 2)
        headers = self.module.js.fetch.call_args.args[1]['headers']
        self.assertEqual(headers['If-Modified-Since'], modified)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM receipts').first('n'), 2)
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())['verified'])

    async def test_cache_without_retained_evidence_fetches_unconditionally(self):
        self.body = xml()
        await self.worker.response('met_no', URL)
        for key in list(self.worker.env.ARCHIVE.objects):
            if key.startswith('evidence/'):
                del self.worker.env.ARCHIVE.objects[key]
        fetch = self.module.js.fetch
        self.module.js.fetch = AsyncMock(side_effect=fetch)
        await self.worker.response('met_no', URL)
        self.assertNotIn('If-Modified-Since', self.module.js.fetch.call_args.args[1]['headers'])

    async def test_deprecation_is_visible_and_never_silently_accepted(self):
        self.http_status, self.body = 203, xml()
        with self.assertRaisesRegex(RuntimeError, 'HTTP 203'):
            await self.worker.collect({'source':'met_no','kind':'report','url':URL}, 'live')
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM reports').first('n'), 0)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) n FROM receipts').first('n'), 1)

    async def test_live_plan_batches_recent_history_without_duplicate_recovery(self):
        import planner
        tasks = list(planner.live_jobs([self.airport], self.now))
        met = [t for t in tasks if t['source']=='met_no']
        self.assertEqual(len(met),1)
        self.assertEqual(json.loads(met[0]['payload'])['url'],URL)
        self.assertEqual(met[0]['interval_seconds'],60)
