/**
 * The FINAL COPY of the reception inbox and the request panel (integrated Phase 2), asserted on the real functions and the real components rendered to
 * static markup — never on a stand-in sentence:
 *   1. FIRST LAYER versus the technical disclosure, asserted separately: no blocker id/key, raw severity literal, request/conversation ref, stage literal,
 *      digest or reason code above the disclosure; the exact ids and codes INSIDE it. For a blocker, a merged decision, a delivery proposal and a clarification.
 *   2. NEXT STEP versus the button: one result decides both. The merged panel decision; and the negatives (panel not set up / not available / not read /
 *      unlinked / a kind of item with no panel route), each with truthful text and NO panel button.
 *   3. COUNTS: the request panel's own card, the inbox cards and the coverage line say the same number, defined once (`waitingOf`).
 *   4. TRANSCRIPT: the declared `mission-questions` read as questions, the exact reply behind a disclosure, legacy and unreadable replies unchanged.
 * (The rendered inbox in a real browser is `diReceptionSources.test.ts` / `diReceptionInbox.test.ts`.)
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState, DiReceptionEntry } from '../../core/src/messages.ts';
import { TurnView } from '../src/components/DiMissionRequest.tsx';
import type { MrOverview, MrState, MrTurn } from '../src/components/diMissionRequestModel.ts';
import {
  countsOf,
  entryWords,
  INITIAL_STATE,
  MR_DRAFTING_NOTE,
  MR_FIXTURE_SHORT,
  MR_FIXTURE_WORDS,
  parseConversation,
  parseOverview,
  replyWithoutFencedBlocks,
  replyWithoutQuestionsBlock,
  technicalBlockNote,
  waitingOf,
} from '../src/components/diMissionRequestModel.ts';
import { EntryDetail } from '../src/components/DiReceptionEntryDetail.tsx';
import { utcStamp } from '../src/components/diReceptionFormat.ts';
import { buildInbox, missionNextStep, plainDelivery } from '../src/components/diReceptionItems.ts';
import {
  blockerSentence,
  firstLayerHappened,
  firstLayerWhy,
  plainReasons,
  uncertaintyHeadline,
} from '../src/components/diReceptionPlain.ts';
import { routeTextOf } from './helpers/receptionRouteText.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const base = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { state: DiOfficeState }
).state.reception!;
const template = base.entries[0];

type Cause = DiReceptionEntry['causes'][number];
const cause = (id: string, label: string, details: Array<[string, string | null]>): Cause => ({
  cause: id,
  label,
  details: details.map(([l, value]) => ({ label: l, value })),
  staleReasons: [],
});
const entryOf = (
  causes: Cause[],
  category: DiReceptionEntry['category'] = 'blockers',
): DiReceptionEntry => ({ ...template, category, causes });

/** A category's real route text, read from the server projection's own source (string literals joined by +; see helpers/receptionRouteText.ts). */
const serverSource = fs.readFileSync(
  path.join(root, '..', 'server/src/di/receptionDesk.ts'),
  'utf-8',
);
const routeOf = (id: string): string => routeTextOf(serverSource, id);

const BLOCKER_ID = 'bl-7f3a2c9d41e8b605';
const BLOCKER_KEY = 'deploy-gate-credentials';
const OPENED = 1_789_000_000;
const record = (
  severity: string | null,
  opened: number | null = OPENED,
  key = BLOCKER_KEY,
  id = BLOCKER_ID,
) =>
  JSON.stringify({
    blocker_id: id,
    key,
    ...(severity === null ? {} : { severity }),
    ...(opened === null ? {} : { opened_at: opened }),
  });
const blockers = (items: string[], stated?: string) =>
  cause('mission_core_blockers', 'Mission Core: active blockers on this Mission', [
    ['hard blocker active', 'true'],
    ['active blockers', stated ?? String(items.length)],
    ...items.map((item): [string, string] => ['blocker', item]),
  ]);

/** Every word of the card's FIRST LAYER for a Mission entry (the sentences, the why and the next step). */
const firstLayerText = (e: DiReceptionEntry, next: string) => {
  const h = firstLayerHappened(e);
  return [...h.lines, firstLayerWhy(e), next].join(' ');
};
const FORBIDDEN_ABOVE =
  /bl-[0-9a-f]{6,}|deploy-gate|\bHARD\b|blocker_id|opened_at|\blr-|\bcv-|of_record|[0-9a-f]{24,}|[a-z]+_[a-z]+_[a-z_]+/;

describe('1a. a blocker: one source-backed sentence, no id, key or severity literal; the exact record inside the disclosure', () => {
  const single = entryOf([blockers([record('HARD')], '1')]);

  it('says what the source gave — how many, how severe (a word), when — in one sentence', () => {
    expect(blockerSentence(single)).toBe(
      `Mission Core reports an active hard blocker, opened ${utcStamp(OPENED)}.`,
    );
    expect(firstLayerHappened(single)).toEqual({
      lines: [`Mission Core reports an active hard blocker, opened ${utcStamp(OPENED)}.`],
      more: 0,
    });
  });

  it('carries NO identifier or literal above the disclosure', () => {
    expect(
      firstLayerText(single, missionNextStep(single, null, { kind: 'not_provided' }, false).text),
    ).not.toMatch(FORBIDDEN_ABOVE);
  });

  it('keeps the exact record — id, key, severity literal — inside the disclosure', () => {
    const inside = renderToStaticMarkup(
      createElement(EntryDetail, { entry: single, fixture: false }),
    ).replace(/&quot;/g, '"');
    expect(inside).toContain(BLOCKER_ID);
    expect(inside).toContain(BLOCKER_KEY);
    expect(inside).toContain('"severity":"HARD"');
    // and the card's own disclosure holds the plain-words reasons, with the key (a name the source gave) there too
    expect(plainReasons(single).join(' ')).toContain(BLOCKER_KEY);
  });

  it('says severity only where the source gave one, and never guesses it', () => {
    expect(blockerSentence(entryOf([blockers([record(null)], '1')]))).toBe(
      `Mission Core reports an active blocker, opened ${utcStamp(OPENED)}.`,
    );
    expect(blockerSentence(entryOf([blockers([record('SOFT')], '1')]))).toMatch(
      /an active soft blocker/,
    );
    // a severity that is not a plain word is not shown as one
    expect(blockerSentence(entryOf([blockers([record('P0:critical!')], '1')]))).toBe(
      `Mission Core reports an active blocker, opened ${utcStamp(OPENED)}.`,
    );
  });

  it('says how many when there are several, and a severity only when every one agrees', () => {
    expect(
      blockerSentence(
        entryOf([
          blockers([record('HARD'), record('HARD', OPENED, 'other', 'bl-1111111111111111')]),
        ]),
      ),
    ).toBe('Mission Core reports 2 active hard blockers.');
    expect(
      blockerSentence(
        entryOf([
          blockers([record('HARD'), record('SOFT', OPENED, 'other', 'bl-1111111111111111')]),
        ]),
      ),
    ).toBe('Mission Core reports 2 active blockers.');
    // omitted by the display cap: the stated total counts, and the severity of the unseen ones is not claimed
    expect(
      blockerSentence(
        entryOf([
          blockers(
            [record('HARD'), record('HARD', OPENED, 'k2', 'bl-2222222222222222')],
            '3 (showing 2; 1 omitted by the display cap)',
          ),
        ]),
      ),
    ).toBe('Mission Core reports 3 active blockers.');
  });

  it('shows a plain-text blocker the source wrote, verbatim, and never prints a record it cannot read', () => {
    expect(blockerSentence(entryOf([blockers(['waiting on the signing key'], '1')]))).toBe(
      'Mission Core reports an active blocker: waiting on the signing key',
    );
    const unreadable = entryOf([blockers(['{not json'], '1')]);
    expect(blockerSentence(unreadable)).toBe('Mission Core reports an active blocker.');
    expect(firstLayerText(unreadable, '')).not.toMatch(/\{|not json/);
  });
});

describe('1b. the first layer is short: two sentences at most, one why — nothing is dropped', () => {
  const three = entryOf([
    blockers([record('HARD')], '1'),
    cause('di_progression_paused', 'DI progression paused', []),
    cause('member_activity_blocked', 'A member is blocked', [
      ['member', 'r · lead (lead) · claude · observed_running (fresh)'],
    ]),
  ]);
  it('shows two cause sentences, counts the rest, and says ONE why', () => {
    const h = firstLayerHappened(three);
    expect(h.lines).toHaveLength(2);
    expect(h.more).toBe(1);
    expect(h.lines[1]).toBe('DI progression of this Mission is paused.');
    expect(firstLayerWhy(three)).toBe(
      'A blocker is something Mission Core records as standing in the Mission’s way.',
    );
    expect(h.lines).not.toContain(firstLayerWhy(three));
  });
  it('an unknown cause from a newer producer is shown with its own label, never dropped', () => {
    expect(
      firstLayerHappened(entryOf([cause('some_new_cause', 'A newer producer’s cause', [])])).lines,
    ).toEqual(['A newer producer’s cause']);
  });
});

describe('2. the next step comes from the same route data that decides the button', () => {
  const decision = {
    ...entryOf(
      [cause('awaiting_decision', 'Awaiting a human decision', [['revision', '2']])],
      'authorization',
    ),
    route: routeOf('authorization'),
  };
  const twin = {
    requestRef: 'lr-' + 'd'.repeat(32),
    identity: 'lr-' + 'd'.repeat(32),
    stage: 'of_record',
  } as never;

  it('a merged panel decision: decide in the panel (it records the decision; this inbox approves nothing) — and the button is offered', () => {
    const next = missionNextStep(
      decision,
      twin,
      { kind: 'read', overview: {} as MrOverview },
      true,
    );
    expect(next).toEqual({
      text: 'Decide in the request panel (it records your decision; this inbox approves nothing).',
      panelButton: true,
    });
    expect(next.text).not.toMatch(/Operator/);
    expect(firstLayerText(decision, next.text)).not.toMatch(FORBIDDEN_ABOVE);
  });

  it('a merged decision this view cannot open the panel for says so, and offers NO button', () => {
    const next = missionNextStep(
      decision,
      twin,
      { kind: 'read', overview: {} as MrOverview },
      false,
    );
    expect(next.panelButton).toBe(false);
    expect(next.text).toMatch(/can’t be opened from this view/);
    expect(next.text).toMatch(/this inbox approves nothing/);
  });

  const negatives: Array<[string, Parameters<typeof missionNextStep>[2], RegExp]> = [
    [
      'panel not set up here',
      { kind: 'not_provided' },
      /no request panel set up, so there is no in-office route for this decision/,
    ],
    [
      'panel not available here',
      { kind: 'not_available', reason: 'x' },
      /isn’t available in this office, so there is no in-office route/,
    ],
    [
      'panel not vouched for',
      { kind: 'not_vouched', reason: 'x' },
      /couldn’t be read just now, so this decision can’t be tied to a request in it/,
    ],
    ['panel not read yet', { kind: 'not_read' }, /couldn’t be read just now/],
    [
      'panel read, decision unlinked',
      { kind: 'read', overview: {} as MrOverview },
      /doesn’t list this decision \(no request in it is tied to this Mission\), so it can’t be decided there/,
    ],
  ];
  for (const [name, panel, reason] of negatives)
    it(`${name}: the Operator route is named WITH the reason, and there is no panel button`, () => {
      const next = missionNextStep(decision, null, panel, true);
      expect(next.panelButton).toBe(false);
      expect(next.text).toMatch(reason);
      expect(next.text).toMatch(/Operator decision route for this Mission/);
      expect(next.text).not.toMatch(/read-only/i);
    });

  it('a kind of item with no panel route (a blocker, a failed verification, a result) says exactly that, then names its route', () => {
    for (const category of ['blockers', 'failed_verification', 'ready_for_review'] as const) {
      const e = entryOf([blockers([record('HARD')], '1')], category); // carries a real route text (the projection's)
      const next = missionNextStep(e, null, { kind: 'read', overview: {} as MrOverview }, true);
      expect(next.panelButton).toBe(false);
      expect(next.text).toMatch(/^This kind of item has no request-panel route\./);
    }
  });

  it('a reauthorization that is not a pending decision has no panel route either', () => {
    const reauth = {
      ...entryOf(
        [cause('workflow_needs_reauthorization', 'Needs reauthorization', [])],
        'authorization',
      ),
      route: routeOf('authorization'),
    };
    const next = missionNextStep(reauth, null, { kind: 'read', overview: {} as MrOverview }, true);
    expect(next.panelButton).toBe(false);
    expect(next.text).toMatch(/^This kind of item has no request-panel route\./);
  });
});

/** A request-panel overview (the wire shape the panel sends) with the given requests and conversations. */
function overviewWith(opts: {
  requests?: Array<{ ref: string; actions: string[]; stage?: string }>;
  conversations?: Array<Record<string, unknown>>;
}): MrOverview {
  return parseOverview({
    server_now: 1000,
    requests_truncated: false,
    capabilities: {
      run_available: false,
      delivery_authority: 'none',
      decision: { transport: 't', proof: 'p', reply: 'approve', approval_valid_seconds: 900 },
      scope_meanings: {},
    },
    requests: (opts.requests ?? []).map((r) => ({
      request_ref: r.ref,
      mission_id: 'mn-' + r.ref.slice(3, 11),
      revision: 2,
      created_at: 1,
      objective: 'make the check stable',
      proposal_digest_sha256: 'b'.repeat(64),
      lifecycle: {
        stage: r.stage ?? 'of_record',
        label: 'make the check stable',
        terminal: false,
        actions: r.actions,
      },
    })),
    conversations: opts.conversations ?? [],
  })!;
}
const CV = `cv-${'a'.repeat(32)}`;
const CT = `ct-${'b'.repeat(32)}`;
const asking = (id = CV) => ({
  conversation_id: id,
  turns: 2,
  last_activity: 9,
  first_words: 'make it stable',
  awaiting_answer: true,
  questions_reporting: 'read',
  questions_problem: null,
  awaiting_turn_id: CT,
  awaiting_questions: ['Which branch?', 'Which tests?'],
});

describe('3. the request panel, the inbox and the coverage line say the same number', () => {
  const overview = overviewWith({
    requests: [
      { ref: 'lr-' + 'c'.repeat(32), actions: ['decide'] },
      { ref: 'lr-' + 'e'.repeat(32), actions: ['verify'] },
    ],
    conversations: [asking()],
  });
  const state: MrState = { ...INITIAL_STATE, overview };

  it('defines the waiting count once: two requests and one conversation', () => {
    expect(waitingOf(overview)).toEqual({ requests: 2, conversations: 1, total: 3 });
    expect(countsOf(overview).needsYou).toBe(2);
    expect(waitingOf(null)).toEqual({ requests: 0, conversations: 0, total: 0 });
  });

  it('the panel card names both kinds', () => {
    expect(entryWords(state)).toBe(
      '2 requests need you · 1 conversation waiting for your answer · 2 requests in all',
    );
  });

  it('the inbox lists exactly that many panel items and the coverage line says the same total with the two kinds apart', () => {
    const model = buildInbox(undefined, { kind: 'read', overview }, [], new Set());
    const panelItems = model.all.filter((i) => i.kind === 'panel');
    expect(panelItems).toHaveLength(waitingOf(overview).total);
    expect(
      panelItems.filter((i) => i.kind === 'panel' && i.entry.conversationId !== null),
    ).toHaveLength(waitingOf(overview).conversations);
    const line = model.sources.find((s) => s.sourceId === 'request_panel')!;
    expect(line.headline).toBe('3 waiting');
    expect(line.summary).toBe('read · 3 waiting for you (2 requests, 1 conversation)');
  });

  it('a conversation alone is still counted by the panel card (the card no longer says only “No requests yet”)', () => {
    const only = overviewWith({ conversations: [asking()] });
    expect(entryWords({ ...INITIAL_STATE, overview: only })).toBe(
      '1 conversation waiting for your answer. No requests yet.',
    );
    expect(entryWords({ ...INITIAL_STATE, overview: overviewWith({}) })).toBe(
      'No requests yet. Describe the work you want done.',
    );
  });

  it('a source that was not read says why in its own words, never a number or “none”', () => {
    for (const panel of [
      { kind: 'not_provided' },
      { kind: 'not_read' },
      { kind: 'not_available', reason: 'x' },
    ] as const) {
      const line = buildInbox(undefined, panel, [], new Set()).sources.find(
        (s) => s.sourceId === 'request_panel',
      )!;
      expect(line.headline).not.toMatch(/\d|none/i);
    }
  });
});

describe('1c. a clarification and a delivery: plain first layer, the exact ids only in the record', () => {
  it('a conversation entry: no cv- ref, no turn id above the disclosure; the ref is the entry identity', () => {
    const model = buildInbox(
      undefined,
      { kind: 'read', overview: overviewWith({ conversations: [asking()] }) },
      [],
      new Set(),
    );
    const item = model.all.find((i) => i.kind === 'panel')!;
    if (item.kind !== 'panel') throw new Error('panel item');
    const e = item.entry;
    expect([e.what, e.why, e.next].join(' ')).not.toMatch(/\bcv-|\bct-/);
    expect(e.what).toBe(
      'The assistant asked 2 questions in this conversation and is waiting for your reply.',
    );
    expect(e.identity).toBe(CV); // shown only in the card's record disclosure
  });

  it('a delivery proposal: no workflow id and no digest in the first layer', () => {
    const p = plainDelivery({
      key: 'k',
      signature: 's',
      category: 'delivery_decision',
      sourceId: 'grok_delivery_presentations',
      proposalDigest: 'f'.repeat(64),
      mode: 'pull_request',
      repositoryUrl: 'https://github.com/synthetic/repo-1',
      presentedAt: 1_800_000_000,
      expiresAt: 1_800_003_600,
      state: 'awaiting_no_recorded_authorization',
      linkState: 'unlinked_no_registered_mission',
      workflowId: 'wf-synthetic',
      missionRef: null,
      matchingDeliveries: 0,
      authorizationCheck: 'compared',
      needsYou: true,
      current: true,
      staleReasons: [],
      route: 'r',
    } as never);
    expect([p.what, p.when, p.why ?? '', p.next, p.link].join(' ')).not.toMatch(
      /wf-synthetic|f{16}/,
    );
  });
});

describe('the compact coverage line', () => {
  it('names what could not be fully checked in plain words and what the catalog does not report — never “none”', () => {
    const headline = uncertaintyHeadline(base, 0);
    expect(
      headline === null ||
        /couldn’t be (fully )?checked|isn’t reported by the catalog|aren’t reported by the catalog/.test(
          headline,
        ),
    ).toBe(true);
    const withUndetermined = uncertaintyHeadline(base, 2)!;
    expect(withUndetermined).toMatch(
      /2 delivery proposals couldn’t be checked for a recorded authorization/,
    );
    expect(withUndetermined).not.toMatch(/\bnone\b/i);
  });
});

describe('4. the transcript reads the declared questions as questions; the exact reply is one disclosure away', () => {
  const FENCED = [
    'Thanks. Two things first:',
    '',
    '```mission-questions',
    '{"questions": ["Which branch should this change?", "Which tests prove it?"]}',
    '```',
    '',
    'Then I can draft.',
  ].join('\n');

  it('removes exactly one closed questions block for display, and answers null for anything else', () => {
    expect(replyWithoutQuestionsBlock(FENCED)).toBe(
      'Thanks. Two things first:\n\nThen I can draft.',
    );
    expect(replyWithoutQuestionsBlock('no block at all')).toBeNull();
    expect(replyWithoutQuestionsBlock('```mission-questions\n{"questions": ["a"]')).toBeNull(); // never closed
    expect(replyWithoutQuestionsBlock(`${FENCED}\n${FENCED}`)).toBeNull(); // two blocks
    expect(
      replyWithoutQuestionsBlock('x\r\n```mission-questions\r\n{"questions": ["a"]}\r\n```\r\ny'),
    ).toBe('x\ny'); // CRLF is one line break
    // another fence is skipped whole, including a questions-looking line inside it, and is kept
    const other = [
      'a',
      '```text',
      '```mission-questions',
      '```',
      'b',
      '```mission-questions',
      '{"questions": ["q?"]}',
      '```',
    ].join('\n');
    expect(replyWithoutQuestionsBlock(other)).toBe(
      ['a', '```text', '```mission-questions', '```', 'b'].join('\n'),
    );
  });

  const wire = (over: Record<string, unknown>) => ({
    turn_id: CT,
    conversation_id: CV,
    sequence: 1,
    state: 'COMPLETED',
    in_flight: false,
    user_text: 'make it stable',
    assistant_text: FENCED,
    questions: {
      reporting: 'read',
      items: ['Which branch should this change?', 'Which tests prove it?'],
      problem: null,
    },
    ...over,
  });
  const turnFrom = (over: Record<string, unknown>): MrTurn =>
    parseConversation({ conversation_id: CV, turns: [wire(over)] })!.turns[0];
  const render = (turn: MrTurn) =>
    renderToStaticMarkup(
      createElement(TurnView, {
        turn,
        latestDraftTurn: null,
        control: null as never,
        meanings: {},
      }),
    );

  it('reads the panel’s own validated extraction, and only a clean `read` report counts', () => {
    expect(turnFrom({}).declaredQuestions).toEqual([
      'Which branch should this change?',
      'Which tests prove it?',
    ]);
    for (const bad of [
      { reporting: 'not_reported_by_this_panel_version', items: [], problem: null },
      { reporting: 'read', items: [], problem: null },
      { reporting: 'read', items: ['a'], problem: 'the questions block was never closed' },
      { reporting: 'read', items: ['a', 'a'], problem: null },
      { reporting: 'read', items: ['a', 'b', 'c', 'd'], problem: null },
      { reporting: 'read', items: [''], problem: null },
    ])
      expect(turnFrom({ questions: bad }).declaredQuestions).toEqual([]);
    expect(turnFrom({ questions: undefined }).declaredQuestions).toEqual([]);
  });

  it('shows the questions as a numbered list with the prose, and the raw reply (fence and all) only inside a closed disclosure', () => {
    const html = render(turnFrom({}));
    const [visible, disclosure] = html.split('<details');
    expect(visible).toContain('The assistant asked 2 questions:');
    expect(visible).toContain('Which branch should this change?');
    expect(visible).toContain('Which tests prove it?');
    expect(visible).toContain('Thanks. Two things first:');
    expect(visible).not.toContain('mission-questions');
    expect(visible).not.toContain('```');
    expect(disclosure).toContain('The assistant’s exact reply (technical detail)');
    expect(disclosure).toContain('```mission-questions');
    expect(disclosure).toContain('{&quot;questions&quot;');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });

  it('a legacy reply (no fence, or a panel that did not read) is shown exactly as written, with no disclosure', () => {
    const legacy = render(
      turnFrom({ assistant_text: 'Sure. What is the goal?', questions: undefined }),
    );
    expect(legacy).toContain('Sure. What is the goal?');
    expect(legacy).not.toContain('<details');
    expect(legacy).not.toContain('The assistant asked');
    // a fenced reply the panel could not read is also shown as written (the fence is visible), never rewritten
    const unreadable = render(
      turnFrom({
        questions: {
          reporting: 'read',
          items: [],
          problem: 'the questions block is not valid JSON (x)',
        },
      }),
    );
    expect(unreadable).toContain('```mission-questions');
    expect(unreadable).not.toContain('The assistant asked');
  });
});

describe('8. panel polish: an older reply with technical blocks, short role labels, one drafting note', () => {
  const OLDER = [
    'An earlier reply, recorded by a panel that did not read replies for questions.',
    '',
    '```mission-questions',
    '{"questions": ["Which environment is this for?"]}',
    '```',
  ].join('\n');
  const wire = (over: Record<string, unknown>) => ({
    turn_id: CT,
    conversation_id: CV,
    sequence: 1,
    state: 'COMPLETED',
    in_flight: false,
    user_text: 'make it stable',
    assistant_text: OLDER,
    ...over,
  });
  const turnFrom = (over: Record<string, unknown>): MrTurn =>
    parseConversation({ conversation_id: CV, turns: [wire(over)] })!.turns[0];
  const render = (turn: MrTurn) =>
    renderToStaticMarkup(
      createElement(TurnView, {
        turn,
        latestDraftTurn: null,
        control: null as never,
        meanings: {},
      }),
    );

  it('folds closed fenced blocks away for display, and answers null for no block, an unclosed fence or a malformed one', () => {
    expect(replyWithoutFencedBlocks(OLDER)).toEqual({
      prose: 'An earlier reply, recorded by a panel that did not read replies for questions.',
      blocks: 1,
    });
    expect(replyWithoutFencedBlocks('Run this:\n\n```sh\nnpm test\n```\n\nthen look.')).toEqual({
      prose: 'Run this:\n\nthen look.',
      blocks: 1,
    });
    expect(replyWithoutFencedBlocks('a\n```x\n1\n```\nb\n```y\n2\n```')).toEqual({
      prose: 'a\nb',
      blocks: 2,
    });
    expect(replyWithoutFencedBlocks('```text\nonly a block\n```')).toEqual({
      prose: '',
      blocks: 1,
    });
    expect(replyWithoutFencedBlocks('no block at all')).toBeNull();
    expect(replyWithoutFencedBlocks('```mission-questions\n{"questions": ["a"]}')).toBeNull(); // never closed
    expect(replyWithoutFencedBlocks('ok\n```a\n1\n```\n```b\n2')).toBeNull(); // one closed, one left open: shown as written, nothing repaired
    expect(replyWithoutFencedBlocks('```inline``` on one line\nmore')).toBeNull(); // a fence that opens and never closes on its own line
    expect(technicalBlockNote(1)).toBe(
      'This earlier reply includes a technical block (view only; not a current question).',
    );
    expect(technicalBlockNote(2)).toBe(
      'This earlier reply includes 2 technical blocks (view only; not current questions).',
    );
  });

  it('shows an older reply’s prose without its block, one compact note, and the EXACT original behind a closed disclosure', () => {
    const turn = turnFrom({});
    expect(turn.questionsStatus).toBe('not_reported');
    const html = render(turn);
    const [visible, disclosure] = html.split('<details');
    expect(visible).toContain(
      'An earlier reply, recorded by a panel that did not read replies for questions.',
    );
    expect(visible).toContain(technicalBlockNote(1));
    expect(visible).not.toContain('mission-questions');
    expect(visible).not.toContain('```');
    expect(visible).not.toContain('Which environment is this for?');
    expect(visible).not.toContain('<ol');
    expect(visible).not.toContain('The assistant asked');
    expect(disclosure).toContain('The exact original reply');
    expect(disclosure).toContain('```mission-questions');
    expect(disclosure).toContain(
      '{&quot;questions&quot;: [&quot;Which environment is this for?&quot;]}',
    );
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });

  it('NEVER turns an older block into questions or a list: the model keeps no declared question, and the markup has no question item or numbered list', () => {
    const turn = turnFrom({});
    expect(turn.declaredQuestions).toEqual([]);
    expect(turn.questionsStatus).not.toBe('declared');
    const html = render(turn);
    expect(html).not.toContain('data-testid="di-mr-question"');
    expect(html).not.toContain('data-testid="di-mr-questions-');
    expect(html).not.toContain('<ol');
    // the same block inside a reply the panel DID read for questions and found none stays folded; the folding never changes what the panel declared
    expect(
      turnFrom({ questions: { reporting: 'read', items: [], problem: null } }).declaredQuestions,
    ).toEqual([]);
  });

  it('a reply the panel read and found no questions in (a code block) is folded the same way', () => {
    const turn = turnFrom({
      assistant_text: 'Run this:\n\n```sh\nnpm test\n```',
      questions: { reporting: 'read', items: [], problem: null },
    });
    expect(turn.questionsStatus).toBe('none_declared');
    const [visible, disclosure] = render(turn).split('<details');
    expect(visible).toContain('Run this:');
    expect(visible).toContain(technicalBlockNote(1));
    expect(visible).not.toContain('npm test');
    expect(disclosure).toContain('npm test');
  });

  it('shows a REFUSED block, an unclosed fence and a malformed one exactly as written; a reply that is only a block is the note plus the disclosure', () => {
    const refused = turnFrom({
      questions: {
        reporting: 'read',
        items: [],
        problem: 'the questions block is not valid JSON (x)',
      },
    });
    expect(refused.questionsStatus).toBe('refused');
    const asWritten = render(refused);
    expect(asWritten).toContain('```mission-questions');
    expect(asWritten).not.toContain('<details');
    expect(asWritten).not.toContain('technical block');
    const unclosed = render(
      turnFrom({ assistant_text: 'Sure.\n```mission-questions\n{"questions": ["a"]}' }),
    );
    expect(unclosed).toContain('```mission-questions');
    expect(unclosed).not.toContain('<details');
    const onlyBlock = render(turnFrom({ assistant_text: '```text\nonly a block\n```' }));
    const [visible, disclosure] = onlyBlock.split('<details');
    expect(visible).toContain(technicalBlockNote(1));
    expect(visible).not.toContain('only a block');
    expect(disclosure).toContain('only a block');
  });

  it('the current-questions path is unchanged: the list and the raw disclosure, and no earlier-reply note', () => {
    const current = render(
      turnFrom({
        assistant_text: [
          'Thanks.',
          '',
          '```mission-questions',
          '{"questions": ["Which branch?"]}',
          '```',
        ].join('\n'),
        questions: { reporting: 'read', items: ['Which branch?'], problem: null },
      }),
    );
    expect(current).toContain('The assistant asked a question:');
    expect(current).toContain('The assistant’s exact reply (technical detail)');
    expect(current).not.toContain('This earlier reply');
  });

  it('labels the turns You and Assistant, and offers the drafting meaning through one shared description instead of a long label', () => {
    const html = render(turnFrom({ assistant_text: 'Sure. What is the goal?' }));
    expect(html).toContain('<span class="di-mr-who">You</span>');
    expect(html).toContain('<span class="di-mr-who">Assistant</span>');
    expect(html).toContain('aria-describedby="di-mr-drafting-note"');
    expect(html).not.toContain('read-only drafting turn');
    expect(html).not.toContain('carry no authority');
  });

  it('says the drafting-versus-decision meaning once, and the synthetic statement short with the full wording kept', () => {
    expect(MR_DRAFTING_NOTE).toMatch(/only drafts with you/);
    expect(MR_DRAFTING_NOTE).toMatch(/not a decision and carry no authority/);
    expect(MR_DRAFTING_NOTE).toMatch(/decisions happen in Requests/);
    expect(MR_FIXTURE_SHORT).toMatch(/synthetic; no approval given here is real/);
    expect(MR_FIXTURE_SHORT.length).toBeLessThan(MR_FIXTURE_WORDS.length);
    expect(MR_FIXTURE_WORDS).toMatch(/Nothing here is a real request awaiting a real decision/);
  });
});
