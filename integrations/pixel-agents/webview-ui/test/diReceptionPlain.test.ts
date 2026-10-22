/**
 * The reception inbox's plain first layer: what happened, why it needs a person, the next supported
 * step, what could not be checked, and search/filter — as pure functions over the SAME office states
 * the real server projected into the packaged fixtures. Nothing here asserts a stand-in: every reason
 * code, cause id and route text below is one the projection actually emits.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

import type {
  DiOfficeState,
  DiReception,
  DiReceptionEntry,
} from '../../core/src/messages.ts';
import { plainCategoryHeadline } from '../src/components/diReceptionFormat.ts';
import {
  categoryChips,
  entryGoal,
  filterEntries,
  INFORMATION_HOLD_LABEL,
  notReportedLabels,
  PLAIN_CAUSES,
  PLAIN_HOLDS,
  plainCauses,
  plainReason,
  plainReasons,
  plainWhy,
  routeAction,
  uncertaintyLines,
} from '../src/components/diReceptionPlain.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixtureDir = path.join(root, 'test/fixtures');

/** Every reception the packaged fixtures carry (each state of every fixture). */
const receptions: DiReception[] = [];
for (const f of fs.readdirSync(fixtureDir)) {
  const doc = JSON.parse(fs.readFileSync(path.join(fixtureDir, f), 'utf-8')) as {
    state?: DiOfficeState;
    states?: Record<string, DiOfficeState>;
  };
  for (const s of doc.states ? Object.values(doc.states) : [doc.state!])
    if (s?.reception) receptions.push(s.reception);
}
const mismatch = (
  JSON.parse(
    fs.readFileSync(path.join(fixtureDir, 'di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8'),
  ) as { state: DiOfficeState }
).state.reception!;
const allEntries = receptions.flatMap((r) => r.entries);
const FORBIDDEN = /can[’']t say/i;

test('every cause the server emits has a plain sentence pair, and none is stale', () => {
  const source = fs.readFileSync(path.join(root, '..', 'server/src/di/receptionDesk.ts'), 'utf-8');
  const emitted = new Set([...source.matchAll(/cause: '([a-z_]+)'/g)].map((m) => m[1]));
  assert.ok(emitted.size >= 15, `read the server's cause ids (${emitted.size})`);
  assert.deepEqual([...emitted].sort(), Object.keys(PLAIN_CAUSES).sort());
  for (const [id, c] of Object.entries(PLAIN_CAUSES)) {
    assert.ok(c.happened.length > 10 && c.why.length > 10, id);
    assert.doesNotMatch(c.happened + c.why, FORBIDDEN, id);
  }
});

test('an entry says what happened and why, once each; an unknown cause is shown, never dropped', () => {
  const blockers = mismatch.entries.find((e) => e.category === 'blockers')!;
  assert.deepEqual(
    plainCauses(blockers).map((c) => c.happened),
    blockers.causes.map((c) => PLAIN_CAUSES[c.cause].happened),
  );
  const future: DiReceptionEntry = {
    ...blockers,
    causes: [
      { cause: 'a_future_cause', label: 'A newer producer’s own words', details: [], staleReasons: [] },
      ...blockers.causes,
      ...blockers.causes, // the same cause twice: its reason is said once
    ],
  };
  assert.equal(plainCauses(future)[0].happened, 'A newer producer’s own words');
  assert.match(plainCauses(future)[0].why, /a person should look at/);
  assert.equal(plainWhy(future).length, new Set(plainWhy(future)).size, 'each reason once');
  assert.ok(plainWhy(future).length >= 2);
});

test('the next step is the projection’s own route, minus the read-only sentence said once at the top', () => {
  assert.ok(allEntries.length > 0, 'the fixtures carry entries');
  for (const e of allEntries) {
    const next = routeAction(e.route);
    assert.doesNotMatch(next, /read-only|hiding an entry/i, e.category);
    assert.match(next, /^Outside this office:/, e.category);
    assert.ok(e.route.startsWith(next.replace(/\.$/, '')), 'a prefix of the full route, nothing added');
  }
  assert.equal(routeAction('Act through the operator.'), 'Act through the operator.');
  assert.equal(routeAction(''), '');
});

test('reason codes the projection emits become plain words, never "can’t say", never guessed', () => {
  const reasons = new Set<string>();
  for (const r of receptions)
    for (const c of r.categories) {
      if (c.reason) reasons.add(c.reason);
      for (const g of c.gaps) reasons.add(g.reason);
    }
  assert.ok(reasons.size >= 6, `real reasons collected: ${[...reasons].join(' | ')}`);
  for (const reason of reasons) {
    const plain = plainReason(reason);
    assert.doesNotMatch(plain, FORBIDDEN, reason);
    assert.doesNotMatch(plain, /[a-z]+_[a-z]+/, `no raw code in: ${plain}`);
  }
  const expected: Record<string, RegExp> = {
    'pending_decisions:source_kind_has_no_mission_state': /carries no Mission Core state/,
    'mission_core_blockers:source_kind_has_no_mission_state': /carries no Mission Core state/,
    'completion:source_kind_has_no_mission_state': /carries no Mission Core state/,
    'member_activity:projected_reason:binding_not_applicable:mission_not_active': /not active/,
    'member_activity:projected_reason:binding_not_applicable:covered_by_canonical_record': /canonical record already answers/,
    'member_activity:unavailable:binding_unverified:task_record_not_corroborated_by_a_live_session': /no live session confirms/,
    'not_in_catalog_contract:v1_and_v2': /no source reports it/,
    'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks': /older contract version 1/,
    'canonical_review_unavailable:read_failed': /could not be read/,
    'completion_holds:truncated_by_display_cap': /display limit/,
    'completion:contradicted_not_recorded': /does not say/,
  };
  for (const [reason, re] of Object.entries(expected)) assert.match(plainReason(reason), re, reason);
  assert.equal(plainReason('something_we_never_saw'), 'its record does not say', 'unknown → says so');
});

test('what could not be checked is stated per category in plain words; clarification is "not reported" and delivery proposals are a source of their own', () => {
  const lines = uncertaintyLines(mismatch);
  assert.ok(lines.length > 0);
  for (const l of lines) {
    assert.doesNotMatch(l, FORBIDDEN);
    assert.match(l, /couldn’t be checked/);
    assert.doesNotMatch(l, /[a-z]+_[a-z]+/, `no raw reason code in: ${l}`);
  }
  assert.ok(lines.some((l) => /^Blockers: /.test(l)));
  // The catalog still reports no clarification. Delivery proposals are no longer a category the catalog "does
  // not carry": a catalog answer that predates the block reads "not reported by this catalog" for that SOURCE
  // (its own reason, its own coverage line), not as a missing kind of the whole contract.
  assert.deepEqual(notReportedLabels(mismatch), ['Clarification']);
  const delivery = mismatch.categories.find((c) => c.category === 'delivery_decision')!;
  assert.equal(delivery.outcome, 'unsupported');
  assert.equal(delivery.reason, 'catalog_does_not_report:delivery_presentations');
  assert.equal(
    mismatch.sourceCoverage?.find((c) => c.sourceId === 'grok_delivery_presentations')?.outcome,
    'unsupported',
  );
  // a not-reported category is not also an "unchecked" line
  assert.ok(!lines.some((l) => /^(Clarification|Delivery)/.test(l)));
});

test('at contract v1 a v2-only category says it needs the newer contract version', () => {
  const v1: DiReception = {
    ...mismatch,
    contractVersion: 1,
    categories: mismatch.categories.map((c) =>
      c.category === 'failed_verification'
        ? {
            ...c,
            outcome: 'unsupported',
            reason: 'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks',
            gaps: [],
          }
        : c,
    ),
  };
  const line = uncertaintyLines(v1).find((l) => l.startsWith('Failed verification'))!;
  assert.match(line, /older contract version 1/);
  assert.doesNotMatch(line, FORBIDDEN);
});

test('the category headline never says "can’t say"; a category with unchecked Missions says so plainly', () => {
  for (const r of receptions)
    for (const c of r.categories) assert.doesNotMatch(plainCategoryHeadline(c), FORBIDDEN, c.category);
  const withGaps = receptions
    .flatMap((r) => r.categories)
    .find((c) => c.outcome === 'present' && c.counts.unsupported + c.counts.unavailable > 0);
  if (withGaps) assert.match(plainCategoryHeadline(withGaps), /couldn’t be checked/);
});

test('search matches the category, the goal, the ids and the causes; every word must match', () => {
  const labelOf = (e: DiReceptionEntry) =>
    mismatch.categories.find((c) => c.category === e.category)?.label ?? e.category;
  const [e] = mismatch.entries;
  const all = mismatch.entries;
  const f = (query: string, category: DiReceptionEntry['category'] | null = null) =>
    filterEntries(all, { query, category }, labelOf);
  assert.equal(f('').length, all.length);
  assert.equal(f(labelOf(e).toUpperCase()).length >= 1, true, 'case-insensitive category label');
  assert.equal(f(e.nativeId).length >= 1, true, 'native id');
  assert.equal(f(entryGoal(e).split(/\s+/)[0]).length >= 1, true, 'a word of the goal');
  assert.equal(f('blockers zzz-no-such-word').length, 0, 'every word must match');
  assert.equal(f('', 'blockers').every((x) => x.category === 'blockers'), true);
  assert.equal(f('', 'ready_for_review').length, 0);
  assert.equal(f(e.nativeId, 'ready_for_review').length, 0, 'filters combine');
});

test('category chips count only what is in view and omit empty categories', () => {
  const reception: DiReception = {
    ...mismatch,
    entries: mismatch.entries,
  };
  const chips = categoryChips(reception, reception.entries);
  assert.ok(chips.every((c) => c.count > 0));
  assert.equal(chips.reduce((n, c) => n + c.count, 0), reception.entries.length);
  assert.deepEqual(categoryChips(reception, []), []);
});

// ── hold codes and reported reasons (the copy fix) ────────────────────────────────────────────

/** An entry carrying exactly these cause details (a labelled synthetic entry for a mapping case). */
const withDetails = (
  cause: string,
  details: Array<{ label: string; value: string | null }>,
): DiReceptionEntry => ({
  ...mismatch.entries[0],
  causes: [{ cause, label: 'a cause label', details, staleReasons: [] }],
});

test('PLAIN_HOLDS covers every hold code Mission Core defines, and nothing it does not', () => {
  const source = fs.readFileSync(path.join(root, '..', '..', '..', 'mission/observation.py'), 'utf-8');
  const defined = [...source.matchAll(/^HOLD_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(defined.length >= 15, `read Mission Core's hold codes (${defined.length})`);
  assert.deepEqual([...defined].sort(), Object.keys(PLAIN_HOLDS).sort());
  for (const [code, text] of Object.entries(PLAIN_HOLDS)) {
    assert.ok(text.length > 10, code);
    assert.doesNotMatch(text, /[a-z]+_[a-z]+/, `${code}: no raw code in its plain words`);
    assert.doesNotMatch(text, FORBIDDEN, code);
  }
});

test('the information-hold label is the one the server emits', () => {
  const source = fs.readFileSync(path.join(root, '..', 'server/src/di/receptionDesk.ts'), 'utf-8');
  const label = /const INFORMATION_HOLD_LABEL =\s*'([^']+)'/.exec(source)?.[1];
  assert.equal(INFORMATION_HOLD_LABEL, label);
});

test('a hold code reads in plain words; an unmapped code reads "reported reason: <code>", never a guess', () => {
  const mapped = withDetails('completion_holds', [
    { label: 'hold', value: 'evidence_contradicted' },
    { label: 'hold', value: 'candidate_drifted' },
    { label: 'hold', value: 'no_canonical_closure' },
    { label: 'hold', value: 'proof_not_satisfied' },
    { label: 'hold', value: 'review_not_approved' },
  ]);
  assert.deepEqual(plainReasons(mapped), [
    PLAIN_HOLDS['evidence_contradicted'],
    PLAIN_HOLDS['candidate_drifted'],
    PLAIN_HOLDS['no_canonical_closure'],
    PLAIN_HOLDS['proof_not_satisfied'],
    PLAIN_HOLDS['review_not_approved'],
  ]);
  const unmapped = plainReasons(
    withDetails('completion_holds', [{ label: 'hold', value: 'a_hold_from_a_newer_core' }]),
  );
  assert.deepEqual(unmapped, ['reported reason: a_hold_from_a_newer_core']);
  // an information hold is said to be information only
  const info = plainReasons(
    withDetails('completion_contradicted', [
      { label: INFORMATION_HOLD_LABEL, value: 'review_unavailable' },
    ]),
  );
  assert.deepEqual(info, [
    `${PLAIN_HOLDS['review_unavailable']} (information only: it does not block by itself)`,
  ]);
  // an unmapped INFORMATION hold keeps the fallback and the information-only note
  assert.deepEqual(
    plainReasons(withDetails('completion_holds', [{ label: INFORMATION_HOLD_LABEL, value: 'zzz_new' }])),
    ['reported reason: zzz_new (information only: it does not block by itself)'],
  );
});

test('a reported blocker, a hard blocker and a blocked member are said from the cause details only', () => {
  assert.deepEqual(
    plainReasons(
      withDetails('mission_core_blockers', [
        { label: 'hard blocker active', value: 'true' },
        { label: 'active blockers', value: '2' },
        { label: 'blocker', value: 'waiting on the signing key' },
      ]),
    ),
    [PLAIN_HOLDS['hard_blocker_active'], 'Reported blocker: waiting on the signing key'],
  );
  // a hard blocker that is not active adds nothing
  assert.deepEqual(
    plainReasons(withDetails('mission_core_blockers', [{ label: 'hard blocker active', value: 'false' }])),
    [],
  );
  assert.deepEqual(
    plainReasons(
      withDetails('member_activity_blocked', [
        { label: 'member', value: 'ref-1 · lead (lead) · claude · observed_running (fresh)' },
      ]),
    ),
    ['lead (lead) reports it is blocked.'],
  );
  // a member line in a shape the mapping does not know is quoted, never reinterpreted
  assert.deepEqual(
    plainReasons(withDetails('member_activity_blocked', [{ label: 'member', value: 'odd shape' }])),
    ['Member reporting blocked activity: odd shape'],
  );
  // missing values say nothing; the same reason from two causes is said once
  assert.deepEqual(plainReasons(withDetails('completion_holds', [{ label: 'hold', value: null }])), []);
  const twice: DiReceptionEntry = {
    ...mismatch.entries[0],
    causes: [
      ...withDetails('completion_holds', [{ label: 'hold', value: 'proof_not_satisfied' }]).causes,
      ...withDetails('completion_contradicted', [{ label: 'hold', value: 'proof_not_satisfied' }]).causes,
    ],
  };
  assert.equal(plainReasons(twice).length, 1);
});

test('a reason already said by the cause sentence is not repeated', () => {
  // the evidence_contradicted code reads the same as the evidence_contradicted_hold cause sentence
  const entry = withDetails('evidence_contradicted_hold', [
    { label: 'hold', value: 'evidence_contradicted' },
  ]);
  assert.deepEqual(plainReasons(entry), [], 'no repeat of the happened line');
  assert.equal(plainCauses(entry)[0].happened, PLAIN_HOLDS['evidence_contradicted']);
});

test('every real fixture entry has first-layer words free of raw codes and phase literals', () => {
  assert.ok(allEntries.length > 0);
  for (const e of allEntries) {
    const lines = [
      ...plainCauses(e).flatMap((c) => [c.happened, c.why]),
      ...plainReasons(e).filter((r) => !r.startsWith('reported reason:') && !r.startsWith('Reported blocker:')),
      routeAction(e.route),
    ];
    for (const line of lines) {
      assert.doesNotMatch(line, /[A-Z]{3,}_[A-Z_]+/, `no raw phase literal: ${line}`);
      assert.doesNotMatch(line, /\b[a-z]+_[a-z_]+\b/, `no raw code: ${line}`);
    }
  }
});

test('reauthorization and a blocked workflow lead with their plain meaning; the literal phase is only in the details', () => {
  const reauth = PLAIN_CAUSES['workflow_needs_reauthorization'];
  assert.match(reauth.happened, /fresh authorization before it can continue/);
  const blocked = PLAIN_CAUSES['workflow_phase_blocked'];
  assert.match(blocked.happened, /blocked and has stopped/);
  for (const c of Object.values(PLAIN_CAUSES))
    assert.doesNotMatch(c.happened + ' ' + c.why, /NEEDS_REAUTHORIZATION|\bBLOCKED\b/);
  // the literal is still carried by the cause's own detail rows (the disclosure shows them)
  const entry = withDetails('workflow_needs_reauthorization', [
    { label: 'workflow phase', value: 'NEEDS_REAUTHORIZATION' },
  ]);
  assert.deepEqual(plainReasons(entry), [], 'the phase literal is not promoted to the first layer');
  assert.equal(entry.causes[0].details[0].value, 'NEEDS_REAUTHORIZATION');
});
