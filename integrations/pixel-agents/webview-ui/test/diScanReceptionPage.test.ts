/**
 * 03 Reception on the scan face (pass B, CONTROL-MAP R1 to R12, gap 8, AC-4.7-02/-05/-06/-09/-10/-12/-14/-15/-16).
 *
 * The scan face has ONE Reception: a full page beside the rail, fed by the same inbox model as the rail badge, so the
 * header and the badge are one number by construction. Every authority-dependent control (Recover, Approve, Request
 * changes, Reject, a suggested answer, the reply and Send, Accept CI, View log and View checks) is drawn and DISABLED
 * with its reason; Stage 4 wires them. Open Mission is navigation and works; Reception itself never merges.
 * Clicking (Needs you/All, Clear info, Show, opening a Mission) is proven in a real browser in diScanPassB.test.ts.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState, DiReceptionSourceEntry } from '../../core/src/messages.ts';
import { attentionLine, buildInbox } from '../src/components/diReceptionItems.ts';
import { DI_SCAN_V3_CAST, type DiScanRow, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import {
  DI_EARLIER_NOT_READ,
  DI_RECEPTION_SPECIMEN,
  type DiScanReception,
  receptionFromInbox,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const noop = () => undefined;
const render = (reception: DiScanReception | null, rows: readonly DiScanRow[] = DI_SCAN_V3_CAST) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows,
      initialView: 'reception',
      onMinimize: noop,
      reception,
      specimen: reception?.source === 'fixture',
    }),
  );
/** The page's own markup (the rail is rendered before it). */
const page = (html: string) => /data-testid="di-reception-page"[\s\S]*$/.exec(html)?.[0] ?? '';
const rail = (html: string) => /<nav[^>]*data-testid="di-rail"[\s\S]*?<\/nav>/.exec(html)?.[0] ?? '';
/** Every opening tag carrying `testid`. */
const tags = (html: string, testid: string) =>
  [...html.matchAll(new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`, 'g'))].map((m) => m[0]);
const cards = (html: string) =>
  [...page(html).matchAll(/<li[^>]*data-testid="di-reception-card"[\s\S]*?<\/li>/g)].map((m) => m[0]);
const card = (html: string, kind: string) => cards(html).find((c) => c.includes(`data-kind="${kind}"`)) ?? '';

describe('R1: Needs you / All, and the header reads how many need you (AC-4.7-09)', () => {
  const html = render(DI_RECEPTION_SPECIMEN);
  it('opens on Needs you; All is offered beside it', () => {
    expect(html).toMatch(/data-testid="di-reception-filter-needs"[^>]*aria-pressed="true"/);
    expect(html).toMatch(/data-testid="di-reception-filter-all"[^>]*aria-pressed="false"/);
  });
  it('Needs you lists only the items waiting on the person; information items are not among them', () => {
    expect(cards(html)).toHaveLength(5);
    expect(cards(html).some((c) => c.includes('data-kind="info"'))).toBe(false);
  });
  it('the header states the count in the same words the rail badge speaks', () => {
    expect(page(html)).toMatch(new RegExp(`data-testid="di-reception-count"[^>]*>[\\s\\S]*?${attentionLine(5)}<`));
  });
});

describe('AC-4.7-16 by construction: the rail badge and the header are one number', () => {
  it('both read the reception source the page is drawn from', () => {
    for (const n of [1, 5, 12]) {
      const html = render({ ...DI_RECEPTION_SPECIMEN, attention: n });
      expect(rail(html)).toMatch(new RegExp(`data-testid="di-rail-reception-count"[^>]*>${n}<`));
      expect(page(html)).toContain(attentionLine(n));
    }
  });
});

describe('AC-4.7-15: nothing waiting is a short scoped note, and the badge is gone', () => {
  it('says nothing is LISTED (never that nothing needs the person), and no badge is drawn', () => {
    const html = render({ ...DI_RECEPTION_SPECIMEN, attention: 0, cards: [] });
    expect(page(html)).toContain('Nothing is listed as needing you right now.');
    expect(rail(html)).not.toContain('di-rail-reception-count');
  });
});

describe('R2 and AC-4.7-10: each item names its Mission, which opens Mission details', () => {
  const html = render(DI_RECEPTION_SPECIMEN);
  it('the Mission name is a button bound to that Mission, on every card', () => {
    const opens = tags(page(html), 'di-reception-open-mission');
    expect(opens).toHaveLength(5);
    expect(opens.map((t) => /data-mission-ref="([^"]+)"/.exec(t)?.[1])).toEqual([
      'v3-install',
      'v3-dark',
      'v3-probe',
      'v3-inbox',
      'v3-settings',
    ]);
  });
  it('each card shows a status tag as a dot and a word, what is needed, and repos, who raised it and when', () => {
    const stalled = card(html, 'stalled');
    expect(stalled).toMatch(/<span class="di-scan-dot" data-tone="failed" aria-hidden="true"><\/span>Stalled/);
    expect(stalled).toContain('The Executor stopped responding. Recover was tried once, 12 minutes ago.');
    expect(stalled).toContain('public · dodging-infinity · Supervisor · 13m ago');
  });
  it('a Mission this office does not list is named as text, never a button that goes nowhere', () => {
    const html2 = render(DI_RECEPTION_SPECIMEN, []);
    expect(tags(page(html2), 'di-reception-open-mission')).toHaveLength(0);
    expect(page(html2)).toContain('Install guide for new users');
  });
});

describe('R3 to R10: authority controls are drawn for their item type and DISABLED, each with its reason', () => {
  const html = render(DI_RECEPTION_SPECIMEN);
  const expectOff = (c: string, testid: string, reason: RegExp) => {
    const t = tags(c, testid);
    expect(t.length, testid).toBeGreaterThan(0);
    for (const one of t) {
      expect(one, testid).toContain('disabled=""');
      expect(one, testid).toMatch(reason);
    }
  };
  it('R3 Recover and R10 View log on a stalled item', () => {
    const c = card(html, 'stalled');
    expectOff(c, 'di-reception-recover', /title="Recover is not connected in this layout preview\.[^"]*restarts/);
    expectOff(c, 'di-reception-view-log', /title="Logs and checks are not read in this layout preview\."/);
  });
  it('R4 Approve, R5 Request changes (asks for a note), R6 Reject (asks you to confirm; ends the Mission) on a proposal', () => {
    const c = card(html, 'proposal');
    expectOff(c, 'di-reception-approve', /title="Deciding a proposal is not connected in this layout preview\./);
    expectOff(c, 'di-reception-request-changes', /title="[^"]*Request changes asks for a note\./);
    expectOff(c, 'di-reception-reject', /title="[^"]*Reject asks you to confirm, and ends the Mission\./);
  });
  it('R7 suggested answers and R8 a free-text reply with Send on a question', () => {
    const c = card(html, 'question');
    expect(tags(c, 'di-reception-suggestion')).toHaveLength(2);
    expectOff(c, 'di-reception-suggestion', /title="Answering a Mission is not connected in this layout preview\."/);
    expectOff(c, 'di-reception-reply', /title="Answering a Mission is not connected in this layout preview\."/);
    expectOff(c, 'di-reception-send', /title="Answering a Mission is not connected in this layout preview\."/);
  });
  it('R9 Accept CI results and R10 View checks on a CI result', () => {
    const c = card(html, 'ci');
    expectOff(c, 'di-reception-accept-ci', /title="Accepting CI results is not connected in this layout preview\."/);
    expectOff(c, 'di-reception-view-checks', /title="Logs and checks are not read in this layout preview\."/);
  });
  it('every reason is also spoken to a screen reader, not only shown on hover', () => {
    const c = card(html, 'proposal');
    for (const t of tags(c, 'di-reception-approve')) {
      const id = /aria-describedby="([^"]+)"/.exec(t)?.[1];
      expect(id).toBeTruthy();
      expect(c).toContain(`id="${id}"`);
    }
  });
});

describe('R11: a merge waiting offers only Open Mission; Reception never merges (AC-4.7-05/-27)', () => {
  const html = render(DI_RECEPTION_SPECIMEN);
  it('Open Mission is live navigation, bound to its Mission', () => {
    const open = tags(card(html, 'delivery'), 'di-reception-open-delivery');
    expect(open).toHaveLength(1);
    expect(open[0]).not.toContain('disabled');
    expect(open[0]).toContain('data-mission-ref="v3-settings"');
  });
  it('no control anywhere on the page merges', () => {
    const buttons = [...page(html).matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) =>
      m[1].replace(/<[^>]+>/g, ''),
    );
    expect(buttons.filter((b) => /merge/i.test(b))).toEqual([]);
  });
});

describe('R12: Earlier today, and Clear info as a distinct control (AC-4.7-06/-14)', () => {
  it('the specimen says how many were handled earlier today, with Show', () => {
    const html = render(DI_RECEPTION_SPECIMEN);
    expect(page(html)).toMatch(/data-testid="di-reception-earlier"[\s\S]*?Earlier today: 3 handled/);
    expect(tags(page(html), 'di-reception-earlier-show')).toHaveLength(1);
  });
  it('on real data the handled count is NOT read: it says so, and offers no Show', () => {
    const html = render({ ...DI_RECEPTION_SPECIMEN, source: 'office', earlier: null });
    expect(page(html)).toContain(DI_EARLIER_NOT_READ);
    expect(tags(page(html), 'di-reception-earlier-show')).toHaveLength(0);
  });
  it('action items have no clear or hide control of any kind', () => {
    const html = render(DI_RECEPTION_SPECIMEN);
    for (const c of cards(html)) expect(c).not.toMatch(/Clear|Hide|Dismiss/);
  });
});

describe('the scan face has ONE Reception: the page, never the dialog (lead Q2, D-10)', () => {
  it('no inbox dialog is drawn on the scan face', () => {
    const html = render(DI_RECEPTION_SPECIMEN);
    expect(html).not.toContain('di-reception-inbox');
    expect(html).not.toContain('role="dialog"');
  });
  it('without a reception source the rail entry is drawn, disabled, with its reason', () => {
    const html = render(null);
    const entry = /<button[^>]*data-testid="di-open-reception"[^>]*>/.exec(rail(html))?.[0] ?? '';
    expect(entry).toContain('disabled=""');
    expect(entry).toContain('title="Reception is not available in this office"');
  });
  it('sample data says it is sample data, as clearly as the lock specimen does', () => {
    expect(page(render(DI_RECEPTION_SPECIMEN))).toContain('Sample data (fixture)');
    expect(page(render({ ...DI_RECEPTION_SPECIMEN, source: 'office' }))).not.toContain('fixture');
  });
});

describe('receptionFromInbox: the page reads the SAME inbox model as the badge (real office data)', () => {
  const fixture = JSON.parse(
    fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
  ) as { states: Record<string, DiOfficeState> };
  const state = fixture.states.live!;
  const info: DiReceptionSourceEntry = {
    key: 'grok:info-1',
    signature: 's1',
    category: 'delivery_decision',
    sourceId: 'grok_delivery_presentations',
    proposalDigest: 'd'.repeat(64),
    state: 'authorized',
    needsYou: false,
    presentedAt: 1_800_000_000,
    expiresAt: 1_800_003_600,
    mode: 'pr',
    repositoryUrl: null,
    workflowId: null,
    matchingDeliveries: 1,
    linkState: 'unlinked',
    missionRef: null,
    authorizationCheck: 'checked',
    current: true,
    staleReasons: [],
    route: 'grok',
  };
  const reception = { ...state.reception!, sourceEntries: [info] };
  const model = buildInbox(reception, { kind: 'not_provided' }, state.missions.missions, new Set());
  const rows = scanRowsFromOffice(state);
  const page = receptionFromInbox(model, reception, rows);
  it('its count IS the model attention, and every listed action item is a card', () => {
    expect(page.attention).toBe(model.attention);
    expect(page.cards).toHaveLength(model.visible.length);
    expect(page.cards.map((c) => c.key)).toEqual(model.visible.map((i) => i.key));
  });
  it('information items are kept apart from action items', () => {
    expect(page.information.map((c) => c.key)).toEqual(['grok:info-1']);
    expect(page.information[0]!.kind).toBe('info');
  });
  it('names a listed Mission by its title and binds it; says what it does not know', () => {
    const first = page.cards[0]!;
    expect(rows.some((r) => r.id === first.missionRef)).toBe(true);
    expect(first.title).toBe(rows.find((r) => r.id === first.missionRef)!.title);
    expect(first.meta).toContain('Repos not recorded');
    expect(first.meta).toContain('time not recorded');
  });
  it('the handled-earlier count is unread (null), never zero', () => {
    expect(page.earlier).toBeNull();
    expect(page.source).toBe('office');
  });
});

describe('AC-4.7-16 under UNCERTAINTY (pass C): the rail badge and the header express the SAME state, not only the same digits', () => {
  // AC-4.7-16's surfaces here: the rail Reception badge (the sidebar) and the Reception page header. The third, a macOS
  // menu-bar count, does not exist in this webview build. A count whose coverage is incomplete is a FLOOR: the badge says
  // "N+" (or "?" when nothing is known to need attention) and the header must say the same, never a confident number.
  const states = (file: string) =>
    (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', file), 'utf-8')) as {
      states: Record<string, DiOfficeState>;
    }).states;
  const historical = states('di-office-state-evidence-historical-v2.FIXTURE.json');
  const review = states('di-office-state-code-review-v2.FIXTURE.json');
  const pageOf = (state: DiOfficeState) => {
    const model = buildInbox(state.reception, { kind: 'not_provided' }, state.missions.missions, new Set());
    return receptionFromInbox(model, state.reception, scanRowsFromOffice(state));
  };
  const decided = (state: DiOfficeState): DiOfficeState => ({
    ...state,
    reception: {
      ...state.reception!,
      notices: [],
      categories: state.reception!.categories.map((c) => ({
        ...c,
        outcome: c.counts.present > 0 ? ('present' as const) : ('known_empty' as const),
        reason: null,
        counts: { ...c.counts, unsupported: 0, unavailable: 0 },
        gaps: [],
      })),
    },
  });
  const railBadge = (html: string) =>
    /data-testid="di-rail-reception-count"[^>]*>([^<]*)</.exec(rail(html))?.[1];
  const header = (html: string) =>
    /data-testid="di-reception-count"[^>]*>(?:<span[^>]*><\/span>)?([^<]*)</.exec(page(html))?.[1];
  /** The one state both surfaces must express. */
  const stateOf = (badgeText: string | undefined, headerText: string | undefined) => ({
    badge: badgeText === undefined ? 'none' : badgeText.endsWith('+') ? 'floor' : badgeText === '?' ? 'unknown' : 'exact',
    header: headerText?.startsWith('At least ')
      ? 'floor'
      : headerText?.startsWith('Not known whether')
        ? 'unknown'
        : headerText?.startsWith('Nothing listed')
          ? 'none'
          : 'exact',
  });

  it('MIXED (evidence-historical-v2 live, the catalog does not report clarification): badge "N+" and header "At least N", the same N', () => {
    const reception = pageOf(historical.live!);
    expect(reception.attention).toBeGreaterThan(0);
    const html = render(reception, scanRowsFromOffice(historical.live!));
    const n = reception.attention;
    expect(railBadge(html)).toBe(`${n}+`);
    expect(header(html)).toMatch(new RegExp(`^At least ${n} `));
    expect(stateOf(railBadge(html), header(html))).toEqual({ badge: 'floor', header: 'floor' });
    // the reasons are said under the header in the same plain words the rows use, never a raw code
    expect(page(html)).toMatch(/data-testid="di-reception-doubt"[^>]*>[^<]*Some things could not be checked: [^<.]*questions for you/);
    // C-U1: the reason under the header is one plain sentence: no implementation vocabulary
    expect(/data-testid="di-reception-doubt"[^>]*>([^<]*)</.exec(page(html))?.[1] ?? '').not.toMatch(
      /\b(record|records|catalog|Mission Core|source|sources|projection|contract|covered)\b/i,
    );
    expect(page(html)).not.toMatch(/data-testid="di-reception-doubt"[^>]*>[^<]*\b[a-z_]+:[a-z_0-9:]+/);
  });
  it('NOTHING KNOWN, coverage incomplete (no entries listed, clarification unreported): badge "?" and header "Not known whether", never no badge', () => {
    const state = historical.live!;
    const empty = { ...state, reception: { ...state.reception!, entries: [], entriesTotal: 0 } } as DiOfficeState;
    const reception = pageOf(empty);
    expect(reception.attention).toBe(0);
    const html = render(reception, scanRowsFromOffice(empty));
    expect(railBadge(html)).toBe('?');
    expect(header(html)).toMatch(/^Not known whether anything needs attention/);
    expect(stateOf(railBadge(html), header(html))).toEqual({ badge: 'unknown', header: 'unknown' });
  });
  it('EVERYTHING KNOWN (code-review-v2 live with every category decided): the plain count on both, no plus', () => {
    const state = decided(review.live!);
    const reception = pageOf(state);
    const n = reception.attention;
    expect(n).toBeGreaterThan(0);
    const html = render(reception, scanRowsFromOffice(state));
    expect(railBadge(html)).toBe(String(n));
    expect(header(html)).toBe(attentionLine(n));
    expect(stateOf(railBadge(html), header(html))).toEqual({ badge: 'exact', header: 'exact' });
    expect(page(html)).not.toContain('data-testid="di-reception-doubt"');
  });
  it('NOTHING WAITING, everything known: no badge and "Nothing listed" on both (AC-4.7-15 holds when coverage is whole)', () => {
    const base = decided(review.live!);
    const state = { ...base, reception: { ...base.reception!, entries: [], entriesTotal: 0 } } as DiOfficeState;
    const html = render(pageOf(state), scanRowsFromOffice(state));
    expect(stateOf(railBadge(html), header(html))).toEqual({ badge: 'none', header: 'none' });
  });
});
