"""Retired NWS evidence fidelity, conservative eligibility and queue retirement."""
from datetime import timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch

import test_cloudflare as support
from ledger.metar import parse_nws
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


class HistoricalNwsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await support.CloudflareTests.asyncSetUp(self)
        self.current_policy = self.module.SETTINGS['policy']
        self.module.SETTINGS['policy'] = json.loads((support.ROOT/'config/policies/routine-metar-v4.json').read_text())
        # Reproduce pre-retirement ingestion using the mocked transport. Runtime
        # has no NWS allowlist/pacing entry; retained decoders still verify v4.
        self.module.GOVERNMENT_HOSTS.add('api.weather.gov')
        self.module.REQUEST_SPACING_MS['noaa_nws'] = 1000
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

    async def test_existing_v4_lock_and_trigger_survive_source_retirement(self):
        self.body=json.dumps({'type':'FeatureCollection','features':[feature()]}).encode()
        await self.worker.collect(self.task_payload(),'live')
        await self.worker.publish(self.now)
        self.now = self.now.replace(hour=23,minute=22)
        self.body=json.dumps({'type':'FeatureCollection','features':[
            feature('METAR EGLC 222320Z 25005KT CAVOK 21/12 Q1013',timestamp='2026-09-22T23:20:00Z')]}).encode()
        await self.worker.collect(self.task_payload(),'live')
        self.now += timedelta(seconds=1)
        await self.worker.publish(self.now)
        bucket = self.worker.env.ARCHIVE
        previous = json.loads(await (await bucket.get('index.json')).text())
        before = previous['locks']
        self.assertIn('2026-09-23/EGLC',previous['first_publications'])
        self.module.SETTINGS['policy'] = self.current_policy
        await self.worker.publish(self.now)
        after = json.loads(await (await bucket.get('index.json')).text())
        self.assertEqual(before, after['locks'])
        self.assertEqual(previous['first_publications'],after['first_publications'])
        self.assertTrue((await self.verify_published_day())['verified'])


class NwsRetirementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.CloudflareTests.asyncSetUp
    asyncTearDown = support.CloudflareTests.asyncTearDown

    async def old_task(self, kind='report', mode='live'):
        task = self.module.job('noaa_nws', mode, kind,
            'https://api.weather.gov/stations/EGLC/observations?limit=100',60,self.now+timedelta(hours=3))
        # Seed the existing DB as it looked before the code upgrade.
        fields=('id','source','mode','kind','payload','interval_seconds','expires_at')
        await self.worker.statement('INSERT INTO tasks('+','.join(fields)+') VALUES(?,?,?,?,?,?,?)',
                                    *[task[k] for k in fields]).run()
        return task

    async def test_planners_and_task_insertion_do_not_revive_nws(self):
        import planner
        jobs=list(planner.live_jobs([self.airport],self.now))+list(planner.planning_jobs(self.now))
        self.assertFalse(any(t['source']=='noaa_nws' for t in jobs))
        task=self.module.job('noaa_nws','recovery','plan','https://api.weather.gov/stations',60,self.now+timedelta(hours=3))
        await self.worker.upsert_tasks([task])
        self.assertIsNone(await self.worker.statement('SELECT id FROM tasks WHERE id=?',task['id']).first())
        self.assertNotIn('noaa_nws', self.module.SETTINGS['policy']['source_order'])
        self.assertNotIn('noaa_nws', [s['id'] for s in self.module.SETTINGS['sources']])

    async def test_old_queued_reports_and_plans_are_acked_without_fetching(self):
        for mode,kind in [('live','report'),('recovery','plan')]:
            task=await self.old_task(kind,mode)
            with patch.object(self.worker,'collect',new=AsyncMock()) as collect:
                await self.worker.perform(task['id'])
                collect.assert_not_awaited()
            row=await self.worker.statement('SELECT * FROM tasks WHERE id=?',task['id']).first()
            self.assertEqual(row['expires_at'],int(self.now.timestamp()))
            self.assertEqual(row['lease_until'],0)
            self.assertEqual(row['queued_until'],0)

    async def test_cron_retirement_removes_jobs_from_dispatch_and_health_without_touching_evidence(self):
        task=await self.old_task()
        await self.worker.perform(self.task['id'])  # retained, active AWC evidence
        before=await self.worker.rows('SELECT * FROM reports')
        await self.worker.retire_inactive_tasks(self.now)
        await self.worker.dispatch('live',300)
        self.assertNotIn(task['id'],[m['body']['task'] for m in self.worker.env.LIVE.messages])
        self.assertEqual(before,await self.worker.rows('SELECT * FROM reports'))
        await self.worker.publish(self.now)
        index=json.loads(await (await self.worker.env.ARCHIVE.get('index.json')).text())
        self.assertFalse(any(s['source']=='noaa_nws' for s in index['collection']+index['recovery']))
        with patch.object(self.worker,'response',new=AsyncMock()) as response:
            await self.worker.collect(json.loads(task['payload']),'live')
            response.assert_not_awaited()
