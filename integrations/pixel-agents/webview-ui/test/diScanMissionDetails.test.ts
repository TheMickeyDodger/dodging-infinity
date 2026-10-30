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

import { DI_SCAN_LOCK, type DiScanRow, type DiScanView } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import { DI_RAIL_WIDTH_PX } from '../src/constants.ts';
import { productionRow } from './helpers/productionRow.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** The trash's scope sentence, as owner decision 2 requires it (copied here, never read from the code under test). */
const TRASH_SCOPE =
  'Deletes this Mission’s reasoning, transcripts and logs: DI’s copies, the herd’s own terminal history, and the providers’ own session transcripts. Keeps the summary and the approval record. If any part cannot be deleted, it says what remains.';
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
const LAYOUT_NOTE = 'Layout preview. These controls are drawn but not connected yet.';

describe('PRESENTATION: the pixel-lock composition, in order (pixel-lock/02-mission-details.png, D-2)', () => {
  it('Back and breadcrumb, title, status pills, details card, Swarm, Delivery, state actions, the three buttons, trash last', () => {
    const html = render(building);
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
      'di-scan-herdr',
      'di-scan-comments',
      'di-scan-trash',
    ].map((id) => [id, position(html, id)] as const);
    const sorted = [...order].sort((a, b) => a[1] - b[1]).map(([id]) => id);
    expect(sorted).toEqual(order.map(([id]) => id));
  });
  it("the stage starts at the rail's edge, so the rail stays beside it", () => {
    expect(tag(render(building), 'di-scan-stage')).toContain(`left:${DI_RAIL_WIDTH_PX}px`);
  });
  it('the three buttons read Code Diff, Herdr and Comments, in plain title case (no pixel display labels)', () => {
    const html = render(building);
    for (const [id, label] of [
      ['di-scan-code-diff', 'Code Diff'],
      ['di-scan-herdr', 'Herdr'],
      ['di-scan-comments', 'Comments'],
    ] as const)
      expect(block(html, id)).toMatch(new RegExp(`>${label}</button>$`));
  });
  it('status pills are a dot AND a word (AC-UX-10)', () => {
    const pills = [
      ...render(building).matchAll(
        /data-testid="di-scan-pill"[^>]*>([\s\S]*?)<\/span>(?=<span class="di-scan-pill"|<\/span>)/g,
      ),
    ].map((m) => m[1]);
    expect(pills.length).toBe(2);
    for (const pill of pills)
      expect(pill).toMatch(
        /<span class="di-scan-dot" data-tone="[a-z]+" aria-hidden="true"><\/span>(Needs you|Building)/,
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
  it('sits bottom-right, after the three buttons, and says what is deleted and what is kept', () => {
    const html = render(merged);
    expect(position(html, 'di-scan-trash')).toBeGreaterThan(position(html, 'di-scan-comments'));
    expect(tag(html, 'di-scan-trash')).toContain('aria-label="Hold to delete this Mission’s data"');
    expect(html).toContain(TRASH_SCOPE);
  });
  it('the trash copy is true at FULL scope: DI, the herd tools and the providers, the summary kept, a partial delete said as partial', () => {
    // three owners, each named: DI, the herd tools, and the model providers (their own session transcripts)
    expect(TRASH_SCOPE).toMatch(/DI’s copies, the herd’s own terminal history, and the providers’ own session transcripts/);
    expect(TRASH_SCOPE).not.toMatch(/[\u2013\u2014]/);
    expect(TRASH_SCOPE).toMatch(/Keeps the summary and the approval record\./);
    expect(TRASH_SCOPE).toMatch(/If any part cannot be deleted, it says what remains\./);
    // no DI-only promise anywhere in the trash's words
    const html = render(merged);
    const words = [
      tag(html, 'di-scan-trash'),
      block(html, 'di-scan-trash-reason'),
      html.match(/id="di-scan-trash-note"[^>]*>[^<]*/)?.[0] ?? '',
    ].join(' ');
    expect(words).not.toMatch(/\blocal\b/i);
  });
  it('a running Mission: disabled, and the reason is that it is running', () => {
    const html = render(building);
    expect(tag(html, 'di-scan-trash')).toContain('disabled=""');
    expect(block(html, 'di-scan-trash-reason')).toContain('A running Mission cannot be deleted.');
  });
  it('a finished Mission: still disabled at this stage, and says the delete is not connected (it never hides the row)', () => {
    const html = render(merged);
    expect(tag(html, 'di-scan-trash')).toContain('disabled=""');
    expect(block(html, 'di-scan-trash-reason')).toContain(
      'The office does not delete Missions. Deletion stays outside this office.',
    );
    expect(html).not.toMatch(/aria-label="Clear /);
  });
  it('the Home expanded card has the same disabled trash, bottom-right, and no hide gesture', () => {
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: DI_SCAN_LOCK,
        initialSelectedId: building.id,
        onMinimize: noop,
      }),
    );
    const card = block(html, 'di-scan-detail');
    const foot = /class="di-scan-detail-foot"[\s\S]*$/.exec(card)![0];
    expect(foot.indexOf('data-testid="di-scan-meta"')).toBeLessThan(
      foot.indexOf('data-testid="di-scan-trash"'),
    );
    expect(tag(card, 'di-scan-trash')).toContain('disabled=""');
    expect(card).not.toMatch(/aria-label="Clear /);
  });
});

describe('A4: no in-app Herdr terminal remains (GAP-RESOLUTIONS 9, 11, 13; supersedes the v3 05-herdr pane grid)', () => {
  it('the Herdr button keeps its place but is disabled, with its plain reason, and opens no nested screen', () => {
    const html = render(building);
    const herdr = tag(html, 'di-scan-herdr');
    expect(herdr).not.toContain('disabled');
    expect(herdr).toContain('aria-describedby="di-scan-herdr-reason"');
    expect(block(html, 'di-scan-herdr-reason')).toContain(
      'Herdr runs in your own terminal. This office does not start it. From your Dodging Infinity checkout, the documented command is herdctl.',
    );
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
