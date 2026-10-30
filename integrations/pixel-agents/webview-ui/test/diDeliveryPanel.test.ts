/**
 * b3: the office delivery panel's MARKUP (renderToStaticMarkup: no DOM, no listener, no browser), and a structural pin on its hook.
 *
 * The markup is rendered from model states the reducer itself produces, so the panel is checked against what the model decides:
 *  - one id at a time, and no list to choose from (an earlier id can stay visible only in a notice that names its delivery);
 *  - an eligible step: under a visible LOCAL CHECK label, with the adapter's two notes VERBATIM, a confirmation box, and ONE button that
 *    is disabled until the box is ticked and names the step. Never a BASE_REFRESH control;
 *  - not eligible: the adapter's reason and detail, and NO step control;
 *  - the panel's own words never claim that nothing happened or changed, never say safe to retry, and name no merge, release or deploy.
 * THE HOOK (useDiDelivery.ts) is not executed by any test (this route has no DOM renderer). Its STRUCTURE is pinned from its parsed source:
 * no timer of any kind, ONE transport.send, and every send(...) passes one of the model's three builders.
 */
import * as fs from 'fs';
import * as path from 'path';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { fileURLToPath } from 'url';
import { describe, expect, it } from 'vitest';

import type { DiDeliveryReply, DiOfficeState } from '../../core/src/messages.ts';
import * as panelModule from '../src/components/DiDeliveryEntry.tsx';
import type { DlEvent, DlState } from '../src/components/diDeliveryModel.ts';
import { INITIAL_DL_STATE, reduce } from '../src/components/diDeliveryModel.ts';
import type { DiMissionRequestAccess } from '../src/components/DiMissionRequestEntry.tsx';
import { INITIAL_STATE as INITIAL_MR_STATE } from '../src/components/diMissionRequestModel.ts';
import { DiOfficePanel } from '../src/components/DiOfficePanel.tsx';
import { officePanelPresentation } from '../src/components/diScanModel.ts';
import type { OfficeState } from '../src/office/engine/officeState.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const ID = `prd-${'a'.repeat(24)}`;
const LOCAL_NOTE = 'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps.';
const PERFORMS_NOTE = 'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh).';
const binding = (step = 'COMMIT'): Any => ({ delivery_id: ID, revision: 4, candidate_identity_digest_sha256: 'c'.repeat(64), step });
const office = (over: Any = {}): Any => ({
  eligible: { step: 'COMMIT', binding: binding() },
  not_eligible: null,
  action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
  local_confirmation_note: LOCAL_NOTE,
  performs_steps: true,
  performs_steps_note: PERFORMS_NOTE,
  ...over,
});
const delivery: Any = {
  delivery_id: ID,
  revision: 4,
  phase: 'base_refreshed',
  authorization: { valid: true },
  commit: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0 },
  base_refresh: { state: 'succeeded', receipt_id: 'rcpt-base-1', receipt_state: 'succeeded', attempts: 1 },
  next_action: { action: 'run_step', step: 'COMMIT' },
};
const reply = (seq: number, op: 'status' | 'advanceOnce', state: DiDeliveryReply['state'], problem: string | null, reason: string | null, answer: Any | null): DiDeliveryReply =>
  ({ type: 'diDeliveryReply', seq, op, state, problem, reason, answer }) as DiDeliveryReply;
const states = (...events: DlEvent[]): DlState => events.reduce((s: DlState, e: DlEvent) => reduce(s, e), { ...INITIAL_DL_STATE, open: true });
const shown = (o: Any = office()): DlState =>
  states(
    { type: 'entered', text: ID },
    { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
    { type: 'reply', reply: reply(1, 'status', 'ok', null, null, { ok: true, status: { delivery, office: o } }), nowMs: 1 },
  );
const noop = (): void => undefined;
const control = (state: DlState): Any => ({
  state,
  open: noop,
  close: noop,
  setEntered: noop,
  setConfirmed: noop,
  readStatus: noop,
  askForStep: noop,
  dismiss: noop,
});
const Panel = (panelModule as Any).DiDeliveryPanel as ((props: Any) => unknown) | undefined;
const Entry = (panelModule as Any).DiDeliveryEntry as ((props: Any) => unknown) | undefined;
const html = (state: DlState): string => (typeof Panel === 'function' ? renderToStaticMarkup(createElement(Panel as never, { control: control(state) })) : '');
/** The markup with the adapter's own words removed, so the panel's OWN words can be checked. */
const ownWords = (markup: string, ...adapterWords: string[]): string => adapterWords.reduce((m, w) => m.split(w).join(''), markup);
const FORBIDDEN = /(?<!establish that )nothing (happened|changed|ran)|safe to (retry|try again)|try again|merge|release|deploy/i;
const buttonsIn = (markup: string): string[] => [...markup.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((x) => x[0]);

describe('the panel and its entry exist', () => {
  it('exports the entry and the panel', () => {
    expect(typeof Entry).toBe('function');
    expect(typeof Panel).toBe('function');
  });
});

describe('one id at a time, and no list to choose from', () => {
  // ROUND 55: the panel's own words about what it keeps stay TRUE across the sequence in which an earlier id IS kept, in a notice that
  // names its delivery. What is guaranteed: one id at a time; no list to choose from, so no earlier delivery can be picked here. What is
  // NOT guaranteed, and so is never promised: that no earlier id stays visible.
  it("the panel's words about what it keeps stay true across the sequence in which an earlier id is kept in a notice", () => {
    const OTHER = `prd-${'b'.repeat(24)}`;
    const afterChange = [
      { type: 'confirm', on: true },
      { type: 'sent', seq: 2, op: 'advanceOnce', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'reply', reply: reply(2, 'advanceOnce', 'unknown', 'delivery_timeout', 'ADAPTER-WORDS', null), nowMs: 2 },
    ].reduce((s: DlState, e) => reduce(s, e as DlEvent), shown());
    const sequence: Array<[string, DlState]> = [
      ['nothing entered', states()],
      ['A shown', shown()],
      ['B entered, a notice about A kept', afterChange],
    ];
    for (const [what, s] of sequence) {
      const markup = html(s);
      expect(markup, what).not.toMatch(/keeps none|none of them|nothing of (a|the) (previous|earlier) id/i);
      expect(markup, what).toMatch(/one at a time/i);
      expect(markup, what).toMatch(/no earlier delivery can be (picked|chosen)/i);
      expect(markup, what).toMatch(/an earlier id can (stay|remain) visible in a notice/i);
      expect(markup, what).not.toMatch(/recent|history|previous deliver/i);
    }
    // and the retention those words describe does happen in the last state: the field shows B, a kept notice names A
    const last = html(afterChange);
    const start = last.indexOf('data-testid="di-dl-notice"');
    expect(start).toBeGreaterThanOrEqual(0);
    expect(last.slice(start, last.indexOf('</div>', start))).toContain(ID);
    expect(last).toContain(`value="${OTHER}"`);
  });

  it('offers one id field and a read that is disabled until the id is a delivery id; it shows no list of ids', () => {
    const empty = html(states());
    expect(empty).toContain('data-testid="di-dl-id"');
    expect(buttonsIn(empty).find((b) => b.includes('data-testid="di-dl-read"'))).toMatch(/disabled/);
    const valid = html(states({ type: 'entered', text: ID }));
    expect(buttonsIn(valid).find((b) => b.includes('data-testid="di-dl-read"'))).not.toMatch(/disabled/);
    expect(valid).not.toMatch(/recent|history|previous deliver/i);
    expect(valid).toMatch(/one at a time/i);
  });
});

describe('an eligible step, under a LOCAL CHECK label', () => {
  it("shows the adapter's two notes VERBATIM, the step, a confirmation box, and one button disabled until it is ticked", () => {
    const markup = html(shown());
    expect(markup).toContain(LOCAL_NOTE);
    expect(markup).toContain(PERFORMS_NOTE);
    expect(markup).toMatch(/LOCAL CHECK/);
    expect(markup).toContain('data-testid="di-dl-confirm"');
    const advance = buttonsIn(markup).filter((b) => b.includes('data-testid="di-dl-advance"'));
    expect(advance).toHaveLength(1);
    expect(advance[0]).toMatch(/disabled/);
    expect(advance[0]).toContain('COMMIT');
    const ticked = html(reduce(shown(), { type: 'confirm', on: true }));
    expect(buttonsIn(ticked).find((b) => b.includes('data-testid="di-dl-advance"'))).not.toMatch(/disabled/);
  });

  it('never offers a BASE_REFRESH control', () => {
    for (const o of [office(), office({ eligible: { step: 'BASE_REFRESH', binding: binding('BASE_REFRESH') } })]) {
      const markup = html(shown(o));
      expect(markup).toContain('data-testid="di-dl-view"');
      expect(buttonsIn(markup).some((b) => /BASE_REFRESH|base refresh/i.test(b))).toBe(false);
    }
  });

  it('shows the recorded facts as text (a receipt id as it came)', () => {
    expect(html(shown())).toContain('rcpt-base-1');
  });
});

describe('not eligible: the reason, and no step control', () => {
  it("shows the adapter's reason and detail, and no confirmation box and no step button", () => {
    const ne = { reason: 'base_refresh_unmet', step: 'BASE_REFRESH', detail: 'The base refresh comes first. This office does not perform it.' };
    const markup = html(shown(office({ eligible: null, not_eligible: ne })));
    expect(markup).toContain('data-testid="di-dl-view"');
    expect(markup).toContain(ne.detail);
    expect(markup).toContain(ne.reason);
    expect(markup).not.toContain('data-testid="di-dl-confirm"');
    expect(markup).not.toContain('data-testid="di-dl-advance"');
    // No button at all in the view (it is drawn last in the panel): neither the adapter's step nor anything else becomes a control here.
    const view = markup.slice(markup.indexOf('data-testid="di-dl-view"'));
    expect(view).toContain(ne.detail);
    expect(view).not.toMatch(/<button/);
  });
});

describe("the panel's own words", () => {
  it('a refusal, an unknown outcome and a busy refusal are shown with their headings, and the panel never claims nothing happened, never says safe to retry, and names no merge, release or deploy', () => {
    const refused = states(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'advanceOnce', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'advanceOnce', 'refused', 'guard_refused', 'ADAPTER-WORDS', null), nowMs: 1 },
    );
    const unknown = states(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'advanceOnce', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'advanceOnce', 'unknown', 'delivery_timeout', 'ADAPTER-WORDS', null), nowMs: 1 },
    );
    const busy = states(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'status', 'refused', 'office_busy_advance_in_flight', 'ADAPTER-WORDS', null), nowMs: 1 },
    );
    for (const [what, s] of [['refused', refused], ['unknown', unknown], ['busy', busy]] as const) {
      const markup = html(s);
      expect(markup, what).toContain('data-testid="di-dl-notice"');
      expect(markup, what).toContain(s.notices[0].heading);
      expect(ownWords(markup, 'ADAPTER-WORDS'), what).not.toMatch(FORBIDDEN);
    }
    expect(html(busy)).toMatch(/may still be running/);
  });

  it('an advance answer kept as a notice is drawn naming its delivery, while a different id is in the field', () => {
    const OTHER = `prd-${'b'.repeat(24)}`;
    const unknownAfterChange = [
      { type: 'confirm', on: true },
      { type: 'sent', seq: 2, op: 'advanceOnce', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'reply', reply: reply(2, 'advanceOnce', 'unknown', 'delivery_timeout', 'ADAPTER-WORDS', null), nowMs: 2 },
    ].reduce((s: DlState, e) => reduce(s, e as DlEvent), shown());
    const markup = html(unknownAfterChange);
    expect(markup).toContain(`value="${OTHER}"`);
    const start = markup.indexOf('data-testid="di-dl-notice"');
    expect(start).toBeGreaterThanOrEqual(0);
    const notice = markup.slice(start, markup.indexOf('</div>', start));
    expect(notice).toContain(ID);
    expect(notice).not.toContain(OTHER);
  });

  it('while a request waits, the read and the step button are disabled and the wait is said', () => {
    const waiting = reduce(reduce(shown(), { type: 'confirm', on: true }), { type: 'sent', seq: 2, op: 'advanceOnce', deliveryId: ID });
    const markup = html(waiting);
    expect(markup).toMatch(/waiting for the delivery adapter/i);
    expect(buttonsIn(markup).find((b) => b.includes('data-testid="di-dl-read"'))).toMatch(/disabled/);
  });
});

describe('the entry', () => {
  it('names the panel and opens it; closed, it shows no id field', () => {
    expect(typeof Entry).toBe('function');
    const closed = renderToStaticMarkup(createElement(Entry as never, { control: control({ ...INITIAL_DL_STATE }) }));
    expect(closed).toContain('data-testid="di-dl-entry"');
    expect(closed).toContain('data-testid="di-dl-entry-open"');
    expect(closed).not.toContain('data-testid="di-dl-id"');
    const open = renderToStaticMarkup(createElement(Entry as never, { control: control({ ...INITIAL_DL_STATE, open: true }) }));
    expect(open).toContain('data-testid="di-dl-id"');
  });
});

describe('the mount: the office panel draws the entry beside the Mission request entry (its operational face)', () => {
  const officeFixture = (
    JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as {
      state: DiOfficeState;
    }
  ).state;
  const officeStub = {
    characters: new Map<number, unknown>(),
    selectedAgentId: null,
    cameraFollowId: null,
    areaMappings: {},
    getLayout: () => ({ areas: [] }),
  } as unknown as OfficeState;
  const configured: DiMissionRequestAccess = {
    state: {
      ...INITIAL_MR_STATE,
      availability: { kind: 'ok' },
      overview: {
        serverNow: 1,
        capabilities: {
          runAvailable: true,
          progression: null,
          repositoryUrl: null,
          deliveryAuthority: 'none',
          decisionProof: 'operator_attested_not_independently_verified',
          decisionPrincipal: 'operator_attested_relay',
          approvalValidSeconds: 900,
          scopeMeanings: {},
        },
        requests: [],
        requestsTruncated: false,
        conversations: [],
      },
    } as DiMissionRequestAccess['state'],
    open: () => {},
  };
  const panel = (extra: Any): string =>
    renderToStaticMarkup(
      createElement(DiOfficePanel as never, {
        state: structuredClone(officeFixture),
        officeState: officeStub,
        collapsed: false,
        onCollapsedChange: () => {},
        mission: configured,
        ...extra,
      }),
    );
  const at = (html: string, id: string): number => html.indexOf(`data-testid="${id}"`);

  it('with the control, the entry is drawn after the Mission request entry and before Find', () => {
    const html = panel({ delivery: control({ ...INITIAL_DL_STATE }) });
    expect(at(html, 'di-mr-entry')).toBeGreaterThanOrEqual(0);
    expect(at(html, 'di-dl-entry')).toBeGreaterThan(at(html, 'di-mr-entry'));
    expect(at(html, 'di-dl-entry')).toBeLessThan(at(html, 'di-find'));
  });

  it('without the control, no entry is drawn', () => {
    const html = panel({});
    expect(at(html, 'di-mr-entry')).toBeGreaterThanOrEqual(0);
    expect(at(html, 'di-dl-entry')).toBe(-1);
  });

  // THE BOUNDARY, PINNED (lead1's ruling, after a reach test written red on this face): the delivery surface is drawn on the OPERATIONAL
  // face only. The face an office shows when nothing selects one is the one App asks officePanelPresentation for with no face and no e2e
  // flag (scan). Reaching the surface there would need a new deviation from the locked Home navigation (DiScanPanel.tsx:1542-1546), would
  // make the rail add an action route against that comment and diScanRail.test.ts:6, and would carry no geometry evidence (browser tests
  // only). So on that face, giving the panel the delivery control must change NOTHING it draws that names a delivery: equal counts, not
  // zero, since the face already draws one such enabled button without the control. It fails if a delivery control is mounted on the
  // scan face without a new ruling. Static markup shows what is drawn and enabled, not what a click does.
  it('on the face an office shows by default, giving the panel the delivery control adds no control naming a delivery (operational face only)', () => {
    const face = officePanelPresentation({});
    expect(face).toBe('scan');
    const enabledNaming = (html: string): number =>
      buttonsIn(html).filter((b) => !/\sdisabled=""/.test(b) && /\bdeliver(y|ies)\b/i.test(b.replace(/<[^>]+>/g, ' '))).length;
    const without = enabledNaming(panel({ presentation: face }));
    const withControl = enabledNaming(panel({ presentation: face, delivery: control({ ...INITIAL_DL_STATE }) }));
    expect(withControl).toBe(without);
    expect(panel({ presentation: face, delivery: control({ ...INITIAL_DL_STATE }) })).not.toContain('data-testid="di-dl-');
  });
});

describe('the hook sends nothing by itself (a STRUCTURAL pin on its parsed source; the hook itself is not executed here)', () => {
  const source = fs.readFileSync(path.join(root, 'src/hooks/useDiDelivery.ts'), 'utf-8');
  const file = ts.createSourceFile('useDiDelivery.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const calls: ts.CallExpression[] = [];
  const identifiers: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) calls.push(node);
    if (ts.isIdentifier(node)) identifiers.push(node.text);
    ts.forEachChild(node, visit);
  };
  visit(file);

  it('schedules nothing: no timer, frame, microtask or immediate', () => {
    for (const timer of ['setInterval', 'setTimeout', 'requestAnimationFrame', 'queueMicrotask', 'setImmediate', 'requestIdleCallback']) {
      expect(identifiers, timer).not.toContain(timer);
    }
  });

  it("calls transport.send exactly once, with the message a model builder built; and every send(...) passes one of the model's three builders", () => {
    const transportSends = calls.filter((c) => c.expression.getText(file) === 'transport.send');
    expect(transportSends).toHaveLength(1);
    expect(transportSends[0].arguments.map((a) => a.getText(file))).toEqual(['message']);
    const localSends = calls.filter((c) => c.expression.getText(file) === 'send');
    expect(localSends.length).toBeGreaterThan(0);
    for (const c of localSends) {
      expect(c.arguments.map((a) => a.getText(file)), c.getText(file)).toHaveLength(1);
      expect(['statusMessage', 'advanceMessage', 'followUpMessage'], c.getText(file)).toContain(c.arguments[0].getText(file));
    }
  });

  it("each builder is sent from exactly one place: the status read from the person's readStatus, the step from askForStep, the follow-up from an effect", () => {
    // Where a send(...) is: its nearest enclosing useEffect, or useCallback with the property it is assigned to.
    const where = (c: ts.CallExpression): string => {
      for (let n: ts.Node | undefined = c.parent; n !== undefined; n = n.parent) {
        if (!ts.isCallExpression(n)) continue;
        const callee = n.expression.getText(file);
        if (callee === 'useEffect') return 'effect';
        if (callee === 'useCallback') return ts.isPropertyAssignment(n.parent) ? `callback ${n.parent.name.getText(file)}` : 'callback';
      }
      return 'top level';
    };
    const sends = calls
      .filter((c) => c.expression.getText(file) === 'send')
      .map((c) => `${c.arguments[0]?.getText(file)} @ ${where(c)}`)
      .sort();
    expect(sends).toEqual(['advanceMessage @ callback askForStep', 'followUpMessage @ effect', 'statusMessage @ callback readStatus']);
  });
});

describe('owner decision 2026-10-29 (Q2): the standalone panel is kept for harnesses only, and the product draws no entry to it (a STRUCTURAL pin on App.tsx)', () => {
  const source = fs.readFileSync(path.join(root, 'src/App.tsx'), 'utf-8');
  const file = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const deliveryCalls: ts.CallExpression[] = [];
  const panelElements: ts.JsxOpeningLikeElement[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) && node.expression.getText(file) === 'useDiDelivery') deliveryCalls.push(node);
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText(file) === 'DiOfficePanel')
      panelElements.push(node);
    ts.forEachChild(node, visit);
  };
  visit(file);

  it("App starts exactly one delivery session: Mission details' own linked one, Delivery under Swarm", () => {
    expect(deliveryCalls.map((c) => c.arguments.map((a) => a.getText(file)))).toEqual([['diClientReason', "'linked'"]]);
  });

  it("App's office panel is given Mission details' delivery session and no standalone one", () => {
    expect(panelElements.length).toBe(1);
    const names = panelElements[0].attributes.properties.filter(ts.isJsxAttribute).map((a) => a.name.getText(file));
    expect(names).toContain('missionDelivery');
    expect(names).not.toContain('delivery');
  });
});
