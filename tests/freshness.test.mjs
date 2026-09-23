import assert from 'node:assert/strict';
import { test } from 'node:test';
import { awcStale } from '../src/lib/freshness.ts';

const checked = '2026-09-23T10:00:00Z';
const now = Date.parse(checked) + 15 * 60 * 1000;
const collection = [
  { source: 'noaa_awc', status: 'partial', airport_last_success_at: {
    EHAM: checked, EGLC: '2026-09-23T10:14:00Z', ZBAA: null,
  } },
  { source: 'eccc', status: 'partial' },
];

test('AWC warning begins at exactly 15 minutes for the affected airport only', () => {
  assert.equal(awcStale(collection, 'EHAM', now - 1), false);
  assert.equal(awcStale(collection, 'EHAM', now), true);
  assert.equal(awcStale(collection, 'EGLC', now), false);
  assert.equal(awcStale(collection, 'EHAM', now + 1), true);
});

test('unknown checks and legacy global health do not claim airport staleness', () => {
  for (const icao of ['ZBAA', 'XXXX']) assert.equal(awcStale(collection, icao, now), false);
  assert.equal(awcStale(undefined, 'EHAM', now), false);
  assert.equal(awcStale([{ source: 'noaa_awc', last_success_at: checked }], 'EHAM', now), false);
  assert.equal(awcStale([{ source: 'noaa_awc', airport_last_success_at: { EHAM: 'invalid' } }], 'EHAM', now), false);
});
