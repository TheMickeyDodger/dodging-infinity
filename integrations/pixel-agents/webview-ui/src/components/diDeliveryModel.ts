/**
 * b3 — the office delivery panel's model. Pure functions and a reducer: no React, no DOM, no transport. Everything the panel shows or
 * may send is decided here, so it can be tested without a browser and a renderer cannot disagree with it.
 *
 * The rules, each enforced below:
 *  - ONE delivery id, pattern-checked before anything is sent, with NO SELECTABLE HISTORY AND NO ENUMERATION: no list, and no earlier
 *    delivery can be chosen from this window. A notice the panel keeps names the delivery it is about, so an earlier id can stay
 *    visible in a notice; that is not a list to pick from (round 55). That is scope and minimization, NOT authentication. A
 *    pattern-valid id shows only that an identifier was supplied; the authority is the delivery's own record and ceremony.
 *    WHERE THE ID COMES FROM is the session's `subject`: the standalone panel's is ENTERED by the person; Mission details' (M8) is
 *    LINKED, set only from the shown Mission's delivery link (the catalog's declared `delivery_link`), and ignores typed input.
 *  - The adapter's answer is UNTRUSTED data, shown as inert text. Eligibility comes from the RECORD, as the adapter states it, and only
 *    for COMMIT, PUSH or PR_CREATE, with a binding that names the displayed delivery and step. Never BASE_REFRESH. The adapter's two
 *    notes are shown VERBATIM, never paraphrased or truncated. Without its local-confirmation note, no step is offered.
 *  - Absence of data is not data about absence: a field the answer does not carry is shown as missing from the answer, a part that is
 *    there but cannot be read as unreadable, and only a field the record gives as null as "none recorded".
 *  - A STATUS ANSWER COUNTS ONLY FOR THE ID ENTERED (OR LINKED) AT THE MOMENT IT ARRIVES. Request identity (seq and op) is not selection identity:
 *    the right answer to the right request is still the wrong answer for a screen whose id was changed while it was on its way, so such
 *    an answer is not shown, and nothing of it is kept.
 *  - The LOCAL confirmation is possible only with an eligible step from the status on display, for the id entered now, and it clears on
 *    any reply, on any newer read (when it is SENT) and on any change of id. That keeps the panel from DISPLAYING superseded
 *    eligibility. What refuses a confirmation against
 *    eligibility that went stale after the read is the SERVER guard (office_delivery/adapter.py `check_confirmation`, and pr_delivery's
 *    advance_once checking the expected step against the record it loads), with its own limits: not a lock, not isolation, not an
 *    exactly-once proof.
 *  - Nothing here resends an advance. After ANY advance reply, the next action is a status READ of the same delivery, never another
 *    advance. A read changes nothing; its answer may still show the step executing or unresolved, and it settles nothing.
 *  - `refused` is never worded as "nothing happened", "nothing changed" or "safe to retry": the adapter answers an exception during a
 *    step with ok false too. The two busy codes are told apart by their CODE, never by their prose.
 *  - No word or control here implies merge, release or deploy.
 */
import type { DiDeliveryOp, DiDeliveryReply, DiDeliveryRequest } from '../../../core/src/messages.js';

export const DL_ID_PATTERN = /^prd-[0-9a-f]{24}$/;
/** The office's action steps, exactly (office_delivery/adapter.py OFFICE_ACTION_STEPS). BASE_REFRESH is not one of them. */
export const DL_ACTION_STEPS = ['COMMIT', 'PUSH', 'PR_CREATE'] as const;
export type DlStep = (typeof DL_ACTION_STEPS)[number];
const DIGEST = /^[0-9a-f]{64}$/;
const BINDING_KEYS = ['delivery_id', 'revision', 'candidate_identity_digest_sha256', 'step'] as const;
const MAX_NOTICES = 6;

/** The exact binding of the one eligible step, in the adapter's own keys (returned unchanged in an advance). */
export interface DlBinding {
  delivery_id: string;
  revision: number;
  candidate_identity_digest_sha256: string;
  step: DlStep;
}

export interface DlFact {
  label: string;
  text: string;
}

export interface DlNotEligible {
  reason: string;
  step: string | null;
  detail: string;
}

/**
 * WHO established that no step is offered (round 58: a reason names its author). The adapter: its answer says no step is eligible. The
 * answer: this window could not use what the answer says about eligibility. This window: the answer names a step eligible but carries
 * no local-confirmation note, so this window offers no confirmation. Null: a step is offered.
 */
export type DlNoStepBy = 'adapter' | 'answer' | 'window';

/** One delivery's status as the adapter answered it, read into inert facts. */
export interface DlView {
  deliveryId: string;
  facts: DlFact[];
  eligible: DlBinding | null;
  /** The office step the answer names eligible, when this window could read it as one, whether or not it is offered here. */
  namedStep: DlStep | null;
  noStepBy: DlNoStepBy | null;
  notEligible: DlNotEligible | null;
  localConfirmationNote: string | null;
  performsStepsNote: string | null;
  readAtMs: number;
}

export type DlNoticeKind = 'info' | 'refusal' | 'unknown' | 'unavailable' | 'busy';

export interface DlNotice {
  id: number;
  kind: DlNoticeKind;
  op: DiDeliveryOp;
  heading: string;
  text: string;
  /**
   * The delivery this notice is about. An answer the panel KEEPS names its subject, because a reader binds a notice to what is
   * displayed beside it, and the entered id may have changed since its request was sent. A DISCARDED answer's notice names nothing
   * (null), so nothing of that answer, its id included, is kept. Complementary rules: refuse it or label it, never keep it silent.
   */
  deliveryId: string | null;
}

export interface DlPending {
  seq: number;
  op: DiDeliveryOp;
  deliveryId: string;
}

/** Where the ONE id comes from: typed by the person (the standalone panel), or the Mission's own delivery link (Mission details). */
export type DlSubject = 'entered' | 'linked';

export interface DlState {
  subject: DlSubject;
  open: boolean;
  /**
   * The ONE id: what the person typed (subject `entered`), or the shown Mission's linked delivery id (subject `linked`; empty when
   * the Mission has no link). No other id can be chosen; a kept notice may still name an earlier one.
   */
  entered: string;
  view: DlView | null;
  confirmed: boolean;
  /** At most one request waits: the office relay holds one delivery call at a time. */
  pending: DlPending | null;
  notices: DlNotice[];
  /** After an advance reply: the delivery whose status is read next (a READ, never an advance). */
  followUpRead: string | null;
  nextNotice: number;
  unvouched: string | null;
}

/**
 * ONE sequence for every delivery session of this window (M8): the standalone panel's and Mission details' sessions both send
 * `diDeliveryRequest`, and a reply is matched by its seq and op only, so no two requests of this window may share a seq.
 */
let nextDeliverySeq = 1;
export function takeDeliverySeq(): number {
  const seq = nextDeliverySeq;
  nextDeliverySeq += 1;
  return seq;
}

export const INITIAL_DL_STATE: DlState = {
  subject: 'entered',
  open: false,
  entered: '',
  view: null,
  confirmed: false,
  pending: null,
  notices: [],
  followUpRead: null,
  nextNotice: 1,
  unvouched: null,
};

/** M8: Mission details' session. Its ONE id is the shown Mission's delivery link, set only by `linked`; nothing typed reaches it. */
export const INITIAL_DL_LINKED_STATE: DlState = { ...INITIAL_DL_STATE, subject: 'linked' };

/** Why a status answer is not shown, in the words of where the id came from. */
const DISCARDED: Record<DlSubject, string> = {
  entered:
    'It answers the read of the delivery id entered before, and a different id is entered now, so it is not shown. Nothing of that answer is kept.',
  linked:
    'It answers the read of the delivery linked to the Mission shown before, and a different delivery, or none, is linked to the Mission shown now, so it is not shown. Nothing of that answer is kept.',
};

export type DlEvent =
  | { type: 'open' }
  | { type: 'close' }
  | { type: 'entered'; text: string }
  | { type: 'linked'; deliveryId: string | null }
  | { type: 'confirm'; on: boolean }
  | { type: 'sent'; seq: number; op: DiDeliveryOp; deliveryId: string }
  | { type: 'reply'; reply: DiDeliveryReply; nowMs: number }
  | { type: 'unvouched'; reason: string | null }
  | { type: 'dismiss'; id: number };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStep(value: unknown): value is DlStep {
  return typeof value === 'string' && (DL_ACTION_STEPS as readonly string[]).includes(value);
}

/** The entered id, if it is exactly a delivery id once surrounding spaces are removed. */
export function enteredId(state: DlState): string | null {
  const id = state.entered.trim();
  return DL_ID_PATTERN.test(id) ? id : null;
}

/** A field the answer does not carry (round 54): NOT a statement that the record holds none. */
const MISSING = 'not in the adapter’s answer';
/** A part of the answer that is there but is not the shape that could carry the field. */
const UNREADABLE = 'unreadable in the adapter’s answer';

/** One field of the answer: its value, or that it is missing from the answer, or that its container cannot be read. */
type DlRead = { kind: 'value'; value: unknown } | { kind: 'missing' } | { kind: 'unreadable' };

function readField(container: unknown, key: string): DlRead {
  if (container === undefined) return { kind: 'missing' };
  // a container the record gives as null holds nothing, as the record says: each of its fields reads "none recorded"
  if (container === null) return { kind: 'value', value: null };
  if (!isObject(container)) return { kind: 'unreadable' };
  if (!Object.hasOwn(container, key)) return { kind: 'missing' };
  return { kind: 'value', value: container[key] };
}

/** A value of the adapter's answer as inert text: a string as it is, a number or boolean written out, anything else as JSON. */
function inert(value: unknown): string {
  if (value === null) return 'none recorded';
  if (value === undefined) return MISSING;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function shown(r: DlRead): string {
  if (r.kind === 'missing') return MISSING;
  if (r.kind === 'unreadable') return UNREADABLE;
  return inert(r.value);
}

/** One step's view, as recorded. A step the answer leaves out still gets its fact, saying so; it is never silently dropped. */
function stepFacts(name: string, view: unknown, extraKey: string | null): DlFact[] {
  const label = `${name}, as recorded`;
  if (view === undefined) return [{ label, text: MISSING }];
  if (view !== null && !isObject(view)) return [{ label, text: UNREADABLE }];
  const keys = ['state', 'receipt_id', 'receipt_state', 'attempts', ...(extraKey === null ? [] : [extraKey])];
  const names: Record<string, string> = { state: 'state', receipt_id: 'receipt', receipt_state: 'receipt state', attempts: 'attempts' };
  return [{ label, text: keys.map((key) => `${names[key] ?? key}: ${shown(readField(view, key))}`).join('; ') }];
}

/** The eligible binding, only when it is exactly an office step's binding for the displayed delivery; otherwise null. */
function readBinding(deliveryId: string, eligible: unknown): DlBinding | null {
  if (!isObject(eligible) || !isStep(eligible.step) || !isObject(eligible.binding)) return null;
  const b = eligible.binding;
  if (Object.keys(b).length !== BINDING_KEYS.length || !BINDING_KEYS.every((k) => Object.hasOwn(b, k))) return null;
  if (b.delivery_id !== deliveryId || b.step !== eligible.step) return null;
  if (typeof b.revision !== 'number' || !Number.isInteger(b.revision) || b.revision < 1) return null;
  if (typeof b.candidate_identity_digest_sha256 !== 'string' || !DIGEST.test(b.candidate_identity_digest_sha256)) return null;
  return {
    delivery_id: b.delivery_id,
    revision: b.revision,
    candidate_identity_digest_sha256: b.candidate_identity_digest_sha256,
    step: eligible.step,
  };
}

/** ONLY when the answer names a step by a readable step word that this window cannot use as one of this office's steps' bindings. */
const UNREADABLE_ELIGIBLE: DlNotEligible = {
  reason: 'eligible_step_unreadable',
  step: null,
  detail: 'This window could not read the eligible step the adapter named as one of this office’s steps for this delivery, so no step can be confirmed from here.',
};

/**
 * No readable claim about eligibility: the answer names no step by a readable step word (an eligible object that is empty or partial),
 * and gives no readable reason why none is eligible (a not_eligible that is absent, empty or partial). No step the adapter named is
 * involved, and no verdict of the adapter's is shown (rounds 58 and 59).
 */
const UNREADABLE_ELIGIBILITY: DlNotEligible = {
  reason: 'eligibility_unreadable',
  step: null,
  detail: 'This window could not read the adapter’s answer about which step is eligible, so no step can be confirmed from here.',
};

const NOTE_MISSING: DlNotEligible = {
  reason: 'confirmation_note_missing',
  step: null,
  detail: 'The adapter’s answer carries no local-confirmation note, so no step is offered for confirmation here.',
};

/** One delivery's status answer (`{ok: true, status: {delivery, office}}`), read into inert facts; null when it is not that delivery's. */
export function parseStatusView(deliveryId: string, answer: unknown, nowMs: number): DlView | null {
  if (!isObject(answer) || !isObject(answer.status)) return null;
  const d = answer.status.delivery;
  const o = answer.status.office;
  if (!isObject(d) || !isObject(o) || d.delivery_id !== deliveryId) return null;
  const a = d.authorization;
  const facts: DlFact[] = [
    { label: 'Delivery', text: deliveryId },
    { label: 'Revision', text: shown(readField(d, 'revision')) },
    { label: 'Phase', text: shown(readField(d, 'phase')) },
    { label: 'Authorization valid (as recorded)', text: shown(readField(a, 'valid')) },
    { label: 'Authorization problem', text: shown(readField(a, 'problem')) },
    { label: 'Authorized by', text: shown(readField(a, 'human_identity')) },
    { label: 'Allowed actions', text: shown(readField(a, 'allowed_actions')) },
    { label: 'Mode', text: shown(readField(a, 'mode')) },
    { label: 'Repository', text: shown(readField(a, 'repository_url')) },
    { label: 'Source branch', text: shown(readField(a, 'source_branch')) },
    { label: 'Target base branch', text: shown(readField(a, 'target_base_branch')) },
    ...stepFacts('BASE_REFRESH', d.base_refresh, null),
    ...stepFacts('COMMIT', d.commit, 'commit_oid'),
    ...stepFacts('PUSH', d.push, 'remote_oid'),
    { label: 'Pull request', text: shown(readField(d, 'pr_url')) },
    { label: 'Blocker', text: shown(readField(d, 'blocker')) },
    { label: 'Next action (as recorded)', text: shown(readField(d, 'next_action')) },
  ];
  const localConfirmationNote = typeof o.local_confirmation_note === 'string' ? o.local_confirmation_note : null;
  const performsStepsNote = typeof o.performs_steps_note === 'string' ? o.performs_steps_note : null;
  let eligible: DlBinding | null = null;
  let namedStep: DlStep | null = null;
  let noStepBy: DlNoStepBy | null = null;
  let notEligible: DlNotEligible | null = null;
  // ROUND 59: presence and shape are not content. "The adapter named a step" needs a readable step word, and "the adapter's verdict"
  // needs a readable reason; anything less is the answer this window could not use (UNREADABLE_ELIGIBILITY), never a named step or a
  // verdict made from an empty or partial object.
  const namesAStep = isObject(o.eligible) && typeof o.eligible.step === 'string' && o.eligible.step !== '';
  const statesAReason = isObject(o.not_eligible) && typeof o.not_eligible.reason === 'string' && o.not_eligible.reason !== '';
  if (o.eligible !== null && o.eligible !== undefined) {
    eligible = readBinding(deliveryId, o.eligible);
    if (eligible === null) {
      // an eligible step this window cannot use: named (a readable step word, not usable as an office step's binding), or not named at all
      notEligible = namesAStep ? UNREADABLE_ELIGIBLE : UNREADABLE_ELIGIBILITY;
      noStepBy = 'answer';
    } else if (localConfirmationNote === null) {
      // the answer names it; THIS WINDOW offers no confirmation without the note
      namedStep = eligible.step;
      eligible = null;
      notEligible = NOTE_MISSING;
      noStepBy = 'window';
    } else {
      namedStep = eligible.step;
    }
  } else if (statesAReason && isObject(o.not_eligible)) {
    // the ADAPTER's own verdict, with its readable reason: no step is eligible
    const ne = o.not_eligible;
    notEligible = {
      reason: shown(readField(ne, 'reason')),
      step: typeof ne.step === 'string' ? ne.step : null,
      detail: shown(readField(ne, 'detail')),
    };
    noStepBy = 'adapter';
  } else {
    notEligible = UNREADABLE_ELIGIBILITY;
    noStepBy = 'answer';
  }
  return { deliveryId, facts, eligible, namedStep, noStepBy, notEligible, localConfirmationNote, performsStepsNote, readAtMs: nowMs };
}

/** The busy heading, by CODE: the two conditions are told apart here, never from the prose. */
export function busyHeading(problem: string): string {
  if (problem === 'office_busy_advance_in_flight') return 'An advance from this office is still in progress; its step may still be running';
  if (problem === 'office_busy_status_in_flight') return 'A status read from this office is still in progress';
  return 'This office is busy with another delivery request';
}

const BUSY_CODES: ReadonlySet<string> = new Set(['office_busy_advance_in_flight', 'office_busy_status_in_flight']);

/** The adapter's result for an advance, as inert text: its own fields, never summarised into a claim. */
function resultWords(answer: Record<string, unknown> | null): string {
  const result = answer === null ? undefined : answer.result;
  // a result that cannot be read passes through, so its receipt reads as unreadable too, not as missing
  const receipt = isObject(result) ? result.receipt : result;
  return [
    `step: ${shown(readField(result, 'step'))}`,
    `advanced: ${shown(readField(result, 'advanced'))}`,
    `outcome: ${shown(readField(result, 'outcome'))}`,
    `receipt: ${shown(readField(receipt, 'receipt_id'))}`,
    `receipt state: ${shown(readField(receipt, 'state'))}`,
  ].join('; ');
}

function noticeFor(reply: DiDeliveryReply): Omit<DlNotice, 'id' | 'deliveryId'> | null {
  const { op, state, problem } = reply;
  const reason = reply.reason ?? '';
  if (state === 'ok') {
    if (op !== 'advanceOnce') return null;
    return {
      kind: 'info',
      op,
      heading: 'The adapter answered this step request',
      text: `As the adapter reported it: ${resultWords(reply.answer)}. The delivery's status is read again now.`,
    };
  }
  if (state === 'unknown') {
    return {
      kind: 'unknown',
      op,
      heading: 'Whether a step was taken is not known',
      text: `${reason} The status is read again now; it may still show the step executing or unresolved, and that read settles nothing by itself.`.trim(),
    };
  }
  if (state === 'unavailable') {
    return {
      kind: 'unavailable',
      op,
      heading: op === 'status' ? 'The delivery status could not be read' : 'The step request did not reach a step',
      text: reason,
    };
  }
  if (problem !== null && BUSY_CODES.has(problem)) {
    return { kind: 'busy', op, heading: busyHeading(problem), text: reason };
  }
  if (problem === 'request_invalid') {
    return { kind: 'refusal', op, heading: 'This office refused to send that request', text: reason };
  }
  return {
    kind: 'refusal',
    op,
    heading: op === 'advanceOnce' ? 'The adapter refused this step request' : 'The adapter refused to read this delivery',
    text:
      op === 'advanceOnce'
        ? `${reason} (${problem ?? 'no code given'}) A refusal does not establish that nothing ran: the delivery's status is the record of what was done, and it is read again now.`.trim()
        : `${reason} (${problem ?? 'no code given'})`.trim(),
  };
}

function withNotice(state: DlState, notice: Omit<DlNotice, 'id'> | null): DlState {
  if (notice === null) return state;
  return {
    ...state,
    nextNotice: state.nextNotice + 1,
    notices: [{ ...notice, id: state.nextNotice }, ...state.notices].slice(0, MAX_NOTICES),
  };
}

function applyReply(state: DlState, reply: DiDeliveryReply, nowMs: number): DlState {
  const pending = state.pending;
  // Only the reply to the request waiting is applied: one from before (or another window's) is not this panel's.
  if (pending === null || reply.seq !== pending.seq || reply.op !== pending.op) return state;
  // SELECTION IDENTITY (round 54): a STATUS answer counts only for the id entered now. The right answer to the right request is still
  // the wrong answer for a screen whose id was changed while it was on its way: it is not shown, and nothing of it, its id included,
  // is kept. An advance's answer is applied whatever is entered: it reports what happened to that step request.
  if (reply.op === 'status' && enteredId(state) !== pending.deliveryId) {
    return withNotice(
      { ...state, pending: null, confirmed: false },
      {
        kind: 'info',
        op: 'status',
        heading: 'A status answer is not shown',
        text: DISCARDED[state.subject],
        // DISCARDED, so it names nothing
        deliveryId: null,
      },
    );
  }
  let next: DlState = { ...state, pending: null, confirmed: false };
  if (reply.op === 'advanceOnce') {
    // Whatever the answer, the displayed eligibility is superseded: the next action is a READ of the same delivery.
    next = { ...next, view: null, followUpRead: pending.deliveryId };
  } else if (reply.state === 'ok') {
    const view = parseStatusView(pending.deliveryId, reply.answer, nowMs);
    next = { ...next, view };
    if (view === null) {
      return withNotice(next, {
        kind: 'unavailable',
        op: 'status',
        heading: 'The delivery status could not be read',
        text: 'The adapter’s answer is not a status of the delivery that was asked for, so nothing of it is shown.',
        deliveryId: pending.deliveryId,
      });
    }
  } else {
    next = { ...next, view: null };
  }
  // KEPT, so it names its subject: the delivery its request was for, whatever is entered now
  const words = noticeFor(reply);
  return withNotice(next, words === null ? null : { ...words, deliveryId: pending.deliveryId });
}

export function reduce(state: DlState, event: DlEvent): DlState {
  switch (event.type) {
    case 'open':
      return { ...state, open: true };
    case 'close':
      return { ...state, open: false };
    case 'entered': {
      // a linked session takes no typed id: its ONE id is the shown Mission's link (no manual id inside a Mission)
      if (state.subject === 'linked') return state;
      const changed = state.view !== null && state.view.deliveryId !== event.text.trim();
      return { ...state, entered: event.text, confirmed: false, view: changed ? null : state.view };
    }
    case 'linked': {
      // only a linked session follows a Mission's link; the standalone session's id is the person's
      if (state.subject !== 'linked') return state;
      const id = event.deliveryId ?? '';
      const changed = state.view !== null && state.view.deliveryId !== id;
      return { ...state, entered: id, confirmed: false, view: changed ? null : state.view };
    }
    case 'confirm':
      if (!event.on) return { ...state, confirmed: false };
      if (state.view?.eligible == null || state.pending !== null || state.unvouched !== null) return state;
      // only for the id entered now: a view whose id is not the entered one is never confirmable
      if (state.view === null || state.view.deliveryId !== enteredId(state)) return state;
      return { ...state, confirmed: true };
    case 'sent':
      return {
        ...state,
        pending: { seq: event.seq, op: event.op, deliveryId: event.deliveryId },
        followUpRead: event.op === 'status' ? null : state.followUpRead,
        // a newer read supersedes the displayed eligibility at the moment it is SENT (round 54)
        confirmed: event.op === 'status' ? false : state.confirmed,
      };
    case 'reply':
      return applyReply(state, event.reply, event.nowMs);
    case 'unvouched': {
      if (event.reason === null) return { ...state, unvouched: null };
      const cleared: DlState = { ...state, unvouched: event.reason, view: null, confirmed: false };
      const pending = state.pending;
      if (pending === null) return cleared;
      const lost: DlState = { ...cleared, pending: null, followUpRead: pending.deliveryId };
      return pending.op === 'advanceOnce'
        ? withNotice(lost, {
            kind: 'unknown',
            op: 'advanceOnce',
            heading: 'Whether a step was taken is not known',
            text: 'The connection to this office was lost while a step request was waiting for its answer. The status is read again when the office is back; it may still show the step executing or unresolved.',
            deliveryId: pending.deliveryId,
          })
        : lost;
    }
    case 'dismiss':
      return { ...state, notices: state.notices.filter((n) => n.id !== event.id) };
  }
}

/**
 * The line said while a request waits, naming ITS delivery (round 58): the shown delivery is pending, or the shown delivery cannot be
 * read yet because a request about a DIFFERENT delivery is in flight. Those are different facts. In a linked session (Mission details)
 * the other delivery's id is not named: it belongs to another Mission. Null: nothing waits.
 */
export function pendingLine(state: DlState): string | null {
  const pending = state.pending;
  if (pending === null) return null;
  const shown = enteredId(state);
  const what = pending.op === 'advanceOnce' ? 'the step request for' : 'the status read of';
  if (pending.deliveryId === shown) return `Waiting for the delivery adapter’s answer to ${what} this delivery, ${pending.deliveryId}.`;
  if (state.subject === 'linked') {
    const blocked = shown === null ? 'Nothing can be read here yet' : 'This delivery’s status cannot be read yet';
    return pending.op === 'advanceOnce'
      ? `${blocked}: this window is waiting for the adapter’s answer to a step request for a different delivery, whose step may still be running.`
      : `${blocked}: this window is waiting for the adapter’s answer to a status read of a different delivery.`;
  }
  return shown === null
    ? `Waiting for the delivery adapter’s answer to ${what} delivery ${pending.deliveryId}.`
    : `The delivery entered now cannot be read yet: this window is waiting for the delivery adapter’s answer to ${what} delivery ${pending.deliveryId}, entered before.`;
}

/** The status read of the entered id: only a pattern-valid id, only when nothing waits and the office is vouched for. */
export function statusMessage(state: DlState, seq: number): DiDeliveryRequest | null {
  const id = enteredId(state);
  if (id === null || state.pending !== null || state.unvouched !== null) return null;
  return { type: 'diDeliveryRequest', seq, op: 'status', deliveryId: id };
}

/** The ONE advance: only with the local confirmation of an eligible step on display. It carries exactly the displayed binding. */
export function advanceMessage(state: DlState, seq: number): DiDeliveryRequest | null {
  const view = state.view;
  if (!state.confirmed || view === null || view.eligible === null || state.pending !== null || state.unvouched !== null) return null;
  // only for the id entered now (round 54): the displayed id and the confirmable binding never diverge
  if (view.deliveryId !== enteredId(state)) return null;
  return { type: 'diDeliveryRequest', seq, op: 'advanceOnce', deliveryId: view.deliveryId, confirmation: { ...view.eligible } };
}

/** After an advance reply: the status READ of that delivery. Never an advance. */
export function followUpMessage(state: DlState, seq: number): DiDeliveryRequest | null {
  if (state.followUpRead === null || state.pending !== null || state.unvouched !== null) return null;
  return { type: 'diDeliveryRequest', seq, op: 'status', deliveryId: state.followUpRead };
}
