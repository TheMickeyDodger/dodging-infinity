/**
 * Item 2 (evidence library): the webview's plain wording and selection rules, pure. States stay
 * distinct, every refusal code either side can send has its own words (no silent default), and an
 * answer is shown only against the exact listing it was read at.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

import type { DiEvidenceContent, DiEvidenceLibrary } from '../../core/src/messages.ts';
import {
  answersSelection,
  contentStillCurrent,
  DI_EVIDENCE_RENDER_MAX_CHARS,
  formatEvidenceSection,
  plainAdmissionBasis,
  plainExecution,
  plainExecutionBasis,
  plainLinkCount,
  plainRefusal,
  renderedText,
} from '../src/components/diEvidenceFormat.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function codes(text: string, start: string, end: string): string[] {
  const block = text.split(start)[1].split(end)[0];
  return [...block.matchAll(/'([a-z0-9_]+)'/g)].map((m) => m[1]);
}

test('every refusal code the office or the backend can send has its own plain words', () => {
  const source = fs.readFileSync(path.join(root, 'server/src/di/evidenceContent.ts'), 'utf-8');
  const backend = codes(
    source,
    '// BEGIN ARTIFACT_CONTENT_PROBLEMS',
    '// END ARTIFACT_CONTENT_PROBLEMS',
  );
  const office = codes(source, 'export const OFFICE_EVIDENCE_PROBLEMS = [', '] as const;');
  assert.ok(backend.length > 30 && office.length > 10);
  for (const code of [...backend, ...office]) {
    const words = plainRefusal(code);
    assert.ok(words.endsWith(`(${code})`), code);
    assert.notEqual(words, `refused (${code})`, `no plain words for ${code}`);
  }
});

const section = (state: string, extra: Record<string, unknown> = {}) =>
  ({ state, reason: null, total: null, items: [], ...extra }) as never;

test('the six section states never collapse into one another', () => {
  const words = [
    formatEvidenceSection(section('observed', { total: 2, items: [{}, {}] })),
    formatEvidenceSection(section('known_empty', { total: 0 })),
    formatEvidenceSection(section('not_recorded')),
    formatEvidenceSection(
      section('not_applicable', { reason: 'source_kind_has_no_mission_core_evidence' }),
    ),
    formatEvidenceSection(section('unavailable', { reason: 'mission_state_source_unavailable' })),
    formatEvidenceSection(section('refused', { reason: 'review_decision_disagrees' })),
  ];
  assert.equal(new Set(words).size, words.length);
  assert.equal(words[1], 'none recorded');
  assert.equal(words[2], 'not reported by the producer');
  assert.match(words[3], /not supported/);
  assert.match(words[5], /refused.*review_decision_disagrees/);
});

const content = {
  type: 'diEvidenceContent',
  missionRef: 'mc1:mn-' + 'a'.repeat(32),
  artifactId: 'mf-' + 'b'.repeat(32),
  cursor: 'c'.repeat(64),
  state: 'admitted',
  decidedBy: 'backend',
  problem: null,
  detail: null,
  artifact: null,
  content: null,
  observedAt: 1,
} as DiEvidenceContent;

test('an answer belongs to its exact reference and is shown only at that listing', () => {
  const selection = {
    missionRef: content.missionRef,
    artifactId: content.artifactId,
    cursor: content.cursor,
  };
  assert.equal(answersSelection(content, selection), true);
  assert.equal(answersSelection(content, { ...selection, cursor: 'd'.repeat(64) }), false);
  assert.equal(
    answersSelection(content, { ...selection, artifactId: 'mf-' + 'e'.repeat(32) }),
    false,
  );
  assert.equal(answersSelection(null, selection), false);
  const library = { cursor: content.cursor, currency: 'current' } as DiEvidenceLibrary;
  assert.deepEqual(contentStillCurrent(content, library), { current: true });
  const moved = contentStillCurrent(content, { ...library, cursor: 'f'.repeat(64) });
  assert.equal(moved.current, false);
  const stale = contentStillCurrent(content, { ...library, currency: 'stale' });
  assert.equal(stale.current, false);
});

test('rendered text is bounded and says so', () => {
  assert.deepEqual(renderedText('abc'), { text: 'abc', truncated: false });
  const big = renderedText('x'.repeat(DI_EVIDENCE_RENDER_MAX_CHARS + 5));
  assert.equal(big.text.length, DI_EVIDENCE_RENDER_MAX_CHARS);
  assert.equal(big.truncated, true);
});

test('the admission basis is said in words', () => {
  assert.equal(
    plainAdmissionBasis(['accepted_evidence_artifact', 'contract_required_artifact']),
    'named by an accepted verification record and a deliverable the approved contract pins',
  );
});

test('round 15 F4: a link count leads with the TRUE total and never hides a cap', () => {
  assert.equal(plainLinkCount(1, 1), 'names 1 recorded link:');
  assert.equal(plainLinkCount(64, 64), 'names 64 recorded links:');
  assert.equal(
    plainLinkCount(64, 70),
    'names 70 recorded links — showing the first 64, 6 not shown (display cap):',
  );
});

test('item 2: the recorded execution has distinct words, apart from freshness', () => {
  const base = { identityProblem: null, lifecycleState: 'COMPLETED', taskId: 't-1' };
  const words = [
    plainExecution({ ...base, basis: 'active_binding', identityState: 'not_applicable' }),
    plainExecution({
      ...base,
      basis: 'historical_completed_execution',
      identityState: 'established',
    }),
    plainExecution({
      ...base,
      basis: 'none',
      identityState: 'not_established',
      identityProblem: 'execution_prepared_binding_absent',
    }),
    plainExecution({ ...base, basis: 'none', identityState: 'not_reported' }),
    plainExecution({ ...base, basis: 'none', identityState: 'unexpected' }),
    plainExecution({ ...base, basis: 'none', identityState: 'not_applicable' }),
  ];
  assert.equal(new Set(words).size, words.length);
  assert.match(words[1], /HISTORICAL recorded execution/);
  assert.match(words[1], /No live team/);
  assert.match(words[1], /task t-1/);
  assert.match(words[2], /never upgraded/);
  assert.doesNotMatch(words.join(' '), /stale|fresh|as last received/i);
  assert.match(plainExecutionBasis('historical_completed_execution'), /not a live team/);
});
