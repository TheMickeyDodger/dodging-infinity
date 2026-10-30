/**
 * G1/G2: New Mission hands the typed ask to the EXISTING request workspace's Ask (its one driver), named for what it does ("Continue in the request workspace"), never "Send request".
 *
 * The three cases (lead's ruling), decided by one pure function of the typed text and the workspace's unsent Ask draft:
 *  (a) the draft is EMPTY (exactly no text) → the typed text transfers in full;
 *  (b) the draft is non-empty and DIFFERENT (a draft of only spaces counts as a draft) → refused, visibly; neither text is touched;
 *  (c) the texts are EXACTLY equal → nothing changes, and it counts as success.
 * The page's own text is never cleared. Whether the control is offered at all keeps the page's existing rules: the Orchestrator limit first, then an empty ask, then a workspace that is not available.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  askHandoff,
  continueAskReason,
  DI_ASK_CONTINUE_HINT,
  DI_ASK_CONTINUE_LABEL,
  DI_ASK_NO_WORKSPACE,
  DI_ORCHESTRATOR_LIMIT,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

describe('the three cases', () => {
  it('(a) an empty draft: the typed text transfers', () => {
    expect(askHandoff('Fix the login timeout', '')).toBe('transfer');
  });

  it('(b) a different draft: refused', () => {
    expect(askHandoff('Fix the login timeout', 'Add a dark mode')).toBe('refused');
  });

  it('(b) a draft of only spaces or a line break is a draft: refused, never replaced', () => {
    expect(askHandoff('Fix the login timeout', ' ')).toBe('refused');
    expect(askHandoff('Fix the login timeout', '\n')).toBe('refused');
  });

  it('(b) near-identical is different: refused (trailing space, case)', () => {
    expect(askHandoff('Fix the login timeout', 'Fix the login timeout ')).toBe('refused');
    expect(askHandoff('Fix the login timeout', 'fix the login timeout')).toBe('refused');
  });

  it('(c) exactly equal: nothing changes, a success', () => {
    expect(askHandoff('Fix the login timeout', 'Fix the login timeout')).toBe('same');
  });
});

describe('when the control is offered', () => {
  it('the Orchestrator limit holds first, then an empty ask, then a workspace that is not available', () => {
    expect(continueAskReason('Fix it', DI_ORCHESTRATOR_LIMIT, true)).toMatch(/Orchestrators are running/);
    expect(continueAskReason('   ', 0, true)).toBe('Type an ask first.');
    expect(continueAskReason('Fix it', 0, false)).toBe(DI_ASK_NO_WORKSPACE);
    expect(continueAskReason('Fix it', 0, true)).toBeNull();
  });
});

describe('the page', () => {
  const page = (withWorkspace: boolean) =>
    renderToStaticMarkup(
      createElement(DiScanPanel, {
        // no rows: nothing is running, so the Orchestrator limit cannot be the reason shown
        rows: [],
        initialView: 'new',
        onMinimize: () => undefined,
        onOpenRequestWorkspace: withWorkspace ? () => undefined : undefined,
        onContinueAsk: withWorkspace ? () => undefined : undefined,
      }),
    );

  it('the control says what it does, and nothing on the page says "Send request" or "to send"', () => {
    const markup = page(true);
    expect(markup).toMatch(new RegExp(`<button[^>]*data-testid="di-new-continue"[^>]*>${DI_ASK_CONTINUE_LABEL}</button>`));
    expect(markup).toContain(DI_ASK_CONTINUE_HINT);
    expect(markup).not.toContain('Send request');
    expect(markup).not.toMatch(/to send\b/);
  });

  it('on first paint the ask is empty, so the control is disabled and says to type an ask first', () => {
    const markup = page(true);
    expect(/<button[^>]*data-testid="di-new-continue"[^>]*>/.exec(markup)?.[0]).toContain('disabled');
    expect(markup).toContain('Type an ask first.');
  });
});
