/**
 * P2-A D1 on the Office server: the catalog's `delivery_presentations` block (contract v2) and the
 * reception `delivery_decision` category built from it.
 *
 * What is REAL: the strict projection, the office view and the pure reception derivation, run over
 * the pinned SYNTHETIC v2 catalog answer (catalog-answer-v2.fc409bbc, hash-pinned) with a
 * delivery_presentations block added by each test. What is SYNTHETIC, labelled so: every proposal
 * digest, repository URL and workflow id below. No Grok state, delivery store or catalog is read.
 *
 * Discrimination: an ABSENT block (an older catalog) reads "not reported" — unsupported, never
 * empty and never known_empty — against a PRESENT block of zero entries, which is the one known
 * empty; a present block that breaks the closed contract refuses the whole answer rather than
 * being half trusted.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type {
  DiReception,
  DiReceptionSourceCoverage,
  DiReceptionSourceEntry,
} from '../../core/src/messages.js';
import { DI_RECEPTION_MAX_SOURCE_ENTRIES } from '../src/constants.js';
import {
  DELIVERY_AUTHORIZATION_CHECKS,
  DELIVERY_ENTRY_STATES,
  DELIVERY_LINK_STATES,
  DELIVERY_MODES,
  DELIVERY_PRESENTATION_ENTRY_KEYS,
  DELIVERY_PRESENTATIONS_KEYS,
  DELIVERY_PRESENTATIONS_PROBLEMS,
  DELIVERY_PRESENTATIONS_SOURCE_ID,
  DELIVERY_PRESENTATIONS_STATES,
  DELIVERY_STORE_STATES,
  MAX_DELIVERY_PRESENTATIONS,
  RESPONSE_KEYS_V2,
} from '../src/di/catalogContract.js';
import type { CatalogEnvelope } from '../src/di/catalogProjection.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { OfficeView } from '../src/di/officeView.js';
import { buildOfficeView, unavailableView } from '../src/di/officeView.js';
import { deriveReception } from '../src/di/receptionDesk.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const V2_PATH = path.join(
  __dirname,
  'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const NOW = 1000000; // the pinned v2 answer's generated_at (valid_until 1000060)
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const WF = 'synthetic_workflow_store:wf-0005';

type Raw = Record<string, unknown>;

function loadV2(): Raw {
  const bytes = fs.readFileSync(V2_PATH);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== V2_SHA256) throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

function envelope(raw: Raw): CatalogEnvelope {
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  return parsed.envelope;
}

const response = (raw: Raw): Raw => raw.response as Raw;

/** A v2 office view over the pinned answer, optionally edited, at `now` (the consumer's clock). */
function viewV2(edit?: (raw: Raw) => void, now = NOW): OfficeView {
  const raw = clone(loadV2());
  edit?.(raw);
  return buildOfficeView(envelope(raw), FRESH, now, 2);
}

function digestOf(n: number): string {
  return n.toString(16).padStart(64, '0');
}

function entryRaw(n: number, over: Raw = {}): Raw {
  return {
    source_id: DELIVERY_PRESENTATIONS_SOURCE_ID,
    proposal_digest_sha256: digestOf(n),
    presented_at: NOW - 600,
    expires_at: NOW + 3000,
    mode: 'pull_request',
    repository_url: 'https://github.com/octo/synthetic-repo',
    binding_mission: { workflow_id: 'wf-0005', authorization_digest_sha256: null },
    state: 'awaiting_no_recorded_authorization',
    matching_deliveries: 0,
    link: { state: 'linked', mission_ref: WF, authorization_digest: 'not_comparable' },
    ...over,
  };
}

function blockRaw(entries: Raw[] = [], over: Raw = {}): Raw {
  return {
    state: 'observed',
    problem: null,
    source_id: DELIVERY_PRESENTATIONS_SOURCE_ID,
    delivery_store: 'registered',
    coverage: { route: 'grok_bot', max_entries: 16, direct_cli: 'not_stored' },
    total: entries.length,
    entries,
    ...over,
  };
}

const withBlock =
  (block: unknown) =>
  (raw: Raw): void => {
    response(raw).delivery_presentations = block;
  };

const desk = (r: DiReception) => {
  const c = r.categories.find((x) => x.category === 'delivery_decision');
  if (!c) throw new Error('no delivery_decision category');
  return c;
};
const coverage = (r: DiReception): DiReceptionSourceCoverage => {
  const found = (r.sourceCoverage ?? []).filter(
    (x) => x.sourceId === DELIVERY_PRESENTATIONS_SOURCE_ID,
  );
  if (found.length !== 1) throw new Error(`${found.length} delivery coverage lines`);
  return found[0];
};
const sourceEntries = (r: DiReception): DiReceptionSourceEntry[] => r.sourceEntries ?? [];

describe('the contract constants, pinned by exact value', () => {
  it('names the closed keys, vocabularies and bounds the Python contract emits', () => {
    expect(DELIVERY_PRESENTATIONS_SOURCE_ID).toBe('grok_delivery_presentations');
    expect(MAX_DELIVERY_PRESENTATIONS).toBe(16);
    expect(DI_RECEPTION_MAX_SOURCE_ENTRIES).toBe(16);
    expect(DI_RECEPTION_MAX_SOURCE_ENTRIES).toBe(MAX_DELIVERY_PRESENTATIONS);
    expect([...DELIVERY_PRESENTATIONS_KEYS]).toEqual([
      'state',
      'problem',
      'source_id',
      'delivery_store',
      'coverage',
      'total',
      'entries',
    ]);
    expect([...DELIVERY_PRESENTATION_ENTRY_KEYS]).toEqual([
      'source_id',
      'proposal_digest_sha256',
      'presented_at',
      'expires_at',
      'mode',
      'repository_url',
      'binding_mission',
      'state',
      'matching_deliveries',
      'link',
    ]);
    expect([...DELIVERY_PRESENTATIONS_STATES]).toEqual([
      'not_registered',
      'observed',
      'unavailable',
    ]);
    expect([...DELIVERY_STORE_STATES]).toEqual(['registered', 'not_registered']);
    expect([...DELIVERY_MODES]).toEqual(['pull_request', 'pr_update']);
    expect([...DELIVERY_ENTRY_STATES]).toEqual([
      'awaiting_no_recorded_authorization',
      'authorized',
      'expired_without_authorization',
      'unknown',
    ]);
    expect([...DELIVERY_LINK_STATES]).toEqual([
      'linked',
      'unlinked_no_mission_binding',
      'unlinked_no_registered_mission',
      'unlinked_ambiguous',
      'unlinked_registry_unavailable',
      'unlinked_sources_incomplete',
    ]);
    expect([...DELIVERY_AUTHORIZATION_CHECKS]).toEqual([
      'corroborated',
      'differs',
      'not_comparable',
    ]);
    expect([...DELIVERY_PRESENTATIONS_PROBLEMS]).toEqual([
      'presentations_directory_missing',
      'presentations_directory_not_private',
      'presentations_file_absent',
      'presentations_unreadable',
      'presentations_json_invalid',
      'presentations_schema_unsupported',
      'presentations_invalid',
      'delivery_store_unreadable',
      'delivery_store_invalid',
      'reader_failed',
    ]);
    // the block is ADDITIVE: the v2 required key set did not grow
    expect(RESPONSE_KEYS_V2).not.toContain('delivery_presentations');
  });
});

describe('a legacy v2 catalog with NO delivery_presentations block', () => {
  it('is accepted, and reads "not reported by this catalog" — never empty, never known_empty', () => {
    const view = viewV2();
    expect(view.missions.state).toBe('observed'); // the answer itself is still accepted
    expect(view.deliveryPresentations.reported).toBe(false);
    const r = deriveReception(view);
    expect(desk(r)).toMatchObject({
      outcome: 'unsupported',
      reason: 'catalog_does_not_report:delivery_presentations',
      entriesTotal: 0,
    });
    expect(desk(r).outcome).not.toBe('known_empty');
    expect(sourceEntries(r)).toEqual([]);
    expect(r.sourceEntriesTotal).toBe(0);
    expect(coverage(r)).toMatchObject({
      outcome: 'unsupported',
      reason: 'catalog_does_not_report:delivery_presentations',
      total: null,
    });
    expect(coverage(r).statement).toMatch(/not "none"/);
    expect(coverage(r).statement).toMatch(/Grok Bot route only/);
    expect(coverage(r).statement).toMatch(/present-dots/);
  });

  it('a v1 office says the block is a contract v2 block, with its own reason', () => {
    const raw = clone(loadFixture());
    const v1 = buildOfficeView(envelope(raw), FRESH, 1800000000, 1);
    const r = deriveReception(v1);
    expect(desk(r)).toMatchObject({
      outcome: 'unsupported',
      reason: 'contract_v1:delivery_presentations_is_a_contract_v2_block',
    });
    expect(coverage(r).reason).toBe('contract_v1:delivery_presentations_is_a_contract_v2_block');
  });

  it('the key is never valid at contract v1', () => {
    const raw = clone(loadFixture());
    (raw.response as Raw).delivery_presentations = blockRaw();
    const v1 = buildOfficeView(envelope(raw), FRESH, 1800000000, 1);
    expect(v1.missions.state).toBe('unavailable');
    expect(v1.deliveryPresentations.reported).toBe(false);
  });
});

describe('a PRESENT block is projected strictly — a malformed one refuses the whole answer', () => {
  const malformed: Array<[string, unknown]> = [
    ['not an object', []],
    ['an unknown key', { ...blockRaw(), approved: true }],
    ['a missing key', (({ total: _t, ...rest }) => rest)(blockRaw())],
    ['an unknown state', blockRaw([], { state: 'gone' })],
    ['a problem on an observed block', blockRaw([], { problem: 'presentations_invalid' })],
    ['an unavailable block without a problem', blockRaw([], { state: 'unavailable', total: null })],
    [
      'an unknown problem code',
      blockRaw([], { state: 'unavailable', problem: 'weird', total: null }),
    ],
    ['another source id', blockRaw([], { source_id: 'somebody_else' })],
    ['another delivery store state', blockRaw([], { delivery_store: 'maybe' })],
    [
      'another coverage',
      blockRaw([], { coverage: { route: 'grok_bot', max_entries: 17, direct_cli: 'not_stored' } }),
    ],
    [
      'an extra coverage key',
      blockRaw([], {
        coverage: { route: 'grok_bot', max_entries: 16, direct_cli: 'not_stored', x: 1 },
      }),
    ],
    ['a total that is not the entries', blockRaw([entryRaw(1)], { total: 2 })],
    ['entries on a not_registered block', blockRaw([entryRaw(1)], { state: 'not_registered' })],
    [
      'a total on an unavailable block',
      blockRaw([], { state: 'unavailable', problem: 'reader_failed', total: 0 }),
    ],
    ['a repeated proposal digest', blockRaw([entryRaw(1), entryRaw(1)])],
    ['more than 16 entries', blockRaw(Array.from({ length: 17 }, (_, i) => entryRaw(i + 1)))],
    ['an extra entry key', blockRaw([entryRaw(1, { extra: 1 })])],
    ['a non-digest proposal id', blockRaw([entryRaw(1, { proposal_digest_sha256: 'nope' })])],
    ['an unknown entry state', blockRaw([entryRaw(1, { state: 'declined' })])],
    ['an unknown mode', blockRaw([entryRaw(1, { mode: 'push' })])],
    [
      'authorized with no matching delivery',
      blockRaw([entryRaw(1, { state: 'authorized', matching_deliveries: 0 })]),
    ],
    ['unknown with a count', blockRaw([entryRaw(1, { state: 'unknown', matching_deliveries: 0 })])],
    ['a count with no state unknown', blockRaw([entryRaw(1, { matching_deliveries: null })])],
    [
      'a link without its Mission',
      blockRaw([
        entryRaw(1, {
          link: { state: 'linked', mission_ref: null, authorization_digest: 'not_comparable' },
        }),
      ]),
    ],
    [
      'a Mission without a link',
      blockRaw([
        entryRaw(1, {
          link: {
            state: 'unlinked_ambiguous',
            mission_ref: WF,
            authorization_digest: 'not_comparable',
          },
        }),
      ]),
    ],
    [
      'an unknown link state',
      blockRaw([
        entryRaw(1, {
          link: { state: 'maybe', mission_ref: null, authorization_digest: 'not_comparable' },
        }),
      ]),
    ],
    [
      'an unknown authorization check',
      blockRaw([
        entryRaw(1, {
          link: {
            state: 'unlinked_no_mission_binding',
            mission_ref: null,
            authorization_digest: 'yes',
          },
        }),
      ]),
    ],
    [
      'a binding_mission with an extra key',
      blockRaw([
        entryRaw(1, {
          binding_mission: { workflow_id: 'wf', authorization_digest_sha256: null, note: 'x' },
        }),
      ]),
    ],
    [
      'a binding_mission digest that is not hex',
      blockRaw([
        entryRaw(1, { binding_mission: { workflow_id: 'wf', authorization_digest_sha256: 'zz' } }),
      ]),
    ],
    ['a negative time', blockRaw([entryRaw(1, { expires_at: -1 })])],
    ['a fractional time', blockRaw([entryRaw(1, { presented_at: 1.5 })])],
  ];

  for (const [label, block] of malformed) {
    it(`refuses ${label}: no entries, never read as empty`, () => {
      const view = viewV2(withBlock(block));
      expect(view.missions.state).toBe('unavailable');
      expect(view.missions).toMatchObject({ reason: expect.stringMatching(/^malformed:/) });
      expect(view.deliveryPresentations.reported).toBe(false);
      const r = deriveReception(view);
      expect(desk(r).outcome).toBe('unavailable');
      expect(desk(r).outcome).not.toBe('known_empty');
      expect(sourceEntries(r)).toEqual([]);
      expect(coverage(r).outcome).toBe('unavailable');
    });
  }

  it('accepts exactly the bound: 16 entries, and every closed vocabulary value', () => {
    const entries = Array.from({ length: 16 }, (_, i) => entryRaw(i + 1));
    const view = viewV2(withBlock(blockRaw(entries)));
    expect(view.deliveryPresentations.entries).toHaveLength(16);
    for (const state of DELIVERY_ENTRY_STATES) {
      const e =
        state === 'unknown'
          ? entryRaw(1, { state, matching_deliveries: null })
          : entryRaw(1, { state, matching_deliveries: state === 'authorized' ? 1 : 0 });
      expect(viewV2(withBlock(blockRaw([e]))).deliveryPresentations.entries[0].state).toBe(state);
    }
    for (const linkState of DELIVERY_LINK_STATES) {
      const link = {
        state: linkState,
        mission_ref: linkState === 'linked' ? WF : null,
        authorization_digest: 'not_comparable',
      };
      expect(
        viewV2(withBlock(blockRaw([entryRaw(1, { link })]))).deliveryPresentations.entries[0].link
          .state,
      ).toBe(linkState);
    }
  });
});

describe('no catalog answer at all', () => {
  it('delivery proposals read unavailable with the connection reason — never unsupported, never none', () => {
    const r = deriveReception(unavailableView('transport:backend_unreachable', NOW));
    expect(desk(r)).toMatchObject({
      outcome: 'unavailable',
      reason: 'connection_unavailable:transport:backend_unreachable',
      entriesTotal: 0,
    });
    expect(coverage(r)).toMatchObject({
      outcome: 'unavailable',
      reason: 'connection_unavailable:transport:backend_unreachable',
      total: null,
    });
    expect(coverage(r).statement).toMatch(/could not be read, and "none" is not claimed/);
    expect(sourceEntries(r)).toEqual([]);
    // the same view at contract v1 is still the v1 reason's concern only when an answer exists
    expect(desk(r).outcome).not.toBe('known_empty');
  });
});

describe('every block state reads as itself in the reception desk', () => {
  it('not_registered: unsupported with its own reason (a source that was never registered)', () => {
    const r = deriveReception(
      viewV2(
        withBlock(
          blockRaw([], { state: 'not_registered', delivery_store: 'not_registered', total: null }),
        ),
      ),
    );
    expect(desk(r)).toMatchObject({
      outcome: 'unsupported',
      reason: 'not_registered:delivery_presentations_source',
    });
    expect(sourceEntries(r)).toEqual([]);
  });

  it('unavailable: unavailable with the source problem, never "none"', () => {
    for (const problem of DELIVERY_PRESENTATIONS_PROBLEMS) {
      const r = deriveReception(
        viewV2(withBlock(blockRaw([], { state: 'unavailable', problem, total: null }))),
      );
      expect(desk(r)).toMatchObject({
        outcome: 'unavailable',
        reason: `delivery_presentations_unavailable:${problem}`,
      });
      expect(coverage(r).statement).toMatch(/not "none"/);
    }
  });

  it('observed with zero entries: the ONE known empty — for the Grok route only, stated', () => {
    const r = deriveReception(viewV2(withBlock(blockRaw([]))));
    expect(desk(r)).toMatchObject({ outcome: 'known_empty', reason: null, entriesTotal: 0 });
    expect(coverage(r)).toMatchObject({ outcome: 'known_empty', total: 0 });
    expect(coverage(r).statement).toMatch(/Grok Bot route/);
    expect(coverage(r).statement).toMatch(/not proof that no delivery is awaiting/);
    expect(coverage(r).statement).not.toMatch(/^No /);
  });

  it('observed with zero entries on a STALE answer is not a "none"', () => {
    const r = deriveReception(viewV2(withBlock(blockRaw([])), NOW + 10_000));
    expect(desk(r).outcome).toBe('unavailable');
    expect(desk(r).reason).toMatch(/^envelope_not_fresh:/);
  });

  it('observed entries: present, awaiting first, each with its own state and identity', () => {
    const r = deriveReception(
      viewV2(
        withBlock(
          blockRaw(
            [
              entryRaw(1, {
                state: 'expired_without_authorization',
                expires_at: NOW - 5,
                presented_at: NOW - 7000,
              }),
              entryRaw(2, { state: 'authorized', matching_deliveries: 1 }),
              entryRaw(3, {
                state: 'awaiting_no_recorded_authorization',
                binding_mission: null,
                link: {
                  state: 'unlinked_no_mission_binding',
                  mission_ref: null,
                  authorization_digest: 'not_comparable',
                },
              }),
              entryRaw(4, { state: 'unknown', matching_deliveries: null }),
            ],
            { delivery_store: 'registered' },
          ),
        ),
      ),
    );
    expect(desk(r).outcome).toBe('present');
    expect(desk(r).entriesTotal).toBe(4);
    expect(r.sourceEntriesCap).toBe(16);
    const entries = sourceEntries(r);
    expect(entries.map((e) => e.state)).toEqual([
      'awaiting_no_recorded_authorization',
      'unknown',
      'authorized',
      'expired_without_authorization',
    ]);
    expect(entries.map((e) => e.needsYou)).toEqual([true, false, false, false]);
    const awaiting = entries[0];
    expect(awaiting.key).toBe(
      JSON.stringify(['delivery_decision', 'grok_delivery_presentations', digestOf(3)]),
    );
    expect(awaiting).toMatchObject({
      sourceId: 'grok_delivery_presentations',
      proposalDigest: digestOf(3),
      workflowId: null,
      missionRef: null,
      linkState: 'unlinked_no_mission_binding',
      authorizationCheck: 'not_comparable',
      current: true,
      staleReasons: [],
    });
    // the original route is TEXT: it offers nothing to click and promises nothing
    expect(awaiting.route).toMatch(/Outside this office/);
    expect(awaiting.route).toMatch(/does not promise an approval is still available/);
    expect(JSON.stringify(entries)).not.toMatch(/declin|reject/i);
    const linked = entries.find((e) => e.proposalDigest === digestOf(2));
    expect(linked).toMatchObject({ missionRef: WF, linkState: 'linked', workflowId: 'wf-0005' });
    expect(coverage(r)).toMatchObject({ outcome: 'present', total: 4 });
  });

  it('the unknown (not determined) state is named, never read as awaiting or authorized', () => {
    const r = deriveReception(
      viewV2(
        withBlock(
          blockRaw([entryRaw(1, { state: 'unknown', matching_deliveries: null })], {
            delivery_store: 'not_registered',
          }),
        ),
      ),
    );
    expect(sourceEntries(r)[0]).toMatchObject({
      state: 'unknown',
      needsYou: false,
      matchingDeliveries: null,
    });
    expect(coverage(r).statement).toMatch(
      /whether a matching authorization is recorded was not determined/,
    );
    expect(coverage(r).statement).toMatch(/none is called waiting, authorized or expired/);
  });

  it('an approval window that ended since the catalog was read makes the entry not current', () => {
    const r = deriveReception(
      viewV2(withBlock(blockRaw([entryRaw(1, { expires_at: NOW + 30 })])), NOW + 45),
    );
    const [e] = sourceEntries(r);
    expect(e.state).toBe('awaiting_no_recorded_authorization'); // as reported at the read
    expect(e.current).toBe(false);
    expect(e.staleReasons).toContain('approval_window_ended_since_catalog_read');
  });

  it('a stale catalog answer makes every entry not current', () => {
    const r = deriveReception(viewV2(withBlock(blockRaw([entryRaw(1)])), NOW + 10_000));
    const [e] = sourceEntries(r);
    expect(e.current).toBe(false);
    expect(e.staleReasons.some((x) => x.startsWith('envelope_not_fresh:'))).toBe(true);
  });

  it('the source cap is the contract bound and the total says when truncated', () => {
    const entries = Array.from({ length: 16 }, (_, i) => entryRaw(i + 1));
    const r = deriveReception(viewV2(withBlock(blockRaw(entries))));
    expect(sourceEntries(r)).toHaveLength(16);
    expect(r.sourceEntriesTotal).toBe(16);
    expect(r.sourceEntriesCap).toBe(16);
  });
});

describe('the delivery source is independent of the Mission registry', () => {
  const unhealthyRegistry = (raw: Raw): void => {
    const registry = response(raw).registry as Raw;
    Object.assign(registry, { health: 'unavailable', problem: 'registry_absent' });
    const sources = response(raw).sources as Raw[];
    for (const s of sources)
      Object.assign(s, { health: 'unavailable', problem: 'source_store_unreadable' });
  };

  it('a registered, readable presentations source stays present beside a degraded registry', () => {
    const view = viewV2((raw) => {
      unhealthyRegistry(raw);
      withBlock(
        blockRaw([
          entryRaw(1, {
            link: {
              state: 'unlinked_registry_unavailable',
              mission_ref: null,
              authorization_digest: 'not_comparable',
            },
          }),
        ]),
      )(raw);
    });
    const r = deriveReception(view);
    expect(r.notices.some((n) => n.reason.startsWith('registry_'))).toBe(true);
    expect(desk(r).outcome).toBe('present');
    expect(sourceEntries(r)[0].linkState).toBe('unlinked_registry_unavailable');
    expect(sourceEntries(r)[0].missionRef).toBeNull();
  });

  it('a known-empty presentations source stays known empty beside an unhealthy Mission source', () => {
    const view = viewV2((raw) => {
      unhealthyRegistry(raw);
      withBlock(blockRaw([]))(raw);
    });
    const r = deriveReception(view);
    expect(desk(r).outcome).toBe('known_empty');
  });
});

describe('the derivation is pure and carries no control', () => {
  it('does not mutate the view and is deterministic', () => {
    const view = viewV2(
      withBlock(
        blockRaw([entryRaw(1), entryRaw(2, { state: 'authorized', matching_deliveries: 1 })]),
      ),
    );
    const before = JSON.stringify(view);
    const a = deriveReception(view);
    const b = deriveReception(view);
    expect(JSON.stringify(view)).toBe(before);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});
