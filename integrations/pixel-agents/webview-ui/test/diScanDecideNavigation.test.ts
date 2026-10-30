/**
 * M9 / R4: "Decide in the request panel" on a Mission's details, a NAVIGATION into the existing human-gated decision, never an Approve button that does not approve.
 *
 * The facts, from the source. A decision exists only for a request-panel proposal and is taken IN the request panel: the `decide` route (server/src/di/missionRequests.ts) relays the person's typed
 * word, recorded only as the exact approval, and since owner decision (c) the `decline`, `reject` and `requestChanges` routes relay the person's other choices on the same offer. Request changes and
 * Reject are therefore taken in the panel, not on a row or a card here, and withdrawing is still the proposer's own act.
 *
 * What this pins:
 *  - the row reads "awaiting a decision" ONLY from facts that already exist: a request in the panel's overview whose actions include `decide`, tied to the Mission by its Mission Core id the way
 *    Reception already ties them (`missionForRequest`). Anything else leaves the fact UNREAD (null), never "nothing is waiting": the panel lists only its own requests;
 *  - a real row offers "Decide in the request panel" (enabled only when the panel can be opened on exactly one proposal), and Request changes and Reject DRAWN DISABLED with their exact reasons, as visible
 *    text; there is no "Approve" button on a real row;
 *  - the labelled specimen is unchanged (its layout-preview Approve, Request changes and Reject).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import type { MrOverview, MrSummary } from '../src/components/diMissionRequestModel.ts';
import type { PanelSource } from '../src/components/diReceptionItems.ts';
import { buildInbox } from '../src/components/diReceptionItems.ts';
import {
  DI_DECIDE_LABEL,
  DI_DECIDE_UNAVAILABLE,
  DI_DECIDE_WHAT,
  DI_REJECT_IN_PANEL,
  DI_REQUEST_CHANGES_IN_PANEL,
  DI_SCAN_LOCK,
  type DiScanRow,
  scanRowsFromOffice,
  stateActions,
} from '../src/components/diScanModel.ts';
import { type DiReceptionCard, receptionFromInbox } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const state = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }
).state;
const core = state.missions.missions.find((m) => m.sourceKind === 'mission_core')!;

const request = (n: number, missionId: string, actions: MrSummary['actions']): MrSummary => ({
  requestRef: `lr-${String(n).repeat(32)}`,
  missionId,
  revision: 1,
  createdAt: 1_800_000_000 + n,
  objective: `Proposal ${n}`,
  digest: 'd'.repeat(64),
  stage: 'of_record',
  label: 'Waiting for your decision',
  terminal: false,
  actions,
  notice: null,
  attestedAt: null,
  cancelledAt: null,
});
const read = (requests: MrSummary[]): PanelSource => ({
  kind: 'read',
  overview: { serverNow: 1_800_000_100, capabilities: {} as MrOverview['capabilities'], requests, requestsTruncated: false, conversations: [] },
});
const coreRow = (panel?: PanelSource): DiScanRow => scanRowsFromOffice(state, panel).find((r) => r.id === core.missionRef)!;
const html = (row: DiScanRow, onOpenDecision?: (ref: string, opener: HTMLElement) => void) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: 'mission', onMinimize: () => undefined, onOpenDecision }),
  );
const button = (markup: string, action: string) => new RegExp(`<button[^>]*data-action="${action}"[^>]*>([^<]*)</button>`).exec(markup);
const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');

describe('awaiting a decision is read only from facts that already exist', () => {
  it('a panel request waiting for a decision, tied to this Mission by its Mission Core id: awaiting, with that one proposal', () => {
    const row = coreRow(read([request(1, core.nativeId, ['decide'])]));
    expect(row.state.awaitingApproval).toBe(true);
    expect(row.decision).toEqual({ requestRef: `lr-${'1'.repeat(32)}`, count: 1 });
  });

  it('no panel, or a panel with no decide request for it, leaves the fact UNREAD (null), never false', () => {
    for (const panel of [undefined, { kind: 'not_provided' } as PanelSource, read([]), read([request(1, core.nativeId, ['check_approval'])])]) {
      const row = coreRow(panel);
      expect(row.state.awaitingApproval).toBeNull();
      expect(row.decision).toBeNull();
    }
  });

  it('a request that names another Mission, or a Mission id listed twice, ties to nothing', () => {
    expect(coreRow(read([request(1, 'mn-' + 'f'.repeat(32), ['decide'])])).decision).toBeNull();
    const twice = structuredClone(state);
    twice.missions.missions.push(structuredClone(core));
    const rows = scanRowsFromOffice(twice, read([request(1, core.nativeId, ['decide'])]));
    for (const r of rows.filter((x) => x.id === core.missionRef)) expect(r.decision).toBeNull();
  });

  it('two proposals waiting for this Mission: awaiting, with no single proposal to open', () => {
    const row = coreRow(read([request(1, core.nativeId, ['decide']), request(2, core.nativeId, ['decide'])]));
    expect(row.state.awaitingApproval).toBe(true);
    expect(row.decision).toEqual({ requestRef: null, count: 2 });
  });
});

describe('the actions a real row offers', () => {
  it('Decide in the request panel (a navigation), and Request changes and Reject with their reasons; never Approve', () => {
    const row = coreRow(read([request(1, core.nativeId, ['decide'])]));
    // (this fixture's Mission Core record is running, so a real row also offers the held Stop, drawn disabled: it is not part of this piece)
    const actions = stateActions(row.state).filter((a) => a.id !== 'stop');
    expect(actions.map((a) => a.id)).toEqual(['decide', 'request_changes', 'reject']);
    expect(actions[0]).toMatchObject({ label: DI_DECIDE_LABEL, navigate: true });
    expect(actions[1]).toMatchObject({ label: 'Request changes', reason: DI_REQUEST_CHANGES_IN_PANEL });
    expect(actions[2]).toMatchObject({ label: 'Reject', reason: DI_REJECT_IN_PANEL });
    expect(actions.some((a) => a.id === 'approve' || a.label === 'Approve')).toBe(false);
  });

  it('the labelled specimen keeps its layout-preview Approve, Request changes and Reject', () => {
    const specimen = DI_SCAN_LOCK.find((r) => r.state.awaitingApproval === true);
    expect(specimen, 'the specimen has a Mission awaiting approval').toBeDefined();
    expect(stateActions(specimen!.state).map((a) => a.id)).toEqual(expect.arrayContaining(['approve', 'request_changes', 'reject']));
    expect(DI_SCAN_LOCK.every((r) => r.state.source === 'fixture')).toBe(true);
  });
});

describe('the page', () => {
  const row = coreRow(read([request(1, core.nativeId, ['decide'])]));

  it('with the request panel available: the navigation is ENABLED, says what it does, and says in visible text which decisions can be recorded there', () => {
    const markup = html(row, () => undefined);
    const decide = button(markup, 'decide');
    expect(decide?.[1]).toBe(DI_DECIDE_LABEL);
    expect(decide?.[0]).not.toContain('disabled');
    expect(text(markup)).toContain(DI_DECIDE_WHAT);
    expect(markup).not.toMatch(/<button[^>]*>Approve<\/button>/);
  });

  it('Request changes and Reject are drawn DISABLED, each with its exact reason as visible text', () => {
    const markup = html(row, () => undefined);
    for (const [action, reason] of [['request_changes', DI_REQUEST_CHANGES_IN_PANEL], ['reject', DI_REJECT_IN_PANEL]] as const) {
      expect(button(markup, action)?.[0], action).toContain('disabled');
      expect(text(markup), action).toContain(reason);
    }
  });

  it('without the request panel the navigation is drawn disabled and says why', () => {
    const markup = html(row);
    expect(button(markup, 'decide')?.[0]).toContain('disabled');
    expect(text(markup)).toContain(DI_DECIDE_UNAVAILABLE);
  });

  it('with two proposals waiting there is no single proposal to open: drawn disabled, saying how many', () => {
    const two = coreRow(read([request(1, core.nativeId, ['decide']), request(2, core.nativeId, ['decide'])]));
    const markup = html(two, () => undefined);
    expect(button(markup, 'decide')?.[0]).toContain('disabled');
    expect(text(markup)).toContain('2 proposals of this Mission wait for a decision in the request panel');
  });
});

/**
 * R4 on the Reception page: a request-panel proposal (a `proposal` card carrying its request reference) offers the same navigation, and Request changes and Reject say they are taken in the panel.
 * A proposal card WITHOUT a request-panel request has nothing to navigate to and is drawn as before.
 */
describe('R4: the Reception page', () => {
  const panel = read([request(1, core.nativeId, ['decide'])]);
  const rows = scanRowsFromOffice(state, panel);
  const reception = receptionFromInbox(buildInbox(state.reception!, panel, state.missions.missions, new Set()), state.reception!, rows);
  const page = (onOpenRequestItem?: (card: DiReceptionCard, opener: HTMLElement) => void) =>
    renderToStaticMarkup(
      createElement(DiScanPanel, { rows, reception, initialView: 'reception', onMinimize: () => undefined, onOpenRequestItem }),
    );
  const byTestid = (markup: string, id: string) => new RegExp(`<button[^>]*data-testid="${id}"[^>]*>([^<]*)</button>`).exec(markup);

  it('the request-panel proposal is a proposal card carrying its request reference', () => {
    const card = reception.cards.find((c) => c.request?.requestRef === `lr-${'1'.repeat(32)}`);
    expect(card?.kind).toBe('proposal');
  });

  it('where the workspace can be opened: Decide in the request panel (enabled), its visible description, and no Approve', () => {
    const markup = page(() => undefined);
    const decide = byTestid(markup, 'di-reception-decide');
    expect(decide?.[1]).toBe(DI_DECIDE_LABEL);
    expect(decide?.[0]).not.toContain('disabled');
    expect(text(markup)).toContain(DI_DECIDE_WHAT);
    expect(byTestid(markup, 'di-reception-approve')).toBeNull();
  });

  it('Request changes and Reject are drawn disabled with their exact reasons: they are taken in the request panel', () => {
    const markup = page(() => undefined);
    for (const [id, reason] of [['di-reception-request-changes', DI_REQUEST_CHANGES_IN_PANEL], ['di-reception-reject', DI_REJECT_IN_PANEL]] as const) {
      expect(byTestid(markup, id)?.[0], id).toContain('disabled');
      expect(text(markup), id).toContain(reason);
    }
  });

  it('where the workspace cannot be opened, the card is drawn as before (no navigation)', () => {
    const markup = page();
    expect(byTestid(markup, 'di-reception-decide')).toBeNull();
    expect(byTestid(markup, 'di-reception-approve')?.[0]).toContain('disabled');
  });
});
