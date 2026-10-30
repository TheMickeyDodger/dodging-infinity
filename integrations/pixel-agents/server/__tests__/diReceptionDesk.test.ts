/**
 * The Mission reception desk (item 06) — the attention derivation as a PURE function of the
 * office view, under BOTH contract versions. v2 cases run against the child's EMITTED v2 answer
 * (catalog-answer-v2.fc409bbc, hash-pinned, SYNTHETIC ids) and edited clones of it; v1 cases run
 * against the CONTRACT-v3 canonical FIXTURE. FIXTURE evidence only — never live acceptance.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type {
  DiReception,
  DiReceptionCategory,
  DiReceptionCategoryId,
  DiReceptionEntry,
} from '../../core/src/messages.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { DI_RECEPTION_MAX_ENTRIES } from '../src/constants.js';
import type { CatalogEnvelope } from '../src/di/catalogProjection.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import { DiOffice } from '../src/di/diOffice.js';
import type { FixtureScript } from '../src/di/fixtureSource.js';
import {
  createFixtureSource,
  parseFixtureScript,
  readFixtureScript,
} from '../src/di/fixtureSource.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import type { OfficeView } from '../src/di/officeView.js';
import { buildOfficeView, unavailableView } from '../src/di/officeView.js';
import { deriveReception } from '../src/di/receptionDesk.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, declareWorkflowPhases, loadFixture } from './helpers/diFixture.js';

const V2_PATH = path.join(
  __dirname,
  'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const NOW_V2 = 1000000; // the v2 fixture's generated_at (valid_until 1000060)
const NOW_V1 = 1800000000; // the v1 fixture's generated_at (valid_until 1800000060)
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };

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

function viewV2(edit?: (raw: Raw) => void, now = NOW_V2): OfficeView {
  const raw = clone(loadV2());
  edit?.(raw);
  return buildOfficeView(envelope(raw), FRESH, now, 2);
}

function viewV1(edit?: (raw: Raw) => void): OfficeView {
  const raw = clone(loadFixture());
  edit?.(raw);
  return buildOfficeView(envelope(raw), FRESH, NOW_V1, 1);
}

const missions = (raw: Raw): Raw[] => (raw.response as Raw).missions as Raw[];
const report = (raw: Raw, i: number): Raw => (missions(raw)[i].mission_state as Raw).report as Raw;
const factValue = (raw: Raw, i: number, fact: string): Raw =>
  (report(raw, i)[fact] as Raw).value as Raw;
const completion = (raw: Raw, i: number): Raw => report(raw, i).completion as Raw;
const review = (raw: Raw, i: number): Raw =>
  (missions(raw)[i].admitted_artifacts as Raw).canonical_review as Raw;

const M2 = 'synthetic_mission_core:mn-00000000000000000000000000000002';
const M9 = 'synthetic_mission_core:mn-00000000000000000000000000000009';
const WF = 'synthetic_workflow_store:wf-0005';
const V1_CORE = 'synthetic-core:mn-1714199978b50a9d93349749406a85a7';
const V1_LEGACY = 'synthetic-legacy:synthetic-wf-0001';

const category = (r: DiReception, id: DiReceptionCategoryId): DiReceptionCategory => {
  const c = r.categories.find((x) => x.category === id);
  if (!c) throw new Error(`no category ${id}`);
  return c;
};
const entriesOf = (r: DiReception, id: DiReceptionCategoryId): DiReceptionEntry[] =>
  r.entries.filter((e) => e.category === id);
const entryFor = (r: DiReception, id: DiReceptionCategoryId, ref: string): DiReceptionEntry => {
  const found = entriesOf(r, id).filter((e) => e.missionRef === ref);
  if (found.length !== 1) throw new Error(`${found.length} ${id} entries for ${ref}`);
  return found[0];
};
const causes = (e: DiReceptionEntry): string[] => e.causes.map((c) => c.cause);
const detail = (e: DiReceptionEntry, cause: string, label: string): string | null | undefined =>
  e.causes.find((c) => c.cause === cause)?.details.find((d) => d.label === label)?.value;

/**
 * Through the REAL consumer: a labelled FIXTURE script served by --fixture-source's in-process
 * source into a DiOffice launched at ONE contract version, read back from its diOfficeState.
 */
async function officeReception(version: 1 | 2, answer: Raw): Promise<DiReception> {
  const script = parseFixtureScript({
    fixture: true,
    fixture_id: `reception-v${version}`,
    label: 'FIXTURE — reception desk: a pinned synthetic catalog answer, unedited',
    frames: [{ kind: 'catalog', timing: 'fresh', envelope: answer }],
  });
  const nowMs = 1_900_000_000_000;
  const source = createFixtureSource(script, () => nowMs);
  const office = new DiOffice({
    store: new AgentStateStore(),
    discoveryPath: `fixture-source:reception-v${version}`,
    fixturePreview: true,
    contractVersion: version,
    deps: { ...source.deps, nowMs: () => nowMs, log: () => {} },
  });
  await office.poll();
  const message = office.officeStateMessage();
  expect(message.fixtureMode).toBe(true);
  if (!message.reception) throw new Error('this office projects the reception desk');
  return message.reception;
}

describe('through the real DiOffice consumer (labelled FIXTURE source, one version per process)', () => {
  it('a v2 office: unsupported-at-both categories say so; the awaiting decision is present', async () => {
    const r = await officeReception(2, loadV2());
    expect(r.contractVersion).toBe(2);
    expect(category(r, 'clarification')).toMatchObject({
      outcome: 'unsupported',
      reason: 'not_in_catalog_contract:v1_and_v2',
    });
    // P2-A: delivery proposals ARE a contract v2 block now. This pinned catalog answer predates it
    // (no `delivery_presentations` key), which reads "not reported by this catalog" — unsupported,
    // never empty and never known_empty.
    expect(category(r, 'delivery_decision')).toMatchObject({
      outcome: 'unsupported',
      reason: 'catalog_does_not_report:delivery_presentations',
    });
    expect(entriesOf(r, 'authorization').map((e) => e.missionRef)).toEqual([M9]);
  });

  it('a v1 office: the v2-only categories are unsupported at contract version 1', async () => {
    const r = await officeReception(1, loadFixture());
    expect(r.contractVersion).toBe(1);
    for (const id of ['failed_verification', 'ready_for_review'] as const)
      expect(category(r, id).reason).toBe(
        'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks',
      );
    expect(category(r, 'authorization').reason).toBe(
      'contract_v1:pending_decisions_and_workflow_phase_are_contract_v2_fields',
    );
  });

  it('a v1 office given a v2 answer reads it INCOMPATIBLE: nothing readable becomes "none"', async () => {
    const r = await officeReception(1, loadV2());
    expect(category(r, 'blockers').outcome).toBe('unavailable');
    expect(category(r, 'blockers').reason).toMatch(/response_contract_unsupported/);
    expect(r.entries).toEqual([]);
  });
});

describe('the six categories, in the order item 06 names them', () => {
  it('every category is present in every desk, v1 and v2 alike', () => {
    const order = [
      'blockers',
      'clarification',
      'authorization',
      'failed_verification',
      'ready_for_review',
      'delivery_decision',
    ];
    expect(deriveReception(viewV2()).categories.map((c) => c.category)).toEqual(order);
    expect(deriveReception(viewV1()).categories.map((c) => c.category)).toEqual(order);
  });
});

describe('UNSUPPORTED is its own outcome — never empty, zero, healthy or clear', () => {
  it('clarification is unsupported at BOTH contract versions, saying so', () => {
    for (const r of [deriveReception(viewV1()), deriveReception(viewV2())]) {
      for (const id of ['clarification'] as const) {
        const c = category(r, id);
        expect(c.outcome).toBe('unsupported');
        expect(c.reason).toBe('not_in_catalog_contract:v1_and_v2');
        expect(c.statement).toMatch(/contract versions? 1 (and|or) 2/);
        expect(c.counts.knownEmpty).toBe(0);
        expect(c.counts.present).toBe(0);
        expect(entriesOf(r, id)).toEqual([]);
      }
    }
  });

  it('delivery proposals: a catalog that does not report them (v1 office, or a v2 answer without the block) is unsupported with its OWN reason', () => {
    const v1 = category(deriveReception(viewV1()), 'delivery_decision');
    expect(v1).toMatchObject({
      outcome: 'unsupported',
      reason: 'contract_v1:delivery_presentations_is_a_contract_v2_block',
    });
    expect(v1.statement).toMatch(/contract version 1/);
    const v2 = deriveReception(viewV2());
    expect(category(v2, 'delivery_decision')).toMatchObject({
      outcome: 'unsupported',
      reason: 'catalog_does_not_report:delivery_presentations',
    });
    for (const r of [deriveReception(viewV1()), v2]) {
      const c = category(r, 'delivery_decision');
      expect(c.counts.knownEmpty).toBe(0);
      expect(c.counts.present).toBe(0);
      expect(r.sourceEntries).toEqual([]);
      expect(r.sourceEntriesTotal).toBe(0);
    }
  });

  it('a v1 office: the v2-only categories are unsupported WITH the version and reason (compatibility)', () => {
    const r = deriveReception(viewV1());
    expect(r.contractVersion).toBe(1);
    for (const id of ['failed_verification', 'ready_for_review'] as const) {
      const c = category(r, id);
      expect(c.outcome).toBe('unsupported');
      expect(c.reason).toBe(
        'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks',
      );
      expect(c.statement).toMatch(/contract version 1/);
      expect(c.counts).toEqual({ present: 0, knownEmpty: 0, unsupported: 2, unavailable: 0 });
    }
    const a = category(r, 'authorization');
    expect(a).toMatchObject({
      outcome: 'unsupported',
      reason: 'contract_v1:pending_decisions_and_workflow_phase_are_contract_v2_fields',
    });
    expect(a.statement).toMatch(/contract version 1/);
  });

  it('a v1 office keeps the blocker facts v1 DOES carry: member activity and the pause flag', () => {
    const quiet = category(deriveReception(viewV1()), 'blockers');
    // core: nothing blocked, but the blocker records are a v2 block -> unsupported, never "none";
    // legacy: its roster is a projected reason -> unknown.
    expect(quiet.outcome).toBe('partial');
    expect(quiet.counts).toEqual({ present: 0, knownEmpty: 0, unsupported: 1, unavailable: 1 });
    const blocked = deriveReception(
      viewV1((raw) => {
        ((missions(raw)[0].members as Raw[])[0] as Raw).activity = 'blocked';
      }),
    );
    const e = entryFor(blocked, 'blockers', V1_CORE);
    expect(causes(e)).toEqual(['member_activity_blocked']);
    expect(e.alsoUnknown).toContain(
      'mission_core_blockers:contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks',
    );
  });
});

describe('contract v2, the EMITTED answer unedited', () => {
  const r = deriveReception(viewV2());

  it('authorization: the AWAITING_DECISION Mission is present from pending_decisions, with full source-qualified identity', () => {
    const c = category(r, 'authorization');
    expect(c.outcome).toBe('present');
    // U3: this historical emission predates the declared workflow_phase field, so the
    // workflow_store Mission's phase is UNKNOWN (not reported) — never read from lifecycle.state.
    expect(c.counts).toEqual({ present: 1, knownEmpty: 1, unsupported: 0, unavailable: 1 });
    expect(c.gaps).toContainEqual({
      outcome: 'unavailable',
      reason: 'workflow_phase:not_reported_by_this_producer',
      missions: 1,
    });
    const e = entryFor(r, 'authorization', M9);
    expect(e).toMatchObject({
      key: JSON.stringify([
        'authorization',
        'synthetic_mission_core',
        'mn-00000000000000000000000000000009',
      ]),
      sourceId: 'synthetic_mission_core',
      nativeId: 'mn-00000000000000000000000000000009',
      sourceKind: 'mission_core',
      producerDeclared: 'synthetic-mission-producer',
      lifecycleState: 'AWAITING_DECISION',
      standing: 'inactive',
      bindingState: 'not_applicable',
      bindingReason: 'mission_not_active',
      live: false,
      occurrences: 1,
      identityConflict: false,
      current: true,
    });
    expect(causes(e)).toEqual(['awaiting_decision']);
    expect(detail(e, 'awaiting_decision', 'awaiting decision')).toBe('true');
    expect(detail(e, 'awaiting_decision', 'revision')).toBe('1');
    expect(detail(e, 'awaiting_decision', 'live authorization')).toBe('false');
    expect(detail(e, 'awaiting_decision', 'next permitted step')).toBeNull();
    expect(e.routeIdentifiers).toEqual(
      expect.arrayContaining([
        { label: 'mission_ref', value: M9 },
        { label: 'source_id', value: 'synthetic_mission_core' },
        { label: 'native_id', value: 'mn-00000000000000000000000000000009' },
      ]),
    );
    expect(e.route).toMatch(/outside this office/i);
  });

  it('the workflow_store Mission is unsupported for the Mission State categories, never "none"', () => {
    for (const id of ['failed_verification', 'ready_for_review'] as const) {
      const gap = category(r, id).gaps.find((g) => g.outcome === 'unsupported');
      expect(gap?.reason).toMatch(
        /source_kind_has_no_mission_state|canonical_review_not_applicable/,
      );
    }
    expect(r.entries.some((e) => e.missionRef === WF)).toBe(false);
  });

  it('failed verification: the running Mission reports none; others cannot report — PARTIAL, never "none"', () => {
    const c = category(r, 'failed_verification');
    expect(c.outcome).toBe('partial');
    expect(c.counts).toEqual({ present: 0, knownEmpty: 1, unsupported: 2, unavailable: 0 });
    expect(entriesOf(r, 'failed_verification')).toEqual([]);
  });

  it('results ready for review: none reported by either Mission Core Mission', () => {
    const c = category(r, 'ready_for_review');
    expect(c.outcome).toBe('partial');
    expect(c.counts).toEqual({ present: 0, knownEmpty: 2, unsupported: 1, unavailable: 0 });
  });

  it('blockers: Mission Core holds on verified success are listed verbatim, information holds kept apart', () => {
    const e = entryFor(r, 'blockers', M9);
    expect(causes(e)).toEqual(['completion_holds']);
    const holds = e.causes[0].details;
    expect(holds).toEqual(
      expect.arrayContaining([
        { label: 'hold', value: 'no_canonical_closure' },
        { label: 'hold', value: 'proof_not_satisfied' },
        { label: 'information hold (not blocking, per Mission Core)', value: 'review_unavailable' },
      ]),
    );
    // M9 has no roster (binding not_applicable): its member activity is UNKNOWN, said so.
    expect(e.alsoUnknown.some((u) => u.startsWith('member_activity:'))).toBe(true);
    expect(category(r, 'blockers').counts.unavailable).toBe(1); // the workflow_store Mission
  });
});

describe('contract v2 — each cause present from its canonical field, each kept distinguishable', () => {
  it('blockers: Mission Core active blockers, a blocked member, and the DI-progression pause flag', () => {
    const r = deriveReception(
      viewV2((raw) => {
        factValue(raw, 0, 'blockers').active = [
          { blocker_id: 'bk-0001', key: 'synthetic-blocker', severity: 'HARD', opened_at: 999990 },
        ];
        factValue(raw, 0, 'blockers').hard_active = true;
        ((missions(raw)[0].members as Raw[])[1] as Raw).activity = 'blocked';
        (missions(raw)[0].native as Raw).paused = true;
      }),
    );
    const e = entryFor(r, 'blockers', M2);
    expect(causes(e)).toEqual([
      'mission_core_blockers',
      'member_activity_blocked',
      'di_progression_paused',
      'completion_holds',
    ]);
    expect(detail(e, 'mission_core_blockers', 'hard blocker active')).toBe('true');
    expect(detail(e, 'mission_core_blockers', 'active blockers')).toBe('1');
    expect(detail(e, 'mission_core_blockers', 'blocker')).toContain('bk-0001');
    expect(e.causes.find((c) => c.cause === 'member_activity_blocked')!.details[0].value).toContain(
      `${M2}#`,
    );
    expect(e.causes.find((c) => c.cause === 'di_progression_paused')!.label).toMatch(
      /DI progression paused.*not external work suspended/,
    );
  });

  it('failed verification: contradicted, a withdrawn verified success and each review failure are separate causes', () => {
    const r = deriveReception(
      viewV2((raw) => {
        completion(raw, 0).contradicted = true;
        completion(raw, 0).closure_verified = true;
        completion(raw, 0).verified_success = false;
        Object.assign(review(raw, 0), { verdict: 'REJECT', integrity: 'digest_mismatch' });
        (review(raw, 0).record as Raw).protocol_token = 'REJECT';
      }),
    );
    const e = entryFor(r, 'failed_verification', M2);
    expect(causes(e)).toEqual([
      'completion_contradicted',
      'verified_success_withdrawn',
      'review_integrity_not_corroborated',
      'review_verdict_reject',
    ]);
    expect(detail(e, 'review_integrity_not_corroborated', 'integrity')).toBe('digest_mismatch');
    expect(detail(e, 'review_verdict_reject', 'round')).toBe('1');
    expect(detail(e, 'review_verdict_reject', 'reviewer')).toBe(
      'reviewer1 (synthetic-direct-reviewer1)',
    );
  });

  it('a refused canonical review record is a failure cause with its problem verbatim', () => {
    const r = deriveReception(
      viewV2((raw) => {
        Object.assign(review(raw, 0), { state: 'refused', problem: 'review_linkage_mismatch' });
      }),
    );
    const e = entryFor(r, 'failed_verification', M2);
    expect(causes(e)).toEqual(['review_record_refused']);
    expect(detail(e, 'review_record_refused', 'problem')).toBe('review_linkage_mismatch');
  });

  it('NOT YET verified is not a failure: verified_success false without a closure is not failed verification', () => {
    const r = deriveReception(viewV2()); // M2: verified_success false, closure_verified false
    expect(entriesOf(r, 'failed_verification')).toEqual([]);
  });

  it('an unparsed review gate is NOT a rejection', () => {
    const r = deriveReception(
      viewV2((raw) => Object.assign(review(raw, 0), { gate: 'unparsed', verdict: null })),
    );
    expect(entriesOf(r, 'failed_verification')).toEqual([]);
  });

  it('results ready for review: reported complete while not verified, with the review gate facts verbatim — never "complete"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        completion(raw, 0).reported_complete = true;
      }),
    );
    const e = entryFor(r, 'ready_for_review', M2);
    expect(causes(e)).toEqual(['reported_complete_not_verified']);
    const c = e.causes[0];
    expect(c.label).toMatch(/report, not a verification/);
    expect(c.label).not.toMatch(/\b(is|are) complete\b|\bcompleted\b/i);
    expect(detail(e, 'reported_complete_not_verified', 'verified success')).toBe('false');
    expect(detail(e, 'reported_complete_not_verified', 'review gate')).toBe('parsed');
    expect(detail(e, 'reported_complete_not_verified', 'review task status')).toBe('ACTIVE');
    expect(detail(e, 'reported_complete_not_verified', 'candidate applicability')).toBe('unproven');
  });

  it('reported complete AND verified is not "ready for review"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        Object.assign(completion(raw, 0), {
          reported_complete: true,
          closure_verified: true,
          verified_success: true,
        });
      }),
    );
    expect(entriesOf(r, 'ready_for_review')).toEqual([]);
  });
});

describe('activity is not completion', () => {
  it('every member "done" changes no completion, review or verification category', () => {
    const r = deriveReception(
      viewV2((raw) => {
        for (const m of missions(raw)[0].members as Raw[]) m.activity = 'done';
      }),
    );
    expect(entriesOf(r, 'ready_for_review')).toEqual([]);
    expect(entriesOf(r, 'failed_verification')).toEqual([]);
    expect(category(r, 'ready_for_review').counts.knownEmpty).toBe(2);
    expect(causes(entryFor(r, 'blockers', M2))).toEqual(['completion_holds']);
  });
});

describe('unknown is not zero; unrecognized is unrecognized', () => {
  it('an unrecognized member activity is listed as unknown, never read as "not blocked"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        ((missions(raw)[0].members as Raw[])[0] as Raw).activity = 'sleeping';
      }),
    );
    expect(entryFor(r, 'blockers', M2).alsoUnknown).toContain(
      'member_activity:unrecognized:sleeping:1_member(s)',
    );
  });

  it('an unrecognized review verdict makes failed verification UNAVAILABLE for that Mission, not none', () => {
    const r = deriveReception(viewV2((raw) => Object.assign(review(raw, 0), { verdict: 'MAYBE' })));
    const c = category(r, 'failed_verification');
    expect(c.counts.knownEmpty).toBe(0);
    expect(c.gaps).toContainEqual({
      outcome: 'unavailable',
      reason: 'canonical_review_verdict_unrecognized:MAYBE',
      missions: 1,
    });
  });

  it('an unavailable Mission State report is unavailable with its problem, never "none"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        missions(raw)[0].mission_state = {
          state: 'unavailable',
          problem: 'mission_state_over_refresh_limit',
          detail: null,
          report: null,
          inputs: null,
        };
      }),
    );
    for (const id of ['failed_verification', 'ready_for_review', 'authorization'] as const) {
      const gap = category(r, id).gaps.find((g) => g.outcome === 'unavailable');
      expect(gap?.reason).toMatch(/mission_state_over_refresh_limit/);
    }
  });
});

describe('stale is not live; an absent source is not an empty one', () => {
  const onlyM2 = (raw: Raw) => {
    (raw.response as Raw).missions = [missions(raw)[0]];
  };

  it('readable and reporting none, from fresh healthy sources: KNOWN EMPTY', () => {
    expect(category(deriveReception(viewV2(onlyM2)), 'failed_verification').outcome).toBe(
      'known_empty',
    );
  });

  it('the same "none" from an expired answer is UNAVAILABLE, and a present entry is marked NOT current', () => {
    const stale = deriveReception(viewV2(onlyM2, NOW_V2 + 61));
    const c = category(stale, 'failed_verification');
    expect(c.outcome).toBe('unavailable');
    expect(c.reason).toBe('envelope_not_fresh:expired_locally:response_expired');
    const e = entryFor(deriveReception(viewV2(undefined, NOW_V2 + 61)), 'authorization', M9);
    expect(e.current).toBe(false);
    expect(e.causes[0].staleReasons).toContain(
      'envelope_not_fresh:expired_locally:response_expired',
    );
  });

  it('an unhealthy source is a notice, and "none" cannot be claimed while it is absent', () => {
    const r = deriveReception(
      viewV2((raw) => {
        onlyM2(raw);
        const sources = (raw.response as Raw).sources as Raw[];
        Object.assign(sources[1], { health: 'unavailable', problem: 'source_store_unreadable' });
      }),
    );
    expect(r.notices).toContainEqual(
      expect.objectContaining({
        reason: 'source_unavailable:synthetic_workflow_store:source_store_unreadable',
      }),
    );
    expect(category(r, 'failed_verification').outcome).toBe('unavailable');
  });

  it('no answer at all: every readable category is unavailable with the reason; unsupported stays unsupported', () => {
    const r = deriveReception(unavailableView('transport:backend_unreachable', NOW_V2, 2));
    for (const id of [
      'blockers',
      'authorization',
      'failed_verification',
      'ready_for_review',
    ] as const) {
      expect(category(r, id)).toMatchObject({
        outcome: 'unavailable',
        reason: 'transport:backend_unreachable',
      });
    }
    expect(category(r, 'clarification').outcome).toBe('unsupported');
    expect(r.entries).toEqual([]);
  });

  it('zero Missions from fresh, healthy sources: KNOWN EMPTY (a positive observation)', () => {
    const r = deriveReception(
      viewV2((raw) => {
        (raw.response as Raw).missions = [];
      }),
    );
    expect(category(r, 'blockers').outcome).toBe('known_empty');
    expect(category(r, 'authorization').outcome).toBe('known_empty');
  });
});

describe('identity: source-qualified, never a name; dedupe only on canonical identity', () => {
  it('two Missions with IDENTICAL objectives stay two entries', () => {
    const r = deriveReception(
      viewV2((raw) => {
        missions(raw)[0].objective = missions(raw)[1].objective;
        factValue(raw, 0, 'pending_decisions').awaiting_decision = true;
      }),
    );
    const keys = entriesOf(r, 'authorization').map((e) => e.key);
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(2);
  });

  it('the same native id in a DIFFERENT source is a different record: both appear', () => {
    const r = deriveReception(
      viewV2((raw) => {
        const other = clone(missions(raw)[1]);
        other.source_id = 'synthetic_mission_core_b';
        other.mission_ref = 'synthetic_mission_core_b:mn-00000000000000000000000000000009';
        missions(raw).push(other);
      }),
    );
    expect(entriesOf(r, 'authorization').map((e) => e.missionRef)).toEqual([
      M9,
      'synthetic_mission_core_b:mn-00000000000000000000000000000009',
    ]);
  });

  it('an exact duplicate record collapses into ONE entry that says it was reported twice', () => {
    const r = deriveReception(viewV2((raw) => missions(raw).push(clone(missions(raw)[1]))));
    const e = entryFor(r, 'authorization', M9);
    expect(e.occurrences).toBe(2);
    expect(e.identityConflict).toBe(false);
  });

  it('the same canonical identity with DIFFERENT content is never merged: both kept, flagged', () => {
    const r = deriveReception(
      viewV2((raw) => {
        const twin = clone(missions(raw)[1]);
        (((twin.mission_state as Raw).report as Raw).pending_decisions as Raw).value = {
          awaiting_decision: true,
          live_authorization: false,
          next_permitted_step: null,
          revision: 2,
        };
        missions(raw).push(twin);
      }),
    );
    const found = entriesOf(r, 'authorization').filter((e) => e.missionRef === M9);
    expect(found).toHaveLength(2);
    expect(found.every((e) => e.identityConflict)).toBe(true);
  });

  it.each([
    ['unbound', 'run_not_started'],
    ['disappeared', 'task_not_in_snapshot'],
    ['ambiguous', 'task_claimed_by_multiple_missions'],
    ['unverified', 'workspace_not_observed'],
  ])('binding %s reads as itself, with its reason — never as bound', (state, reason) => {
    const r = deriveReception(
      viewV2((raw) => {
        missions(raw)[1].binding = { state, reason, task_id: null };
      }),
    );
    const e = entryFor(r, 'authorization', M9);
    expect(e.bindingState).toBe(state);
    expect(e.bindingReason).toBe(reason);
    expect(e.live).toBe(false);
  });
});

describe('bounds, purity and the wire', () => {
  it('entries are capped at the declared bound, and the true totals are disclosed', () => {
    const r = deriveReception(
      viewV2((raw) => {
        for (let i = 0; i < 30; i++) {
          const m = clone(missions(raw)[1]);
          const nativeId = `mn-${String(100 + i).padStart(32, '0')}`;
          m.native_id = nativeId;
          m.mission_ref = `synthetic_mission_core:${nativeId}`;
          missions(raw).push(m);
        }
      }),
    );
    expect(r.entriesCap).toBe(DI_RECEPTION_MAX_ENTRIES);
    expect(r.entries).toHaveLength(DI_RECEPTION_MAX_ENTRIES);
    expect(r.entriesTotal).toBeGreaterThan(DI_RECEPTION_MAX_ENTRIES);
    expect(category(r, 'authorization').entriesTotal).toBe(31);
  });

  it('the derivation is pure: a frozen view, the same answer twice, plain data only', () => {
    const view = viewV2();
    const freeze = (v: unknown): void => {
      if (v && typeof v === 'object') {
        Object.freeze(v);
        for (const x of Object.values(v)) freeze(x);
      }
    };
    freeze(view);
    const a = deriveReception(view);
    expect(deriveReception(view)).toEqual(a);
    expect(JSON.parse(JSON.stringify(a))).toEqual(a);
  });

  it('dismissal and refresh are stated: local view only, and no new polling loop', () => {
    const r = deriveReception(viewV2());
    expect(r.dismissalStatement).toMatch(/this view only/);
    expect(r.dismissalStatement).toMatch(/resolves nothing/);
    expect(r.dismissalStatement).toMatch(/approves nothing/);
    expect(r.refreshStatement).toMatch(/office feed/);
    expect(r.minPollIntervalSeconds).toBe(viewV2().minPollIntervalSeconds);
  });

  it('the diOfficeState message carries exactly this desk', () => {
    const view = viewV2();
    expect(buildDiOfficeStateMessage(view, () => null).reception).toEqual(deriveReception(view));
  });

  it('no category, entry or route offers an approval or a control', () => {
    const r = deriveReception(viewV2());
    for (const e of r.entries) expect(e.route).toMatch(/read-only/);
    expect(JSON.stringify(r)).not.toMatch(/"(onClick|href|action|command)"/);
  });

  it('the v1 legacy Mission never reads as having no blockers', () => {
    const r = deriveReception(viewV1());
    expect(r.entries.some((e) => e.missionRef === V1_LEGACY)).toBe(false);
    expect(category(r, 'blockers').gaps).toContainEqual(
      expect.objectContaining({ outcome: 'unavailable' }),
    );
  });
});

describe('review round 1 findings (regressions)', () => {
  it('F1: an evidence_contradicted hold on an UNCLOSED, unreported Mission is its own failure cause', () => {
    // Mission Core sets `contradicted` for this hold only when verified or reported complete
    // (mission/observation.py), so `contradicted` stays false here; the hold itself is the record.
    const r = deriveReception(
      viewV2((raw) => {
        const c = completion(raw, 0);
        c.holds = ['evidence_contradicted', 'no_canonical_closure', 'proof_not_satisfied'];
        expect(c).toMatchObject({
          contradicted: false,
          closure_verified: false,
          reported_complete: false,
        });
      }),
    );
    const e = entryFor(r, 'failed_verification', M2);
    expect(causes(e)).toEqual(['evidence_contradicted_hold']);
    expect(detail(e, 'evidence_contradicted_hold', 'hold')).toBe('evidence_contradicted');
    expect(category(r, 'failed_verification').statement).toMatch(/evidence_contradicted/);
  });

  it('F1: drifted evidence holds on an unclosed Mission are a separate failure cause', () => {
    const r = deriveReception(
      viewV2((raw) => {
        completion(raw, 0).holds = [
          'baseline_drifted',
          'candidate_drifted',
          'no_canonical_closure',
        ];
      }),
    );
    const e = entryFor(r, 'failed_verification', M2);
    expect(causes(e)).toEqual(['evidence_drifted_hold']);
    expect(e.causes[0].details).toEqual(
      expect.arrayContaining([
        { label: 'hold', value: 'baseline_drifted' },
        { label: 'hold', value: 'candidate_drifted' },
      ]),
    );
  });

  it('F1: a holds list cut by the display cap cannot read as "no failure"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        completion(raw, 0).holds = Array.from(
          { length: 40 },
          (_, i) => `synthetic_hold_${String(i).padStart(2, '0')}`,
        );
      }),
    );
    expect(category(r, 'failed_verification').gaps).toContainEqual({
      outcome: 'unavailable',
      reason: 'completion_holds:truncated_by_display_cap',
      missions: 1,
    });
  });

  it('F1: the closure guard is intact — not yet verified is still not a failure', () => {
    expect(entriesOf(deriveReception(viewV2()), 'failed_verification')).toEqual([]);
  });

  const round = (n: number) => (raw: Raw) => {
    (review(raw, 0).record as Raw).round = n;
  };

  it('F2: ready for review — identical causes in review rounds 1 and 2 are two records; a round-1 hide cannot hide round 2', () => {
    const reported = (raw: Raw) => {
      completion(raw, 0).reported_complete = true;
    };
    const one = entryFor(
      deriveReception(viewV2((raw) => (reported(raw), round(1)(raw)))),
      'ready_for_review',
      M2,
    );
    const two = entryFor(
      deriveReception(viewV2((raw) => (reported(raw), round(2)(raw)))),
      'ready_for_review',
      M2,
    );
    expect(two.causes.map((c) => [c.cause, c.label])).toEqual(
      one.causes.map((c) => [c.cause, c.label]),
    );
    expect(two.key).toBe(one.key);
    expect(two.signature).not.toBe(one.signature);
  });

  it('F2: failed verification — the same contradiction across review rounds 1 and 2 is two records', () => {
    const contradicted = (raw: Raw) => {
      completion(raw, 0).contradicted = true;
    };
    const one = entryFor(
      deriveReception(viewV2((raw) => (contradicted(raw), round(1)(raw)))),
      'failed_verification',
      M2,
    );
    const two = entryFor(
      deriveReception(viewV2((raw) => (contradicted(raw), round(2)(raw)))),
      'failed_verification',
      M2,
    );
    expect(causes(two)).toEqual(causes(one));
    expect(two.signature).not.toBe(one.signature);
  });

  it('F2: authorization — a new revision awaiting decision is a new record', () => {
    const at = (revision: number) => (raw: Raw) => {
      Object.assign(factValue(raw, 1, 'pending_decisions'), { revision });
    };
    const one = entryFor(deriveReception(viewV2(at(1))), 'authorization', M9);
    const two = entryFor(deriveReception(viewV2(at(2))), 'authorization', M9);
    expect(two.signature).not.toBe(one.signature);
  });

  it('F3: reception is OPTIONAL on diOfficeState — a payload without it meets the contract, and the key is declared', async () => {
    const { Parser } = await import('@asyncapi/parser');
    const spec = fs.readFileSync(path.join(__dirname, '../../core/asyncapi.yaml'), 'utf-8');
    const { document } = await new Parser().parse(spec);
    const schema = document!.components().schemas().get('DiOfficeState')!.json() as {
      required: string[];
      properties: Record<string, unknown>;
    };
    const message = buildDiOfficeStateMessage(viewV2(), () => null) as unknown as Record<
      string,
      unknown
    >;
    // A NEW payload: every key it carries is declared.
    for (const k of Object.keys(message)) expect(Object.keys(schema.properties)).toContain(k);
    // An OLD payload (an office without the desk): it still satisfies every required key.
    const { reception: _omitted, ...old } = message;
    for (const k of schema.required) expect(Object.keys(old)).toContain(k);
  });
});

describe('U3: reauthorization from the producer-DECLARED workflow phase (never lifecycleState)', () => {
  const setLiteral = (i: number, literal: string) => (raw: Raw) => {
    (missions(raw)[i].lifecycle as Raw).state = literal;
  };
  /** As the current producer emits it: the declared field beside its record's own phase. */
  const declared = (i: number, phase: string) => (raw: Raw) => {
    setLiteral(i, phase)(raw);
    declareWorkflowPhases(raw.response as Raw);
  };

  it('v2: a workflow whose DECLARED phase is NEEDS_REAUTHORIZATION is present, model-qualified', () => {
    const r = deriveReception(viewV2(declared(2, 'NEEDS_REAUTHORIZATION')));
    const e = entryFor(r, 'authorization', WF);
    expect(causes(e)).toEqual(['workflow_needs_reauthorization']);
    expect(detail(e, 'workflow_needs_reauthorization', 'workflow phase')).toBe(
      'NEEDS_REAUTHORIZATION',
    );
    expect(detail(e, 'workflow_needs_reauthorization', 'lifecycle model')).toBe(
      'workflow_authority.workflow_phase/2',
    );
  });

  it('the display literal alone is NEVER read: NEEDS_REAUTHORIZATION in lifecycle.state with no declared field', () => {
    const r = deriveReception(viewV2(setLiteral(2, 'NEEDS_REAUTHORIZATION')));
    expect(entriesOf(r, 'authorization').some((e) => e.missionRef === WF)).toBe(false);
    expect(category(r, 'authorization').gaps).toContainEqual({
      outcome: 'unavailable',
      reason: 'workflow_phase:not_reported_by_this_producer',
      missions: 1,
    });
  });

  it('the DECLARED field wins over the literal: declared DISPATCHED beside a literal that says otherwise', () => {
    const r = deriveReception(
      viewV2((raw) => {
        declareWorkflowPhases(raw.response as Raw); // declares DISPATCHED (the emitted phase)
        setLiteral(2, 'NEEDS_REAUTHORIZATION')(raw); // the display literal only
      }),
    );
    expect(entriesOf(r, 'authorization').some((e) => e.missionRef === WF)).toBe(false);
  });

  it('v1: there is no declared phase, so the literal in a v1 answer is display only — unsupported', () => {
    const r = deriveReception(viewV1(setLiteral(1, 'NEEDS_REAUTHORIZATION')));
    expect(category(r, 'authorization')).toMatchObject({
      outcome: 'unsupported',
      reason: 'contract_v1:pending_decisions_and_workflow_phase_are_contract_v2_fields',
    });
    expect(
      r.entries.some((e) => e.missionRef === V1_LEGACY && e.category === 'authorization'),
    ).toBe(false);
  });

  it('a Mission Core record is never given a workflow phase, whatever its native literal says', () => {
    const r = deriveReception(
      viewV2((raw) => {
        setLiteral(0, 'NEEDS_REAUTHORIZATION')(raw);
        declareWorkflowPhases(raw.response as Raw);
      }),
    );
    expect(entriesOf(r, 'authorization').some((e) => e.missionRef === M2)).toBe(false);
  });

  it('a declared phase outside the vocabulary is unrecognized: unknown, never "none"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        declareWorkflowPhases(raw.response as Raw);
        (missions(raw)[2].workflow_phase as Raw).phase = 'SOMEDAY_PHASE';
      }),
    );
    expect(category(r, 'authorization').gaps).toContainEqual({
      outcome: 'unavailable',
      reason: 'workflow_phase_unrecognized:SOMEDAY_PHASE',
      missions: 1,
    });
  });

  it('a workflow whose declared phase is BLOCKED is a blocker cause of its own', () => {
    const r = deriveReception(viewV2(declared(2, 'BLOCKED')));
    const e = entryFor(r, 'blockers', WF);
    expect(causes(e)).toEqual(['workflow_phase_blocked']);
    expect(detail(e, 'workflow_phase_blocked', 'workflow phase')).toBe('BLOCKED');
  });
});

describe('an UNAVAILABLE runtime observation reads as unavailable, with its reason', () => {
  const captureUnavailable = (raw: Raw) => {
    Object.assign(raw.runtime_capture as Raw, {
      available: false,
      problem: 'capture_not_finished_within_wait',
    });
  };

  it('the desk states it as a notice, and member activity is unknown — never none or healthy', () => {
    const r = deriveReception(viewV2(captureUnavailable));
    expect(r.notices).toContainEqual(
      expect.objectContaining({
        reason: 'runtime_not_fresh:runtime_capture_unavailable:capture_not_finished_within_wait',
      }),
    );
    expect(entryFor(r, 'blockers', M2).alsoUnknown).toContain(
      'member_activity:unavailable:runtime_capture_unavailable:capture_not_finished_within_wait',
    );
  });

  it('it does not turn a category that never reads the runtime into "unknown"', () => {
    const r = deriveReception(
      viewV2((raw) => {
        captureUnavailable(raw);
        (raw.response as Raw).missions = [missions(raw)[0]];
      }),
    );
    expect(category(r, 'failed_verification').outcome).toBe('known_empty');
  });
});

describe('the packaged FIXTURE scripts (packaging/di/fixtures), through the real DiOffice', () => {
  const PACKAGED = path.join(__dirname, '../../packaging/di/fixtures');
  type Doc = {
    label: string;
    contract_version: 1 | 2;
    derived_from: { path: string; sha256: string };
  };
  const docOf = (id: string): Doc =>
    JSON.parse(fs.readFileSync(path.join(PACKAGED, `${id}.FIXTURE.json`), 'utf-8')) as Doc;
  async function serve(id: string): Promise<DiReception> {
    const doc = docOf(id);
    const script: FixtureScript = readFixtureScript(path.join(PACKAGED, `${id}.FIXTURE.json`));
    const nowMs = 1_900_000_000_000;
    const source = createFixtureSource(script, () => nowMs);
    const office = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: `fixture-source:${id}`,
      fixturePreview: true,
      contractVersion: doc.contract_version,
      deps: { ...source.deps, nowMs: () => nowMs, log: () => {} },
    });
    await office.poll();
    const message = office.officeStateMessage();
    expect(message.fixtureMode).toBe(true);
    if (!message.reception) throw new Error('this office projects the reception desk');
    return message.reception;
  }

  it('reception-v2: labelled, derived from the pinned emitted v2 answer, every implemented category present', async () => {
    const doc = docOf('reception-v2');
    expect(doc.label).toMatch(/^FIXTURE .*SYNTHETIC VISUAL TEST/);
    expect(doc.contract_version).toBe(2);
    expect(doc.derived_from.sha256).toBe(V2_SHA256);
    const r = await serve('reception-v2');
    expect(r.contractVersion).toBe(2);
    expect(causes(entryFor(r, 'authorization', M9))).toEqual(['awaiting_decision']);
    expect(causes(entryFor(r, 'authorization', WF))).toEqual(['workflow_needs_reauthorization']);
    expect(causes(entryFor(r, 'failed_verification', M2))).toEqual([
      'completion_contradicted',
      'evidence_contradicted_hold',
    ]);
    expect(causes(entryFor(r, 'ready_for_review', M2))).toEqual(['reported_complete_not_verified']);
    expect(causes(entryFor(r, 'blockers', M2))).toContain('mission_core_blockers');
    for (const id of ['clarification', 'delivery_decision'] as const)
      expect(category(r, id).outcome).toBe('unsupported');
  });

  it('reception-v2-partial (pass C, section 7): the same three Missions listed and NOTHING for wf-0006, whose need is therefore not known', async () => {
    const doc = docOf('reception-v2-partial');
    expect(doc.label).toMatch(/^FIXTURE .*SYNTHETIC VISUAL TEST/);
    expect(doc.contract_version).toBe(2);
    expect(doc.derived_from.sha256).toBe(V2_SHA256);
    const r = await serve('reception-v2-partial');
    expect(new Set(r.entries.map((e) => e.missionRef))).toEqual(new Set([M2, M9, WF]));
    expect(r.entries.some((e) => e.missionRef === 'synthetic_workflow_store:wf-0006')).toBe(false);
    // the coverage that keeps wf-0006 UNKNOWN rather than "does not need you"
    for (const id of ['clarification', 'delivery_decision'] as const)
      expect(category(r, id).outcome).toBe('unsupported');
  });
  it('reception-v2-unavailable: a FIXTURE-INDUCED unavailable runtime reads as unavailable with its reason', async () => {
    const doc = docOf('reception-v2-unavailable');
    expect(doc.label).toMatch(/^FIXTURE .*fixture-induced.*SYNTHETIC VISUAL TEST/);
    const r = await serve('reception-v2-unavailable');
    expect(r.notices.some((n) => n.reason.startsWith('runtime_not_fresh:'))).toBe(true);
    expect(category(r, 'blockers').outcome).not.toBe('known_empty');
    expect(
      entryFor(r, 'blockers', M2).alsoUnknown.some((u) =>
        u.startsWith('member_activity:unavailable:'),
      ),
    ).toBe(true);
  });

  it('reception-v1: the same desk at contract version 1 — v2-only categories unsupported, v1 facts present', async () => {
    const doc = docOf('reception-v1');
    expect(doc.label).toMatch(/^FIXTURE .*SYNTHETIC VISUAL TEST/);
    expect(doc.contract_version).toBe(1);
    const r = await serve('reception-v1');
    expect(r.contractVersion).toBe(1);
    for (const id of ['failed_verification', 'ready_for_review'] as const)
      expect(category(r, id).reason).toBe(
        'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks',
      );
    // Its workflow's literal says NEEDS_REAUTHORIZATION, but v1 declares no phase: display only.
    expect(category(r, 'authorization')).toMatchObject({
      outcome: 'unsupported',
      reason: 'contract_v1:pending_decisions_and_workflow_phase_are_contract_v2_fields',
    });
    expect(causes(entryFor(r, 'blockers', V1_CORE))).toEqual(['member_activity_blocked']);
  });
});
