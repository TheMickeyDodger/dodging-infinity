/**
 * PRESENTATION of Mission details (pass A, A3 and A4): the pixel-lock composition in Home tokens, the Swarm's model
 * and provider per role, the Delivery block below Swarm, the state actions, the trash, and the ABSENCE of any in-app
 * Herdr terminal (GAP-RESOLUTIONS 9, 11, 13).
 *
 * Stage 2 is layout only. Every action drawn here is DISABLED with an honest layout-stage reason; nothing commits,
 * pushes, opens a pull request, accepts CI, merges, stops, approves, recovers, deletes or opens a terminal. A passing
 * presentation test is not acceptance of any of those controls: Stage 4 must wire each to an existing human-gated
 * route. Sample rows are the labelled lock specimen (DI_SCAN_LOCK), shown as fixture state.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import type { MrDeletion } from '../src/components/diMissionRequestModel.ts';
import { DI_SCAN_LOCK, type DiScanRow, type DiScanView, scanRowsFromOffice, swarmSummary } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import { DI_RAIL_WIDTH_PX } from '../src/constants.ts';
import { populatedRow, UNRESOLVED_IDENTITY } from './diPopulatedSample.ts';
import { markupText } from './helpers/markupText.ts';
import { productionRow } from './helpers/productionRow.ts';
import { AVAILABLE, herdrControl } from './herdrControlStub.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/**
 * The trash's scope sentence (copied here, never read from the code under test). CHANGED IN Deliverable 2 (Addendum B, which bounds owner decision 2's full-scope
 * wording): the owner's "Delete local data" removes DI-owned local data AND the retained summary, irreversibly, and says plainly what it cannot reach (the providers'
 * transcripts, the target herd's own state, PR delivery receipts); it never promises a deeper erase than DI can perform.
 */
const TRASH_SCOPE =
  'Holding deletes this Mission’s local data from this office: its conversation, its request and its logs, AND its retained summary (final status, verdict, CI results and comments). It cannot be undone. What remains: Mission Core’s own record of the request and its authority (authority history is never erased) and a record that it was deleted. Not reached from here: the providers’ own session transcripts, the target herd’s own state, and PR delivery receipts. Without this, its reasoning and logs are reclaimed automatically 30 days after it finished, and its summary is kept.';
const noop = () => undefined;
const render = (row: DiScanRow, view: DiScanView = 'mission') =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: [row],
      initialSelectedId: row.id,
      initialView: view,
      onMinimize: noop,
      onOpenRequestWorkspace: noop,
      onOpenSettings: noop,
    }),
  );
/** B2c: the same, with "More actions" open (a closed popover's items are not in the markup; DiScanPanelProps.initialMoreActionsOpen). */
const renderOpen = (row: DiScanRow, view: DiScanView = 'mission') =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: [row],
      initialSelectedId: row.id,
      initialView: view,
      initialMoreActionsOpen: true,
      onMinimize: noop,
      onOpenRequestWorkspace: noop,
      onOpenSettings: noop,
    }),
  );
const [building, merged, failedCi] = [DI_SCAN_LOCK[0]!, DI_SCAN_LOCK[1]!, DI_SCAN_LOCK[2]!];

/** Opening tag of the first element carrying `testid`. */
function tag(html: string, testid: string): string {
  const m = new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`).exec(html);
  if (!m) throw new Error(`no ${testid}`);
  return m[0];
}
/** Markup of the first element carrying `testid` (its tag name closes it; the element must not nest its own tag). */
function block(html: string, testid: string): string {
  const m = new RegExp(
    `<(section|div|ul|li|p|button)[^>]*data-testid="${testid}"[^>]*>[\\s\\S]*?</\\1>`,
  ).exec(html);
  if (!m) throw new Error(`no ${testid}`);
  return m[0];
}
const position = (html: string, testid: string) => {
  const at = html.indexOf(`data-testid="${testid}"`);
  if (at < 0) throw new Error(`no ${testid}`);
  return at;
};
/**
 * B2a (Direction B): Home, the Mission list, stays mounted beside every screen and carries some of the same ids (a row's pills). An assertion about the
 * stage looks at the stage's own markup. CHANGED IN B2b: Home comes AFTER the screens in the document (Tab follows the window's order, rail | screen |
 * list), so the stage's markup ends where Home's begins. Scoping changes WHERE an assertion looks, never WHAT it expects.
 */
const stageOf = (html: string) => {
  const at = position(html, 'di-scan-stage');
  const home = html.indexOf('data-testid="di-scan-home"', at);
  return html.slice(at, home < 0 ? undefined : home);
};
const LAYOUT_NOTE = 'Layout preview. These controls are drawn but not connected yet.';

describe('PRESENTATION: the pixel-lock composition, in order (pixel-lock/02-mission-details.png, D-2)', () => {
  // CHANGED IN B2a (scoping only): the order is read in the stage, since Home (mounted beside it, first in the markup) has its own pills
  // CHANGED IN B2c: Herdr and Comments (and Evidence and review) moved into "More actions", the one secondary beside Code Diff; the popover's own order,
  // trigger first and Close last, before the trash, is pinned on the open render below. BEFORE: "..., state actions, the three buttons, trash last".
  it('Back and breadcrumb, title, status pills, details card, Swarm, Delivery, state actions, Code Diff and More actions, trash last', () => {
    const html = stageOf(render(building));
    const order = [
      'di-scan-back',
      'di-scan-crumb',
      'di-scan-mission-title',
      'di-scan-pill',
      'di-scan-details',
      'di-scan-swarm',
      'di-scan-delivery',
      'di-scan-state-actions',
      'di-scan-code-diff',
      'di-scan-more-actions',
      'di-scan-trash',
    ].map((id) => [id, position(html, id)] as const);
    const sorted = [...order].sort((a, b) => a[1] - b[1]).map(([id]) => id);
    expect(sorted).toEqual(order.map(([id]) => id));
    // the seam is initial state only (the Lead's condition): with initialMoreActionsOpen unset, the product's own render draws NO popover and the trigger
    // says it is closed, exactly as without the prop
    expect(html).not.toContain('data-testid="di-scan-more-actions-popover"');
    expect(tag(html, 'di-scan-more-actions')).toContain('aria-expanded="false"');
    // open: the popover sits right after its trigger, its items in order, its Close last, all before the trash
    const open = stageOf(renderOpen(building));
    const inPopover = ['di-scan-more-actions', 'di-scan-more-actions-popover', 'di-scan-comments', 'di-scan-evidence', 'di-scan-herdr', 'di-scan-more-actions-close', 'di-scan-trash'].map(
      (id) => [id, position(open, id)] as const,
    );
    expect([...inPopover].sort((a, b) => a[1] - b[1]).map(([id]) => id)).toEqual(inPopover.map(([id]) => id));
  });
  it("the stage starts at the rail's edge, so the rail stays beside it", () => {
    expect(tag(render(building), 'di-scan-stage')).toContain(`left:${DI_RAIL_WIDTH_PX}px`);
  });
  // CHANGED IN B2c: Herdr and Comments are items of "More actions" now (read on the open render), and the trigger is pinned too. BEFORE: "the three buttons
  // read Code Diff, Herdr and Comments". Every label is still pinned in plain title case.
  it('Code Diff and More actions, and the items Comments, Evidence and review and Herdr, read in plain title case (no pixel display labels)', () => {
    const html = render(building);
    for (const [id, label] of [
      ['di-scan-code-diff', 'Code Diff'],
      ['di-scan-more-actions', 'More actions'],
    ] as const)
      expect(block(html, id)).toMatch(new RegExp(`>${label}</button>$`));
    const open = renderOpen(building);
    for (const [id, label] of [
      ['di-scan-comments', 'Comments'],
      ['di-scan-evidence', 'Evidence and review'],
      ['di-scan-herdr', 'Herdr'],
    ] as const)
      expect(block(open, id)).toMatch(new RegExp(`>${label}</button>$`));
  });
  // CHANGED IN B2a (scoping only): the stage's own pills; Home's row pills are a separate surface
  it('status pills are a dot AND a word (AC-UX-10)', () => {
    const pills = [
      ...stageOf(render(building)).matchAll(
        /data-testid="di-scan-pill"[^>]*>([\s\S]*?)<\/span>(?=<span class="di-scan-pill"|<\/span>)/g,
      ),
    ].map((m) => m[1]);
    expect(pills.length).toBe(2);
    for (const pill of pills)
      expect(pill).toMatch(
        /<span class="di-scan-dot" data-tone="[a-z]+" aria-hidden="true"><\/span>(Needs you|Running)/,
      );
  });
});

describe("PRESENTATION: Swarm names each role's exact model and provider, observed apart from configured (gap 4, D23)", () => {
  it('every role carries a model line and a provider line, each saying how it is known', () => {
    const swarm = block(render(building), 'di-scan-swarm');
    const members = [...swarm.matchAll(/data-testid="di-scan-swarm-member"[\s\S]*?<\/li>/g)].map(
      (m) => m[0],
    );
    expect(members).toHaveLength(4);
    for (const m of members) {
      expect(m).toMatch(
        /data-testid="di-scan-model" data-fact="(observed|stale|not_recorded|none_reported|unavailable|not_applicable)"/,
      );
      expect(m).toMatch(/data-testid="di-scan-provider" data-fact="(configured|not_recorded)"/);
    }
    // the specimen states one of each kind the design must keep apart
    expect(swarm).toContain('Model gpt-5.5 (observed)');
    expect(swarm).toContain('Provider codex (configured)');
    expect(swarm).toContain('(last seen, not live)');
    expect(swarm).toContain('Model not recorded');
  });
  // CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…, A8/A9: "default each details section to one short status line"; the Lead's scope ruling: the
  // Mission DETAILS layout is not frozen by pixel-lock 02): the Swarm leads with ONE summary line, and the roster, every card exactly as before, is shown on demand behind one
  // toggle. Every word asserted above is still in each member's card, which is asserted here to sit in the roster the toggle controls, never removed.
  it('A9: the Swarm leads with ONE truthful summary line; the whole roster (each card exactly as before) is behind ONE toggle that starts closed', () => {
    const html = render(building);
    expect(html).toContain('data-testid="di-scan-swarm-status">4 roles listed.<');
    expect(html).toMatch(/<div class="di-scan-swarm-box" data-expanded="false"><ul class="di-scan-cards" data-testid="di-scan-swarm" id="di-scan-swarm-roster">/);
    const toggle = tag(html, 'di-scan-swarm-toggle');
    expect(toggle).toContain('aria-expanded="false"');
    expect(toggle).toContain('aria-controls="di-scan-swarm-roster"');
    expect(html).toMatch(/data-testid="di-scan-swarm-toggle"[^>]*>Show the roster: each role’s agent, model, provider and notes</);
    // the summary sits BEFORE the roster it summarises
    expect(position(html, 'di-scan-swarm-status')).toBeLessThan(position(html, 'di-scan-swarm'));
    const members = [...block(html, 'di-scan-swarm').matchAll(/data-testid="di-scan-swarm-member"[\s\S]*?<\/li>/g)].map((m) => m[0]);
    expect(members).toHaveLength(4);
    for (const m of members) expect(m).toMatch(/class="di-scan-role"[\s\S]*class="di-scan-name"[\s\S]*data-testid="di-scan-model"[\s\S]*data-testid="di-scan-provider"/);
  });
  it('A9 (A8.3): with an UNRESOLVED identity in the roster, the summary says so; it never reads as though every member were resolved, and claims no liveness', () => {
    const row = populatedRow();
    const html = render(row);
    expect(html).toContain('data-testid="di-scan-swarm-status">4 roles listed; 1 identity is not resolved.<');
    expect(swarmSummary(row.swarm)).not.toMatch(/live|working|running|confirmed|all/i);
    // the unresolved member's mark and words are in the roster, exactly
    const words = UNRESOLVED_IDENTITY.kind === 'unresolved' ? UNRESOLVED_IDENTITY.words : '';
    expect(block(html, 'di-scan-swarm')).toContain(words);
    expect(block(html, 'di-scan-swarm')).toContain('Replaced by another session');
  });
  it('A9: folded, the details card reserves no height of its own (sized by its line, toggle and foot); shown in full it keeps its frame', () => {
    expect(tag(render(building), 'di-scan-details')).toContain('data-expanded="false"');
    const css = fs.readFileSync(path.join(root, 'src/components/diScanPanel.css'), 'utf-8');
    // reserves no height, and is never shrunk below what it shows (a flex item of the scrolling Mission column)
    expect(css).toMatch(/\.di-scan-card\[data-expanded='false'\] \{\n {2}min-height: 0;\n {2}flex-shrink: 0;\n\}/);
    expect(css).toMatch(/\.di-scan-card \{[\s\S]*?min-height: 348px;/);
  });
  it('A8: the description is drawn as one line by default, whole in the document, with the handoff folded behind one toggle', () => {
    const html = render(building);
    expect(html).toMatch(/data-testid="di-scan-details-body" data-expanded="false"/);
    expect(html).toContain(`data-testid="di-scan-details-text">${building.description}<`);
    for (const line of building.handoff.split('\n')) expect(html).toContain(`data-testid="di-scan-details-handoff">${line}<`);
    expect(tag(html, 'di-scan-details-toggle')).toContain('aria-expanded="false"');
    expect(html).toMatch(/data-testid="di-scan-details-toggle"[^>]*>Show the whole description and the handoff</);
  });
  it('offers no control to change a model (D23, AC-4.3-34)', () => {
    const swarm = block(render(building), 'di-scan-swarm');
    expect(swarm).not.toMatch(/<(button|select|input)\b/);
  });
});

describe('PRESENTATION: Delivery sits below Swarm, state-gated against labelled fixture state, every action disabled (gap 2, gap 3)', () => {
  it('is labelled as sample state where the state is a fixture', () => {
    expect(block(render(building), 'di-scan-delivery')).toContain('Sample state (fixture)');
  });
  it('shows each step with a dot and a word; a ready step offers a DISABLED control, a locked step only its reason', () => {
    // the specimen's first Mission: pull request open, CI passed (its footer), results not yet accepted
    const d = block(render(building), 'di-scan-delivery');
    const steps = [
      ...d.matchAll(
        /<li[^>]*data-testid="di-scan-step" data-step="([a-z_]+)" data-gate="([a-z]+)"[^>]*>([\s\S]*?)<\/li>/g,
      ),
    ];
    expect(steps.map((m) => `${m[1]}:${m[2]}`)).toEqual([
      'commit:done',
      'push:done',
      'pr:done',
      'accept_ci:ready',
    ]);
    for (const [, id, gate, body] of steps) {
      expect(body, id).toMatch(
        /<span class="di-scan-dot" data-tone="[a-z]+" aria-hidden="true"><\/span>(Ready|Locked|Done)/,
      );
      const button = /<button[^>]*>/.exec(body)?.[0];
      if (gate === 'ready') {
        expect(button, id).toBeDefined();
        expect(button!, id).toContain('disabled=""');
        expect(button!, id).toContain('aria-describedby="di-scan-delivery-note"');
      } else {
        expect(button, id).toBeUndefined();
      }
    }
    expect(d).toContain(LAYOUT_NOTE);
  });
  // CHANGED BY THE OWNER RULING (2026-10-28, owner-decisions.md:93): AC-4.6-40/-48 ("Hold to merge", the only merge control) are SUPERSEDED. There is no merge control and no merge wording in
  // Delivery (pr_delivery's no-merge boundary is pinned); the check for no typed merge phrase is kept. test/diScanDeliveryHold.test.ts states the removal in full.
  it('there is no merge control and no merge wording in Delivery, and no typed merge phrase', () => {
    for (const row of DI_SCAN_LOCK) {
      const d = block(render(row), 'di-scan-delivery');
      expect(d).not.toMatch(/<(input|textarea)\b/);
      expect(d).not.toMatch(/[Mm]erg/);
    }
  });
  it('failed CI offers nothing to accept, and says so', () => {
    const d = block(render(failedCi), 'di-scan-delivery');
    expect(d).toMatch(/data-step="accept_ci" data-gate="locked"/);
    expect(d).toContain('CI failed. There are no passing results to accept.');
  });
});

describe('PRESENTATION: state actions appear only when the state matches, and are disabled at this stage (gap 3)', () => {
  it('a running Mission waiting on a proposal: a held Stop, and Approve, Request changes, Reject; all disabled', () => {
    const s = block(render(building), 'di-scan-state-actions');
    const actions = [...s.matchAll(/<button[^>]*data-action="([a-z_]+)"[^>]*>([^<]*)</g)].map(
      (m) => [m[1], m[2], m[0]],
    );
    expect(actions.map(([id, label]) => `${id}:${label}`)).toEqual([
      'stop:Hold to stop',
      'approve:Approve',
      'request_changes:Request changes',
      'reject:Reject',
    ]);
    for (const [, , button] of actions) expect(button).toContain('disabled=""');
  });
  it('a finished Mission with nothing waiting offers no Stop, no approval and no recovery', () => {
    const html = render(merged);
    const s = block(html, 'di-scan-state-actions');
    expect(s).not.toMatch(/data-action="(stop|approve|request_changes|reject|recover|cancel)"/);
  });
});

describe('PRESENTATION: the trash (D-1, AC-4.4-29/-30/-32), disabled at this stage, never a hide', () => {
  // Superseded copy (owner decision 2, 2026-10-26, owner-decisions.md "Mission Trash scope"): Trash deletes DI's own
  // Mission data AND the herd tools' own terminal history and transcripts, so the pass A words "local data" understated
  // it. The copy must be true at that full scope, keep the summary and approval-record promise, and never promise a
  // whole delete it cannot report per owner.
  // CHANGED IN B2c: the trash follows "More actions" (Comments is inside it). BEFORE: "after the three buttons", read after Comments.
  it('sits bottom-right, after More actions, and says what is deleted and what is kept', () => {
    const html = render(merged);
    expect(position(html, 'di-scan-trash')).toBeGreaterThan(position(html, 'di-scan-more-actions'));
    expect(tag(html, 'di-scan-trash')).toContain('aria-label="Hold to delete this Mission’s local data"');
    expect(html).toContain(TRASH_SCOPE);
  });
  // CHANGED IN Deliverable 2 (Addendum B): this test pinned owner decision 2's full-scope promise (DI, the herd tools and the providers deleted, the summary kept).
  // Addendum B bounds the owner's delete to DI-owned local data and requires the copy to state the actual outcome before the person commits: the summary goes
  // too, it is irreversible, and what DI cannot reach is named as not reached. The protection kept: no dash-joined copy, and no promise the control cannot keep.
  it('the trash copy states the actual outcome: local data AND the summary removed, irreversible, what remains, and what it cannot reach', () => {
    expect(TRASH_SCOPE).toMatch(/AND its retained summary \(final status, verdict, CI results and comments\)/);
    expect(TRASH_SCOPE).toMatch(/It cannot be undone\./);
    // CHANGED IN round 10: "the Mission’s approved request" put the word "approved" on every Mission page, which the page-wide guard against approval claims
    // (diMissionDelivery "no ready, mint or go-ahead claim") rightly refuses. Same facts, without an approval word: what remains is Mission Core’s record.
    expect(TRASH_SCOPE).toMatch(/What remains: Mission Core’s own record of the request and its authority \(authority history is never erased\)/);
    expect(TRASH_SCOPE).not.toMatch(/\bapproved\b|\bready\b/i);
    expect(TRASH_SCOPE).toMatch(/Not reached from here: the providers’ own session transcripts, the target herd’s own state, and PR delivery receipts\./);
    expect(TRASH_SCOPE).not.toMatch(/[\u2013\u2014]/);
    // it never claims to reach what it does not
    expect(TRASH_SCOPE).not.toMatch(/deletes[^.]*(herd’s own terminal history|providers’ own session transcripts)/i);
  });
  it('a running Mission: disabled, and the reason is that it is running', () => {
    const html = render(building);
    expect(tag(html, 'di-scan-trash')).toContain('disabled=""');
    expect(block(html, 'di-scan-trash-reason')).toContain('A running Mission cannot be deleted.');
  });
  // CHANGED IN Deliverable 2: the delete is connected now, through the request panel, and offered only for an archived Mission Core Mission whose local data this
  // office reads. The specimen row carries no local-retention reading, so it is disabled with THAT reason (never hidden), not "not connected".
  it('a specimen Mission whose local data is not read: disabled, and says so (it never hides the row)', () => {
    const html = render(merged);
    expect(tag(html, 'di-scan-trash')).toContain('disabled=""');
    expect(block(html, 'di-scan-trash-reason')).toContain('This Mission’s local data is not read here.');
    expect(html).not.toMatch(/aria-label="Clear /);
  });
  // CHANGED IN B2a (Direction B, Q8 = (a), the Lead 2026-11-05): one click docks a Mission, so Home's inline expanded card is removed, and its disabled Trash
  // with it. This test pinned that card's Trash (disabled, after the meta line, no hide gesture). Its subject is gone. What stays pinned: Home offers NO Trash
  // and still no hide gesture; the one Trash is Mission details' own (last in the composition above).
  it('B2a: Home has no expanded card and no Trash (the one Trash is in Mission details), and still no hide gesture', () => {
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: DI_SCAN_LOCK,
        initialSelectedId: building.id,
        onMinimize: noop,
      }),
    );
    expect(html).toContain('data-testid="di-scan-row"');
    expect(html).not.toContain('data-testid="di-scan-detail"');
    expect(html).not.toContain('data-testid="di-scan-trash"');
    expect(html).not.toMatch(/aria-label="Clear /);
  });
});

describe('A4: no in-app Herdr terminal remains (GAP-RESOLUTIONS 9, 11, 13; supersedes the v3 05-herdr pane grid)', () => {
  // Package A: the Herdr button keeps its place and opens no nested screen, but it is no longer a button with a static note about another tool. A click asks the office server to focus the Mission's herd
  // workspace and open the machine's own terminal. It is enabled only by the office's answer for the Mission, and its words are that answer's.
  const withControl = (options?: Parameters<typeof herdrControl>[1]) =>
    renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: [building],
        initialSelectedId: building.id,
        initialView: 'mission' as DiScanView,
        // B2c: the Herdr control is an item of "More actions"; its state is read on the open render
        initialMoreActionsOpen: true,
        onMinimize: noop,
        herdr: herdrControl(building.id, options),
      }),
    );

  // CHANGED IN B2c: the Herdr control is an ITEM of "More actions" (the Lead's ruling (i): its status line stays in the notes row). Held, it is aria-disabled and
  // stays focusable, its reason drawn IN the item and describing it (BEFORE: a native disabled button described by the notes-row line). The line is unchanged.
  it('the Herdr item opens no nested screen; with no control wired it is held, its reason in the item, and the line says nothing is connected', () => {
    const html = renderOpen(building);
    const herdr = tag(html, 'di-scan-herdr');
    expect(herdr).toContain('aria-disabled="true"');
    expect(herdr).toContain('aria-describedby="di-scan-herdr-held"');
    expect(block(html, 'di-scan-herdr-held')).toContain('The Herdr control is not connected to this view, so nothing can be opened from here.');
    expect(block(html, 'di-scan-herdr-reason')).toContain('The Herdr control is not connected to this view, so nothing can be opened from here.');
    expect(html).not.toMatch(/herdctl|does not start it|Herdr runs in your own terminal/);
  });
  // CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…, A1): Mission details leads each section with ONE short status line and
  // discloses policy text on demand. Herdr's line is the first statement of what a click does; the office's full words (the workspace it focuses first, the
  // pane as identity evidence only, the basis, the window requirement) are in the "What Herdr does here" disclosure, `di-scan-herdr-note`, word for word as
  // before, and the Herdr button is described by both. Every assertion on those words is kept, on the element that now holds them.
  it('where the office said a launch is available it is enabled, and the note says what a click does: focus the workspace first, the pane as identity evidence only, nothing typed', () => {
    const html = withControl();
    expect(tag(html, 'di-scan-herdr')).not.toContain('disabled');
    expect(tag(html, 'di-scan-herdr')).toContain('aria-describedby="di-scan-herdr-reason di-scan-herdr-desc"');
    expect(block(html, 'di-scan-herdr-reason')).toMatch(/>Opens Terminal on this Mission&#x27;s herd\. It types nothing into any pane or terminal\.<\/p>$/);
    const note = block(html, 'di-scan-herdr-note');
    expect(note).toContain('Opens Terminal on this Mission&#x27;s herd. It first asks Herdr to focus workspace w6V, and types nothing into any pane or terminal.');
    expect(note).toContain('Pane w6V:p3 is matched as identity evidence only; no pane is focused.');
    expect(block(html, 'di-scan-herdr-reason')).not.toContain('data-testid="di-scan-herdr-recheck"');
    // the full words are folded, not removed: a closed disclosure, named, holding them
    expect(html).toMatch(/<details class="di-scan-more" data-testid="di-scan-herdr-more"><summary[^>]*>What Herdr does here<\/summary>/);
  });
  it('the note says which basis vouched for the herd, in different words for a verified runtime identity and for a team-roster confirmation, never one for the other', () => {
    const runtime = block(withControl(), 'di-scan-herdr-note');
    expect(runtime).toContain('The catalog&#x27;s runtime identity for the role is verified');
    expect(runtime).not.toContain('team roster');
    const roster = block(withControl({ check: { ...AVAILABLE, identityBasis: 'team_roster_session_confirmed' } }), 'di-scan-herdr-note');
    expect(roster).toContain('The role was found in the catalog&#x27;s team roster with its recorded and live sessions equal (a roster confirmation, not a runtime-identity verification)');
    expect(roster).not.toContain('runtime identity for the role is verified');
  });
  // CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…, A7): the default line is SHORT and says only what the office said (here its own first
  // statement, "Herdr is not set up in this office"); the office's reason, flags and paths included, is kept VERBATIM one disclosure away and still describes the button.
  // CHANGED IN B2c: held (aria-disabled) rather than natively disabled, described by its reason in the item and the full words (BEFORE: by the notes-row line and
  // the full words). The line, the verbatim reason and Check again are unchanged and stay in the notes row.
  it('where the office refused, it is held; the line says so in short, the office\'s own reason is kept verbatim on demand, with a way to ask again', () => {
    const reason = 'Herdr is not set up in this office: it was started without --herdr-bin, --herdr-home, --herdr-socket. Nothing was attempted.';
    const html = withControl({ check: { kind: 'unavailable', problem: 'herdr_not_configured', reason } });
    expect(tag(html, 'di-scan-herdr')).toContain('aria-disabled="true"');
    expect(tag(html, 'di-scan-herdr')).toContain('aria-describedby="di-scan-herdr-held di-scan-herdr-desc"');
    expect(block(html, 'di-scan-herdr-held')).toContain('Herdr is not set up in this office.');
    const line = block(html, 'di-scan-herdr-reason');
    expect(line).toMatch(/>Herdr is not set up in this office\. <button/);
    expect(line).not.toContain('--herdr');
    expect(line).toContain('data-testid="di-scan-herdr-recheck"');
    expect(html).toMatch(/data-testid="di-scan-herdr-more"><summary[^>]*>The office’s reason, in its own words<\/summary>/);
    expect(block(html, 'di-scan-herdr-note')).toContain(`>${reason}</p>`);
  });
  it('A7: a refused launch says THAT it was refused, in short; the route\'s words stay verbatim on demand; an answer that established nothing is never called a refusal or "nothing opened"', () => {
    const detail = 'Nothing was opened: Workspace focus was not established, and Herdr&#x27;s call failed, so nothing is known to have moved. The launcher was written at /x/herdr-attach-a1b2.command and was not used.';
    const refused = withControl({ launch: { kind: 'refused', problem: 'focus_not_established', reason: detail.replace('&#x27;', "'") } });
    expect(block(refused, 'di-scan-herdr-reason')).toMatch(/>The office refused this launch\.<\/p>$/);
    expect(block(refused, 'di-scan-herdr-note')).toContain(detail);
    const unknown = withControl({ launch: { kind: 'refused', problem: null, reason: 'The office answered without saying what it established, so nothing is known.' } });
    expect(block(unknown, 'di-scan-herdr-reason')).toMatch(/>The office did not confirm this launch\.<\/p>$/);
    const other = withControl({ check: { kind: 'unavailable', problem: 'not_live', reason: "Nothing was opened: the recorded session is not in Herdr's live agent list now." } });
    expect(block(other, 'di-scan-herdr-reason')).toMatch(/>The office did not offer Herdr for this Mission\. <button/);
  });
  it('A7, ARCHIVED: the line is the archived reason in Herdr\'s words, and the office\'s own answer is still one disclosure away', () => {
    const reason = 'Herdr is not set up in this office: it was started without --herdr-bin, --herdr-home, --herdr-socket. Nothing was attempted.';
    const archivedRow = { ...building, tab: 'archived' as const };
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: [archivedRow],
        initialSelectedId: archivedRow.id,
        initialView: 'mission' as DiScanView,
        onMinimize: noop,
        herdr: herdrControl(archivedRow.id, { check: { kind: 'unavailable', problem: 'herdr_not_configured', reason } }),
      }),
    );
    expect(block(html, 'di-scan-herdr-reason')).toMatch(/>Herdr is not offered: this Mission is archived, so it is read-only\. <button/);
    expect(html).toMatch(/data-testid="di-scan-herdr-more"><summary[^>]*>The office’s answer about Herdr<\/summary>/);
    expect(block(html, 'di-scan-herdr-note')).toContain(`>${reason}</p>`);
  });
  // CHANGED IN B2c: held (aria-disabled) rather than natively disabled; busy as before
  it('before the office has answered it is held and says it has not been checked; while it is being asked it says so and is busy', () => {
    const unknown = withControl({ check: { kind: 'unknown' } });
    expect(tag(unknown, 'di-scan-herdr')).toContain('aria-disabled="true"');
    expect(block(unknown, 'di-scan-herdr-reason')).toContain('has not been checked yet');
    const checking = withControl({ check: { kind: 'checking' } });
    expect(tag(checking, 'di-scan-herdr')).toContain('aria-busy="true"');
    expect(block(checking, 'di-scan-herdr-reason')).toContain('Checking whether Herdr can open for this Mission');
  });
  it('after a launch the note reports exactly what was established: focused as Herdr reported it, the terminal asked to open, no attach and no keyboard claim', () => {
    const opened = withControl({ launch: { kind: 'opened', workspaceId: 'w6V', windowMinCols: 190, windowMinRows: 49, matchedPaneId: 'w6V:p3', terminalLabel: 'Terminal', terminalState: 'opened' } });
    // the line says what was established; the rest of the office's words (nothing typed, the pane, the window) are on demand (CHANGED IN the Free UI streamline, A1)
    expect(block(opened, 'di-scan-herdr-reason')).toContain('Herdr reported workspace w6V focused, then Terminal was asked to open the Herdr launcher.');
    const note = block(opened, 'di-scan-herdr-note');
    expect(note).toContain('Herdr reported workspace w6V focused, then Terminal was asked to open the Herdr launcher.');
    expect(note).toContain('nothing was typed into any pane or terminal');
    expect(opened).not.toMatch(/attach|keyboard|typing works/i);
    const refused = withControl({
      launch: { kind: 'refused', problem: 'focus_not_established', reason: 'Nothing was opened: Workspace focus was not established, and Herdr&#x27;s call failed.' },
    });
    // CHANGED IN the Free UI streamline (A7): the line says THAT the office refused; the route's words are kept verbatim in the on-demand note
    expect(block(refused, 'di-scan-herdr-reason')).toContain('The office refused this launch.');
    expect(block(refused, 'di-scan-herdr-note')).toContain('Nothing was opened: Workspace focus was not established');
    expect(tag(refused, 'di-scan-herdr')).not.toContain('disabled');
  });
  // CHANGED IN B2c: a held item is aria-disabled (and stays focusable); static markup cannot show a click handler, so "a held item's click sends nothing" is
  // proven RENDERED in diScanB2cMoreActionsBrowser.test.ts. BEFORE: "a disabled one carries the disabled attribute".
  it('the markup marks what can launch: the enabled item is the only one not held, and a held one is aria-disabled', () => {
    expect(tag(withControl(), 'di-scan-herdr')).not.toContain('disabled');
    expect(tag(withControl({ check: { kind: 'unavailable', problem: 'not_live', reason: 'x' } }), 'di-scan-herdr')).toContain('aria-disabled="true"');
    expect(tag(withControl({ launch: { kind: 'launching' } }), 'di-scan-herdr')).toContain('aria-disabled="true"');
  });
  it('no view renders a pane grid, a prompt or terminal chrome', () => {
    for (const view of ['list', 'mission', 'diff', 'comments'] as const) {
      const html = render(building, view);
      expect(html, view).not.toMatch(
        /di-scan-herdr-view|di-scan-panes?\b|di-scan-term|Ask Codex|herdr@|data-view="herdr"/,
      );
    }
  });
  it('a stale request for the removed view draws no terminal either', () => {
    const html = render(building, 'herdr' as unknown as DiScanView);
    expect(html).not.toMatch(/di-scan-herdr-view|di-scan-panes?\b|di-scan-term|Ask Codex|herdr@/);
  });
  it('the stylesheet keeps no rule that only served the in-app terminal', () => {
    const css = fs.readFileSync(path.join(root, 'src/components/diScanPanel.css'), 'utf-8');
    expect(css).not.toMatch(/\.di-scan-herdr-view|\.di-scan-panes?\b|\.di-scan-term-/);
  });
});

describe('REVIEW FINDING 1: unread is not absent; an attention-bearing production row never claims nothing waits', () => {
  it('a production row that Reception lists as needing you says so, says what is not read, and never says "Nothing is waiting on you here"', () => {
    const row = productionRow('needs-me', {}, ['needs-me']);
    expect(row.pills.map((p) => p.label)).toContain('Needs you');
    const s = block(render(row), 'di-scan-state-actions');
    expect(s).not.toContain('Nothing is waiting on you here');
    expect(s).toContain('Reception lists this Mission as needing you.');
    expect(s).toContain(
      'Approvals, repository requests, stalls and logs are not reported for this Mission.',
    );
  });
  it('a production row with no attention still says what is not read, and still makes no absence claim', () => {
    const s = block(render(productionRow('quiet', {})), 'di-scan-state-actions');
    expect(s).not.toContain('Nothing is waiting on you here');
    expect(s).not.toContain('Reception lists this Mission');
    expect(s).toContain(
      'Approvals, repository requests, stalls and logs are not reported for this Mission.',
    );
  });
  it('the absence claim appears only where every fact was read (labelled sample state) and none waits', () => {
    const quiet: DiScanRow = {
      ...DI_SCAN_LOCK[3]!,
      state: {
        source: 'fixture',
        running: { known: true, value: false },
        awaitingApproval: false,
        repoRequest: false,
        stalled: false,
        logRecorded: false,
      },
    };
    expect(block(render(quiet), 'di-scan-state-actions')).toContain(
      'Nothing is waiting on you here.',
    );
  });
});

// Free UI streamline (2026-11-04), the trash description copy: the Trash control is DESCRIBED by an always-present copy of its scope (a closed disclosure is computed into
// no description). While a deletion is NOT finished, no copy, drawn or spoken, may say the Mission "was deleted" (the class diRetentionHonesty.test.ts guards). Pinned here for
// the states that test does not reach: this window's own delete in flight, and accepted and still finishing; and on Home's card. Finished, the record that it was deleted is true.
describe('an unfinished deletion is never said as done, drawn or spoken: every copy of the Trash scope in the tense that holds', () => {
  const abandoned = (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-progress-abandoned-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state;
  const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
  const DONE_CLAIM = /was deleted|were deleted|Deleted by its owner/;
  const copies = (html: string) => ({
    spoken: /<span class="di-scan-visually-hidden" id="di-scan-trash-desc">([^<]*)<\/span>/.exec(html)?.[1] ?? '',
    folded: /<p class="di-scan-stage-note" id="di-scan-trash-note">([^<]*)<\/p>/.exec(html)?.[1] ?? '',
  });
  const details = (phase: 'deleting' | 'reclaimed' | 'deleted' | null, deletion?: MrDeletion) => {
    const state = structuredClone(abandoned);
    const mission = state.missions.missions.find((m) => m.missionRef === REF)!;
    if (phase !== null) mission.retention = { ...mission.retention!, phase, phaseAt: 100, summary: null };
    return renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: scanRowsFromOffice(state),
        initialView: 'mission',
        initialSelectedId: REF,
        onMinimize: noop,
        onDeleteLocalData: noop,
        ...(deletion ? { deleteResults: { [REF]: deletion } } : {}),
      }),
    );
  };
  for (const [name, html] of [
    ['its deletion started and is retried (deleting)', () => details('deleting')],
    ['this window\'s delete is sent, its answer not back (in flight)', () => details(null, { state: 'in_flight', text: 'Deleting: waiting.', problem: null })],
    ['this window\'s delete was accepted and is still finishing (pending)', () => details(null, { state: 'pending', text: 'Accepted, still finishing.', problem: null })],
  ] as const)
    it(`${name}: neither the spoken nor the folded copy says it was deleted`, () => {
      const markup = html();
      const { spoken, folded } = copies(markup);
      expect(tag(markup, 'di-scan-trash')).toContain('aria-describedby="di-scan-trash-desc di-scan-trash-reason"');
      for (const [which, words] of [['spoken', spoken], ['folded', folded]] as const) {
        expect(words, which).toMatch(/^A deletion of this Mission is not finished\./);
        expect(words, which).not.toMatch(DONE_CLAIM);
        expect(words, which).toMatch(/It cannot be undone\./);
      }
    });
  it('finished (deleted, or nothing under way): the scope as before, where the record that it was deleted is true or is what a hold will leave', () => {
    for (const markup of [details('deleted'), details('reclaimed')]) {
      const { spoken, folded } = copies(markup);
      expect(spoken).toBe(TRASH_SCOPE);
      expect(folded).toBe(TRASH_SCOPE);
    }
  });
  // CHANGED IN B2a (Direction B, Q8 = (a)): Home's inline expanded card is removed, and with it Home's copy of the Trash scope. The tests here pinned that
  // copy's TENSE (review round 1, F3: Home read this Mission's own delete, by its source-qualified ref) and its REASON by whether the office has a delete route
  // (review round 2, REPORT section 6 item 1). Their subject is gone: in every one of those states Home now draws and speaks NO Trash copy, so it cannot say one
  // in the wrong tense, and the whole-screen guard those tests carried (nothing on Home says it was deleted while a deletion is unfinished) is kept exactly.
  // Mission details' copies follow the rule above, its own in-flight and pending cases included.
  const OTHER_REF = 'synthetic_mission_core:mn-00000000000000000000000000000002';
  const listed = (state: DiOfficeState) => scanRowsFromOffice(state).map((r) => ({ ...r, tab: 'open' as const }));
  const home = (deleteResults?: Record<string, MrDeletion>, withRoute = false) =>
    renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: listed(structuredClone(abandoned)),
        initialSelectedId: REF,
        onMinimize: noop,
        ...(withRoute ? { onDeleteLocalData: noop } : {}),
        ...(deleteResults ? { deleteResults } : {}),
      }),
    );
  const homeDeleting = () => {
    const state = structuredClone(abandoned);
    const mission = state.missions.missions.find((m) => m.missionRef === REF)!;
    mission.retention = { ...mission.retention!, phase: 'deleting', phaseAt: 100, summary: null };
    return renderToStaticMarkup(createElement(DiScanPanel, { rows: listed(state), initialSelectedId: REF, onMinimize: noop }));
  };
  // the sentences Home's Trash used to end with: why it could not act there, and where deleting is done
  const NOT_CONNECTED = 'This office has no request panel to delete through, so nothing can be deleted here.';
  const ON_DETAILS = 'Deleting is done from this Mission’s details, not from here: open it, then hold its Trash.';
  for (const [name, html, unfinished] of [
    ['its deletion started and is retried (the retention reading says deleting)', homeDeleting, true],
    ["this window's own delete in flight, BEFORE the retention reading says deleting", () => home({ [REF]: { state: 'in_flight', text: 'Deleting: waiting.', problem: null } }), true],
    ["this window's own delete pending (accepted, still finishing), BEFORE the retention reading says deleting", () => home({ [REF]: { state: 'pending', text: 'Accepted, still finishing.', problem: null } }), true],
    ['a finished delete', () => home({ [REF]: { state: 'completed', text: 'Deleted.', problem: null } }), false],
    ['a delete of a DIFFERENT Mission', () => home({ [OTHER_REF]: { state: 'in_flight', text: 'Deleting: waiting.', problem: null } }), false],
    ['no delete, an office with NO delete route', () => home(), false],
    ['no delete, an office WITH a delete route', () => home(undefined, true), false],
  ] as const)
    it(`B2a, Home, ${name}: the Mission is listed and NO Trash copy is drawn or spoken${unfinished ? '; nothing on the whole Home screen says it was deleted' : ''}`, () => {
      const markup = html();
      expect(markup).toContain(`data-mission-ref="${REF}"`);
      expect(markup).not.toContain('data-testid="di-scan-trash"');
      expect(markup).not.toContain('di-scan-detail-trash-reason');
      expect(markup).not.toContain(TRASH_SCOPE);
      expect(markup).not.toContain(NOT_CONNECTED);
      expect(markup).not.toContain(ON_DETAILS);
      if (unfinished) expect(markupText(markup, ' ')).not.toMatch(DONE_CLAIM);
    });
});

// B2a (Direction B, Q8 = (a), condition 1, the Lead 2026-11-05): Home's inline expanded card is removed, and NOTHING it showed may be lost. Each thing the card
// drew (title, pills, description, the unresolved runtime records, every swarm member with role, name and identity label, the meta line, the Trash) is in the
// docked stage. Two are one disclosure deeper there, by Mission details' own A8 and A9 design: the members behind the roster toggle, under the one summary
// line; the runtime records behind "Each record, with its state and reason", under their one count line. The removed card test was the only pin on the
// meta line, so this pins it in the stage.
describe('B2a, Q8 condition 1: everything the removed Home card showed is in the docked stage', () => {
  it('title, pills, description, the unresolved records, every member with role and identity label, the meta line and the Trash', () => {
    const row = populatedRow();
    const stage = stageOf(render(row));
    const words = markupText(stage, ' ').replace(/\s+/g, ' ');
    expect(words).toContain(row.title);
    expect(stage.match(/data-testid="di-scan-pill"/g)).toHaveLength(row.pills.length);
    expect(words).toContain(row.description.replace(/\s+/g, ' '));
    const stray = row.unresolvedAgents![0]!;
    expect(stage).toContain('data-testid="di-scan-unresolved-status"');
    expect(words).toContain(stray.name);
    expect(words).toContain(stray.label);
    expect(stage.match(/data-testid="di-scan-swarm-member"/g)).toHaveLength(row.swarm.length);
    for (const member of row.swarm) expect(stage).toContain(`<span class="di-scan-role">${member.role}</span>`);
    expect(stage.match(/data-testid="di-scan-identity" data-identity="[a-z_]+"/g)).toHaveLength(row.swarm.filter((m) => m.identity?.kind === 'unresolved').length);
    expect(stage).toContain('data-testid="di-scan-swarm-status"');
    expect(stage).toContain('data-testid="di-scan-swarm-toggle"');
    expect(block(stage, 'di-scan-meta')).toContain(`<span>${row.footer}</span>`);
    expect(stage).toContain('data-testid="di-scan-trash"');
  });
});
