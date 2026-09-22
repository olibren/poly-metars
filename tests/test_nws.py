"""NWS evidence fidelity, bounded collection and conservative eligibility."""
from datetime import timedelta
import json
import unittest
from urllib.parse import urlparse

import test_cloudflare as support
from ledger.metar import parse_nws, parse_time
from ledger.policy import market_value, resolve


def feature(raw='METAR EGLC 221250Z 25005KT 9999 FEW020 23/12 Q1013', **extra):
    return {'type':'Feature', 'properties':{'stationId':'EGLC', 'timestamp':'2026-09-22T12:50:00Z',
                                          'rawMessage':raw, 'temperature':{'value':99}, **extra}}


class NwsParsingTests(unittest.TestCase):
    def test_raw_temperature_wins_and_metadata_is_not_receipt_order(self):
        row = parse_nws(feature())
        self.assertEqual(row['temperature_c'], 23)
        self.assertTrue(row['eligible'])
        self.assertNotIn('source_received_at', row)

    def test_unclassified_and_special_reports_are_excluded(self):
        raw=feature()['properties']['rawMessage']
        self.assertEqual(parse_nws(feature(raw.removeprefix('METAR ')))['reason'], 'unclassified_report')
        self.assertEqual(parse_nws(feature(raw.replace('METAR', 'SPECI')))['reason'], 'special_report')

    def test_missing_raw_and_contradictory_metadata_are_rejected(self):
        for f in [feature(''), feature(None), feature(stationId='KJFK'), feature(timestamp='2026-09-22T12:51:00Z')]:
            with self.assertRaises(ValueError): parse_nws(f)

    def test_weather_gov_rounding_preserves_tenths_and_legacy_replay(self):
        self.assertEqual(market_value(-2.5,'C'), -3)
        self.assertEqual(market_value(-2.5,'C','half_toward_positive'), -2)
        self.assertEqual(market_value(2.5,'C','half_toward_positive'), 3)
        self.assertEqual(market_value(-0.5,'C','half_toward_positive'), 0)
        self.assertEqual(market_value(19.4,'F','half_toward_positive'), 67)
        self.assertEqual(market_value(19,'F','half_toward_positive'), 66)


class NwsCollectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.CloudflareTests.asyncSetUp
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    def task_payload(self):
        return {'source':'noaa_nws','kind':'report','url':'https://api.weather.gov/stations/EGLC/observations?limit=100'}

    async def test_nws_only_reading_replays_from_original_geojson(self):
        self.body=json.dumps({'type':'FeatureCollection','features':[feature()]}).encode()
        await self.worker.collect(self.task_payload(),'live')
        await self.worker.publish(self.now)
        self.assertTrue((await self.verify_published_day())['verified'])
        row=json.loads(await self.worker.statement('SELECT payload FROM reports').first('payload'))
        self.assertEqual(row['source'],'noaa_nws')
        self.assertEqual(row['temperature_c'],23)
        reports=[{**row,'source':s,'id':s} for s in self.module.SETTINGS['policy']['source_order']]
        self.assertEqual(resolve(reports[1:],self.module.SETTINGS['policy']['source_order'])['selected']['source'],'noaa_tgftp')
        self.assertEqual(resolve(reports[2:],self.module.SETTINGS['policy']['source_order'])['selected']['source'],'noaa_nws')

    async def test_non_metar_observations_stay_in_raw_evidence_without_rejection_storm(self):
        self.body=json.dumps({'type':'FeatureCollection','features':[feature(''),feature('METAR KJFK 221250Z 25005KT 10SM 23/12 A3000',stationId='KJFK')]}).encode()
        await self.worker.collect(self.task_payload(),'live')
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) AS n FROM reports').first('n'),0)
        self.assertEqual(await self.worker.statement('SELECT COUNT(*) AS n FROM rejected').first('n'),0)
        # Entire original response (including the unexpected station) stays in R2.
        self.assertTrue(any(x.body == self.body for x in self.worker.env.ARCHIVE.objects.values()))

    async def test_recovery_pagination_is_bounded_and_stays_on_same_station(self):
        next_url='https://api.weather.gov/stations/EGLC/observations?cursor=opaque'
        self.body=json.dumps({'type':'FeatureCollection','features':[feature()], 'pagination':{'next':next_url}}).encode()
        await self.worker.collect(self.task_payload(),'recovery')
        tasks=await self.worker.rows("SELECT payload FROM tasks WHERE source='noaa_nws'")
        self.assertEqual(len(tasks),1)
        self.assertEqual(json.loads(tasks[0]['payload'])['url'],next_url)
        for url in ['https://example.com/private','https://api.weather.gov/stations/KJFK/observations?cursor=x']:
            self.body=json.dumps({'type':'FeatureCollection','features':[], 'pagination':{'next':url}}).encode()
            with self.assertRaises(ValueError):await self.worker.collect(self.task_payload(),'recovery')

    async def test_planner_schedules_live_and_separate_six_hour_recovery(self):
        import planner
        tasks=list(planner.live_jobs([self.airport],self.now))
        self.assertEqual(len([t for t in tasks if t['source']=='noaa_nws']),1)
        jobs=list(planner.nws_recovery_jobs([self.airport],self.now))
        self.assertEqual(len(jobs),28)
        from urllib.parse import parse_qs
        for job in jobs:
            query=parse_qs(urlparse(json.loads(job['payload'])['url']).query)
            self.assertEqual(parse_time(query['end'][0])-parse_time(query['start'][0]),timedelta(hours=6))
