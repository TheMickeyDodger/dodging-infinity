/**
 * The webview's identity-keyed seat preference store (office/layout/diSeatPrefs.ts): merging with where characters sit, recency, per-entry refusal,
 * eviction by entry count and by record bytes (oldest first, newest kept), and exact keys — never truncated, normalised or case-folded.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import {
  checkDiSeatPreferences,
  DI_SEAT_PREFERENCES_MAX_BYTES,
  DI_SEAT_PREFERENCES_MAX_ENTRIES,
  diSeatPreferencesBytes,
} from '../../core/src/diSeatPreferences.ts';
import { mergeSeatPrefs, mergeSeatPrefsReport, sameSeatPrefs, seatPrefsFromRecord, seatPrefsToRecord } from '../src/office/layout/diSeatPrefs.ts';

const key = (n: number | string, task: string | null = 'task-1', member = `m-${n}`) =>
  JSON.stringify(['direct', 'syn', `syn:mn-${n}`, task, member]);

test('merging: the identities present NOW are protected and come last (history first, then the known present ones, then new or changed); absent history is kept', () => {
  const prev = new Map([[key(1), 's1'], [key(2), 's2'], [key(3), 's3']]);
  const next = mergeSeatPrefs(prev, new Map([[key(1), 's1'], [key(2), 'moved'], [key(4), 's4']]));
  assert.deepEqual([...next], [[key(3), 's3'], [key(1), 's1'], [key(2), 'moved'], [key(4), 's4']]);
  assert.ok(sameSeatPrefs(prev, new Map(prev)));
  assert.ok(!sameSeatPrefs(prev, next));
  assert.ok(sameSeatPrefs(next, new Map([...next].reverse())), 'recency order is not content');
  // an unchanged merge changes nothing but the order: present identities move behind the absent ones
  const same = mergeSeatPrefsReport(next, new Map([[key(3), 's3']]));
  assert.deepEqual([...same.prefs.keys()].at(-1), key(3));
  assert.ok(sameSeatPrefs(same.prefs, next), 'no content change');
  assert.equal(same.historyEvicted + same.activeDropped, 0);
});

test('NO-CHURN PERSISTENCE: a continuously present identity is never forgotten for being unchanged (the exact repro: 512 entries, the present one OLDEST, a newcomer arrives)', () => {
  const prev = new Map(Array.from({ length: 512 }, (_, n) => [key(n), `s${n}`] as const)); // agent0 is the oldest
  assert.equal(prev.size, 512);
  const r = mergeSeatPrefsReport(prev, new Map([[key(0), 's0'], [key(512), 's512']]));
  assert.ok(r.prefs.has(key(0)), 'agent0 is present and unchanged: KEPT');
  assert.equal(r.prefs.get(key(0)), 's0');
  assert.ok(r.prefs.has(key(512)), 'the newcomer is kept');
  assert.ok(!r.prefs.has(key(1)), 'the oldest ABSENT entry is the one evicted');
  assert.ok(r.prefs.has(key(2)), 'the rest of the history stands');
  assert.equal(r.prefs.size, DI_SEAT_PREFERENCES_MAX_ENTRIES);
  assert.equal(r.historyEvicted, 1);
  assert.equal(r.activeDropped, 0);
  // order: history first, the present identities last ("most relevant last")
  const keys = [...r.prefs.keys()];
  assert.deepEqual(keys.slice(-2), [key(0), key(512)]);
  // a simulated server restart: the record round-trips and agent0's exact seat comes back
  const restarted = seatPrefsFromRecord(JSON.parse(JSON.stringify(seatPrefsToRecord(r.prefs))))!;
  assert.equal(restarted.get(key(0)), 's0');
  assert.equal(checkDiSeatPreferences(seatPrefsToRecord(restarted)), null);
  // and it keeps surviving every later merge while it stays present, however many others come and go
  let cur = r.prefs;
  for (let n = 600; n < 700; n++) cur = mergeSeatPrefs(cur, new Map([[key(0), 's0'], [key(n), `s${n}`]]));
  assert.equal(cur.get(key(0)), 's0', 'still there after 100 newcomers');
});

test('the present identities ALONE over the budget: a deterministic subset (ascending stable key) is kept and the dropped count is REPORTED', () => {
  const current = new Map(Array.from({ length: 600 }, (_, n) => [key(n), `s${n}`] as const));
  const r = mergeSeatPrefsReport(new Map(), current);
  assert.equal(r.prefs.size, DI_SEAT_PREFERENCES_MAX_ENTRIES);
  assert.equal(r.activeDropped, 88);
  assert.equal(r.historyEvicted, 0);
  const expected = [...current.keys()].sort().slice(0, 512);
  assert.deepEqual([...r.prefs.keys()], expected, 'the lowest 512 keys, in key order');
  // independent of the order the caller lists them in (discovery order)
  const shuffled = new Map([...current].reverse());
  assert.deepEqual([...mergeSeatPrefsReport(new Map(), shuffled).prefs.keys()], expected);
  // history is evicted before any present identity: with history present too, all of it goes first
  const history = new Map(Array.from({ length: 50 }, (_, n) => [key(`h${n}`), `h${n}`] as const));
  const both = mergeSeatPrefsReport(history, current);
  assert.equal(both.historyEvicted, 50);
  assert.equal(both.activeDropped, 88);
  assert.ok([...both.prefs.keys()].every((k) => current.has(k)), 'only present identities remain');
});

test('the present identities alone over the BYTE budget: the same deterministic subset and a reported count', () => {
  const long = 'm'.repeat(2900);
  const current = new Map(Array.from({ length: 80 }, (_, n) => [key(n, 'task-1', `m-${n}-${long}`), `s${n}`] as const));
  const r = mergeSeatPrefsReport(new Map(), current);
  assert.ok(r.activeDropped > 0 && r.prefs.size > 0 && r.prefs.size + r.activeDropped === 80);
  const record = seatPrefsToRecord(r.prefs);
  assert.ok(diSeatPreferencesBytes(record) <= DI_SEAT_PREFERENCES_MAX_BYTES);
  assert.equal(checkDiSeatPreferences(record), null);
  assert.deepEqual([...r.prefs.keys()], [...current.keys()].sort().slice(0, r.prefs.size));
  // one more would not have fit: the subset is as large as the budget allows
  const next = [...current.keys()].sort()[r.prefs.size];
  assert.ok(diSeatPreferencesBytes(seatPrefsToRecord(new Map([...r.prefs, [next, current.get(next)!]]))) > DI_SEAT_PREFERENCES_MAX_BYTES);
});

test('ordinary history eviction is unchanged: absent entries go oldest-first, and nothing present is touched', () => {
  const prev = new Map(Array.from({ length: 600 }, (_, n) => [key(n), `s${n}`] as const)); // 600 absent entries (over budget: a long history)
  const r = mergeSeatPrefsReport(prev, new Map([[key(900), 's900']]));
  assert.equal(r.prefs.size, DI_SEAT_PREFERENCES_MAX_ENTRIES);
  assert.equal(r.historyEvicted, 89);
  assert.ok(!r.prefs.has(key(88)) && r.prefs.has(key(89)) && r.prefs.has(key(599)), 'the oldest 89 absent entries are gone, the newer ones stay');
  assert.ok(r.prefs.has(key(900)));
  assert.equal(r.activeDropped, 0);
  // by bytes
  const long = 'm'.repeat(2900);
  const absent = new Map(Array.from({ length: 80 }, (_, n) => [key(n, 'task-1', `m-${n}-${long}`), `s${n}`] as const));
  const fresh = mergeSeatPrefsReport(absent, new Map([[key(900), 's900']]));
  assert.ok(fresh.historyEvicted > 0 && fresh.activeDropped === 0);
  assert.ok(fresh.prefs.has(key(900)) && !fresh.prefs.has(key(0, 'task-1', `m-0-${long}`)) && fresh.prefs.has(key(79, 'task-1', `m-79-${long}`)));
});

test('an entry that breaks its bounds is refused for that entry only, and reported; nothing else is lost', () => {
  const refused: string[] = [];
  const next = mergeSeatPrefs(
    new Map([[key(1), 's1']]),
    new Map([
      [key(2), 's2'],
      [key(3, 'x'.repeat(1025)), 's3'], // over the catalog text bound
      [key(4, 'a\u0000b'), 's4'], // NUL
      [key(5), 'q'.repeat(129)], // over the layout id bound
      ['not json', 's6'],
    ]),
    (why) => refused.push(why),
  );
  assert.deepEqual([...next.keys()], [key(1), key(2)]);
  assert.equal(refused.length, 4);
  assert.ok(refused.some((r) => /taskId/.test(r)) && refused.some((r) => /seat/.test(r)) && refused.some((r) => /identity tuple/.test(r)));
});

test('a key is exactly the identity: round-trips unchanged, and look-alikes stay distinct', () => {
  const long = 't'.repeat(1000);
  const keys = [key(1, long), key(1, 'Task'), key(1, 'task'), key(1, 'café'), key(1, 'café'), key(1, 'task '), key(1, null)];
  const prefs = mergeSeatPrefs(new Map(), new Map(keys.map((k, i) => [k, `s${i}`] as const)));
  assert.equal(prefs.size, keys.length, 'no two collapse into one');
  const back = seatPrefsFromRecord(JSON.parse(JSON.stringify(seatPrefsToRecord(prefs))))!;
  assert.deepEqual([...back], [...prefs]);
  for (const k of keys) assert.ok(back.has(k), 'matched exactly');
  assert.ok(JSON.parse(keys[0])[3].length === 1000, 'never truncated');
});

test('a record from the server: valid entries are applied, a bad one is refused for that entry only, a non-record is ignored whole', () => {
  const good = { i: ['direct', 'syn', 'syn:mn-1', null, 'm1'], s: 'seat-1' };
  const bad = { i: ['direct', 'syn', 'syn:mn-2', 'a\u0000b', 'm2'], s: 'seat-2' };
  const refused: string[] = [];
  const prefs = seatPrefsFromRecord({ v: 1, entries: [good, bad] }, (why) => refused.push(why))!;
  assert.deepEqual([...prefs], [[JSON.stringify(good.i), 'seat-1']]);
  assert.equal(refused.length, 1);
  assert.equal(seatPrefsFromRecord('x'), null);
  assert.equal(seatPrefsFromRecord({ v: 9, entries: [] }), null);
});
