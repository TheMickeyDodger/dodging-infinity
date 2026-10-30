/**
 * Stage 4, increment 1: the status bar of the scan Code Diff on REAL data: its MARKUP (server-rendered; no browser, no process).
 *
 * What this pins (the browser file `diScanCodeDiffRead.test.ts` measures the FIT and reads the accessibility tree of the same markup):
 *  - the bar's real-only classes exist only on a real read; the owner-approved specimen's bar is untouched (its sample values UTF-8, LF and Spaces: 2, no real-only classes);
 *  - ONE collective phrase ("Format unknown") stands for the three file facts the route does not carry, and the three are ENUMERATED in the
 *    element's hidden text and its title, so nothing is lost to assistive technology or the hover;
 *  - a fixed label ("Detached", "Branch unknown") is never marked as a value the bar may truncate; a real branch NAME is, and its full value
 *    stays in the text and the title;
 *  - TWO FACTS, TWO FIELDS: the one revision drawn on the bar is the chip labelled "base" (route field `comparison.baseOid`); the checkout's own
 *    revision (`target.head`) is NEVER drawn visibly, and for a detached HEAD it is always present in the branch title ("Detached HEAD at <oid>"),
 *    which is now the only place the checkout's identity is stated. The office refuses an answer in which the two differ (server/src/di/codeReview.ts,
 *    parseComparison), so they are equal on every answer it admits; this file does not rely on that and also renders an answer in which they differ.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import type { ReviewSession, ReviewTargetRef } from '../src/components/diCodeReviewModel.ts';
import { CLOSED_SESSION, reduceSession } from '../src/components/diCodeReviewModel.ts';
import type { DiScanRow } from '../src/components/diScanModel.ts';
import { DI_SCAN_LOCK, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import type { DiScanReception } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'),
    'utf-8',
  ),
) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const clone = <T>(value: T): T => structuredClone(value);
const OVERVIEW = doc.replies.overview;
const ROW: DiScanRow = scanRowsFromOffice(doc.states.live)[0]!;
const TARGET = (ROW.reviewOffer as { kind: 'offered'; target: ReviewTargetRef }).target;
const noop = () => undefined;
const RECEPTION: DiScanReception = {
  source: 'office',
  attention: 0,
  attentionDoubt: null,
  cards: [],
  information: [],
  notListed: 0,
  earlier: null,
  empty: [],
  checked: [],
};

function session(
  overview: DiCodeReview,
  selected: { path: string; answer: DiCodeReview } | null = null,
): ReviewSession {
  let s = reduceSession(CLOSED_SESSION, { type: 'open', target: TARGET });
  s = reduceSession(s, { type: 'asked', request: { kind: 'overview' } });
  s = reduceSession(s, { type: 'answer', answer: overview, nowMs: 0 });
  if (selected !== null) {
    const entry = overview.overview!.files.find((f) => f.path === selected.path)!;
    s = reduceSession(s, { type: 'select', path: selected.path });
    s = reduceSession(s, {
      type: 'asked',
      request: {
        kind: 'file',
        fileId: entry.fileId,
        snapshot: overview.snapshot!,
        path: selected.path,
      },
    });
    s = reduceSession(s, { type: 'answer', answer: selected.answer, nowMs: 0 });
  }
  return s;
}

function render(reviewSession: ReviewSession | null, rows: readonly DiScanRow[] = [ROW]): string {
  return renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows,
      initialSelectedId: rows[0]?.id ?? null,
      initialView: 'diff',
      onMinimize: noop,
      reception: RECEPTION,
      reviewSession,
    }),
  );
}

/** The status bar's markup, with React's text-node separators removed so a test reads what a browser's text content reads. */
function barOf(html: string): string {
  const bar = /<footer[^>]*data-testid="di-scan-statusbar"[\s\S]*?<\/footer>/.exec(html)?.[0] ?? '';
  return bar.replace(/<!-- -->/g, '');
}

/** What is DRAWN: the bar's text without the visually hidden spans, tags or attributes. */
function visible(bar: string): string {
  return bar
    .replace(/<span class="di-scan-visually-hidden">[^<]*<\/span>/g, '')
    .replace(/<svg[\s\S]*?<\/svg>/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function withTarget(
  change: (target: NonNullable<DiCodeReview['target']>, answer: DiCodeReview) => void,
): DiCodeReview {
  const answer = clone(OVERVIEW);
  change(answer.target!, answer);
  return answer;
}

// ONE VOCABULARY: every fact the review cannot state is "unknown" (the longer "not determined" did not fit the approved box at 900 px); the
// words "to this review" stay, because they state a limit of the REVIEW'S reading, not a property of the file.
const FACTS = [
  'Encoding: unknown to this review',
  'Line ending: unknown to this review',
  'Indentation: unknown to this review',
];
const fileAnswer = Object.values(doc.replies.files)[0]!;
const fileEntry = OVERVIEW.overview!.files.find((f) => f.fileId === fileAnswer.fileId)!;

describe('the real status bar: the real-only classes, the collective phrase, the enumerated facts', () => {
  const bar = barOf(render(session(OVERVIEW)));

  it('a real read carries the real-only classes on the footer and the left group', () => {
    expect(bar).toContain('class="di-scan-statusbar di-scan-statusbar-real"');
    expect(bar).toContain('class="di-scan-status-group di-scan-status-real"');
  });

  it('the three file facts the route does not carry are ONE phrase; the old three phrases are not drawn', () => {
    const text = visible(bar);
    expect(text).toContain('Format unknown');
    for (const old of [
      'Encoding not determined',
      'Line ending not determined',
      'Indentation not determined',
    ])
      expect(text).not.toContain(old);
  });

  it("the three facts are ENUMERATED in the element's hidden text and in its title", () => {
    const element =
      /<span[^>]*data-testid="di-scan-status-format"[^>]*>[\s\S]*?<\/span><\/span>/.exec(
        bar,
      )?.[0] ?? '';
    expect(element).not.toBe('');
    const enumerated = FACTS.join('. ');
    expect(element).toContain(`title="${enumerated}"`);
    expect(element).toMatch(
      /<span class="di-scan-visually-hidden"> \(Encoding: unknown to this review\. Line ending: unknown to this review\. Indentation: unknown to this review\)<\/span>/,
    );
    for (const fact of FACTS) expect(element).toContain(fact);
    expect(element).not.toMatch(/UTF|\bLF\b|CRLF|Spaces|Tabs/i);
  });

  it('the language stays, after the collective phrase, when a file is selected', () => {
    const withFile = barOf(
      render(session(OVERVIEW, { path: fileEntry.path, answer: clone(fileAnswer) })),
    );
    const format = withFile.indexOf('data-testid="di-scan-status-format"');
    const language = withFile.indexOf('data-testid="di-scan-editor-language"');
    expect(format).toBeGreaterThan(-1);
    expect(language).toBeGreaterThan(format);
  });

  it('the owner-approved SPECIMEN bar is untouched: its sample values (UTF-8, LF, Spaces: 2), no real-only class, no collective phrase', () => {
    const specimen = barOf(render(null, [DI_SCAN_LOCK[0]!]));
    expect(specimen).toContain('di-scan-statusbar');
    expect(specimen).not.toContain('di-scan-statusbar-real');
    expect(specimen).not.toContain('di-scan-status-real');
    expect(specimen).not.toContain('di-scan-status-format');
    for (const word of ['>main<', '8 files changed', '>UTF-8<', '>LF<', '>Spaces: 2<'])
      expect(specimen, word).toContain(word);
  });
});

describe('the branch: a real NAME may be truncated by the bar (its full value stays), a fixed LABEL never is', () => {
  it('a named branch is a VALUE: marked as such, with its full name as text and as title', () => {
    const name = `feature/${'x'.repeat(69)}`;
    const bar = barOf(
      render(
        session(
          withTarget((t) => {
            t.branch = name;
            t.detached = false;
          }),
        ),
      ),
    );
    expect(bar).toContain(
      `<span class="di-scan-status-branch di-scan-status-value" title="${name}">${name}</span>`,
    );
  });

  it('the base chip is a value too (its full revision is in its title), labelled "base"', () => {
    const bar = barOf(render(session(OVERVIEW)));
    const full = OVERVIEW.comparison!.baseOid;
    expect(bar).toContain(
      `<span class="di-scan-status-base di-scan-status-value" title="Compared with revision ${full}">base ${full.slice(0, 10)}</span>`,
    );
  });

  it('a branch that is neither named nor detached is a fixed LABEL (not a value) with its own title', () => {
    const bar = barOf(
      render(
        session(
          withTarget((t) => {
            t.branch = null;
            t.detached = false;
          }),
        ),
      ),
    );
    expect(bar).toContain(
      '<span class="di-scan-status-branch" title="Branch unknown">Branch unknown</span>',
    );
  });
});

describe('ONE VOCABULARY: the real bar says "unknown", in every state, for the text it draws AND the text it reads out', () => {
  // The whole footer's markup is checked, so the visible text, the hidden text and every title attribute are all covered at once.
  const unknownBranch = withTarget((t) => {
    t.branch = null;
    t.detached = false;
  });
  const detached = withTarget((t) => {
    t.branch = null;
    t.detached = true;
  });
  const states: Array<[string, string]> = [
    ['an ordinary read', barOf(render(session(OVERVIEW)))],
    [
      'a file selected',
      barOf(render(session(OVERVIEW, { path: fileEntry.path, answer: clone(fileAnswer) }))),
    ],
    ['a detached HEAD', barOf(render(session(detached)))],
    ['a branch that is neither named nor detached', barOf(render(session(unknownBranch)))],
  ];
  for (const [name, bar] of states) {
    it(`${name}: the footer's markup never says "not determined" (visible text, hidden text and titles)`, () => {
      expect(bar).not.toMatch(/not determined/i);
      expect(bar).toMatch(/unknown/i); // not vacuous: the bar does state the facts it cannot know
    });
  }
});

describe('TWO FACTS, TWO FIELDS: a detached HEAD', () => {
  const head = 'a'.repeat(40);
  const base = 'b'.repeat(40);
  const detached = (checkout: string, compared: string) =>
    withTarget((t, answer) => {
      t.branch = null;
      t.detached = true;
      t.head = checkout;
      answer.comparison!.baseOid = compared;
    });

  it('says "Detached" (a fixed label), completes "HEAD" in hidden text, and ALWAYS carries the checkout\'s identity in the title', () => {
    for (const [label, answer] of [
      ['equal, as the office admits', detached(head, head)],
      ['different, which the office refuses', detached(head, base)],
    ] as const) {
      const bar = barOf(render(session(answer)));
      expect(bar, label).toContain(
        `<span class="di-scan-status-branch" title="Detached HEAD at ${head}">Detached<span class="di-scan-visually-hidden"> HEAD</span></span>`,
      );
    }
  });

  it('the base chip is present beside it, labelled base, in both cases', () => {
    for (const answer of [detached(head, head), detached(head, base)]) {
      const bar = barOf(render(session(answer)));
      expect(bar).toContain(
        `title="Compared with revision ${answer.comparison!.baseOid}">base ${answer.comparison!.baseOid.slice(0, 10)}</span>`,
      );
    }
  });

  it("the checkout's revision is NEVER drawn visibly: when the two differ only the BASE short revision appears; when equal it appears exactly once", () => {
    const different = visible(barOf(render(session(detached(head, base)))));
    expect(different).toContain(base.slice(0, 10));
    expect(different).not.toContain(head.slice(0, 10));
    const equal = visible(barOf(render(session(detached(head, head)))));
    expect(equal.split(head.slice(0, 10)).length - 1).toBe(1);
    expect(equal).toMatch(/Detached base a{10}/);
  });

  it('the checkout identity and the base never swap places in the markup', () => {
    const bar = barOf(render(session(detached(head, base))));
    expect(bar).toContain(`Detached HEAD at ${head}`);
    expect(bar).not.toContain(`Detached HEAD at ${base}`);
    expect(bar).toContain(`Compared with revision ${base}`);
    expect(bar).not.toContain(`Compared with revision ${head}`);
  });
});

describe('the new CSS is SCOPED: it cannot move the owner-approved specimen (static check of the stylesheet text)', () => {
  const css = fs.readFileSync(path.join(root, 'src/components/diScanPanel.css'), 'utf-8');
  const marker = 'Stage 4, increment 1: REAL data only. These selectors hang off classes';
  const END = '/* end of the REAL-only status bar rules */';
  const block = css.slice(css.lastIndexOf('/*', css.indexOf(marker)), css.indexOf(END));
  const rules = block
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('}')
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.includes('{'))
    .map((chunk) => ({
      selector: chunk.slice(0, chunk.indexOf('{')).trim(),
      body: chunk.slice(chunk.indexOf('{') + 1),
    }));

  it('finds the block, its end marker and its rules (the check is not vacuous)', () => {
    expect(css).toContain(marker);
    expect(css).toContain(END);
    expect(css.indexOf(END)).toBeGreaterThan(css.indexOf(marker));
    expect(rules.length).toBe(7);
  });

  it("every selector in the block starts from a real-only class, so the specimen's bar (which never carries one) is untouched", () => {
    for (const { selector } of rules) {
      for (const part of selector.split(','))
        expect(part.trim(), selector).toMatch(/^\.di-scan-(statusbar-real|status-real)\b/);
    }
  });

  it('uses no colour literal (the chrome tokens are the only colours)', () => {
    for (const { body } of rules) expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
  });

  // OBSERVED in the browser (Stage 4, increment 1, run 2 captures): the browser draws an ellipsis for ANY overflow, however small. With the base
  // chip allowed to shrink at all (a weight of 100, and again of 1000) it was still cut in the two long-branch states: the screenshots show
  // `base 20a99955…` (eight of its ten hex characters and an ellipsis), because its share of the shortage was a fraction of a pixel that no
  // integer-rounded measurement could see. The SIZE of a shrink weight is therefore irrelevant. The base revision does not shrink at all, and the
  // branch name, the one variable field, absorbs the whole shortage.
  it('the branch name gives way and the base revision never does: the base does not shrink, the branch can', () => {
    const shrink = (needle: string) => {
      const rule = rules.find((r) => r.selector.includes(needle));
      expect(rule, needle).toBeDefined();
      const match = /flex:\s*0\s+(\d+)\s+auto/.exec(rule!.body);
      expect(match, `${needle} sets flex: 0 <shrink> auto`).not.toBeNull();
      return Number(match![1]);
    };
    expect(shrink('.di-scan-status-branch.di-scan-status-value')).toBeGreaterThanOrEqual(1);
    expect(shrink('.di-scan-status-base.di-scan-status-value')).toBe(0);
  });
});
