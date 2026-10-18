/**
 * The Mission reception desk (item 06) as the webview renders it: each outcome distinct (words and
 * shape, not colour alone), keyboard reachability and visible focus, the list itself as the
 * non-spatial alternative, dismissal as a LOCAL presentation preference, the declared cap, and no
 * control of any kind. The desk data is projected server-side; nothing here derives attention.
 */
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'vitest';

import type {
  DiOfficeState,
  DiReception,
  DiReceptionCategory,
  DiReceptionEntry,
  DiReceptionOutcome,
} from '../../core/src/messages.ts';
import { withClientInvalidation } from '../src/components/diOfficeFormat.ts';
import { DiReceptionDesk } from '../src/components/DiReceptionDesk.tsx';
import {
  formatCategoryHeadline,
  formatEntryCurrency,
  formatTruncation,
  hideKey,
  invalidateReception,
  nextEntryIndex,
  visibleEntries,
} from '../src/components/diReceptionFormat.ts';

const category = (over: Partial<DiReceptionCategory> = {}): DiReceptionCategory => ({
  category: 'blockers',
  label: 'Blockers',
  statement: 'statement',
  backing: 'backing',
  outcome: 'known_empty',
  reason: null,
  counts: { present: 0, knownEmpty: 1, unsupported: 0, unavailable: 0 },
  gaps: [],
  entriesTotal: 0,
  ...over,
});

const entry = (over: Partial<DiReceptionEntry> = {}): DiReceptionEntry => ({
  key: JSON.stringify(['authorization', 'src-a', 'mn-1']),
  signature: '[["awaiting_decision"]]',
  category: 'authorization',
  missionRef: 'src-a:mn-1',
  sourceId: 'src-a',
  nativeId: 'mn-1',
  sourceKind: 'mission_core',
  producerDeclared: 'producer-a',
  objective: { state: 'observed', value: 'Ship the synthetic thing', reason: null },
  lifecycleModel: 'mission_core.mission_state/1',
  lifecycleState: 'AWAITING_DECISION',
  standing: 'inactive',
  bindingState: 'not_applicable',
  bindingReason: 'mission_not_active',
  live: false,
  notLiveReason: 'standing_inactive',
  occurrences: 1,
  identityConflict: false,
  causes: [
    {
      cause: 'awaiting_decision',
      label: 'Mission Core: this Mission awaits a human decision',
      details: [{ label: 'revision', value: '1' }],
      staleReasons: [],
    },
  ],
  alsoUnknown: [],
  current: true,
  route:
    'Outside this office: a decision is made through the existing route. This desk is read-only.',
  routeIdentifiers: [{ label: 'mission_ref', value: 'src-a:mn-1' }],
  ...over,
});

const reception = (over: Partial<DiReception> = {}): DiReception => ({
  contractVersion: 2,
  categories: [
    category({ category: 'blockers', label: 'Blockers' }),
    category({
      category: 'clarification',
      label: 'Clarification',
      outcome: 'unsupported',
      reason: 'not_in_catalog_contract:v1_and_v2',
      statement: 'Not reported at contract version 1 or 2.',
    }),
    category({
      category: 'authorization',
      label: 'Authorization / reauthorization',
      outcome: 'present',
      entriesTotal: 1,
    }),
  ],
  entries: [entry()],
  entriesTotal: 1,
  entriesCap: 24,
  notices: [],
  minPollIntervalSeconds: 5,
  refreshStatement: 'Refreshed with the office feed (no polling of its own).',
  dismissalStatement: 'Hiding an entry is a preference of this view only: it writes nothing.',
  ...over,
});

function render(r: DiReception, hidden: ReadonlySet<string> = new Set()): string {
  return renderToStaticMarkup(
    createElement(DiReceptionDesk, {
      reception: r,
      hidden,
      onHide: () => {},
      onShowHidden: () => {},
      onJump: () => {},
    }),
  );
}

const OUTCOMES: DiReceptionOutcome[] = [
  'present',
  'known_empty',
  'unsupported',
  'unavailable',
  'partial',
];

test('every outcome reads distinctly, with a shape as well as words', () => {
  const lines = OUTCOMES.map((outcome) =>
    formatCategoryHeadline(
      category({
        outcome,
        reason: outcome === 'unsupported' || outcome === 'unavailable' ? 'why_not' : null,
        counts: { present: 1, knownEmpty: 1, unsupported: 1, unavailable: 1 },
        entriesTotal: 1,
      }),
    ),
  );
  assert.equal(new Set(lines).size, OUTCOMES.length);
  // A distinct leading shape per outcome: status never rests on colour alone.
  assert.equal(new Set(lines.map((l) => l[0])).size, OUTCOMES.length);
});

test('only known_empty says "none"; unsupported, unavailable and partial never read as none or clear', () => {
  const line = (outcome: DiReceptionOutcome, reason: string | null = 'why_not') =>
    formatCategoryHeadline(category({ outcome, reason }));
  assert.match(line('known_empty', null), /none reported \(read succeeded\)/);
  for (const o of ['unsupported', 'unavailable'] as const) {
    assert.doesNotMatch(line(o), /none|clear|healthy|\b0\b/i);
    assert.match(line(o), /why_not/);
  }
  assert.match(line('unsupported'), /NOT SUPPORTED/);
  assert.match(line('unavailable'), /UNKNOWN/);
  assert.match(line('partial', null), /NOT FULLY KNOWN/);
});

test('an unsupported category shows its reason and its contract-version statement', () => {
  const html = render(reception());
  assert.match(html, /not_in_catalog_contract:v1_and_v2/);
  assert.match(html, /Not reported at contract version 1 or 2\./);
});

test('the desk is a keyboard list: a labelled region, focusable entries, real buttons, visible focus', () => {
  const html = render(
    reception({ entries: [entry(), entry({ key: 'k2', missionRef: 'src-b:mn-1' })] }),
  );
  assert.match(html, /<section[^>]*aria-labelledby="di-reception-heading"/);
  assert.match(html, /<h2 id="di-reception-heading"/);
  assert.equal((html.match(/<li[^>]*tabindex="0"/g) ?? []).length, 2);
  assert.equal((html.match(/<button type="button"/g) ?? []).length, 4);
  assert.match(html, /focus-visible:outline/);
  assert.match(html, /aria-keyshortcuts="ArrowUp ArrowDown Home End"/);
});

test('arrow keys move through entries, Home and End jump, and focus never leaves either end', () => {
  assert.equal(nextEntryIndex(0, 'ArrowDown', 3), 1);
  assert.equal(nextEntryIndex(2, 'ArrowDown', 3), 2);
  assert.equal(nextEntryIndex(0, 'ArrowUp', 3), 0);
  assert.equal(nextEntryIndex(1, 'Home', 3), 0);
  assert.equal(nextEntryIndex(0, 'End', 3), 2);
  assert.equal(nextEntryIndex(0, 'Enter', 3), null);
  assert.equal(nextEntryIndex(0, 'ArrowDown', 0), null);
});

test('no control can approve, resolve, retry, dispatch or deliver — and nothing links out', () => {
  const html = render(reception());
  const buttons = [...html.matchAll(/<button type="button"[^>]*>([^<]*)</g)].map((m) => m[1]);
  assert.deepEqual(buttons, ['Show this Mission', 'Hide in this view']);
  assert.doesNotMatch(buttons.join(' '), /approve|resolve|retry|dispatch|deliver|authori[sz]e/i);
  assert.doesNotMatch(html, /<a\s|href=/);
});

test('dismissal is presentation only: hidden here, labelled so, and back when the record changes', () => {
  const e = entry();
  const hidden = new Set([hideKey(e)]);
  assert.deepEqual(visibleEntries([e], hidden), { visible: [], hidden: 1 });
  // Same canonical identity, NEW canonical content: it is a different notice and reappears.
  const changed = entry({ signature: '[["awaiting_decision","revision 2"]]' });
  assert.deepEqual(visibleEntries([changed], hidden), { visible: [changed], hidden: 0 });
  const html = render(reception(), hidden);
  assert.match(html, /1 hidden in this view only/);
  assert.match(html, /preference of this view only/);
  // A fresh view (a reload) starts with nothing hidden.
  assert.match(render(reception()), /Ship the synthetic thing/);
});

test('dismissal writes nothing anywhere: no transport, no storage, no client message', () => {
  for (const file of ['DiReceptionDesk.tsx', 'diReceptionFormat.ts']) {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'components', file), 'utf-8');
    assert.doesNotMatch(
      src,
      /transport|postMessage|localStorage|sessionStorage|indexedDB|\.send\(/,
    );
  }
});

test('the declared cap is disclosed when the list is truncated', () => {
  const truncated = reception({ entriesTotal: 31, entriesCap: 24 });
  assert.equal(formatTruncation(truncated), 'showing 1 of 31 entries (display cap 24)');
  assert.equal(formatTruncation(reception()), null);
  assert.match(render(truncated), /showing 1 of 31 entries \(display cap 24\)/);
});

test('a stale entry is never shown as current', () => {
  const stale = entry({
    current: false,
    causes: [
      {
        ...entry().causes[0],
        staleReasons: ['envelope_not_fresh:expired_locally:response_expired'],
      },
    ],
  });
  assert.equal(formatEntryCurrency(entry()), 'current');
  assert.equal(
    formatEntryCurrency(stale),
    'STALE: envelope_not_fresh:expired_locally:response_expired',
  );
  assert.match(render(reception({ entries: [stale] })), /STALE: envelope_not_fresh/);
});

test('a client that cannot vouch invalidates the desk: nothing current, no "none"', () => {
  const r = invalidateReception(reception(), 'transport_disconnected');
  assert.equal(r.entries[0].current, false);
  assert.ok(r.entries[0].causes[0].staleReasons.includes('client:transport_disconnected'));
  const blockers = r.categories.find((c) => c.category === 'blockers')!;
  assert.equal(blockers.outcome, 'unavailable');
  assert.equal(blockers.reason, 'client:transport_disconnected');
  const state = { reception: reception(), envelope: {}, runtime: {}, missions: { missions: [] } };
  const presented = withClientInvalidation(
    state as unknown as DiOfficeState,
    'transport_disconnected',
  );
  assert.equal(presented.reception?.entries[0].current, false);
});

test('identity is source-qualified: identical objectives stay two entries with their full refs', () => {
  const html = render(
    reception({
      entries: [
        entry(),
        entry({
          key: 'k2',
          sourceId: 'src-b',
          missionRef: 'src-b:mn-1',
          producerDeclared: 'producer-b',
        }),
      ],
    }),
  );
  assert.equal((html.match(/Ship the synthetic thing/g) ?? []).length, 2);
  assert.match(html, /src-a:mn-1/);
  assert.match(html, /src-b:mn-1/);
  assert.match(html, /producer-b/);
});

test('binding state and reason read verbatim, and duplicates and identity conflicts say so', () => {
  const html = render(
    reception({
      entries: [
        entry({ bindingState: 'unbound', bindingReason: 'run_not_started', occurrences: 2 }),
        entry({ key: 'k3', identityConflict: true }),
      ],
    }),
  );
  assert.match(html, /binding unbound \(run_not_started\)/);
  assert.match(html, /reported 2 times \(one canonical record\)/);
  assert.match(html, /another record shares this identity with different content/);
});

test('the desk adds no motion: nothing for the reduced-motion seam to suppress', () => {
  assert.doesNotMatch(render(reception()), /animate-|transition/);
});

test('an office state WITHOUT the optional desk is invalidated without inventing one', () => {
  const state = { envelope: {}, runtime: {}, missions: { missions: [] } };
  const presented = withClientInvalidation(
    state as unknown as DiOfficeState,
    'transport_disconnected',
  );
  assert.equal('reception' in presented, false);
});

test('in a FIXTURE preview every LIVE on the desk is qualified — never a bare LIVE over fixture data', () => {
  const live = reception({ entries: [entry({ live: true, notLiveReason: null })] });
  const fixtureHtml = renderToStaticMarkup(
    createElement(DiReceptionDesk, {
      reception: live,
      hidden: new Set<string>(),
      onHide: () => {},
      onShowHidden: () => {},
      onJump: () => {},
      fixture: true,
    }),
  );
  assert.match(fixtureHtml, /LIVE - FIXTURE DATA/);
  assert.doesNotMatch(fixtureHtml, /· LIVE</);
  assert.match(render(live), /· LIVE</);
});

test('the focus outline is drawn INSIDE each focusable element, so the scrolling entry list cannot clip it', () => {
  const html = render(reception());
  const entryClass =
    /<li[^>]*tabindex="0"[^>]*class="([^"]*)"|<li[^>]*class="([^"]*)"[^>]*tabindex="0"/.exec(html);
  const cls = entryClass?.[1] ?? entryClass?.[2] ?? '';
  assert.match(cls, /focus-visible:-outline-offset-2/);
  assert.doesNotMatch(html, /focus-visible:outline-offset-2/);
});
