import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolutionUrl, resolveSelection } from '../src/lib/resolution-url.ts';

const airports = [
  { icao: 'EGLC', timezone: 'Europe/London' },
  { icao: 'KLAX', timezone: 'America/Los_Angeles' },
  { icao: 'ZSQD', timezone: 'Asia/Shanghai' },
];

test('airport/day URLs round-trip without using the visitor timezone', () => {
  const selected = resolveSelection('?airport=eglc&date=2026-09-22', airports);
  assert.deepEqual(selected, { icao: 'EGLC', date: '2026-09-22' });
  assert.equal(resolutionUrl(selected.icao, selected.date), '/?airport=EGLC&date=2026-09-22');
  assert.deepEqual(resolveSelection(resolutionUrl(selected.icao, selected.date).slice(1), airports), selected);
});

test('default day follows the selected airport across UTC midnight', () => {
  const now = new Date('2026-09-22T00:30:00Z');
  assert.equal(resolveSelection('?airport=KLAX', airports, now).date, '2026-09-21');
  assert.equal(resolveSelection('?airport=EGLC', airports, now).date, '2026-09-22');
  assert.equal(resolveSelection('', airports, now).icao, 'ZSQD');
});

test('explicit dates stay pinned through midnight and DST transitions', () => {
  for (const date of ['2026-03-29', '2026-10-25', '2024-02-29', '2020-01-01', '2099-01-01']) {
    assert.equal(resolveSelection(`?airport=EGLC&date=${date}`, airports, new Date('2026-12-01Z')).date, date);
  }
});

test('malformed or ambiguous links never silently select a different market', () => {
  for (const query of [
    '?airport=XXXX&date=2026-09-22', '?airport=&date=2026-09-22',
    '?airport=EGLC&date=', '?airport=EGLC&date=2026-02-29',
    '?airport=EGLC&date=2026-04-31', '?airport=EGLC&date=2026-13-01',
    '?airport=EGLC&date=2026-9-2', '?airport=EGLC&date=garbage',
    '?airport=EGLC&airport=KLAX', '?date=2026-09-22&date=2026-09-23',
  ]) assert.throws(() => resolveSelection(query, airports), Error, query);
});
