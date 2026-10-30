/**
 * UNKNOWN IS NOT ABSENT, as one rule over every scan view (pass B review B-H1; the same defect class as review finding 1
 * and the DI-only trash wording). A production Mission whose diff, history, approvals, delivery and handled items this
 * build has not read must SAY so on every screen, and must never be drawn with an invented default: no "main", no
 * "0 files changed", no "+0", no UTF-8 / LF / Spaces, no success mark, no "No comments", no "No Missions yet" for a list
 * that was never read. Absence of those strings alone would pass on a blank screen, so every view is also held to the
 * POSITIVE: the words that say what it does not know are present. Not read and unavailable stay distinct to the screen.
 *
 * B-E1 is checked here too: in fixture mode the fixture label is drawn on every page and stage (the screens cover the
 * panel's own banner).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_COMMENTS_PAID,
  DI_DELIVERY_NOT_READ,
  DI_DIFF_NOT_READ,
  DI_SCAN_LOCK,
  type DiScanKnown,
  type DiScanMissionsRead,
  type DiScanRow,
  type DiScanView,
  missionsRead,
  scanRowsFromOffice,
  unreadStateFacts,
} from '../src/components/diScanModel.ts';
import {
  DI_EARLIER_NOT_READ,
  DI_RECENT_NOT_READ,
  type DiScanReception,
  missionsCounts,
  missionsShown,
  missionsUnknown,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import { productionRow } from './helpers/productionRow.ts';

const noop = () => undefined;
const OFFICE_RECEPTION: DiScanReception = {
  source: 'office',
  attention: 1,
  // pass C shape migration: a whole count (no doubt), as this literal always meant
  attentionDoubt: null,
  cards: [],
  information: [],
  notListed: 1,
  earlier: null,
  empty: ['Nothing is listed as needing you right now.'],
  checked: [],
};
const render = (
  view: DiScanView,
  extra: {
    rows?: readonly DiScanRow[];
    missionsRead?: DiScanMissionsRead;
    fixtureBanner?: string;
  } = {},
) => {
  const rows = extra.rows ?? [productionRow('real', {})];
  return renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows,
      initialSelectedId: rows[0]?.id ?? null,
      initialView: view,
      onMinimize: noop,
      reception: OFFICE_RECEPTION,
      missionsRead: extra.missionsRead,
      fixtureBanner: extra.fixtureBanner,
    }),
  );
};
/** The words a scan view must never invent for a fact it did not read. */
const INVENTED = [
  />main</,
  /0 files changed/,
  /\+0\b/,
  /−0\b/,
  />UTF-8</,
  />LF</,
  />Spaces: 2</,
  /✓/,
  /No diff is recorded/,
  /No comments are recorded/,
  /Diff not recorded/,
];

describe('B-H1: every scan view of a production Mission says what it has not read, and invents nothing', () => {
  /** view -> the not-read (or unavailable) words that view must SAY. */
  const MUST_SAY: Record<string, string[]> = {
    mission: [
      'Approvals, repository requests, stalls and logs are not read in this layout preview.',
      'Delivery records name only workflow Missions.',
    ],
    diff: [DI_DIFF_NOT_READ, 'Files are not read in this layout preview.'],
    comments: [DI_COMMENTS_PAID],
    missions: ['Not recorded', '>not read<'],
    reception: [DI_EARLIER_NOT_READ],
    new: [DI_RECENT_NOT_READ],
  };
  for (const [view, words] of Object.entries(MUST_SAY)) {
    it(`${view}: says what it does not know, and draws no invented default`, () => {
      const html = render(view as DiScanView);
      for (const w of words) expect(html, `${view} says "${w}"`).toContain(w);
      for (const bad of INVENTED) expect(html, `${view} invents ${bad}`).not.toMatch(bad);
    });
  }
  it('the diff view of an unread diff draws no path, no layout control and no status bar', () => {
    const html = render('diff');
    expect(html).not.toContain('data-testid="di-scan-editor-path"');
    expect(html).not.toContain('aria-label="Diff layout"');
    expect(html).not.toContain('di-scan-statusbar');
    expect(html).not.toContain('aria-label="Copy the diff"');
  });
  it('Comments in Free says only that Comments are Paid and unavailable: no thread, no claim about a history', () => {
    const view = /data-testid="di-scan-comments-view"[\s\S]*$/.exec(render('comments'))?.[0] ?? '';
    expect(view).toContain(DI_COMMENTS_PAID);
    expect(view).not.toMatch(/di-scan-thread|No comments|no comments/);
  });
  it('the SAMPLE diff carries every status fact itself (encoding, line ending, indent): the view defaults none', () => {
    const html = render('diff', { rows: [DI_SCAN_LOCK[0]!] });
    const bar = /<footer[^>]*data-testid="di-scan-statusbar"[\s\S]*?<\/footer>/.exec(html)?.[0] ?? '';
    for (const w of ['>main<', '8 files changed', '+124', '−38', '>UTF-8<', '>LF<', '>Spaces: 2<'])
      expect(bar, w).toContain(w);
    expect(bar, 'no success mark').not.toContain('✓');
    expect(html).toContain('Sample diff (fixture)');
  });
});

describe('B-H1: a Missions list the office has NOT read is never "none"; not read and unavailable stay distinct', () => {
  const notRead = missionsRead({ state: 'not_recorded', reason: null });
  const unavailable = missionsRead({ state: 'unavailable', reason: 'the catalog did not answer' });
  it('the two absences are different kinds with different words', () => {
    expect(notRead.kind).toBe('not_read');
    expect(unavailable.kind).toBe('unavailable');
    expect(notRead.kind === 'read' ? '' : notRead.reason).toBe('Missions are not read in this office.');
    expect(unavailable.kind === 'read' ? '' : unavailable.reason).toBe(
      'Missions are unavailable: the catalog did not answer.',
    );
    expect(missionsRead({ state: 'observed', reason: null }).kind).toBe('read');
    expect(missionsRead({ state: 'known_empty', reason: null }).kind).toBe('read');
  });
  for (const [label, read, word] of [
    ['not read', notRead, 'not read'],
    ['unavailable', unavailable, 'unavailable'],
  ] as const) {
    it(`${label}: the Missions table, the rail and Home say so, each count says "${word}", and nothing says "none"`, () => {
      const reason = read.kind === 'read' ? '' : read.reason;
      const table = render('missions', { rows: [], missionsRead: read });
      expect(table).toMatch(new RegExp(`data-testid="di-missions-not-read" data-read="${read.kind}"[^>]*>${reason}<`));
      expect(table).not.toContain('No Missions yet');
      for (const m of table.matchAll(/data-testid="di-missions-count"[^>]*>([^<]*)</g)) expect(m[1]).toBe(word);
      const home = render('list', { rows: [], missionsRead: read });
      expect(home).toContain(reason);
      expect(home).not.toMatch(/No missions in this list|No Orchestrators are running/);
      expect(home).toMatch(new RegExp(`data-testid="di-rail-missions-not-read" data-read="${read.kind}"`));
    });
  }
  it('a list READ as empty is the one case that says "No Missions yet"', () => {
    expect(render('missions', { rows: [] })).toContain('No Missions yet.');
  });
});

describe('B-E1: in fixture mode every page and stage carries the fixture label (the screens cover the panel banner)', () => {
  const BANNER = 'FIXTURE / VISUAL TEST - synthetic hash-pinned fixture data, not live Missions';
  for (const view of ['mission', 'diff', 'comments', 'missions', 'reception', 'new'] as const) {
    it(view, () => {
      expect(render(view, { fixtureBanner: BANNER })).toMatch(
        new RegExp(`data-testid="di-fixture-banner-in-stage"[^>]*>${BANNER.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}<`),
      );
      expect(render(view)).not.toContain('di-fixture-banner-in-stage');
    });
  }
});

/** One Reception category the server decided for EVERY Mission (known empty, no gap): whole coverage. */
const WHOLE_CATEGORY = {
  category: 'authorization',
  label: 'Authorization',
  statement: '',
  backing: '',
  outcome: 'known_empty',
  reason: null,
  counts: { present: 0, knownEmpty: 1, unsupported: 0, unavailable: 0 },
  gaps: [],
};

describe('B-H1 round 5: plain booleans from possibly-absent inputs carry their uncertainty (needsYou, running)', () => {
  const noReception = productionRow('no-reception', {}, [], { reception: undefined });
  const cutShort = productionRow('cut-short', {}, [], { reception: { entries: [], entriesTotal: 3 } });
  const readInFull = productionRow('read-in-full', {}, [], {
    reception: { entries: [], entriesTotal: 0, notices: [], categories: [WHOLE_CATEGORY] },
  });
  const stale = productionRow('stale', {}, [], { live: false, notLiveReason: 'client_expired: no new office state' });
  const rosterUnread = productionRow('roster-unread', {
    standing: 'active',
    members: { state: 'not_recorded', reason: null, members: [] },
  });

  it('missing Reception: needsYou is NOT KNOWN, said as not read, never false', () => {
    expect(noReception.needsYou).toEqual({
      known: false,
      why: { kind: 'not_read', text: 'Reception is not read in this office.' },
    });
  });
  it('a Reception list cut short: a Mission it does not list is not known, not "does not need you"', () => {
    expect(cutShort.needsYou).toEqual({
      known: false,
      why: { kind: 'not_known', text: 'Reception lists only 0 of 3 items.' },
    });
  });
  it('a Reception list read in full: a Mission it does not list is KNOWN not to need you', () => {
    expect(readInFull.needsYou).toEqual({ known: true, value: false });
  });
  it('a stale office: running is NOT KNOWN, never false', () => {
    expect(stale.state.running).toEqual({
      known: false,
      why: { kind: 'not_known', text: 'its status is not current while the office is not live' },
    });
  });
  it('an unobserved roster: running is not known', () => {
    expect(rosterUnread.state.running).toEqual({
      known: false,
      why: { kind: 'not_known', text: 'its status is not known' },
    });
  });
  it('the shape refuses an unknown without its reason (checked by the compiler, tsc -b)', () => {
    // @ts-expect-error an unknown fact must carry its reason: this line must NOT compile
    const unexplained: DiScanKnown<boolean> = { known: false };
    const explained: DiScanKnown<boolean> = { known: false, why: { kind: 'not_read', text: 'x' } };
    expect([unexplained.known, explained.known]).toEqual([false, false]);
  });
  it('Mission details names running among what it does not know', () => {
    expect(unreadStateFacts(stale.state)).toContain('whether it is running');
    const html = render('mission', { rows: [stale] });
    expect(html).toContain('Whether this Mission is running is not known: its status is not current while the office is not live.');
    expect(html).not.toContain('Nothing is waiting on you here.');
  });
  it('a filter with unknown rows has no confident count; its known matches and its unknown are both kept', () => {
    const rows = [DI_SCAN_LOCK[0]!, stale];
    expect(missionsCounts(rows).running).toBeNull();
    expect(missionsShown(rows, 'running').map((r) => r.id)).toEqual(['lock-next-panel']);
    expect(missionsUnknown(rows, 'running')).toEqual({
      count: 1,
      kind: 'not_known',
      reasons: ['its status is not current while the office is not live'],
      // round 6: the server reason codes behind the reasons (none for a client-observed status)
      codes: [],
    });
    expect(missionsUnknown([noReception], 'needs')).toEqual({
      count: 1,
      kind: 'not_read',
      reasons: ['Reception is not read in this office.'],
      codes: [],
    });
    expect(missionsUnknown(rows, 'all').count).toBe(0);
  });
  it('the chips say what they do not know, per filter: never a 0 for a filter whose input is missing or stale', () => {
    const chip = (html: string, id: string) =>
      new RegExp(`data-testid="di-missions-status-${id}"[\\s\\S]*?data-testid="di-missions-count"[^>]*>([^<]*)<`).exec(html)?.[1];
    const both = render('missions', { rows: [noReception, stale] });
    expect(chip(both, 'needs')).toBe('not read');
    expect(chip(both, 'running')).toBe('not known');
    const partly = render('missions', { rows: [DI_SCAN_LOCK[0]!, stale] });
    // the known match is a floor: at least one is running, and more may be
    expect(chip(partly, 'running')).toBe('1+');
  });
});

describe('durable guard (round 5): needsYou and running are never read for truthiness (an object is always true)', () => {
  /**
   * A known-or-not-known fact is an object, so `if (row.needsYou)`, `row.needsYou ? ...`, `!facts.running` or
   * `filter((r) => r.state.running)` compile and are ALWAYS true: the bug the union was meant to stop, made worse. Every
   * read must go through knownTrue(), knownOrNull() or `.known`. The compiler cannot see this; this test can, IN PART.
   *
   * ITS LIMITS (reviewer advisory, round 6): a bounded, line-based heuristic, not a proof. It reads the four scan-face
   * files below one line at a time with five regular expressions, skipping comment lines. It does NOT catch:
   *  - a read split across lines (`row.needsYou\n ? a : b`);
   *  - a fact first copied into another name (`const n = row.needsYou; if (n)`), or a destructured `running`;
   *  - bracket access (`row['needsYou']`), spreads, or a fact passed on and read for truthiness elsewhere;
   *  - comparisons it does not list (`== true`, `Boolean(row.needsYou)`), or reads in any other file.
   * It is a tripwire for the common forms; the rule itself is the knownTrue()/knownOrNull()/`.known` funnel.
   */
  const FACT = String.raw`(?:\.(?:needsYou|running)|\bneedsYou)`;
  const TRUTHY_READ = [
    new RegExp(String.raw`${FACT}\s*\?(?![.?])`), // fact ? a : b   (not ?. or ??)
    new RegExp(String.raw`${FACT}\s*(?:&&|\|\|)`), // fact && x
    new RegExp(String.raw`(?:&&|\|\||!)\s*[\w.]*${FACT}(?![\w.(])`), // x && fact, !fact
    new RegExp(String.raw`\bif\s*\(\s*[\w.]*${FACT}\s*\)`), // if (fact)
    new RegExp(String.raw`=>\s*[\w.]*${FACT}\s*[),]`), // filter((r) => r.fact)
  ];
  const isTruthyRead = (code: string) => TRUTHY_READ.some((re) => re.test(code));
  it('self-check: the patterns catch each way of reading a fact for truthiness, and pass the safe reads', () => {
    for (const bad of [
      "{row.needsYou ? 'Reception lists this Mission as needing you. ' : ''}",
      'if (row.needsYou) count += 1;',
      'return rows.filter((row) => row.state.running).length;',
      'if (!facts.running) return;',
      'const go = row.needsYou && open;',
      'if (needsYou) pills.push(x);',
    ])
      expect(isTruthyRead(bad), bad).toBe(true);
    for (const safe of [
      'if (knownTrue(row.needsYou)) pills.push(x);',
      '{!row.state.running.known && (',
      '`${row.state.running.why.text}.`',
      'pills: pillsFor(mission, status, tab, needsYou),',
      '[knownOrNull(facts.running), "whether it is running"],',
      'row.needsYou.known ? [] : [row.needsYou.why.text]',
      'return rows.filter((row) => knownTrue(row.state.running)).length;',
    ])
      expect(isTruthyRead(safe), safe).toBe(false);
  });
  it('no scan-face source reads needsYou or running for truthiness', () => {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    const offenders: string[] = [];
    for (const file of [
      'src/components/diScanModel.ts',
      'src/components/diScanPagesModel.ts',
      'src/components/DiScanPanel.tsx',
      'src/components/DiScanPages.tsx',
    ]) {
      fs.readFileSync(path.join(root, file), 'utf-8')
        .split('\n')
        .forEach((line, i) => {
          if (!line.trim().startsWith('//') && !line.trim().startsWith('*') && isTruthyRead(line))
            offenders.push(`${file}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(offenders).toEqual([]);
  });
});

describe('B-H1 round 6: before saying a Mission does NOT need you, the client carries the outcome the SERVER already computed', () => {
  // the server (receptionDesk.ts) already marks a stale "none" unavailable, and reports unsupported and partial
  // coverage with its own reasons; the client must carry that, never flatten it to "does not need you"
  const fixtureRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
  const states = (file: string) =>
    (JSON.parse(fs.readFileSync(path.join(fixtureRoot, file), 'utf-8')) as {
      states: Record<string, DiOfficeState>;
    }).states;
  const review = states('di-office-state-code-review-v2.FIXTURE.json');
  const transitions = states('di-office-state-transitions-v2.FIXTURE.json');
  const withReception = (state: DiOfficeState, reception: Partial<NonNullable<DiOfficeState['reception']>>) =>
    ({ ...state, reception: { ...state.reception!, ...reception } }) as DiOfficeState;
  const unlistedRows = (state: DiOfficeState) => {
    const listed = new Set((state.reception?.entries ?? []).map((e) => e.missionRef));
    return scanRowsFromOffice(state).filter((row) => !listed.has(row.id));
  };
  const whyText = (row: DiScanRow) => (row.needsYou.known ? '' : row.needsYou.why.text);

  const codesOf = (row: DiScanRow) => (row.needsYou.known ? [] : [...(row.needsYou.why.codes ?? [])]);
  /** A raw server reason code (e.g. not_in_catalog_contract:v1_and_v2): provenance, never screen text. */
  const RAW_CODE = /\b[a-z_]+:[a-z_0-9:]+/;
  /**
   * C-U1 (pass C, Supervisor): the visible words are for a person using Missions and Reception, not the record contracts:
   * none of the implementation's vocabulary (which record, catalog, source or projection carries what) reaches the screen.
   */
  const IMPLEMENTATION_WORDS = /\b(record|records|catalog|Mission Core|source|sources|projection|contract|covered)\b/i;
  /** C-U1: the ONE plain sentence that names what could not be checked, said once however many categories are missing. */
  const UNCHECKED = 'Some things could not be checked:';

  it('STALE-EMPTY (the reviewer case): no entries, and the server marked the answer stale: NOT KNOWN, the server\'s reason kept', () => {
    const state = withReception(review.stale!, { entries: [], entriesTotal: 0 });
    const [row] = scanRowsFromOffice(state);
    expect(row!.needsYou.known).toBe(false);
    // C-U1: the stale answer in plain words, the server's notice and category reasons kept as provenance
    expect(whyText(row!)).toContain("Reception's information is out of date.");
    expect(codesOf(row!)).toContain('envelope_not_fresh:expired_locally:response_expired');
    expect(whyText(row!)).toContain(UNCHECKED);
    expect(whyText(row!)).toMatch(/approvals/);
    expect(whyText(row!)).not.toMatch(RAW_CODE);
    expect(whyText(row!)).not.toMatch(IMPLEMENTATION_WORDS);
  });
  it('a real stale office: every Mission Reception does not list is not known, with the server\'s reasons', () => {
    const rows = unlistedRows(transitions.stale!);
    expect(rows.length).toBe(63);
    for (const row of rows) {
      expect(row.needsYou.known, row.id).toBe(false);
      expect(whyText(row), row.id).toContain("Reception's information is out of date.");
    }
  });
  it('a REQUIRED category the catalog does not support (CONTROL-MAP: clarification, delivery decisions): not known, with the server\'s reason', () => {
    const [row] = scanRowsFromOffice(withReception(review.live!, { entries: [], entriesTotal: 0 }));
    expect(row!.needsYou.known).toBe(false);
    expect(codesOf(row!)).toEqual(
      expect.arrayContaining(['not_in_catalog_contract:v1_and_v2', 'catalog_does_not_report:delivery_presentations']),
    );
    // C-U1: said ONCE, in user words, naming what could not be checked
    expect(whyText(row!)).toContain(UNCHECKED);
    expect(whyText(row!)).toMatch(/questions for you/);
    expect(whyText(row!)).toMatch(/delivery decisions/);
    expect(whyText(row!).split(UNCHECKED).length - 1).toBe(1);
    expect(whyText(row!)).not.toMatch(IMPLEMENTATION_WORDS);
  });
  it('a category only partly read: not known, with the server\'s gap reason', () => {
    const [row] = unlistedRows(transitions.live!);
    expect(row!.needsYou.known).toBe(false);
    expect(codesOf(row!)).toContain('pending_decisions:source_kind_has_no_mission_state');
    // C-U1: one plain sentence, every unchecked kind named once, the per-category coverage phrases gone
    expect(whyText(row!)).toContain(UNCHECKED);
    expect(whyText(row!)).toMatch(/approvals/);
    expect(whyText(row!).split(UNCHECKED).length - 1).toBe(1);
    expect(whyText(row!)).not.toMatch(IMPLEMENTATION_WORDS);
    expect(whyText(row!)).not.toMatch(RAW_CODE);
  });
  it('GENUINELY KNOWN EMPTY: every category decided, no notice: KNOWN false, so "does not need you" stays reachable', () => {
    const decided = review.live!.reception!.categories.map((c) => ({
      ...c,
      outcome: 'known_empty' as const,
      reason: null,
      counts: { present: 0, knownEmpty: 1, unsupported: 0, unavailable: 0 },
      gaps: [],
    }));
    const [row] = scanRowsFromOffice(
      withReception(review.live!, { entries: [], entriesTotal: 0, notices: [], categories: decided }),
    );
    expect(row!.needsYou).toEqual({ known: true, value: false });
  });
  it('a category WITH entries and no gap, the rest known empty, no notice: an unlisted Mission is KNOWN not to need you', () => {
    const categories = review.live!.reception!.categories.map((c, i) => ({
      ...c,
      outcome: (i === 0 ? 'present' : 'known_empty') as 'present' | 'known_empty',
      reason: null,
      counts: { present: i === 0 ? 1 : 0, knownEmpty: i === 0 ? 0 : 1, unsupported: 0, unavailable: 0 },
      gaps: [],
    }));
    const other = { ...review.live!.missions.missions[0]!, missionRef: 'not-listed' };
    const state = withReception(
      { ...review.live!, missions: { ...review.live!.missions, missions: [...review.live!.missions.missions, other] } },
      { notices: [], categories },
    );
    const row = scanRowsFromOffice(state).find((r) => r.id === 'not-listed')!;
    expect(row.needsYou).toEqual({ known: true, value: false });
  });
  it('the page: in the stale-empty case Needs you says "not known", never 0, and says the server\'s reason', () => {
    const [row] = scanRowsFromOffice(withReception(review.stale!, { entries: [], entriesTotal: 0 }));
    const html = render('missions', { rows: [row!] });
    // the Needs-you chip's OWN count (captured, so a later chip's words cannot satisfy it)
    const needsChip = /data-testid="di-missions-status-needs"[\s\S]*?data-testid="di-missions-count"[^>]*>([^<]*)</.exec(html)?.[1];
    expect(needsChip).toBe('not known');
    // C-U1: the server's stale-answer reason, in a person's words (its code kept in the row's codes)
    expect(missionsUnknown([row!], 'needs').reasons.join(' ')).toContain("Reception's information is out of date.");
  });
  it('plain words on screen, codes kept as provenance: what a filter says carries no raw code, and keeps the codes for diagnostics', () => {
    const rows = [
      scanRowsFromOffice(withReception(review.stale!, { entries: [], entriesTotal: 0 }))[0]!,
      ...unlistedRows(transitions.live!).slice(0, 2),
    ];
    const unknown = missionsUnknown(rows, 'needs');
    expect(unknown.count).toBe(3);
    for (const reason of unknown.reasons) expect(reason).not.toMatch(RAW_CODE);
    // C-U1: plain words means a person's words too, not only no raw code (this passed under the old vocabulary before)
    for (const reason of unknown.reasons) expect(reason).not.toMatch(IMPLEMENTATION_WORDS);
    expect(unknown.codes).toEqual(
      expect.arrayContaining([
        'envelope_not_fresh:expired_locally:response_expired',
        'pending_decisions:source_kind_has_no_mission_state',
      ]),
    );
  });
});

describe('pass C section 7: "N+" on the server\'s OWN projection (committed exports, drift-checked by diOfficeStateExport)', () => {
  // A filter whose matches are partly KNOWN and partly UNKNOWN shows the known count as a floor ("N+"); a plain count only
  // when every Mission is known. Until now "N+" was asserted only on hand-built rows; these are real server projections.
  const fixtureRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
  const states = (file: string) =>
    (JSON.parse(fs.readFileSync(path.join(fixtureRoot, file), 'utf-8')) as {
      states: Record<string, DiOfficeState>;
    }).states;
  const historical = states('di-office-state-evidence-historical-v2.FIXTURE.json');
  const transitions = states('di-office-state-transitions-v2.FIXTURE.json');
  const review = states('di-office-state-code-review-v2.FIXTURE.json');
  const listedOf = (state: DiOfficeState) => new Set((state.reception?.entries ?? []).map((e) => e.missionRef));
  const needsChip = (html: string) =>
    /data-testid="di-missions-status-needs"[\s\S]*?data-testid="di-missions-count"[^>]*>([^<]*)</.exec(html)?.[1];
  const badge = (html: string) => /data-testid="di-scan-attention"[^>]*>([^<]*)</.exec(html)?.[1];
  const RAW_CODE = /\b[a-z_]+:[a-z_0-9:]+/;

  it('MIXED (evidence-historical-v2 live: one Mission listed, one not): the Needs you chip reads "1+", and says how many more may and why', () => {
    const state = historical.live!;
    const rows = scanRowsFromOffice(state);
    const listed = listedOf(state);
    // the state really mixes: one listed (known), one unlisted (unknown: required coverage is missing)
    expect(rows.filter((r) => listed.has(r.id)).map((r) => r.needsYou)).toEqual([{ known: true, value: true }]);
    expect(rows.filter((r) => !listed.has(r.id)).map((r) => r.needsYou.known)).toEqual([false]);
    expect(needsChip(render('missions', { rows }))).toBe('1+');
    const unknown = missionsUnknown(rows, 'needs');
    expect(unknown.count).toBe(1);
    // C-U1: one plain sentence naming what could not be checked (in the server's category order)
    expect(unknown.reasons.join(' ')).toMatch(/Some things could not be checked: [^.]*questions for you/);
    for (const reason of unknown.reasons) expect(reason).not.toMatch(RAW_CODE);
  });
  it('MIXED AT SCALE (transitions-v2 live: 1 listed, 63 not): "1+", never 1 and never 64', () => {
    const rows = scanRowsFromOffice(transitions.live!);
    expect(needsChip(render('missions', { rows }))).toBe('1+');
    expect(missionsShown(rows, 'needs').length).toBe(1);
    expect(missionsUnknown(rows, 'needs').count).toBe(63);
  });
  it('EVERYTHING KNOWN (code-review-v2 live: its one Mission listed, none unlisted): the plain count, no plus', () => {
    const rows = scanRowsFromOffice(review.live!);
    expect(rows.every((r) => r.needsYou.known)).toBe(true);
    expect(needsChip(render('missions', { rows }))).toBe('1');
    expect(missionsUnknown(rows, 'needs').count).toBe(0);
  });
  it('HOME Open badge, mixed: "1+" (one known to need you, one more may), never a confident "1"', () => {
    const rows = scanRowsFromOffice(historical.live!).map((r) => ({ ...r, tab: 'open' as const }));
    const html = render('list', { rows });
    expect(badge(html)).toBe('1+');
    const title = /<span[^>]*data-testid="di-scan-attention"[^>]*title="([^"]*)"/.exec(html)?.[1] ?? '';
    expect(title).toMatch(/^1 needs you, and more may\. /);
    // C-U1: the reason it speaks is in a person's words (this passed under the old vocabulary before)
    expect(title).toMatch(/Some things could not be checked: /);
    expect(title).not.toMatch(/\b(record|records|catalog|Mission Core|source|sources|projection|contract|covered)\b/i);
  });
  it('HOME Open badge, nothing known to need you but some may (transitions-v2 live without its listed Mission): "?", never no badge', () => {
    const listed = listedOf(transitions.live!);
    const rows = scanRowsFromOffice(transitions.live!)
      .filter((r) => !listed.has(r.id))
      .map((r) => ({ ...r, tab: 'open' as const }));
    const html = render('list', { rows });
    expect(badge(html)).toBe('?');
    // the reason rides in the title and in hidden text, in plain words naming what is not known and why (no raw code)
    const title = /<span[^>]*data-testid="di-scan-attention"[^>]*title="([^"]*)"/.exec(html)?.[1] ?? '';
    expect(title).toMatch(/^Some Missions may need you\. /);
    expect(title).toMatch(/Some things could not be checked: [^.]*questions for you/);
    expect(title).not.toMatch(RAW_CODE);
    expect(html).toContain(`<span class="di-scan-visually-hidden" data-testid="di-scan-attention-reason">${title}</span>`);
  });
  it('HOME Open badge, everything known: the plain count (code-review-v2 live), and NO badge only when every open Mission is known not to need you', () => {
    const rows = scanRowsFromOffice(review.live!).map((r) => ({ ...r, tab: 'open' as const }));
    expect(badge(render('list', { rows }))).toBe('1');
    const decided = review.live!.reception!.categories.map((c) => ({
      ...c,
      outcome: 'known_empty' as const,
      reason: null,
      counts: { present: 0, knownEmpty: 1, unsupported: 0, unavailable: 0 },
      gaps: [],
    }));
    const none = scanRowsFromOffice({
      ...review.live!,
      reception: { ...review.live!.reception!, entries: [], entriesTotal: 0, notices: [], categories: decided },
    } as DiOfficeState).map((r) => ({ ...r, tab: 'open' as const }));
    expect(none.every((r) => r.needsYou.known && r.needsYou.value === false)).toBe(true);
    expect(badge(render('list', { rows: none }))).toBeUndefined();
  });
});
