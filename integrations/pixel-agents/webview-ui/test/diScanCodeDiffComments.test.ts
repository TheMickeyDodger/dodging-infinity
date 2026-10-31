/**
 * 06 Code Diff and 07 Comments, a Mission's nested screens (pass B, CONTROL-MAP C1 to C4 and X1 to X3).
 *
 * Code Diff: Back to Mission details, a sibling switch to Comments (Herdr beside it asks the office to open the machine's own
 * terminal on the Mission's herd, and is enabled only by the office's answer for the Mission), a file tree whose files SELECT, and a diff pane of hunks. The working tree is
 * read only: nothing here edits or commits.
 * Comments (Free): Back, a full-width thread, and in place of a composer a truthful Paid/unavailable label. No working
 * looking control, no upsell (gap 10, D-6, D106). The v3 drawing's Reply and Send are superseded by gap 10.
 * Clicking files and switching screens is proven in a real browser in diScanPassB.test.ts.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  DI_SCAN_LOCK,
  DI_SCAN_LOCK_DIFF,
  DI_SCAN_LOCK_THREAD,
  type DiScanRow,
  type DiScanView,
} from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import { productionRow } from './helpers/productionRow.ts';
import { herdrControl } from './herdrControlStub.ts';

const noop = () => undefined;
const render = (view: DiScanView, row: DiScanRow = DI_SCAN_LOCK[0]!, specimen = true, herdr?: ReturnType<typeof herdrControl>) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: [row],
      initialSelectedId: row.id,
      initialView: view,
      onMinimize: noop,
      specimen,
      ...(herdr ? { herdr } : {}),
    }),
  );
const stage = (html: string) => /data-testid="di-scan-stage"[\s\S]*$/.exec(html)?.[0] ?? '';
const tag = (html: string, testid: string) =>
  new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`).exec(html)?.[0] ?? '';
const nav = (html: string) => /<nav[^>]*data-testid="di-scan-siblings"[\s\S]*?<\/nav>/.exec(html)?.[0] ?? '';

describe("C2: Code Diff and Comments switch between each other; Herdr sits beside them, enabled only by the office's answer", () => {
  for (const view of ['diff', 'comments'] as const) {
    it(`on ${view}: the three siblings, the current one marked`, () => {
      const html = stage(render(view, DI_SCAN_LOCK[0]!, true, herdrControl(DI_SCAN_LOCK[0]!.id)));
      const names = [...nav(html).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1]);
      expect(names).toEqual(['Code Diff', 'Herdr', 'Comments']);
      const current = view === 'diff' ? 'di-scan-sibling-diff' : 'di-scan-sibling-comments';
      expect(tag(nav(html), current)).toContain('aria-current="page"');
    });

    it(`on ${view}: with no Herdr control wired, Herdr is disabled and says nothing is connected, not a note about another tool`, () => {
      const herdr = tag(nav(stage(render(view))), 'di-scan-sibling-herdr');
      expect(herdr).toContain('disabled=""');
      expect(herdr).toContain('title="The Herdr control is not connected to this view, so nothing can be opened from here."');
      expect(stage(render(view))).not.toMatch(/herdctl|does not start it/);
    });

    it(`on ${view}: where the office said a launch is available, Herdr is enabled and its description says what a click does`, () => {
      const html = stage(render(view, DI_SCAN_LOCK[0]!, true, herdrControl(DI_SCAN_LOCK[0]!.id)));
      expect(tag(nav(html), 'di-scan-sibling-herdr')).not.toContain('disabled');
      expect(nav(html)).toContain('It first asks Herdr to focus workspace w6V');
    });

    it(`on ${view}: where the office refused, Herdr is disabled with the office's own reason in its title and description`, () => {
      const reason = 'Nothing was opened: the recorded session is not in Herdr&#x27;s live agent list now.';
      const html = stage(
        render(
          view,
          DI_SCAN_LOCK[0]!,
          true,
          herdrControl(DI_SCAN_LOCK[0]!.id, {
            check: { kind: 'unavailable', problem: 'not_live', reason: "Nothing was opened: the recorded session is not in Herdr's live agent list now." },
          }),
        ),
      );
      expect(tag(nav(html), 'di-scan-sibling-herdr')).toContain('disabled=""');
      expect(tag(nav(html), 'di-scan-sibling-herdr')).toContain(`title="${reason}"`);
      expect(nav(html)).toContain(`>${reason}</span>`);
    });

    it(`on ${view}: after a click the result is shown in the header, and it claims no attach and no working keyboard`, () => {
      const html = stage(
        render(
          view,
          DI_SCAN_LOCK[0]!,
          true,
          herdrControl(DI_SCAN_LOCK[0]!.id, { launch: { kind: 'opened', workspaceId: 'w6V', windowMinCols: 190, windowMinRows: 49, matchedPaneId: null, terminalLabel: 'Terminal', terminalState: 'unconfirmed' } }),
        ),
      );
      expect(nav(html)).toMatch(
        /data-testid="di-scan-herdr-open"[^>]*>Herdr reported workspace w6V focused\. Terminal was asked to open the Herdr launcher, but the opener had not finished within its time limit/,
      );
      expect(nav(html)).not.toMatch(/attach|keyboard|typing works/i);
    });
  }
});

describe('C1/X1: Back returns to Mission details', () => {
  for (const view of ['diff', 'comments'] as const)
    it(view, () => {
      expect(tag(render(view), 'di-scan-back')).toContain('aria-label="Back to mission details"');
    });
});

describe('C3: the file tree selects a file; folders are structure, not targets', () => {
  const html = stage(render('diff'));
  it('every file is a button; no folder is', () => {
    const files = DI_SCAN_LOCK_DIFF.tree.filter((e) => e.kind === 'file');
    const buttons = [
      ...html.matchAll(/<button[^>]*data-testid="di-scan-tree-file"[^>]*data-file-id="([^"]+)"/g),
    ].map((m) => m[1]);
    expect(buttons).toEqual(files.map((f) => f.id));
    expect(html).not.toMatch(/<button[^>]*data-testid="di-scan-tree-dir"/);
  });
  it('the first file is selected, and the diff pane shows its path', () => {
    expect(html).toMatch(/data-file-id="code"[^>]*aria-current="true"/);
    expect(html).toMatch(
      new RegExp(`data-testid="di-scan-editor-path"[^>]*>${DI_SCAN_LOCK_DIFF.files.code!.path}<`),
    );
  });
  it('the sample diff carries one diff per changed file, and the count matches the status bar', () => {
    const changed = DI_SCAN_LOCK_DIFF.tree.filter((e) => e.kind === 'file' && e.mark !== undefined);
    expect(Object.keys(DI_SCAN_LOCK_DIFF.files).sort()).toEqual(changed.map((e) => e.id).sort());
    expect(DI_SCAN_LOCK_DIFF.filesChanged).toBe(`${changed.length} files changed`);
  });
});

describe('C4: the diff pane shows hunks; nothing edits and nothing commits', () => {
  const html = stage(render('diff'));
  it('shows the hunk header and its added and removed lines', () => {
    const editor = /data-testid="di-scan-editor"[\s\S]*?<footer/.exec(html)?.[0] ?? '';
    expect(editor).toContain('@@ -42,14 +42,14 @@');
    expect(editor).toMatch(/data-kind="del"/);
    expect(editor).toMatch(/data-kind="add"/);
    expect(editor).not.toMatch(/<textarea|contenteditable|<input/);
  });
  it('offers no commit, push or save', () => {
    const buttons = [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) =>
      m[1].replace(/<[^>]+>/g, ''),
    );
    expect(buttons.filter((b) => /commit|push|save/i.test(b))).toEqual([]);
  });
});

describe('X2/X3: Comments in Free is a full-width thread and a Paid/unavailable label, never a composer', () => {
  it('the specimen draws its sample thread, labelled as a sample', () => {
    const html = stage(render('comments'));
    const thread = /<ol[^>]*data-testid="di-scan-thread"[\s\S]*?<\/ol>/.exec(html)?.[0] ?? '';
    expect(
      [...thread.matchAll(/class="di-scan-comment-author">([^<]*)</g)].map((m) => m[1]),
    ).toEqual(DI_SCAN_LOCK_THREAD.map((c) => c.author));
    expect(html).toContain('Sample thread (fixture)');
  });
  it('in place of a composer: a plain label that Comments are Paid and unavailable in Free', () => {
    const html = stage(render('comments'));
    expect(html).toMatch(
      /data-testid="di-scan-comments-paid"[^>]*>[\s\S]*?Comments are part of Paid team sharing\. They are not available in Free\./,
    );
    const view = /data-testid="di-scan-comments-view"[\s\S]*$/.exec(html)?.[0] ?? '';
    expect(view).not.toMatch(/<textarea|<input|contenteditable|>Send</);
    expect(view).not.toMatch(/Upgrade|Buy|Checkout|Subscribe|Try Paid/i);
  });
  it('a real Free Mission that is not a Herdr task shows ONLY the Paid/unavailable label', () => {
    const html = stage(render('comments', productionRow('real', { sourceKind: 'mission_core' }), false));
    expect(html).not.toContain('data-testid="di-scan-thread"');
    expect(html).not.toContain('fixture');
    expect(html).not.toMatch(/No comments|no comments/);
    expect(html).toContain(
      'Comments are part of Paid team sharing. They are not available in Free.',
    );
  });
  it('a Herdr task shows its recorded review as comments, and no layout-preview or Paid label', () => {
    const absent = stage(render('comments', productionRow('real', {}), false));
    expect(absent).toContain('Review comments are not reported for this Herdr task.');
    expect(absent).not.toContain('Layout preview');
    expect(absent).not.toContain('data-testid="di-scan-comments-paid"');
    expect(absent).not.toMatch(/No comments|no comments/);
    const recorded = stage(
      render(
        'comments',
        productionRow('real', {
          evidence: {
            reviews: {
              state: 'observed',
              reason: null,
              total: 1,
              items: [
                {
                  findings: { state: 'not_recorded', problem: 'findings_legacy_record_predates_field', count: null, items: [], candidateSha256: null, routedAt: null, provenance: null },
                  round: 4,
                  reviewerLogical: 'reviewer1',
                  reviewerAgent: 'pod',
                  protocolToken: 'APPROVE',
                  gate: 'parsed',
                  verdict: 'APPROVE',
                  currency: 'current',
                  integrity: 'linkage_corroborated',
                  candidateApplicability: 'unproven',
                },
              ],
            },
          },
        }),
        false,
      ),
    );
    expect(recorded).toContain('reviewer1');
    expect(recorded).toContain('APPROVE');
    expect(recorded).toContain('Round 4');
    expect(recorded).not.toContain('Layout preview');
    expect(recorded).not.toContain('data-testid="di-scan-comments-paid"');
  });
});

describe('C3/C4 truthfulness (B-H1): an unread diff is said as unread, never as zero; files carry no folder chevron', () => {
  it('a Mission whose diff is not read says so, and draws no status bar, branch or count for it', () => {
    const html = stage(render('diff', productionRow('real', {}), false));
    expect(html).toContain('This office has not said whether a code review is offered for this Mission.');
    expect(html).not.toContain('di-scan-statusbar');
    expect(html).not.toMatch(/0 files changed|\+0|−0|>main<|Diff not recorded/);
  });
  it('only folders draw the expand chevron; a file row draws none (pixel-lock 03)', () => {
    const html = stage(render('diff'));
    const chevron = 'M2.6 4.2 6 7.8 9.4 4.2';
    const files = [
      ...html.matchAll(/<button[^>]*data-testid="di-scan-tree-file"[\s\S]*?<\/button>/g),
    ].map((m) => m[0]);
    const dirs = [
      ...html.matchAll(/<span[^>]*data-testid="di-scan-tree-dir"[\s\S]*?<\/span><\/span>/g),
    ].map((m) => m[0]);
    expect(files.length).toBeGreaterThan(0);
    for (const f of files) expect(f).not.toContain(chevron);
    expect(dirs.length).toBeGreaterThan(0);
    for (const d of dirs) expect(d).toContain(chevron);
  });
});
