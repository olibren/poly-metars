"""Publication-triggered finality, deadline fallback and auditable trigger evidence."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_cloudflare as support
from ledger.archive import canonical, digest
from ledger.audit import verify_day_manifest
from ledger.metar import UTC, iso
from ledger.policy import resolution_deadline


class NextDayLockTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = support.CloudflareTests.asyncSetUp
    asyncTearDown = support.CloudflareTests.asyncTearDown
    verify_published_day = support.CloudflareTests.verify_published_day

    async def collect(self, temperature=23, observed='2026-09-22T12:50:00Z', kind='METAR', raw=None):
        stamp = datetime.fromisoformat(observed.replace('Z', '+00:00'))
        self.body = json.dumps([{'icaoId': 'EGLC', 'obsTime': stamp.timestamp(),
                                'receiptTime': iso(self.now), 'metarType': kind,
                                'rawOb': raw or f'EGLC {stamp:%d%H%M}Z 25005KT 9999 FEW020 {temperature}/12 Q1013'}]).encode()
        await self.worker.collect(json.loads(self.task['payload']), 'live')

    async def published(self):
        bucket = self.worker.env.ARCHIVE
        index = json.loads(await (await bucket.get('index.json')).text())
        revision = index['revisions']['2026-09-22/EGLC']
        day = json.loads(await (await bucket.get(f'revisions/{revision}/day.json')).text())
        return index, revision, day

    async def next_reading(self):
        self.now = datetime(2026, 9, 22, 23, 22, tzinfo=UTC)
        await self.collect(21, observed='2026-09-22T23:20:00Z')

    async def test_midnight_stays_open_and_late_revision_counts_until_publication(self):
        await self.collect()
        await self.worker.publish(self.now)
        self.now = datetime(2026, 9, 22, 23, 5, tzinfo=UTC)
        await self.collect(25)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day['summary']['status'], 'live')
        self.assertEqual(day['summary']['high'], 25)
        await self.next_reading()
        self.now += timedelta(minutes=1)  # collection alone does not trigger
        await self.collect(26)
        self.now += timedelta(seconds=1)
        await self.worker.publish(self.now)
        index, revision, day = await self.published()
        self.assertEqual(day['summary']['status'], 'locked')
        self.assertEqual(day['summary']['high'], 26)
        self.assertEqual(day['cutoff_at'], iso(self.now))
        self.assertEqual(day['finalization']['reason'], 'next_day_publication')
        self.assertEqual(day['finalization']['trigger']['date'], '2026-09-23')
        self.assertIn('2026-09-23/EGLC', index['first_publications'])
        self.now += timedelta(minutes=1)
        await self.collect(40)
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[1:], (revision, day))
        self.assertTrue((await self.verify_published_day())['verified'])

    async def test_nil_special_unclassified_and_future_observations_do_not_trigger(self):
        await self.collect()
        self.now = datetime(2026, 9, 22, 23, 22, tzinfo=UTC)
        for kind, raw in [('METAR', 'METAR EGLC 222320Z NIL'), ('SPECI', None), ('UNKNOWN', None)]:
            await self.collect(observed='2026-09-22T23:20:00Z', kind=kind, raw=raw)
        await self.collect(observed='2026-09-23T12:50:00Z')
        await self.worker.publish(self.now)
        index, _, day = await self.published()
        self.assertEqual(day['summary']['status'], 'live')
        self.assertNotIn('2026-09-23/EGLC', index['first_publications'])

    async def test_ambiguous_next_day_reading_does_not_trigger(self):
        await self.collect()
        await self.next_reading()
        await self.collect(22, observed='2026-09-22T23:20:00Z')
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[2]['summary']['status'], 'live')

    async def test_deadline_locks_without_next_day_reading_and_excludes_exact_boundary(self):
        await self.collect()
        self.now = resolution_deadline('2026-09-22')
        await self.collect(40)
        self.now += timedelta(hours=5)  # delayed cron cannot extend ET deadline
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day['summary']['high'], 23)
        self.assertEqual(day['finalization']['reason'], 'deadline')
        self.assertEqual(day['cutoff_at'], '2026-09-24T03:59:00Z')
        self.assertIsNone(day['finalization']['trigger'])
        self.assertTrue((await self.verify_published_day())['verified'])

    async def test_next_day_reading_published_after_deadline_does_not_extend_it(self):
        await self.collect()
        await self.next_reading()
        self.now = resolution_deadline('2026-09-22')+timedelta(minutes=5)
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[2]['finalization']['reason'], 'deadline')

    async def test_failed_index_does_not_trigger_and_retry_uses_actual_publication(self):
        await self.collect()
        await self.worker.publish(self.now)
        await self.next_reading()
        self.worker.env.ARCHIVE.fail_key = 'index.json'
        with self.assertRaisesRegex(RuntimeError, 'R2 write failure'):
            await self.worker.publish(self.now)
        self.assertIsNone(await self.worker.env.ARCHIVE.get('first-publications/2026-09-23/EGLC.json'))
        self.now += timedelta(minutes=2)
        await self.collect(28)
        self.now += timedelta(seconds=1)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day['summary']['high'], 28)
        self.assertEqual(day['cutoff_at'], iso(self.now))

    async def test_crash_after_index_commit_recovers_original_cutoff(self):
        await self.collect()
        await self.worker.publish(self.now)
        await self.next_reading()
        original = self.now
        self.worker.env.ARCHIVE.fail_key = 'first-publications/2026-09-23/EGLC.json'
        with self.assertRaisesRegex(RuntimeError, 'R2 write failure'):
            await self.worker.publish(self.now)
        self.now += timedelta(minutes=2)
        await self.collect(40)
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day['summary']['high'], 23)
        self.assertEqual(day['cutoff_at'], iso(original))
        self.assertTrue((await self.verify_published_day())['verified'])

    async def test_fetch_before_trigger_but_archive_after_is_excluded(self):
        await self.collect()
        self.now = datetime(2026, 9, 22, 23, 21, tzinfo=UTC)
        with patch.object(self.worker, 'archive_reports', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                await self.collect(40)
        await self.next_reading()
        await self.worker.publish(self.now)
        self.now += timedelta(minutes=1)
        await self.worker.archive_reports(await self.worker.rows('SELECT id,payload FROM reports WHERE archived=0'))
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[2]['summary']['high'], 23)

    async def test_existing_v3_lock_survives_upgrade_and_index_loss(self):
        v4 = self.module.SETTINGS['policy']
        self.module.SETTINGS['policy'] = self.module.SETTINGS['midnight_policy']
        await self.collect()
        self.now = datetime(2026, 9, 22, 23, tzinfo=UTC)
        await self.worker.publish(self.now)
        _, revision, day = await self.published()
        self.module.SETTINGS['policy'] = v4
        del self.worker.env.ARCHIVE.objects['index.json']
        await self.next_reading()
        await self.worker.publish(self.now)
        self.assertEqual((await self.published())[1:], (revision, day))

    async def test_v4_activation_does_not_rewrite_v3_closed_day_contract(self):
        await self.collect()
        self.now = datetime(2026, 9, 23, 1, tzinfo=UTC)
        await self.worker.statement("UPDATE state SET value=? WHERE name='next_day_lock_started_at'", str(int(self.now.timestamp()))).run()
        await self.worker.publish(self.now)
        day = (await self.published())[2]
        self.assertEqual(day['policy_version'], 'routine-metar-v3')
        self.assertEqual(day['cutoff_at'], '2026-09-22T23:00:00Z')

    async def test_et_deadline_handles_dst(self):
        self.assertEqual(iso(resolution_deadline('2026-03-07')), '2026-03-09T03:59:00Z')
        self.assertEqual(iso(resolution_deadline('2026-10-31')), '2026-11-02T04:59:00Z')

    async def test_rehashed_forged_trigger_and_late_acceptance_fail_offline_replay(self):
        await self.collect()
        await self.next_reading()
        await self.worker.publish(self.now)
        _, revision, _ = await self.published()
        bucket = self.worker.env.ARCHIVE
        original = json.loads(await (await bucket.get(f'revisions/{revision}/audit.json')).text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body_hash in original['evidence']:
                (root/f'{body_hash}.txt').write_bytes(bucket.objects[f'evidence/{body_hash}.txt'].body)
            self.assertEqual(verify_day_manifest(original, root)['summary']['status'], 'locked')
            for mode in ('trigger', 'acceptance'):
                manifest = json.loads(json.dumps(original))
                if mode == 'trigger':
                    manifest['finalization']['trigger']['report_id'] = manifest['report_ids'][0]
                else:
                    manifest['finalization']['accepted_at'][manifest['report_ids'][0]] = int(self.now.timestamp())
                identity = {k:manifest[k] for k in ('date','icao','report_ids','policy_sha256','registry_sha256','engine_sha256','finalization')}
                manifest['revision'] = digest(canonical(identity))
                with self.assertRaisesRegex(ValueError, 'Trigger is not|accepted before cutoff'):
                    verify_day_manifest(manifest, root)

    async def test_overlapping_old_publisher_cannot_replace_trigger_or_lock(self):
        await self.collect()
        await self.worker.publish(self.now)
        self.now += timedelta(minutes=1)
        await self.collect(25)
        async def newer_publication():
            await self.next_reading()
            await self.worker.publish(self.now)
        self.worker.env.ARCHIVE.before_index = newer_publication
        await self.worker.publish(self.now)
        _, _, day = await self.published()
        self.assertEqual(day['summary']['status'], 'locked')
        self.assertEqual(day['summary']['high'], 25)
        self.assertTrue((await self.verify_published_day())['verified'])
