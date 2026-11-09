/**
 * New Mission is ONE screen with ONE composer (Free UI streamline, 2026-11-04, approved Mission mn-92b246d…, B1-B6). Model and rendered markup.
 *
 * REPLACES test/diScanAskHandoff.test.ts, whose subject (handing the typed ask to a SECOND composer, the request workspace's Ask draft, refusing when that draft differed) no longer exists: New
 * Mission's Ask box IS the workspace's Ask draft. What that file protected and where it went:
 *  - the control is named for what it does, and the page says what it does: kept below (Send, "to send. ... it cannot approve or start anything");
 *  - the order of the reasons (the Orchestrator limit, then an empty ask, then no request panel): kept below, extended by the states a real Send has (not vouched for, busy, too long);
 *  - first paint: the ask is empty, so the control is unavailable and says to type an ask first: kept below;
 *  - the three handoff cases (transfer, same, refused) protected one text from being replaced by another; with one composer there is no second text to replace, which the browser test shows
 *    (test/diScanNewMissionBrowser.test.ts: what is typed in either place is the same draft).
 *
 * Every request-panel answer here is LABELLED SYNTHETIC (test/diNewMissionSynthetic.ts, the panel's own wire shape), applied through the request workspace's own reducer.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.ts';
import { DraftCard } from '../src/components/DiMissionRequest.tsx';
import type { MrEvent, MrState } from '../src/components/diMissionRequestModel.ts';
import { INITIAL_STATE, MR_DRAFTING_NOTE, MR_MAX_TEXT_CHARS, reduce } from '../src/components/diMissionRequestModel.ts';
import type { DiNewMissionRequest } from '../src/components/DiScanPages.tsx';
import {
  DI_ASK_BRING_BACK,
  DI_ASK_BUSY,
  DI_ASK_IN_WORKSPACE,
  DI_ASK_IN_WORKSPACE_NOTE,
  DI_ASK_LEAVE_UNREAD,
  DI_ASK_NO_PANEL,
  DI_ASK_READING,
  DI_ASK_SEND_HINT,
  DI_ASK_SEND_LABEL,
  DI_ASK_UNAVAILABLE,
  DI_ASK_UNREAD,
  DI_ASK_UNVOUCHED,
  DI_DRAFT_HEAD,
  DI_DRAFT_HEAD_SUBMITTED,
  DI_DRAFT_HEAD_UNKNOWN,
  DI_DRAFT_IN_PLACE,
  DI_DRAFT_OPEN_LABEL,
  DI_NEW_MISSION_OFFICE,
  DI_NEW_MISSION_SPECIMEN,
  DI_NEW_READ_AGAIN,
  DI_ORCHESTRATOR_LIMIT,
  DI_RECENT_NONE,
  DI_RECENT_NOT_READ,
  recentAsks,
  sendAskReason,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import type { DiMissionRequestControl } from '../src/hooks/useDiMissionRequest.ts';
import {
  GATE_LABEL,
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_OTHER_CONVERSATION_ID,
  SYNTHETIC_PROPOSAL,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';
import { escapeRegExp, markupText, markupWords } from './helpers/markupText.ts';

const noop = () => undefined;
const none = { holding: 0, notKnown: 0 };
const ASK = 'SYNTHETIC ask: make the readiness probe stable under load';

const apply = (state: MrState, ...events: MrEvent[]): MrState => events.reduce(reduce, state);
const reply = (seq: number, op: string, answer: Record<string, unknown>): MrEvent => ({
  type: 'reply',
  nowMs: 1_000,
  reply: relayReply(seq, op, answer) as unknown as DiMissionRequestReply,
});
/** The request workspace's own state after: an overview read, the ask typed, ONE Send, and the conversation read with the drafted turn (all synthetic answers). */
function drafted(): MrState {
  return apply(
    INITIAL_STATE,
    { type: 'sent', seq: 1, op: 'refresh', requestRef: null, turnId: null },
    reply(1, 'refresh', syntheticOverview()),
    { type: 'composer', text: ASK },
    { type: 'sent', seq: 2, op: 'turn', requestRef: null, turnId: null },
    reply(2, 'turn', { ok: true, turn: syntheticTurn(ASK) }),
    { type: 'sent', seq: 3, op: 'conversation', requestRef: null, turnId: null },
    reply(3, 'conversation', syntheticConversation([syntheticDraftedTurn(ASK)])),
  );
}
const seam = (state: MrState): DiNewMissionRequest => ({
  state,
  setComposer: noop,
  sendTurn: noop,
  newConversation: noop,
  closeConversation: noop,
  readConversation: noop,
  watchConversation: noop,
  openAsk: noop,
  openRequest: noop,
  workspaceShown: false,
  closeWorkspace: noop,
  dismiss: noop,
});
const page = (request: DiNewMissionRequest | null, rows = [] as never[]) =>
  /data-testid="di-new-mission"[\s\S]*$/.exec(
    renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'new', onMinimize: noop, onOpenRequestWorkspace: noop, newMissionRequest: request }),
    ),
  )?.[0] ?? '';
const tag = (html: string, testid: string) => new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`).exec(html)?.[0] ?? '';

describe('Send: why it is off, the rule that holds first (AC-4.3-14/-18/-49)', () => {
  it('the Orchestrator limit, then an empty ask, then no request panel, then not vouched for, then busy, then the composer bound; otherwise offered', () => {
    expect(sendAskReason('Fix it', { holding: DI_ORCHESTRATOR_LIMIT, notKnown: 0 }, 'ready')).toMatch(/Orchestrators are in use/);
    // an unknown is never spare capacity: 14 held and 1 not known refuse
    expect(sendAskReason('Fix it', { holding: DI_ORCHESTRATOR_LIMIT - 1, notKnown: 1 }, 'ready')).toMatch(/in use or cannot be checked \(14 in use, 1 cannot be checked\)/);
    expect(sendAskReason('   ', none, 'ready')).toBe('Type an ask first.');
    expect(sendAskReason('Fix it', none, 'none')).toBe(DI_ASK_NO_PANEL);
    expect(sendAskReason('Fix it', none, 'unvouched')).toBe(DI_ASK_UNVOUCHED);
    // review round 1, F1: a panel configured but not available NOW holds Send (after the empty-ask rule, which comes first)
    expect(sendAskReason('Fix it', none, 'unavailable')).toBe(DI_ASK_UNAVAILABLE);
    expect(sendAskReason('', none, 'unavailable')).toBe('Type an ask first.');
    // review rounds 2 and 3: a picked conversation not yet read waits, and one that could not be read holds with a message naming the way out on the page
    expect(sendAskReason('Fix it', none, 'reading')).toBe(DI_ASK_READING);
    expect(sendAskReason('Fix it', none, 'unread')).toBe(DI_ASK_UNREAD);
    expect(DI_ASK_UNREAD.toLowerCase()).toContain(DI_ASK_LEAVE_UNREAD.toLowerCase());
    // B2e: it names the labelled way to read it again word for word, and no longer sends a person to Recent (whose selected ask now closes it)
    expect(DI_ASK_UNREAD.toLowerCase()).toContain(DI_NEW_READ_AGAIN.toLowerCase());
    expect(DI_ASK_UNREAD).not.toMatch(/in Recent/);
    expect(sendAskReason('Fix it', none, 'busy')).toBe(DI_ASK_BUSY);
    expect(sendAskReason('x'.repeat(MR_MAX_TEXT_CHARS + 1), none, 'ready')).toMatch(/longer than/);
    expect(sendAskReason('Fix it', none, 'ready')).toBeNull();
  });
});

describe('Recent asks are shown only as they were READ (B5)', () => {
  it('no overview read: said as not read, never as none', () => {
    expect(recentAsks(DI_NEW_MISSION_OFFICE, null)).toEqual({ kind: 'not_read', words: DI_RECENT_NOT_READ });
    expect(DI_RECENT_NOT_READ).not.toMatch(/layout preview/i);
  });
  it('the overview read: its recent conversations, by their first words; one whose words are no longer kept is not listed as an ask', () => {
    const read = apply(
      INITIAL_STATE,
      { type: 'sent', seq: 1, op: 'refresh', requestRef: null, turnId: null },
      reply(
        1,
        'refresh',
        syntheticOverview([
          syntheticConversationSummary(SYNTHETIC_CONVERSATION_ID, 'SYNTHETIC: fix the flaky readiness test'),
          syntheticConversationSummary(SYNTHETIC_OTHER_CONVERSATION_ID, ''),
        ]),
      ),
    );
    expect(recentAsks(DI_NEW_MISSION_OFFICE, read.overview)).toEqual({
      kind: 'read',
      items: [{ conversationId: SYNTHETIC_CONVERSATION_ID, text: 'SYNTHETIC: fix the flaky readiness test' }],
    });
  });
  it('the overview read and empty: read, and empty (the page says none are recorded)', () => {
    const read = apply(INITIAL_STATE, { type: 'sent', seq: 1, op: 'refresh', requestRef: null, turnId: null }, reply(1, 'refresh', syntheticOverview()));
    expect(recentAsks(DI_NEW_MISSION_OFFICE, read.overview)).toEqual({ kind: 'read', items: [] });
    expect(page(seam(read))).toContain(`data-testid="di-new-recent-none">${DI_RECENT_NONE}<`);
  });
  it('the labelled sample keeps its own sample list, which only fills the box', () => {
    const sample = recentAsks(DI_NEW_MISSION_SPECIMEN, null);
    expect(sample.kind).toBe('read');
    expect(sample.kind === 'read' ? sample.items[0] : null).toEqual({ conversationId: null, text: 'Fix flaky readiness test' });
  });
  it('on the page: not read is said in words, and no ask is drawn', () => {
    const html = page(null);
    expect(html).toContain(`data-testid="di-new-recent-not-read">${DI_RECENT_NOT_READ}<`);
    expect(html).not.toContain('data-testid="di-new-recent-ask"');
  });
});

describe('ONE composer, and Send says what it does (B1)', () => {
  it('the box IS the request workspace’s Ask draft: it shows that draft, and there is no second ask box or handoff on the page', () => {
    const state = apply(INITIAL_STATE, { type: 'composer', text: ASK });
    const html = page(seam(state));
    expect(html).toMatch(new RegExp(`<textarea[^>]*data-testid="di-new-ask"[^>]*>${ASK}</textarea>`));
    expect(html.match(/<textarea/g)).toHaveLength(1);
    expect(html).not.toMatch(/Continue in the request workspace|already holds an unsent draft|di-new-continue/);
  });
  it('Send is named Send, the key hint says what it sends and what the assistant cannot do', () => {
    const html = page(seam(apply(INITIAL_STATE, { type: 'composer', text: ASK })));
    expect(html).toMatch(new RegExp(`<button[^>]*data-testid="di-new-send"[^>]*>${DI_ASK_SEND_LABEL}</button>`));
    expect(DI_ASK_SEND_HINT).toMatch(/cannot approve or start anything/);
    expect(html).toContain(DI_ASK_SEND_HINT);
    expect(tag(html, 'di-new-send')).not.toContain('aria-disabled');
  });
  it('on first paint the ask is empty: Send is held (aria-disabled, never `disabled`, so it keeps focus) and says to type an ask first', () => {
    const send = tag(page(seam(INITIAL_STATE)), 'di-new-send');
    expect(send).toContain('aria-disabled="true"');
    expect(send).not.toContain('disabled=""');
    expect(send).toContain('title="Type an ask first."');
  });
  it('B8: while the request workspace shows its own Ask composer, this page draws NO second box: it says where the one box is, and Send waits with that reason', () => {
    const state = apply(INITIAL_STATE, { type: 'composer', text: ASK }, { type: 'open', tab: 'ask' });
    const html = page({ ...seam(state), workspaceShown: true });
    expect(html).not.toContain('<textarea');
    expect(html).toContain(`data-testid="di-new-ask-in-workspace"`);
    expect(html).toContain(DI_ASK_IN_WORKSPACE_NOTE);
    expect(html).toContain(`>${DI_ASK_BRING_BACK}</button>`);
    expect(tag(html, 'di-new-send')).toContain('aria-disabled="true"');
    expect(tag(html, 'di-new-send')).toContain(`title="${DI_ASK_IN_WORKSPACE}"`);
    // the draft itself is untouched: it is the workspace's composer state, still exactly the ask
    expect(state.composer).toBe(ASK);
  });
  it('without a request panel, the box can be typed in and Send says nothing can be sent from here', () => {
    expect(tag(page(null), 'di-new-send')).toContain('aria-disabled="true"');
  });
});

describe('Send drafts the proposal IN PLACE, and the screen carries no authority (B1, B3)', () => {
  const state = drafted();
  const html = page(seam(state));
  it('the conversation is drawn on the same screen: the ask as the person typed it, once, and the assistant’s reply, with the drafting note said once', () => {
    expect(state.composer).toBe('');
    expect(html).toContain('data-testid="di-new-thread"');
    const you = /data-testid="di-new-turn-1"[\s\S]*?data-who="you"[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    expect(you).toContain(ASK);
    expect(html.split(MR_DRAFTING_NOTE).length - 1).toBe(1);
    expect(html).toContain('data-testid="di-new-assistant-1"');
  });
  it('the draft is drawn in place, exactly as drafted, and says it is a draft and where the gates are', () => {
    const draft = /data-testid="di-new-draft"[\s\S]*$/.exec(html)?.[0] ?? '';
    expect(draft).toContain('data-latest="true"');
    expect(draft).toContain(DI_DRAFT_HEAD);
    expect(draft).toContain(DI_DRAFT_IN_PLACE);
    expect(draft).toMatch(new RegExp(`data-testid="di-new-draft-proposal-objective"[^>]*>[\\s\\S]*?${escapeRegExp(SYNTHETIC_PROPOSAL.objective.slice(0, 40))}`));
    expect(tag(draft, 'di-new-draft-open')).not.toBe('');
    expect(draft).toContain(`>${DI_DRAFT_OPEN_LABEL}</button>`);
  });
  it('NO control on New Mission submits, approves, declines, rejects, withdraws or starts: the one onward control only navigates to the workspace', () => {
    const labels = [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) => markupText(m[1] ?? '').trim());
    for (const label of labels) expect(label, label).not.toMatch(GATE_LABEL);
    expect(html).not.toMatch(/data-testid="di-mr-(submit|approve|decide|dispatch|withdraw|reject|decline)/);
    // and the words claim nothing was approved, started, submitted or recorded
    const text = markupText(html, ' ');
    expect(text).not.toMatch(/\b(is|was|has been|have been) (approved|started|submitted|recorded)\b/i);
    // every "proposal of record" on the screen says the draft is NOT one yet
    const ofRecord = text.match(/proposal of record/gi)?.length ?? 0;
    const notYet = text.match(/not yet a proposal of record|not a proposal of record yet/gi)?.length ?? 0;
    expect(ofRecord).toBeGreaterThan(0);
    expect(ofRecord).toBe(notYet);
  });
  it('a draft submitted elsewhere is said as submitted, with only a navigation to it; nothing here submits again', () => {
    const submitted = apply(
      state,
      { type: 'sent', seq: 9, op: 'conversation', requestRef: null, turnId: null },
      reply(
        9,
        'conversation',
        syntheticConversation([
          { ...syntheticDraftedTurn(ASK), submission: { state: 'SUBMITTED', request_ref: `lr-${'e'.repeat(32)}`, supersedes: null, problem: null, possible_matches: [] } },
        ]),
      ),
    );
    const after = page(seam(submitted));
    expect(after).toContain('data-testid="di-new-draft-submitted"');
    expect(after).toContain('data-testid="di-new-draft-open-request"');
    expect(after).not.toContain('data-testid="di-new-draft-open"');
  });
});

// Free UI streamline, review round 1, F5 (completed in round 2 after Lead1 found the Source facts and the not-runnable line still contradicting the head): the
// WHOLE drafted-proposal card is read, split into sentences, and each sentence is matched against FIVE known phrase families (below, in `claimsIn`): not known;
// being submitted; not a proposal of record yet / was not recorded / can still be recorded; nothing was changed or recorded; was submitted / Submitted as /
// It is recorded. For 7 submission states, with and without a not-runnable problem, the families found on the card must be exactly those true for the state,
// and the Source facts and the not-runnable line must each carry one. What it does NOT catch: a state-bearing sentence worded outside those families is not
// classified, so it would pass. Shown caught: the two contradictions of logs/77 and the "not known" + "Nothing was changed." case (the self-test below; logs/83).
// The authority gates are unchanged.
describe('the drafted proposal card, read WHOLE, across every submission state: every statement agrees with the submission (F5)', () => {
  const ref = `lr-${'e'.repeat(32)}`;
  const RUNNABLE = 'SYNTHETIC: no runner is configured for this repository';
  const card = (submission: Record<string, unknown> | null, runnable: string | null) => {
    const turn = syntheticDraftedTurn(ASK) as Record<string, unknown> & { draft: Record<string, unknown> };
    return page(
      seam(
        apply(
          drafted(),
          { type: 'sent', seq: 9, op: 'conversation', requestRef: null, turnId: null },
          reply(9, 'conversation', syntheticConversation([{ ...turn, draft: { ...turn.draft, runnable_problem: runnable }, submission }])),
        ),
      ),
    );
  };
  /** The card's own element, balanced (never the page after it). */
  const section = (html: string): string => {
    const at = html.indexOf('<section class="di-new-draft"');
    if (at < 0) return '';
    const tag = /<(\/?)section\b[^>]*>/g;
    tag.lastIndex = at;
    let depth = 0;
    for (let m = tag.exec(html); m !== null; m = tag.exec(html)) {
      depth += m[1] === '/' ? -1 : 1;
      if (depth === 0) return html.slice(at, m.index + m[0].length);
    }
    return '';
  };
  const words = (html: string) => markupWords(html);
  /** Every sentence in `text` that says where the draft stands, by what it says. Precedence: not known, then pending, then not recorded, then recorded. */
  const claimsIn = (text: string) => {
    const out: Array<[string, string]> = [];
    for (const sentence of text.split(/(?<=[.;])\s+/)) {
      const kind = /not known/i.test(sentence)
        ? 'not_known'
        : /being submitted|when it is recorded/i.test(sentence)
          ? 'submitting'
          : /not (yet )?a proposal of record|proposal of record yet|was not recorded|can still be recorded|still record it|\bnothing (was )?(changed|recorded)\b/i.test(sentence)
            ? 'not_recorded'
            : /\bwas submitted\b|\bSubmitted as\b|submitted as the proposal of record|\bIt is recorded\b|is now a proposal of record/.test(sentence)
              ? 'recorded'
              : null;
      if (kind !== null) out.push([kind, sentence.trim()]);
    }
    return out;
  };
  const kinds = (text: string) => [...new Set(claimsIn(text).map(([kind]) => kind))].sort();
  /** The inner HTML of the element with this testid inside the card. */
  const inner = (whole: string, testid: string, tagName: string) =>
    new RegExp(`data-testid="${testid}"[^>]*>([\\s\\S]*?)</${tagName}>`).exec(whole)?.[1] ?? '';
  const sub = (state: string, request_ref: string | null, problem: string | null = null) => ({ state, request_ref, supersedes: null, problem, possible_matches: [] });
  for (const [name, submission, head, expected] of [
    ['not submitted', null, DI_DRAFT_HEAD, ['not_recorded']],
    ['being submitted (RESERVED)', sub('RESERVED', null), DI_DRAFT_HEAD, ['not_recorded', 'submitting']],
    ['submitted with its request ref (SUBMITTED)', sub('SUBMITTED', ref), DI_DRAFT_HEAD_SUBMITTED, ['recorded']],
    ['not known (UNCERTAIN)', sub('UNCERTAIN', null), DI_DRAFT_HEAD_UNKNOWN, ['not_known']],
    ['refused (REFUSED)', sub('REFUSED', null, 'mission_panel_submit_refused'), DI_DRAFT_HEAD, ['not_recorded']],
    // the parser also yields these: a SUBMITTED with no ref, and a state this build does not know ("unknown"); neither may be said as recorded or as not recorded
    ['SUBMITTED with no request ref', sub('SUBMITTED', null), DI_DRAFT_HEAD_UNKNOWN, ['not_known']],
    ['a state this build does not know', sub('unknown', null), DI_DRAFT_HEAD_UNKNOWN, ['not_known']],
  ] as const)
    for (const runnable of [null, RUNNABLE])
      it(`${name}${runnable ? ', and not runnable' : ''}: every statement on the card says ${expected.join(' and ')}, and nothing else`, () => {
        const html = card(submission, runnable);
        const whole = section(html);
        expect(whole, 'the card is drawn').not.toBe('');
        expect(markupText(/data-testid="di-new-draft-note"[^>]*>([\s\S]*?)<\/p>/.exec(whole)?.[1] ?? '')).toMatch(
          new RegExp(`^${escapeRegExp(head)}`),
        );
        // the lines that carry a state are all there: the Source facts always, the not-runnable line when it applies
        expect(whole).toContain('data-testid="di-new-draft-proposal-source"');
        if (runnable) expect(whole).toContain(RUNNABLE);
        // the WHOLE card: exactly the standing that is true
        const said = claimsIn(words(whole));
        expect(kinds(words(whole)), JSON.stringify(said, null, 1)).toEqual([...expected].sort());
        // and each line that carries a state, read on its own: the Source facts and the not-runnable line each SAY one (they are read, not skipped), and
        // the head and the submission line say nothing else
        const allowed = (k: string[]) => k.every((kind) => (expected as readonly string[]).includes(kind));
        const source = kinds(words(inner(whole, 'di-new-draft-proposal-source', 'dd')));
        expect(source.length > 0 && allowed(source), `Source facts: ${JSON.stringify(source)}`).toBe(true);
        if (runnable) {
          const notRunnable = kinds(words(inner(whole, 'di-new-draft-not-runnable', 'p')));
          expect(notRunnable.length > 0 && allowed(notRunnable), `not-runnable line: ${JSON.stringify(notRunnable)}`).toBe(true);
        }
        expect(allowed(kinds(words(inner(whole, 'di-new-draft-note', 'p')))), 'the head').toBe(true);
        expect(allowed(kinds(words(inner(whole, 'di-new-draft-submission', 'p') + inner(whole, 'di-new-draft-submitted', 'p')))), 'the submission line').toBe(true);
        // the onward control is offered exactly where the draft can still be submitted from the workspace
        expect(whole.includes('data-testid="di-new-draft-open"')).toBe(name === 'not submitted' || name === 'refused (REFUSED)');
        // the gates are where they were: no control on the card submits, approves, declines, rejects, withdraws or starts
        const labels = [...whole.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) => markupText(m[1] ?? '').trim());
        for (const label of labels) expect(label, label).not.toMatch(GATE_LABEL);
      });
  // Review round 2, REPORT section 6 item 2: the WORKSPACE's own drafted-proposal card, read whole by the same classifier, across the same states. At HEAD its
  // head was fixed ("not yet a proposal of record"), its Source facts were passed requestRef null, and its not-runnable line said "You can still record it",
  // whatever the submission said (provenance-round3-items-1-2.txt). Submit is exactly where it was: offered on the latest draft when not submitted or refused.
  const workspaceCard = (submission: Record<string, unknown> | null, runnable: string | null): string => {
    const turn = syntheticDraftedTurn(ASK) as Record<string, unknown> & { draft: Record<string, unknown> };
    const state = apply(
      drafted(),
      { type: 'sent', seq: 9, op: 'conversation', requestRef: null, turnId: null },
      reply(9, 'conversation', syntheticConversation([{ ...turn, draft: { ...turn.draft, runnable_problem: runnable }, submission }])),
    );
    const control = new Proxy({ state } as Record<string, unknown>, {
      get: (t, k) => (typeof k === 'string' && k in t ? t[k] : noop),
    }) as unknown as DiMissionRequestControl;
    return renderToStaticMarkup(createElement(DraftCard, { turn: state.conversation.turns[0], latest: true, control, meanings: {} }));
  };
  for (const [name, submission, expected, submit] of [
    ['not submitted', null, ['not_recorded'], true],
    ['being submitted (RESERVED)', sub('RESERVED', null), ['not_recorded', 'submitting'], false],
    ['submitted with its request ref (SUBMITTED)', sub('SUBMITTED', ref), ['recorded'], false],
    ['not known (UNCERTAIN)', sub('UNCERTAIN', null), ['not_known'], false],
    ['refused (REFUSED)', sub('REFUSED', null, 'mission_panel_submit_refused'), ['not_recorded'], true],
    ['SUBMITTED with no request ref', sub('SUBMITTED', null), ['not_known'], false],
    ['a state this build does not know', sub('unknown', null), ['not_known'], false],
  ] as const)
    for (const runnable of [null, RUNNABLE])
      it(`the WORKSPACE card, ${name}${runnable ? ', and not runnable' : ''}: every statement on it says ${expected.join(' and ')}, and Submit is ${submit ? 'offered' : 'not offered'} as before`, () => {
        const whole = workspaceCard(submission, runnable);
        expect(whole).toContain('data-testid="di-mr-draft"');
        expect(kinds(words(whole)), JSON.stringify(claimsIn(words(whole)), null, 1)).toEqual([...expected].sort());
        const allowed = (k: string[]) => k.every((kind) => (expected as readonly string[]).includes(kind));
        const source = kinds(words(inner(whole, 'di-mr-draft-proposal-source', 'dd')));
        expect(source.length > 0 && allowed(source), `Source facts: ${JSON.stringify(source)}`).toBe(true);
        if (runnable) {
          const notRunnable = kinds(words(/data-testid="di-mr-draft-not-runnable"[^>]*>([\s\S]*?)<\/div>/.exec(whole)?.[1] ?? ''));
          expect(notRunnable.length > 0 && allowed(notRunnable), `not-runnable line: ${JSON.stringify(notRunnable)}`).toBe(true);
        }
        expect(allowed(kinds(words(inner(whole, 'di-mr-draft-head', 'p')))), 'the head').toBe(true);
        expect(whole.includes('data-testid="di-mr-submit"'), 'Submit offered exactly as before').toBe(submit);
      });
  // Reviewer round 2: "not known" together with "Nothing was changed." escaped the classifier (no family named it). This self-test puts that exact sentence on
  // a real "not known" card (the rendered markup, not the product) and requires the classifier to flag it; logs/83 shows it failing without the family.
  it('the classifier flags "Nothing was changed." on a not-known card (it says the draft had no effect, which is not known)', () => {
    const html = card({ state: 'UNCERTAIN', request_ref: null, supersedes: null, problem: null, possible_matches: [] }, null).replace(
      'Look under Requests.',
      'Look under Requests. Nothing was changed.',
    );
    expect(html).toContain('Look under Requests. Nothing was changed.');
    expect(kinds(words(section(html)))).toEqual(['not_known', 'not_recorded']);
  });
});

// Free UI streamline, review round 1, F2: what came of a Send is said on New Mission itself, in the workspace's own notice words, in a live region that is always present.
describe('a Send that is refused, not known or not sent is said here (F2)', () => {
  const after = (r: Record<string, unknown>) =>
    apply(
      INITIAL_STATE,
      { type: 'sent', seq: 1, op: 'refresh', requestRef: null, turnId: null },
      reply(1, 'refresh', syntheticOverview()),
      { type: 'composer', text: ASK },
      { type: 'sent', seq: 2, op: 'turn', requestRef: null, turnId: null },
      { type: 'reply', nowMs: 1_000, reply: { type: 'diMissionRequestReply', seq: 2, op: 'turn', answer: null, ...r } as unknown as DiMissionRequestReply },
    );
  const outcome = (html: string) => markupText(/data-testid="di-new-send-outcome"[^>]*>([\s\S]*?)<\/li>/.exec(html)?.[1] ?? '').replace(/Dismiss$/, '').trim();
  it('the live region is present before any outcome (so one that arrives is announced), and empty', () => {
    const html = page(seam(drafted()));
    expect(tag(html, 'di-new-send-outcomes')).toContain('role="status"');
    expect(html).not.toContain('data-testid="di-new-send-outcome"');
  });
  for (const [name, r, said, never] of [
    ['refused', { state: 'refused', problem: 'mission_panel_conversation_busy', reason: 'SYNTHETIC: busy.' }, /^Refused\. /, /not known|Not sent/],
    ['not known', { state: 'unknown', problem: 'panel_timeout', reason: 'SYNTHETIC: no answer in time.' }, /^Outcome not known\. SYNTHETIC: no answer in time\.$/, /Refused|Not sent|nothing changed|Nothing was changed/i],
  ] as const)
    it(`${name}: said with its reason; the ask is kept exactly; never said as something else`, () => {
      const state = after(r);
      expect(state.composer).toBe(ASK);
      const words = outcome(page(seam(state)));
      expect(words).toMatch(said);
      expect(words).not.toMatch(never);
    });
});

// Review round 2 (identity), at the reducer the workspace shares: a reply is attributed by the REQUEST it answers (its seq, and the conversation that request
// was about, kept from the request as sent), never by when it arrives.
describe('the reducer attributes a read reply by the request it answers, not by arrival (review round 2, identity)', () => {
  const A_ID = SYNTHETIC_CONVERSATION_ID;
  const B_ID = SYNTHETIC_OTHER_CONVERSATION_ID;
  const refused = (seq: number): MrEvent => ({
    type: 'reply',
    nowMs: 1_000,
    reply: { type: 'diMissionRequestReply', seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: unreadable.', answer: null } as unknown as DiMissionRequestReply,
  });
  const pickA = (): MrState =>
    apply(INITIAL_STATE, { type: 'openConversation', conversationId: A_ID }, { type: 'sent', seq: 5, op: 'conversation', requestRef: null, turnId: null, conversationId: A_ID });
  it('a picked conversation has no read marker until a read OF IT succeeds; the success records that read\'s seq', () => {
    const picked = pickA();
    expect(picked.conversation.readSeq ?? null).toBeNull();
    const read = apply(picked, reply(5, 'conversation', syntheticConversation([], A_ID)));
    expect(read.conversation.id).toBe(A_ID);
    expect(read.conversation.turns).toEqual([]);
    expect(read.conversation.readSeq, 'read, and empty: told apart from not read').toBe(5);
    // a new pick clears it again
    expect(apply(read, { type: 'openConversation', conversationId: A_ID }).conversation.readSeq ?? null).toBeNull();
  });
  it('a LATE refusal of A, landing after B is picked and read, is a notice ABOUT A; B keeps its read marker', () => {
    const both = apply(
      pickA(),
      { type: 'openConversation', conversationId: B_ID },
      { type: 'sent', seq: 6, op: 'conversation', requestRef: null, turnId: null, conversationId: B_ID },
      reply(6, 'conversation', syntheticConversation([syntheticDraftedTurn(ASK)], B_ID)),
    );
    expect(both.conversation.readSeq).toBe(6);
    const late = apply(both, refused(5));
    expect(late.conversation.id).toBe(B_ID);
    expect(late.conversation.readSeq, 'B is still read').toBe(6);
    expect(late.notices[0].about).toEqual({ seq: 5, conversationId: A_ID });
  });
});
