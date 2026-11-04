/**
 * New Mission, drawn as ONE scan-face page (pass B, gap 1, Figures 4.3a/4.3b; CONTROL-MAP coverage G1/G2; AC-4.3-01/-02/-05/-06/-14/-18/-20/-26/-34/-49).
 *
 * Reached from the rail only (D-8). Approve / Request changes / Reject on the sample proposal are drawn and DISABLED with their reason.
 * CHANGED IN G1/G2 (2026-10-28): "Send request" (always disabled, "not connected in this layout preview") was replaced by "Continue in the request workspace", a HANDOFF of the ask to the existing
 * workspace's Ask.
 * CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…, B1/B2): the handoff and the layout-preview aside ("This page is a layout preview. Today a Mission starts in the existing
 * request workspace.") are gone. The Ask box IS the request workspace's own Ask draft (ONE composer), Send sends it as one read-only drafting turn, and the draft appears IN PLACE
 * (test/diScanNewMissionSend.test.ts, test/diScanNewMissionBrowser.test.ts). Send's reasons keep the same order: the limit, then an empty ask, then no request panel. The sample proposal is drawn in
 * place too (it is no longer a screen of its own). The request workspace stays reachable from this page through ONE plainly labelled control, "Review requests".
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { DI_SCAN_LOCK, type DiScanRow } from '../src/components/diScanModel.ts';
import {
  DI_ASK_NO_PANEL,
  DI_HARNESS_FULL,
  DI_NEW_MISSION_OFFICE,
  DI_NEW_MISSION_SPECIMEN,
  DI_NEW_MISSION_SUB,
  DI_ORCHESTRATOR_LIMIT,
  DI_RECENT_NOT_READ,
  type DiNewMissionSource,
  sendAskReason,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel, type DiScanPanelProps } from '../src/components/DiScanPanel.tsx';

const noop = () => undefined;
const render = (props: Partial<DiScanPanelProps> & { newMission?: DiNewMissionSource }) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialView: 'new',
      onMinimize: noop,
      onOpenRequestWorkspace: noop,
      newMission: DI_NEW_MISSION_SPECIMEN,
      specimen: true,
      ...props,
    }),
  );
const page = (html: string, testid = 'di-new-mission') =>
  new RegExp(`data-testid="${testid}"[\\s\\S]*$`).exec(html)?.[0] ?? '';
const tag = (html: string, testid: string) =>
  new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`).exec(html)?.[0] ?? '';
/** `n` Missions read as running. */
const running = (n: number): DiScanRow[] =>
  Array.from({ length: n }, (_, i) => ({
    ...DI_SCAN_LOCK[0]!,
    id: `run-${i}`,
    state: { ...DI_SCAN_LOCK[0]!.state, running: { known: true as const, value: true } },
  }));

describe('G1: New Mission as drawn (AC-4.3-01/-06/-20)', () => {
  const html = page(render({}));
  it('is titled New Mission and says nothing runs before a proposal', () => {
    expect(html).toContain('>New Mission<');
    // CHANGED IN the Free UI streamline: the line says the ask is entered once, drafted here, and that nothing runs before the person's own three steps
    expect(DI_NEW_MISSION_SUB).toMatch(/nothing runs until you submit it, approve it and press Start/);
    expect(html).toContain(DI_NEW_MISSION_SUB);
  });
  it('the harness repo is locked under Always included, with NO control that removes it', () => {
    const chip = /<div[^>]*data-testid="di-new-harness"[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    expect(chip).toContain(DI_HARNESS_FULL);
    expect(chip).toContain('Harness');
    expect(chip).not.toMatch(/<button|<input|aria-label="Remove/);
    expect(html.indexOf('Always included')).toBeLessThan(
      html.indexOf('data-testid="di-new-harness"'),
    );
  });
  it('the repo limit is optional, and empty means DI chooses', () => {
    expect(html).toMatch(/Limit to repos[\s\S]{0,120}Optional/);
    expect(html).toContain('Leave empty to let DI choose');
  });
  it('Recent asks sit BELOW the request box', () => {
    const ask = html.indexOf('data-testid="di-new-ask"');
    const recent = html.indexOf('data-testid="di-new-recent"');
    expect(ask).toBeGreaterThan(-1);
    expect(recent).toBeGreaterThan(ask);
  });
  it('Recent asks the build cannot read are said to be unread, not shown as none', () => {
    const real = page(render({ newMission: DI_NEW_MISSION_OFFICE, specimen: false }));
    expect(real).toContain(DI_RECENT_NOT_READ);
    expect(real).not.toContain('data-testid="di-new-recent-ask"');
  });
});

// CHANGED IN G1/G2, then in the Free UI streamline: the control is Send again, now a real one (the request workspace's own drafting turn). It is HELD (aria-disabled, so it keeps
// focus while its message is answered), never shown live when it cannot send, and it says why.
describe('Send: off when the ask is empty or spaces, at the limit, or with no request panel (AC-4.3-18)', () => {
  it('opens unavailable, saying to type an ask first', () => {
    const send = tag(render({}), 'di-new-send');
    expect(send).toContain('aria-disabled="true"');
    expect(send).toContain('title="Type an ask first."');
  });
  it('the reason follows the rule that holds first: limit, then empty, then no request panel; otherwise it is offered', () => {
    const held = (holding: number) => ({ holding, notKnown: 0 });
    expect(sendAskReason('   ', held(0), 'ready')).toBe('Type an ask first.');
    expect(sendAskReason('Add a toggle', held(0), 'none')).toBe(DI_ASK_NO_PANEL);
    expect(sendAskReason('Add a toggle', held(0), 'ready')).toBeNull();
    // AC-4.3-26: with 14 in use the 15th would start normally, so the limit is not the reason
    expect(sendAskReason('Add a toggle', held(DI_ORCHESTRATOR_LIMIT - 1), 'ready')).toBeNull();
    // a held Orchestrator is "in use", never "running" (review finding 4: a bound task observed without a confirmed team holds one)
    expect(sendAskReason('Add a toggle', held(DI_ORCHESTRATOR_LIMIT), 'ready')).toBe(
      '15 of 15 Orchestrators are in use. Finish or stop one to start another.',
    );
  });
});

describe('At 15 running, New Mission refuses with a plain reason, Open Missions, and Send off (AC-4.3-14/-49)', () => {
  it('shows 15 of 15 with the reason and an Open Missions button', () => {
    const html = page(render({ rows: running(DI_ORCHESTRATOR_LIMIT) }));
    expect(html).toMatch(/data-testid="di-new-limit"[\s\S]*?15 of 15 Orchestrators are in use/);
    expect(html).toContain('Finish or stop one to start another.');
    expect(tag(html, 'di-new-open-missions')).not.toContain('disabled');
    expect(tag(html, 'di-new-send')).toContain(
      'title="15 of 15 Orchestrators are in use. Finish or stop one to start another."',
    );
  });
  it('at 14 running there is no refusal', () => {
    expect(page(render({ rows: running(DI_ORCHESTRATOR_LIMIT - 1) }))).not.toContain(
      'di-new-limit',
    );
  });
});

// CHANGED IN the Free UI streamline (B2): the workspace is no longer framed as THE way to start a Mission (that starts here, in place). It stays reachable through ONE plainly labelled
// control that says what it opens: its Requests, where proposals of record are read and decided.
describe('the request workspace stays reachable, labelled plainly for what it opens, and is never framed as the way to start', () => {
  it('one control, "Review requests", live when the office offers it', () => {
    const html = page(render({}));
    const link = tag(html, 'di-new-open-workspace');
    expect(link).not.toContain('disabled');
    expect(html).toMatch(/data-testid="di-new-open-workspace"[^>]*>Review requests</);
    expect(html.match(/data-testid="di-new-open-workspace"/g)).toHaveLength(1);
  });
  it('the layout-preview framing and the redirect are gone from the page (the real path says no "layout preview" at all; the specimen only inside its labelled sample proposal)', () => {
    const real = page(render({ newMission: DI_NEW_MISSION_OFFICE, specimen: false }));
    expect(real).not.toMatch(/layout preview/i);
    const specimen = page(render({}));
    const sample = /<div class="di-proposal" data-testid="di-proposal">[\s\S]*?<\/section><\/div>/.exec(specimen)?.[0] ?? '';
    expect(sample).toContain('Sample proposal (fixture)');
    expect(specimen.replace(sample, '')).not.toMatch(/layout preview/i);
    for (const html of [specimen, real]) {
      expect(html).not.toContain('Today a Mission starts in the existing request workspace');
      expect(html).not.toContain('Open the existing request workspace');
      expect(html).not.toContain('Continue in the request workspace');
      expect(html).not.toContain('di-new-existing');
    }
  });
  it('without it, the control stays, disabled, and says why', () => {
    const html = page(render({ onOpenRequestWorkspace: undefined }));
    const link = tag(html, 'di-new-open-workspace');
    expect(link).toContain('disabled=""');
    expect(link).toContain('title="Mission requests are not available in this office"');
    // and the reason is spoken, not only shown on hover
    const id = /aria-describedby="([^"]+)"/.exec(link)?.[1] ?? '';
    expect(id).not.toBe('');
    expect(html).toMatch(
      new RegExp(
        `id="${id.replace(/[^\w-]/g, '\\$&')}"[^>]*>Mission requests are not available in this office<`,
      ),
    );
  });
});

// CHANGED IN the Free UI streamline (B1): the sample proposal is drawn IN PLACE on New Mission (folded until asked for), never on a screen of its own
describe("G2: the proposal shows each role's exact model with NO control to change it (AC-4.3-02/-05/-34, D23)", () => {
  const html = page(render({}), 'di-proposal');
  it('lists every role with its exact model and provider, as drawn', () => {
    const team = /<table[^>]*data-testid="di-proposal-team"[\s\S]*?<\/table>/.exec(html)?.[0] ?? '';
    for (const [role, model] of [
      ['Supervisor', 'gpt-6-astra'],
      ['Lead', 'claude-opus-5'],
      ['Executor', 'claude-opus-5-5'],
      ['Reviewer', 'gpt-6-astra'],
    ])
      expect(team).toMatch(new RegExp(`${role}<\\/th><td><code>${model}<\\/code>`));
    expect(team).not.toMatch(/<button|<select|<input|contenteditable/);
  });
  it('shows the harness repo (locked) and the repos DI chose', () => {
    const repos = /data-testid="di-proposal-repos"[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    expect(repos).toContain(DI_HARNESS_FULL);
    expect(repos).toContain('pixel-agents');
    expect(repos).toContain('DI chose');
  });
  it('Approve, Request changes and Reject are drawn and disabled, each with its reason', () => {
    for (const id of ['di-proposal-approve', 'di-proposal-request-changes', 'di-proposal-reject']) {
      const t = tag(html, id);
      expect(t, id).toContain('disabled=""');
      expect(t, id).toContain('Deciding a proposal needs the request workspace.');
    }
  });
  it('is labelled as a sample, never mistaken for a live proposal', () => {
    expect(html).toContain('Sample proposal (fixture)');
  });
});

describe('the sample proposal is shown in place only when a sample exists', () => {
  it('the specimen offers the sample proposal in place (a disclosure, folded at first); the product does not invent one', () => {
    const html = page(render({}));
    const toggle = tag(html, 'di-new-sample-proposal');
    expect(toggle).toContain('aria-expanded="false"');
    const controls = /aria-controls="([^"]+)"/.exec(toggle)?.[1] ?? '';
    expect(html).toMatch(new RegExp(`<div id="${controls.replace(/[^\w-]/g, '\\$&')}" hidden="">`));
    expect(page(render({ newMission: DI_NEW_MISSION_OFFICE, specimen: false }))).not.toContain(
      'di-new-sample-proposal',
    );
  });
  it('there is no proposal screen: a stale request for one opens the list', () => {
    const html = render({ initialView: 'proposal' as never });
    expect(html).not.toContain('data-testid="di-new-mission"');
    expect(html).not.toContain('data-testid="di-proposal"');
  });
});
