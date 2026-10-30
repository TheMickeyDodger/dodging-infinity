/**
 * New Mission and the proposal, drawn as scan-face pages (pass B, gap 1, Figures 4.3a/4.3b; CONTROL-MAP coverage G1/G2;
 * AC-4.3-01/-02/-05/-06/-14/-18/-20/-26/-34/-49).
 *
 * Reached from the rail only (D-8). Approve / Request changes / Reject on the sample proposal are drawn and DISABLED with their reason. The existing Mission request workspace stays reachable through
 * ONE plainly labelled link (lead Q1).
 * CHANGED IN G1/G2 (2026-10-28): "Send request" (always disabled, "not connected in this layout preview") is replaced by "Continue in the request workspace", a HANDOFF of the ask to the existing
 * workspace's Ask (test/diScanAskHandoff*.test.ts). Its reasons keep the same order: the limit, then an empty ask, then a workspace that is not available.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { DI_SCAN_LOCK, type DiScanRow } from '../src/components/diScanModel.ts';
import {
  continueAskReason,
  DI_ASK_NO_WORKSPACE,
  DI_HARNESS_FULL,
  DI_NEW_MISSION_OFFICE,
  DI_NEW_MISSION_SPECIMEN,
  DI_ORCHESTRATOR_LIMIT,
  DI_RECENT_NOT_READ,
  type DiNewMissionSource,
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
    expect(html).toContain('DI proposes a team and repos before anything runs.');
  });
  it('the harness repo is locked under Always included, with NO control that removes it', () => {
    const chip = /<div[^>]*data-testid="di-new-harness"[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    expect(chip).toContain(DI_HARNESS_FULL);
    expect(chip).toContain('Harness');
    expect(chip).not.toMatch(/<button|<input|aria-label="Remove/);
    expect(html.indexOf('Always included')).toBeLessThan(html.indexOf('data-testid="di-new-harness"'));
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

// CHANGED IN G1/G2: was "Send request: off when the ask is empty or spaces, and off in this layout stage"; the control is now the handoff, offered when the workspace is available
describe('Continue in the request workspace: off when the ask is empty or spaces, at the limit, or with no workspace (AC-4.3-18)', () => {
  it('opens disabled, saying to type an ask first', () => {
    const send = tag(render({}), 'di-new-continue');
    expect(send).toContain('disabled=""');
    expect(send).toContain('title="Type an ask first."');
  });
  it('the reason follows the rule that holds first: limit, then empty, then no workspace; otherwise it is offered', () => {
    expect(continueAskReason('   ', 0, true)).toBe('Type an ask first.');
    expect(continueAskReason('Add a toggle', 0, false)).toBe(DI_ASK_NO_WORKSPACE);
    expect(continueAskReason('Add a toggle', 0, true)).toBeNull();
    // AC-4.3-26: with 14 running the 15th would start normally, so the limit is not the reason
    expect(continueAskReason('Add a toggle', DI_ORCHESTRATOR_LIMIT - 1, true)).toBeNull();
    expect(continueAskReason('Add a toggle', DI_ORCHESTRATOR_LIMIT, true)).toBe(
      '15 of 15 Orchestrators are running. Finish or stop one to start another.',
    );
  });
});

describe('At 15 running, New Mission refuses with a plain reason, Open Missions, and the handoff off (AC-4.3-14/-49)', () => {
  it('shows 15 of 15 with the reason and an Open Missions button', () => {
    const html = page(render({ rows: running(DI_ORCHESTRATOR_LIMIT) }));
    expect(html).toMatch(/data-testid="di-new-limit"[\s\S]*?15 of 15 Orchestrators running/);
    expect(html).toContain('Finish or stop one to start another.');
    expect(tag(html, 'di-new-open-missions')).not.toContain('disabled');
    expect(tag(html, 'di-new-continue')).toContain(
      'title="15 of 15 Orchestrators are running. Finish or stop one to start another."',
    );
  });
  it('at 14 running there is no refusal', () => {
    expect(page(render({ rows: running(DI_ORCHESTRATOR_LIMIT - 1) }))).not.toContain('di-new-limit');
  });
});

describe('the existing request workspace stays reachable, labelled plainly as the existing one (lead Q1)', () => {
  it('one link, named as the existing workspace, live when the office offers it', () => {
    const html = page(render({}));
    const link = tag(html, 'di-new-open-workspace');
    expect(link).not.toContain('disabled');
    expect(html).toMatch(/data-testid="di-new-open-workspace"[^>]*>Open the existing request workspace</);
    expect(html.match(/data-testid="di-new-open-workspace"/g)).toHaveLength(1);
  });
  it('without it, the link stays, disabled, and says why', () => {
    const html = page(render({ onOpenRequestWorkspace: undefined }));
    const link = tag(html, 'di-new-open-workspace');
    expect(link).toContain('disabled=""');
    expect(link).toContain('title="Mission requests are not available in this office"');
    // and the reason is spoken, not only shown on hover
    const id = /aria-describedby="([^"]+)"/.exec(link)?.[1] ?? '';
    expect(id).not.toBe('');
    expect(html).toMatch(new RegExp(`id="${id.replace(/[^\w-]/g, '\\$&')}"[^>]*>Mission requests are not available in this office<`));
  });
});

describe('G2: the proposal shows each role\'s exact model with NO control to change it (AC-4.3-02/-05/-34, D23)', () => {
  const html = page(render({ initialView: 'proposal' }), 'di-proposal');
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
      expect(t, id).toContain('Deciding a proposal is not connected in this layout preview.');
    }
  });
  it('is labelled as a sample, never mistaken for a live proposal', () => {
    expect(html).toContain('Sample proposal (fixture)');
  });
});

describe('the proposal is reached from New Mission only when a sample exists', () => {
  it('the specimen offers the sample proposal; the product does not invent one', () => {
    expect(tag(page(render({})), 'di-new-sample-proposal')).not.toBe('');
    expect(page(render({ newMission: DI_NEW_MISSION_OFFICE, specimen: false }))).not.toContain(
      'di-new-sample-proposal',
    );
  });
});
