/**
 * The Herdr control's MODEL (Package A, A2/A5): what the buttons may send, when they are enabled, and what they say.
 *
 * Shown here: a status is built only when none is in flight and the office is vouched for; a launch is built ONLY for a Mission whose last status said available; a reply changes state only when its seq and op
 * match the request in flight; every note is derived from the office's last answer for that Mission (the office's refusal words arrive unchanged); a refusal that means "not available" stops the click being
 * offered; and no note ever says the terminal is attached, that Herdr shows the herd, or that the keyboard works.
 *
 * WHAT THIS DOES NOT ESTABLISH: the hook (useDiHerdr.ts) is a thin sender over this model and is exercised in a real browser in diScanFace.test.ts (A4); a real office, Herdr or terminal is not involved.
 */
import { describe, expect, it } from 'vitest';

import type { DiHerdrReply } from '../../core/src/messages.js';
import type { HerdrState } from '../src/components/diHerdrModel.ts';
import {
  HERDR_NO_MISSION,
  HERDR_NOT_CONNECTED,
  herdrControlView,
  INITIAL_HERDR_STATE,
  launchMessage,
  missionState,
  reduce,
  statusInFlight,
  statusMessage,
  takeHerdrSeq,
} from '../src/components/diHerdrModel.ts';

const REF = 'di_herdr_tasks:task-1@abc123';
const OTHER = 'di_herdr_tasks:task-2@def456';
const ANSWER = {
  agent: 'he-exec1',
  identityBasis: 'runtime_identity_verified' as 'runtime_identity_verified' | 'team_roster_session_confirmed',
  workspaceId: 'w6V',
  windowMinCols: 190,
  windowMinRows: 49,
  workspaceFocus: 'not_performed' as const,
  matchedPaneId: 'w6V:p3',
  paneFocus: 'not_performed' as const,
  terminal: 'terminal',
  terminalLabel: 'Terminal',
  terminalState: null,
};
const reply = (over: Partial<DiHerdrReply>): DiHerdrReply => ({
  type: 'diHerdrReply',
  seq: 1,
  op: 'status',
  state: 'ok',
  problem: null,
  reason: null,
  answer: ANSWER,
  ...over,
});
/** Send a request and apply its reply, through the model alone. */
function sentThenReplied(state: HerdrState, op: 'status' | 'launch', ref: string, r: Partial<DiHerdrReply>): HerdrState {
  const seq = takeHerdrSeq();
  const sent = reduce(state, { type: 'sent', seq, op, missionRef: ref });
  return reduce(sent, { type: 'reply', reply: reply({ seq, op, ...r }) });
}
const available = (): HerdrState => sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, {});
const launchOk = { op: 'launch' as const, answer: { ...ANSWER, workspaceFocus: 'focused' as const, terminalState: 'opened' as const } };
/** Words that must never appear: the control claims no attach and no working keyboard. */
const OVERCLAIM = /attach|keyboard|typing works|Herdr is showing|is showing the herd/i;
/**
 * What the person is told about the window, stated here independently of the model: a REQUIREMENT (the window is asked to grow to at least C x R, and Herdr is started in it only if it really is that large),
 * with the reason (Herdr sizes its shared session to its smallest client). Never an achievement: nothing in the office has seen the window.
 */
const windowWords = (cols: number, rows: number): string =>
  ` Herdr sizes its shared session to its smallest client, so the window is asked to grow to at least ${cols} x ${rows} cells, and Herdr is started in it only if the window really is that large; if not, the window says so and Herdr is not started.`;

describe('what may be sent', () => {
  it('a status is built for a Mission when nothing is in flight and the office is heard from', () => {
    expect(statusMessage(INITIAL_HERDR_STATE, REF, 7)).toEqual({ type: 'diHerdrRequest', seq: 7, op: 'status', missionRef: REF });
  });

  it('nothing is built while a request is in flight (one at a time, as the office answers one at a time)', () => {
    const inFlight = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF });
    expect(statusMessage(inFlight, OTHER, 2)).toBeNull();
    expect(launchMessage(inFlight, REF, 2)).toBeNull();
  });

  it('nothing is built while the office is not vouched for', () => {
    const lost = reduce(INITIAL_HERDR_STATE, { type: 'unvouched', reason: 'disconnected' });
    expect(statusMessage(lost, REF, 1)).toBeNull();
    expect(launchMessage(lost, REF, 1)).toBeNull();
  });

  it('a launch is built ONLY for a Mission whose last status said available', () => {
    expect(launchMessage(INITIAL_HERDR_STATE, REF, 1), 'never asked').toBeNull();
    const checking = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF });
    expect(launchMessage({ ...checking, pending: null }, REF, 2), 'still checking').toBeNull();
    const refused = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { state: 'refused', problem: 'not_live', reason: 'x', answer: null });
    expect(launchMessage(refused, REF, 3), 'refused').toBeNull();
    const ok = available();
    expect(launchMessage(ok, REF, 4)).toEqual({ type: 'diHerdrRequest', seq: 4, op: 'launch', missionRef: REF });
    expect(launchMessage(ok, OTHER, 5), 'another Mission was not checked').toBeNull();
  });

  it('a launch already in flight for the Mission builds no second one', () => {
    const launching = reduce(available(), { type: 'sent', seq: 9, op: 'launch', missionRef: REF });
    expect(launchMessage({ ...launching, pending: null }, REF, 10)).toBeNull();
  });

  it('the only requests it can build are the two closed ones: no op types, prompts or sends a key', () => {
    const ok = available();
    for (const built of [statusMessage(INITIAL_HERDR_STATE, REF, 1), launchMessage(ok, REF, 2)]) {
      expect(Object.keys(built!).sort()).toEqual(['missionRef', 'op', 'seq', 'type']);
      expect(['status', 'launch']).toContain(built!.op);
    }
  });

  // Found by the real-browser test (diScanFace A4): React StrictMode runs a mount effect twice, so a Mission's screen asks for its status twice in a row. The second ask used to be held and replayed when
  // the first answered, which sent a second status and turned the answer just received back into "checking". A status already in flight for the SAME Mission now answers the repeat.
  it('a status for the Mission is in flight exactly while that Mission\'s status request is pending: not for another Mission, not for a launch, not once answered', () => {
    expect(statusInFlight(INITIAL_HERDR_STATE, REF)).toBe(false);
    const asked = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF });
    expect(statusInFlight(asked, REF)).toBe(true);
    expect(statusInFlight(asked, OTHER)).toBe(false);
    const launching = reduce(available(), { type: 'sent', seq: 2, op: 'launch', missionRef: REF });
    expect(statusInFlight(launching, REF), 'a launch in flight is not a status').toBe(false);
    expect(statusInFlight(reduce(asked, { type: 'reply', reply: reply({ seq: 1 }) }), REF), 'answered').toBe(false);
  });

  it('sequence numbers are never reused', () => {
    const a = takeHerdrSeq();
    const b = takeHerdrSeq();
    expect(b).toBeGreaterThan(a);
  });
});

describe('what a reply does', () => {
  it('sending marks the Mission as being checked, or as launching; it does not touch another Mission', () => {
    const checking = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF });
    expect(missionState(checking, REF).check).toEqual({ kind: 'checking' });
    expect(missionState(checking, OTHER).check).toEqual({ kind: 'unknown' });
    const launching = reduce(available(), { type: 'sent', seq: 2, op: 'launch', missionRef: REF });
    expect(missionState(launching, REF).launch).toEqual({ kind: 'launching' });
    expect(missionState(launching, REF).check.kind, 'the check stands while launching').toBe('available');
  });

  it('an ok status makes the Mission available, carrying what the office established', () => {
    expect(missionState(available(), REF).check).toEqual({
      kind: 'available',
      agent: 'he-exec1',
      identityBasis: 'runtime_identity_verified',
      workspaceId: 'w6V',
      windowMinCols: 190,
      windowMinRows: 49,
      matchedPaneId: 'w6V:p3',
      terminalLabel: 'Terminal',
    });
  });

  it.each([
    ['refused', 'refused' as const],
    ['unavailable', 'unavailable' as const],
  ])('a %s status makes the Mission unavailable, with the office\'s code and its words UNCHANGED', (_name, state) => {
    const words = "Nothing was opened: the recorded session is not in Herdr's live agent list now.";
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { state, problem: 'not_live', reason: words, answer: null });
    expect(missionState(s, REF).check).toEqual({ kind: 'unavailable', problem: 'not_live', reason: words });
  });

  it('an ok status with no answer is not trusted: it is unavailable and says nothing is known', () => {
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { answer: null });
    expect(missionState(s, REF).check).toMatchObject({ kind: 'unavailable' });
  });

  it('a refusal with no words still says something, so no silent disable', () => {
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { state: 'refused', problem: 'x', reason: null, answer: null });
    expect((missionState(s, REF).check as { reason: string }).reason).toBe('The office refused without giving a reason.');
  });

  it('an ok launch records the workspace focused and the terminal state, as the office reported them', () => {
    const s = sentThenReplied(available(), 'launch', REF, launchOk);
    expect(missionState(s, REF).launch).toEqual({ kind: 'opened', workspaceId: 'w6V', windowMinCols: 190, windowMinRows: 49, matchedPaneId: 'w6V:p3', terminalLabel: 'Terminal', terminalState: 'opened' });
  });

  it('an ok launch that does not say the workspace is focused, or says nothing of the terminal, is NOT taken as opened', () => {
    const notFocused = sentThenReplied(available(), 'launch', REF, { op: 'launch', answer: { ...ANSWER, workspaceFocus: 'not_performed', terminalState: 'opened' } });
    expect(missionState(notFocused, REF).launch.kind).toBe('refused');
    const noTerminal = sentThenReplied(available(), 'launch', REF, { op: 'launch', answer: { ...ANSWER, workspaceFocus: 'focused', terminalState: null } });
    expect(missionState(noTerminal, REF).launch.kind).toBe('refused');
  });

  it.each(['no_binding', 'identity_not_verified', 'not_live', 'workspace_unknown', 'geometry_unknown', 'terminal_unavailable', 'herdr_not_configured', 'launcher_directory_unusable'])(
    'a launch refused as %s means the route is not available: the click stops being offered, with the same words',
    (problem) => {
      const s = sentThenReplied(available(), 'launch', REF, { op: 'launch', state: 'refused', problem, reason: 'the words', answer: null });
      expect(missionState(s, REF).check).toEqual({ kind: 'unavailable', problem, reason: 'the words' });
      expect(missionState(s, REF).launch).toEqual({ kind: 'refused', problem, reason: 'the words' });
      expect(herdrControlView(s, REF).enabled).toBe(false);
    },
  );

  it.each(['focus_not_established', 'launch_failed', 'launcher_not_written', 'launch_rate_limited', 'office_busy', 'herdr_route_failed'])(
    'a launch refused as %s is the result of one attempt: the words are shown and the click stays on offer',
    (problem) => {
      const s = sentThenReplied(available(), 'launch', REF, { op: 'launch', state: 'refused', problem, reason: 'the words', answer: null });
      expect(missionState(s, REF).check.kind).toBe('available');
      expect(missionState(s, REF).launch).toEqual({ kind: 'refused', problem, reason: 'the words' });
      const view = herdrControlView(s, REF);
      expect(view.enabled).toBe(true);
      expect(view.note).toBe('the words');
    },
  );

  it('a reply that matches nothing in flight changes nothing: not a stale seq, not another op, not an unsolicited reply', () => {
    const inFlight = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 40, op: 'status', missionRef: REF });
    expect(reduce(inFlight, { type: 'reply', reply: reply({ seq: 39 }) })).toBe(inFlight);
    expect(reduce(inFlight, { type: 'reply', reply: reply({ seq: 40, op: 'launch' }) })).toBe(inFlight);
    expect(reduce(INITIAL_HERDR_STATE, { type: 'reply', reply: reply({ seq: 40 }) })).toBe(INITIAL_HERDR_STATE);
  });

  it('a reply is applied to the Mission it was asked about, whichever Mission is on screen now', () => {
    const asked = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 5, op: 'status', missionRef: REF });
    const s = reduce(asked, { type: 'reply', reply: reply({ seq: 5 }) });
    expect(missionState(s, REF).check.kind).toBe('available');
    expect(missionState(s, OTHER).check.kind).toBe('unknown');
  });

  it('a Mission reference that names an object prototype member is just another reference', () => {
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', '__proto__', {});
    expect(missionState(s, '__proto__').check.kind).toBe('available');
    expect(missionState(s, 'constructor').check.kind).toBe('unknown');
  });

  it('losing the office clears everything and the request in flight; hearing from it again leaves every Mission to be asked again', () => {
    const lost = reduce(available(), { type: 'unvouched', reason: 'disconnected' });
    expect(lost.unvouched).toBe('disconnected');
    expect(lost.pending).toBeNull();
    expect(missionState(lost, REF).check.kind).toBe('unknown');
    const back = reduce(lost, { type: 'unvouched', reason: null });
    expect(back.unvouched).toBeNull();
    expect(missionState(back, REF).check.kind).toBe('unknown');
  });
});

describe('what the control shows: enabled by the office\'s answer, worded by it', () => {
  it('with no control wired, disabled, saying that', () => {
    expect(herdrControlView(null, REF)).toMatchObject({ enabled: false, note: HERDR_NOT_CONNECTED });
  });

  it('with no Mission open, disabled, saying to open one', () => {
    expect(herdrControlView(INITIAL_HERDR_STATE, null)).toMatchObject({ enabled: false, note: HERDR_NO_MISSION });
  });

  it('an office that is not heard from is disabled and says why', () => {
    const view = herdrControlView(reduce(INITIAL_HERDR_STATE, { type: 'unvouched', reason: 'disconnected' }), REF);
    expect(view.enabled).toBe(false);
    expect(view.note).toContain('disconnected');
  });

  it('before an answer it is disabled and offers to check; while asked it is disabled and busy', () => {
    expect(herdrControlView(INITIAL_HERDR_STATE, REF)).toMatchObject({ enabled: false, busy: false, canRecheck: true });
    const checking = reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF });
    expect(herdrControlView(checking, REF)).toMatchObject({ enabled: false, busy: true, canRecheck: false, tone: 'working' });
  });

  it('where unavailable, it is disabled, shows the office\'s reason verbatim, and offers to check again', () => {
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { state: 'unavailable', problem: 'herdr_not_configured', reason: 'Herdr is not set up in this office.', answer: null });
    expect(herdrControlView(s, REF)).toMatchObject({ enabled: false, note: 'Herdr is not set up in this office.', tone: 'refused', canRecheck: true });
  });

  it('where available, it is enabled and says what a click does: the workspace focused first, the pane as identity evidence only, nothing typed', () => {
    const view = herdrControlView(available(), REF);
    expect(view.enabled).toBe(true);
    expect(view.note).toBe(
      "Opens Terminal on this Mission's herd. It first asks Herdr to focus workspace w6V, and types nothing into any pane or terminal. Pane w6V:p3 is matched as identity evidence only; no pane is focused. The catalog's runtime identity for the role is verified, and Herdr's live list names its session once." +
        windowWords(190, 49),
    );
  });

  it('the window requirement in the note is the OFFICE\'s figure, whatever it is, and is worded as a requirement, never as something achieved', () => {
    const big = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { answer: { ...ANSWER, windowMinCols: 233, windowMinRows: 71 } });
    const view = herdrControlView(big, REF);
    expect(view.note).toContain('at least 233 x 71 cells');
    expect(view.note).not.toContain('190');
    expect(view.note).not.toMatch(/was resized|has been resized|is large enough|was large enough|now large|now fits|fits the herd/i);
    expect(view.note).not.toMatch(OVERCLAIM);
  });

  // The two bases are different claims of different strength (a runtime identity the catalog VERIFIED; the team roster listing the role present with equal recorded and live sessions). They reach the person
  // as what they are, in different words, and neither is ever worded as the other.
  describe('the basis that vouched is shown as what it is, and the two are never collapsed into one word', () => {
    const roster = () => sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { answer: { ...ANSWER, identityBasis: 'team_roster_session_confirmed' } });

    it('a runtime-verified role says its runtime identity is verified, and does not mention a roster', () => {
      const note = herdrControlView(available(), REF).note;
      expect(note).toContain("The catalog's runtime identity for the role is verified");
      expect(note).not.toMatch(/roster/);
    });

    it('a roster-confirmed role says it was found in the roster with equal sessions, and says in so many words that this is not a runtime-identity verification', () => {
      const note = herdrControlView(roster(), REF).note;
      expect(note).toContain("The role was found in the catalog's team roster with its recorded and live sessions equal (a roster confirmation, not a runtime-identity verification)");
      expect(note).not.toMatch(/runtime identity for the role is verified/);
      expect(note).not.toMatch(/\bis verified\b|\bwas verified\b/);
    });

    it('both say Herdr\'s own live list names the session once, because the route cross-checks that for either', () => {
      for (const state of [available(), roster()]) expect(herdrControlView(state, REF).note).toContain("Herdr's live list names its session once");
    });

    it('the basis is carried in the Mission\'s state, not inferred from the words', () => {
      expect((missionState(roster(), REF).check as { identityBasis: string }).identityBasis).toBe('team_roster_session_confirmed');
      expect((missionState(available(), REF).check as { identityBasis: string }).identityBasis).toBe('runtime_identity_verified');
    });

    it('neither basis note claims an attach or a working keyboard', () => {
      for (const state of [available(), roster()]) expect(herdrControlView(state, REF).note).not.toMatch(OVERCLAIM);
    });
  });

  it('a pane the office did not match is not mentioned', () => {
    const s = sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { answer: { ...ANSWER, matchedPaneId: null } });
    expect(herdrControlView(s, REF).note).not.toContain('Pane');
  });

  it('while a launch is in flight it is disabled and busy', () => {
    const launching = reduce(available(), { type: 'sent', seq: 2, op: 'launch', missionRef: REF });
    expect(herdrControlView(launching, REF)).toMatchObject({ enabled: false, busy: true, outcome: true });
  });

  it('after an opened terminal the note states exactly the two receipts: Herdr reported the workspace focused, and the terminal was asked to open', () => {
    const view = herdrControlView(sentThenReplied(available(), 'launch', REF, launchOk), REF);
    expect(view.note).toBe(
      'Herdr reported workspace w6V focused, then Terminal was asked to open the Herdr launcher. Look at the Terminal window to see what it shows; nothing was typed into any pane or terminal. Pane w6V:p3 is matched as identity evidence only; no pane is focused.' +
        windowWords(190, 49),
    );
    expect(view).toMatchObject({ enabled: true, tone: 'ok', outcome: true });
  });

  it('an unconfirmed open says the window may not have opened', () => {
    const view = herdrControlView(
      sentThenReplied(available(), 'launch', REF, { op: 'launch', answer: { ...ANSWER, workspaceFocus: 'focused', terminalState: 'unconfirmed' } }),
      REF,
    );
    expect(view.note).toContain('had not finished within its time limit, so the window may not have opened');
  });

  it('NO state the control can be in claims an attach, Herdr showing the herd, or a working keyboard', () => {
    const states: HerdrState[] = [
      INITIAL_HERDR_STATE,
      reduce(INITIAL_HERDR_STATE, { type: 'sent', seq: 1, op: 'status', missionRef: REF }),
      reduce(INITIAL_HERDR_STATE, { type: 'unvouched', reason: 'disconnected' }),
      sentThenReplied(INITIAL_HERDR_STATE, 'status', REF, { state: 'refused', problem: 'not_live', reason: 'r', answer: null }),
      available(),
      reduce(available(), { type: 'sent', seq: 2, op: 'launch', missionRef: REF }),
      sentThenReplied(available(), 'launch', REF, launchOk),
      sentThenReplied(available(), 'launch', REF, { op: 'launch', answer: { ...ANSWER, workspaceFocus: 'focused', terminalState: 'unconfirmed' } }),
      sentThenReplied(available(), 'launch', REF, { op: 'launch', state: 'refused', problem: 'launch_failed', reason: 'r', answer: null }),
    ];
    for (const state of states) expect(herdrControlView(state, REF).note).not.toMatch(OVERCLAIM);
    expect(HERDR_NOT_CONNECTED).not.toMatch(OVERCLAIM);
    expect(HERDR_NO_MISSION).not.toMatch(OVERCLAIM);
  });

  it('the old static note is gone from every state: nothing says the office does not start Herdr, or points at another tool', () => {
    const view = herdrControlView(available(), REF).note + herdrControlView(null, REF).note + herdrControlView(INITIAL_HERDR_STATE, REF).note;
    expect(view).not.toMatch(/does not start|herdctl|your own terminal/);
  });
});
