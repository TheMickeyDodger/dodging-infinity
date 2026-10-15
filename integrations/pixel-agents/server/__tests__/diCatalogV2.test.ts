/**
 * contract v2 read integration, against the child's EMITTED artifact
 * (exec1-v2-contract-early-b04ff40f / catalog-answer-v2.fc409bbc.json — serializer-emitted,
 * validator-checked, SYNTHETIC ids only; copied byte-for-byte and hash-pinned here).
 * FIXTURE evidence only — never live acceptance.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { CatalogEnvelope } from '../src/di/catalogProjection.js';
import { parseEnvelope, projectResponse } from '../src/di/catalogProjection.js';
import type { MissionPresentation, OfficeView } from '../src/di/officeView.js';
import { buildOfficeView, DI_COVERAGE, DI_COVERAGE_V2 } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const V2_PATH = path.join(
  __dirname,
  'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const NOW = 1000000; // the fixture's own generated_at (valid_until 1000060)
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

function view(edit?: (raw: Raw) => void): OfficeView {
  const raw = clone(loadV2());
  edit?.(raw);
  return buildOfficeView(envelope(raw), FRESH, NOW, 2);
}

function missionsOf(v: OfficeView): MissionPresentation[] {
  if (v.missions.state !== 'observed') throw new Error(`missions are ${v.missions.state}`);
  return v.missions.value;
}

const missionRaw = (raw: Raw, i: number): Raw => ((raw.response as Raw).missions as Raw[])[i];
const M2 = 'synthetic_mission_core:mn-00000000000000000000000000000002';
const M9 = 'synthetic_mission_core:mn-00000000000000000000000000000009';

describe('ONE contract version per process (launch-time; never negotiated per request)', () => {
  it('a v2 response projects under v2; the SAME bytes under v1 are INCOMPATIBLE', () => {
    const response = loadV2().response as Raw;
    const v2 = projectResponse(response, 2);
    expect(v2.ok).toBe(true);
    if (v2.ok) expect(v2.view.contractVersion).toBe(2);
    const v1 = projectResponse(response, 1);
    expect(v1.ok).toBe(false);
    if (!v1.ok) expect(v1.verdict.problem).toBe('response_contract_unsupported');
  });

  it('a v1 response under a v2 process is INCOMPATIBLE (honest, never best-effort)', () => {
    const v1 = projectResponse(loadFixture().response as Raw, 2);
    expect(v1.ok).toBe(false);
    if (!v1.ok) {
      expect(v1.verdict.health).toBe('incompatible');
      expect(v1.verdict.problem).toBe('response_contract_unsupported');
    }
  });

  it('the v2 key set is exact: an extra response key is MALFORMED', () => {
    const response = clone(loadV2().response as Raw);
    response.surprise = 1;
    const r = projectResponse(response, 2);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.verdict.problem).toBe('response_shape_invalid');
  });
});

describe('binding reasons are scoped: chain-only reasons never widen the Mission binding', () => {
  it('a chain-only reason is recognized on a task chain, UNRECOGNIZED on missions[].binding (v1 fact)', () => {
    const raw = clone(loadV2());
    const mission = missionRaw(raw, 0);
    (mission.binding as Raw).reason = 'child_record_stale';
    ((mission.task_chains as Raw[])[0].binding as Raw).reason = 'child_record_stale';
    const r = projectResponse(raw.response as Raw, 2);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const m = r.view.missions.find((x) => x.missionRef === M2)!;
    expect(m.binding.reason).toEqual({ known: false, raw: 'child_record_stale' });
    expect(m.v2!.taskChains[0].binding.reason).toEqual({
      known: true,
      value: 'child_record_stale',
    });
  });

  it('a v1 response carrying a chain-only Mission binding reason is unrecognized (v1 stays 16 values)', () => {
    const response = clone(loadFixture().response as Raw);
    ((response.missions as Raw[])[0].binding as Raw).reason = 'chain_membership_ended';
    const r = projectResponse(response, 1);
    expect(r.ok).toBe(true);
    if (r.ok)
      expect(r.view.missions[0].binding.reason).toEqual({
        known: false,
        raw: 'chain_membership_ended',
      });
  });
});

describe('v2 office view: coverage, chains and members decided by their OWN chain', () => {
  it('coverage is stated per version; the chain observation is carried', () => {
    const v = view();
    expect(v.coverage).toEqual(DI_COVERAGE_V2);
    expect(v.coverage).not.toEqual(DI_COVERAGE);
    expect(v.contractVersion).toBe(2);
    expect(v.chainObservation?.health).toBe('fresh');
    expect(v.live).toBe(true);
  });

  it('direct AND linked-child members are live while their own chain is bound', () => {
    const m2 = missionsOf(view()).find((m) => m.missionRef === M2)!;
    expect(m2.members.state).toBe('observed');
    if (m2.members.state !== 'observed') return;
    const members = m2.members.value;
    expect(members.map((m) => `${m.logical}:${m.chain}:${m.presence.live}`)).toEqual([
      'executor1:direct:true',
      'lead1:direct:true',
      'reviewer1:direct:true',
      'executor1:child:true',
      'lead1:child:true',
    ]);
    expect(members.filter((m) => m.coverage === 'child')).toHaveLength(2);
  });

  it('FOCUSED: a direct member whose chainTaskId does not agree with the direct chain is NON-LIVE with a reason', () => {
    const m2 = missionsOf(
      view((raw) => {
        const members = missionRaw(raw, 0).members as Raw[];
        members[0].chain_task_id = 'SYNTHETIC-task-other';
      }),
    ).find((m) => m.missionRef === M2)!;
    if (m2.members.state !== 'observed') throw new Error('members not observed');
    const first = m2.members.value[0];
    expect(first.presence).toEqual({ live: false, reason: 'chain_not_listed' });
    expect(m2.members.value[1].presence.live).toBe(true); // the others still agree
  });

  it('an AMBIGUOUS association (two chains agree) is non-live, never first-wins', () => {
    const m2 = missionsOf(
      view((raw) => {
        const chains = missionRaw(raw, 0).task_chains as Raw[];
        chains.push(clone(chains[0]));
      }),
    ).find((m) => m.missionRef === M2)!;
    if (m2.members.state !== 'observed') throw new Error('members not observed');
    expect(m2.members.value[0].presence).toEqual({ live: false, reason: 'chain_ambiguous' });
  });

  it('a member of an ENDED child chain is never live; an unrecognized chain is never labelled direct', () => {
    const m2 = missionsOf(
      view((raw) => {
        const members = missionRaw(raw, 0).members as Raw[];
        const ended = clone(members[3]);
        ended.chain_task_id = 'SYNTHETIC-task-child-failed';
        ended.member_ref = `${String(ended.mission_ref)}#SYNTHETIC-task-child-failed#executor1`;
        const odd = clone(members[0]);
        odd.chain = 'sideways';
        odd.member_ref = `${String(odd.mission_ref)}#odd#executor1`;
        members.push(ended, odd);
      }),
    ).find((m) => m.missionRef === M2)!;
    if (m2.members.state !== 'observed') throw new Error('members not observed');
    const ended = m2.members.value[5];
    expect(ended.presence).toEqual({
      live: false,
      reason: 'chain_binding_not_applicable:chain_membership_ended',
    });
    const odd = m2.members.value[6];
    expect(odd.coverage).toBe('unrecognized');
    expect(odd.presence).toEqual({ live: false, reason: 'chain_unrecognized' });
  });

  it('a child chain is DEPTH-LIMITED (counted, not followed), never "no children"', () => {
    const m2 = missionsOf(view()).find((m) => m.missionRef === M2)!;
    const failed = m2.v2!.taskChains.find((c) => c.chainTaskId === 'SYNTHETIC-task-child-failed')!;
    expect(failed.descendantsNotFollowed).toBe(0);
    expect(failed.membershipEnded).toBe(true);
    expect(failed.dependencyResolution).toBe('unsatisfied');
    const missing = m2.v2!.taskChains.find(
      (c) => c.chainTaskId === 'SYNTHETIC-task-child-root-missing',
    )!;
    expect(missing.descendantsNotFollowed).toBeNull(); // not recorded — never shown as empty
    expect(missing.rootObservation).toBe('unavailable');
  });
});

describe('v2 G3 report facts: certainty carried, four states distinct, nothing dereferenced', () => {
  const facts = (m: MissionPresentation) =>
    Object.fromEntries(m.v2!.missionState.facts.map((f) => [f.name, f]));

  it('NO ACTIVATION CONTRACT: a null proof/readiness/dependencies/budget VALUE is not applicable, never empty', () => {
    const m9 = missionsOf(view()).find((m) => m.missionRef === M9)!;
    const f = facts(m9);
    for (const name of ['proof', 'readiness', 'dependencies', 'budget']) {
      expect(f[name].state).toBe('not_applicable');
      expect(f[name].reason).toBe('no_activation_contract');
      expect(f[name].standing).toBe('verified'); // the certainty travels with it
    }
  });

  it('empty, unavailable and observed stay distinct; freshness null stays null', () => {
    const m2 = missionsOf(view()).find((m) => m.missionRef === M2)!;
    const f = facts(m2);
    expect(f.evidence.state).toBe('known_empty');
    expect(f.artifacts.state).toBe('known_empty');
    expect(f.delivery.state).toBe('unavailable');
    expect(f.delivery.reason).toContain('no attested delivery receipt');
    expect(f.proof.state).toBe('observed');
    expect(f.proof.freshness).toBeNull();
    expect(f.review.entries).toEqual([{ label: 'review', value: 'APPROVE' }]);
  });

  it('a workflow_store Mission has NO Mission State: every fact is not applicable, never an empty collection', () => {
    const wf = missionsOf(view()).find((m) => m.sourceKind === 'workflow_store')!;
    for (const fact of wf.v2!.missionState.facts) expect(fact.state).toBe('not_applicable');
    expect(wf.v2!.missionState.completion.state).toBe('not_applicable');
  });

  it('completion keeps reported_* and verified_* apart; provenance is separate', () => {
    const m2 = missionsOf(view()).find((m) => m.missionRef === M2)!;
    const c = m2.v2!.missionState.completion;
    expect(c.state).toBe('observed');
    expect([c.reportedComplete, c.reportedSuccess, c.verifiedSuccess, c.closureVerified]).toEqual([
      false,
      false,
      false,
      false,
    ]);
    expect(c.holds).toEqual(['no_canonical_closure', 'proof_not_satisfied']);
    expect(c.holdsTotal).toBe(2);
    expect(m2.v2!.missionState.provenance.collectionStatus).toBe('current');
  });

  it('a capped list carries its REAL total and omitted count; a non-list is UNKNOWN, never 0', () => {
    const m2 = missionsOf(
      view((raw) => {
        const report = (missionRaw(raw, 0).mission_state as Raw).report as Raw;
        ((report.blockers as Raw).value as Raw).active = Array.from(
          { length: 40 },
          (_, i) => `b${i}`,
        );
        (report.completion as Raw).holds = 'not-a-list';
        ((report.delivery_receipts as Raw).value as Raw).recorded = 'not-a-list';
      }),
    ).find((m) => m.missionRef === M2)!;
    const f = facts(m2);
    expect(f.blockers.entries).toContainEqual({
      label: 'active blockers',
      value: '40 (showing 32; 8 omitted by the display cap)',
    });
    expect(f.delivery_receipts.entries).toContainEqual({
      label: 'recorded',
      value: 'unknown (not a list)',
    });
    expect(m2.v2!.missionState.completion.holdsTotal).toBeNull();
    expect(m2.v2!.missionState.completion.holds).toEqual([]);
  });

  it('the canonical review: four preamble fields only, gate and verdict apart', () => {
    const m2 = missionsOf(view()).find((m) => m.missionRef === M2)!;
    const r = m2.v2!.canonicalReview;
    expect(r.state).toBe('admitted');
    expect(r.gate).toBe('parsed');
    expect(r.verdict).toBe('APPROVE');
    expect(r.integrity).toBe('linkage_corroborated');
    expect(r.candidateApplicability).toBe('unproven');
    expect([r.round, r.reviewerLogical, r.reviewerAgent, r.protocolToken]).toEqual([
      1,
      'reviewer1',
      'synthetic-direct-reviewer1',
      'APPROVE',
    ]);
  });
});

/**
 * ROUND-3 (r3b) EMITTED ANSWER — exec1-export-9b3da846-r3b/fixture/catalog-answer-v2.frozen-9b3da846
 * .normalised.json, copied byte-for-byte and hash-pinned. A CANDIDATE's fixture: FIXTURE evidence
 * only, never live acceptance.
 */
const R3B_PATH = path.join(
  __dirname,
  'fixtures/di-backend/v2/catalog-answer-v2.frozen-9b3da846.93976694.FIXTURE.json',
);
const R3B_SHA256 = '939766941b888c07a49630695c3524ff6d6af496e06bf9ab2a346df7cf5d4640';

function loadR3b(): Raw {
  const bytes = fs.readFileSync(R3B_PATH);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== R3B_SHA256) throw new Error(`r3b FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

/** Every classified value the projection could NOT recognize, with its path. */
function unrecognized(v: unknown, at = '$', out: string[] = []): string[] {
  if (Array.isArray(v)) v.forEach((x, i) => unrecognized(x, `${at}[${i}]`, out));
  else if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    if (o.known === false && 'raw' in o) out.push(`${at} = ${JSON.stringify(o.raw)}`);
    for (const [k, x] of Object.entries(o)) unrecognized(x, `${at}.${k}`, out);
  }
  return out;
}

describe('r3b emitted answer: the vocabulary is complete for what the backend actually emits', () => {
  it('projects under v2 with ZERO unrecognized literals; runtime_capture.problem null stays null', () => {
    const raw = loadR3b();
    const env = envelope(raw);
    expect(env.runtimeCapture.problem).toBeNull(); // null = "no problem reported", a real answer
    const projected = projectResponse(raw.response as Raw, 2);
    expect(projected.ok).toBe(true);
    if (!projected.ok) return;
    expect(unrecognized(projected.view)).toEqual([]);
    const now = Number((raw.response as Raw).generated_at);
    const v = buildOfficeView(env, FRESH, now, 2);
    expect(v.contractVersion).toBe(2);
    expect(JSON.stringify(v)).not.toMatch(/unrecognized/);
  });

  it('parent_binding_not_confirmed is a CHAIN reason only: recognized on a chain, UNRECOGNIZED as a Mission binding', () => {
    const raw = clone(loadR3b());
    const mission = ((raw.response as Raw).missions as Raw[]).find(
      (m) => Array.isArray(m.task_chains) && (m.task_chains as Raw[]).length > 0,
    )!;
    (mission.binding as Raw).reason = 'parent_binding_not_confirmed';
    ((mission.task_chains as Raw[])[0].binding as Raw).reason = 'parent_binding_not_confirmed';
    const r = projectResponse(raw.response as Raw, 2);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const m = r.view.missions.find((x) => x.missionRef === mission.mission_ref)!;
    expect(m.binding.reason).toEqual({ known: false, raw: 'parent_binding_not_confirmed' });
    expect(m.v2!.taskChains[0].binding.reason).toEqual({
      known: true,
      value: 'parent_binding_not_confirmed',
    });
  });
});
