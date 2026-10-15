/**
 * REALISTIC-CONTENT FIXTURE for the DI office layout judgment — SYNTHETIC, never live
 * evidence. Built from the hash-pinned CONTRACT-v3 canonical fixture (the same bytes
 * server/__tests__/helpers/diFixture.ts pins), widened to eleven Missions with
 * realistic-length objectives, so the panel is judged on the case that exposed the
 * layout defect rather than on two short objectives. Every objective says FIXTURE, and
 * every source id is the canonical fixture's `synthetic-*` id.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

const CANONICAL_FIXTURE = path.join(
  __dirname,
  '../../server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json',
);
/** Same pin as server/__tests__/helpers/diFixture.ts FIXTURE_SHA256 (e2e cannot import it). */
const CANONICAL_FIXTURE_SHA256 = 'c7dcd7284ee5566f0dee9d20084074e8aa96aeb274b2bc93d159cbe7d815b823';

type Raw = Record<string, unknown>;

const SENTENCES = [
  'Migrate the ingestion service from the nightly batch to the streaming pipeline without losing late-arriving records.',
  'Keep the existing public API stable and add contract tests that pin the response shape for every documented route.',
  'Measure cold-start latency before and after the change on the reference machine and record the empirical maximum.',
  'Every new configuration value must have exactly one definition point and be covered by a focused test.',
  'Write the rollback procedure first, rehearse it once in staging, and attach the transcript to the review.',
  'Do not touch the shared credentials store; request a scoped token from the platform team instead.',
  'Report unknowns explicitly as unknown rather than inferring them from neighbouring fields.',
  'Coordinate with the reviewer before widening the scope beyond the files named in the brief.',
  'Preserve backwards compatibility for the two legacy consumers until their owners confirm the cut-over date.',
  'The acceptance gate is a visible, working build exercised by a person, not a passing type check.',
  'Document each deviation from the plan as it happens, with the exact command and its output.',
  'Prefer small reversible steps, and stop to ask when a step would be hard to undo.',
];

function objective(index: number, targetLength: number): string {
  let text = `FIXTURE objective ${index} (synthetic, realistic length; not a real Mission). `;
  let i = index;
  while (text.length < targetLength) {
    text += `${SENTENCES[i % SENTENCES.length]} `;
    i += 5;
  }
  return text.trim();
}

function hex32(seed: string): string {
  return crypto.createHash('sha256').update(seed).digest('hex').slice(0, 32);
}

interface MemberSpec {
  logical: string;
  role: string;
  classification: 'present' | 'replaced';
  /** Projected provider (FIXTURE mirrors the real direct four: claude + codex). */
  provider?: 'claude' | 'codex';
}

interface MissionSpec {
  kind: 'core' | 'legacy';
  state: string;
  standing: 'active' | 'inactive' | 'terminal';
  binding: [string, string];
  objectiveLength: number;
  members: MemberSpec[];
}

/** Eleven Missions: 7 mission_core + 4 workflow_store, objectives ~180..1400 chars. */
const MISSIONS: MissionSpec[] = [
  {
    kind: 'core',
    state: 'RUNNING',
    standing: 'active',
    binding: ['bound', 'canonical_chain_complete'],
    objectiveLength: 1100,
    members: [
      { logical: 'lead1', role: 'lead', classification: 'present' },
      { logical: 'executor1', role: 'executor', classification: 'present' },
      { logical: 'reviewer1', role: 'reviewer', classification: 'present', provider: 'codex' },
      { logical: 'supervisor', role: 'supervisor', classification: 'present', provider: 'codex' },
    ],
  },
  {
    kind: 'core',
    state: 'RUNNING',
    standing: 'active',
    binding: ['bound', 'canonical_chain_complete'],
    objectiveLength: 720,
    members: [
      { logical: 'executor2', role: 'executor', classification: 'present' },
      { logical: 'reviewer2', role: 'reviewer', classification: 'replaced' },
    ],
  },
  {
    kind: 'core',
    state: 'PAUSED',
    standing: 'inactive',
    binding: ['not_applicable', 'mission_not_active'],
    objectiveLength: 420,
    members: [],
  },
  {
    kind: 'core',
    state: 'COMPLETED',
    standing: 'terminal',
    binding: ['not_applicable', 'mission_not_active'],
    objectiveLength: 950,
    members: [],
  },
  {
    kind: 'core',
    state: 'RUNNING',
    standing: 'active',
    binding: ['unverified', 'workspace_not_observed'],
    objectiveLength: 520,
    members: [],
  },
  {
    kind: 'core',
    state: 'RUNNING',
    standing: 'active',
    binding: ['bound', 'canonical_chain_complete'],
    objectiveLength: 1400,
    members: [{ logical: 'executor3', role: 'executor', classification: 'present' }],
  },
  {
    kind: 'core',
    state: 'CANCELLED',
    standing: 'terminal',
    binding: ['not_applicable', 'mission_not_active'],
    objectiveLength: 260,
    members: [],
  },
  {
    kind: 'legacy',
    state: 'DISPATCHED',
    standing: 'active',
    binding: ['disappeared', 'task_not_in_snapshot'],
    objectiveLength: 680,
    members: [],
  },
  {
    kind: 'legacy',
    state: 'COMPLETED',
    standing: 'terminal',
    binding: ['not_applicable', 'mission_not_active'],
    objectiveLength: 880,
    members: [],
  },
  {
    kind: 'legacy',
    state: 'DISPATCHED',
    standing: 'active',
    binding: ['disappeared', 'task_not_in_snapshot'],
    objectiveLength: 300,
    members: [],
  },
  {
    kind: 'legacy',
    state: 'IN_REVIEW',
    standing: 'active',
    binding: ['unverified', 'workspace_unrecorded'],
    objectiveLength: 180,
    members: [],
  },
];

export interface RealisticFixture {
  /** Builds the envelope with every timestamp at `now` (a FIXTURE VARIATION); optionally a
   *  shorter max age, so a client-side expiry check need not wait the canonical 60 s. */
  envelopeAt(
    now: number,
    maxAgeSeconds?: number,
    roster?: 'base' | 'changed',
    capture?: 'available' | 'starved',
  ): Raw;
  /** The same answer the backend gives while throttled: no response, a reason, retry-after. */
  throttledEnvelopeAt(now: number, retryAfterSeconds: number): Raw;
  missionRefs: string[];
  /** The 'changed' roster (FINDING 2 reconnect evidence), relative to 'base'. */
  changedRoster: { removedRef: string; addedRef: string; replacedRef: string };
  /** member_ref -> spec, for the members the office should and should not draw. */
  members: Map<string, { missionRef: string } & MemberSpec>;
}

function loadCanonical(): Raw {
  const bytes = fs.readFileSync(CANONICAL_FIXTURE);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== CANONICAL_FIXTURE_SHA256) {
    throw new Error(`canonical FIXTURE hash mismatch: ${digest}`);
  }
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

export function buildRealisticFixture(): RealisticFixture {
  const canonical = loadCanonical();
  const response = canonical.response as Raw;
  const [coreTemplate, legacyTemplate] = response.missions as Raw[];
  const memberTemplate = (coreTemplate.members as Raw[])[0];
  const missionRefs: string[] = [];
  const members = new Map<string, { missionRef: string } & MemberSpec>();

  const missions = MISSIONS.map((spec, index) => {
    const template = spec.kind === 'core' ? coreTemplate : legacyTemplate;
    const mission = JSON.parse(JSON.stringify(template)) as Raw;
    const sourceId = mission.source_id as string;
    const nativeId =
      spec.kind === 'core' ? `mn-${hex32(`fixture-${index}`)}` : `wf-${hex32(`legacy-${index}`)}`;
    const missionRef = `${sourceId}:${nativeId}`;
    const taskId = `synthetic-task-${hex32(`task-${index}`).slice(0, 20)}`;
    missionRefs.push(missionRef);
    mission.native_id = nativeId;
    mission.mission_ref = missionRef;
    mission.objective = objective(index + 1, spec.objectiveLength);
    mission.lifecycle = {
      ...(mission.lifecycle as Raw),
      state: spec.state,
      standing: spec.standing,
    };
    mission.binding = { state: spec.binding[0], reason: spec.binding[1], task_id: taskId };
    mission.execution = {
      ...(mission.execution as Raw),
      task_id: taskId,
      workspace_realpath: `/synthetic/ws/${nativeId}`,
    };
    if (spec.kind === 'core') {
      mission.native = { cancel_requested: false, paused: spec.state === 'PAUSED' };
    }
    mission.members = spec.members.map((m, memberIndex) => {
      const member = JSON.parse(JSON.stringify(memberTemplate)) as Raw;
      const memberRef = `${missionRef}#${m.logical}`;
      members.set(memberRef, { missionRef, ...m });
      const recorded = `sess-${m.logical}-1`;
      member.member_ref = memberRef;
      member.mission_ref = missionRef;
      member.logical = m.logical;
      member.role = m.role;
      member.agent = `pod-${m.logical}`;
      member.provider = m.provider ?? 'claude';
      member.task_id = taskId;
      member.pane_id = `w${index + 1}:p${memberIndex + 1}`;
      member.workspace_id = `w${index + 1}`;
      member.activity = 'busy';
      member.classification = m.classification;
      member.reason = m.classification === 'present' ? 'session_confirmed' : 'session_changed';
      member.session = {
        recorded,
        live: m.classification === 'present' ? recorded : `sess-${m.logical}-2`,
      };
      return member;
    });
    return mission;
  });

  // 'changed' roster: mission 1's lead1 returns REPLACED (session_changed), mission 6's
  // executor3 LEAVES, and a new reviewer3 JOINS mission 6 — a removal, an addition and a
  // replacement for the client to reconcile after a reconnect.
  const replacedRef = `${missionRefs[0]}#lead1`;
  const removedRef = `${missionRefs[5]}#executor3`;
  const addedRef = `${missionRefs[5]}#reviewer3`;
  const changeRoster = (all: Raw[]): void => {
    const lead = (all[0].members as Raw[]).find((m) => m.member_ref === replacedRef)!;
    lead.classification = 'replaced';
    lead.reason = 'session_changed';
    lead.session = { recorded: 'sess-lead1-1', live: 'sess-lead1-2' };
    const leaving = (all[5].members as Raw[])[0];
    const joining = JSON.parse(JSON.stringify(leaving)) as Raw;
    joining.member_ref = addedRef;
    joining.logical = 'reviewer3';
    joining.role = 'reviewer';
    joining.agent = 'pod-reviewer3';
    joining.pane_id = 'w6:p2';
    joining.session = { recorded: 'sess-reviewer3-1', live: 'sess-reviewer3-1' };
    all[5].members = [joining];
  };

  const build = (
    now: number,
    maxAgeSeconds?: number,
    roster: 'base' | 'changed' = 'base',
    capture: 'available' | 'starved' = 'available',
  ): Raw => {
    const envelope = JSON.parse(JSON.stringify(canonical)) as Raw;
    const r = envelope.response as Raw;
    envelope.requested_at = now;
    r.generated_at = now;
    if (maxAgeSeconds !== undefined) r.max_age_seconds = maxAgeSeconds;
    r.valid_until = now + (r.max_age_seconds as number);
    r.missions = JSON.parse(JSON.stringify(missions)) as Raw[];
    if (roster === 'changed') changeRoster(r.missions as Raw[]);
    if (capture === 'starved') {
      // FIXTURE VARIATION: the runtime capture starves (no fresh snapshot this answer).
      const rc = envelope.runtime_capture as Raw;
      rc.available = false;
      rc.problem = 'capture_not_finished_within_wait';
      const snap = r.runtime_snapshot as Raw;
      snap.health = 'unavailable';
      snap.problem = 'capture_not_finished_within_wait';
    }
    for (const mission of r.missions as Raw[]) {
      if (typeof mission.updated_at === 'number') mission.updated_at = now - 600;
      for (const member of mission.members as Raw[]) member.observed_at = now;
    }
    (r.runtime_snapshot as Raw).observed_at = now;
    (envelope.runtime_capture as Raw).observed_at = now;
    for (const source of r.sources as Raw[]) {
      source.observed_at = now;
      source.record_count = MISSIONS.filter(
        (m) => (m.kind === 'core') === (source.kind === 'mission_core'),
      ).length;
    }
    return envelope;
  };

  return {
    envelopeAt: build,
    throttledEnvelopeAt: (now, retryAfterSeconds) => {
      const envelope = build(now);
      envelope.response = null;
      envelope.problem = 'catalog_refresh_throttled';
      envelope.detail = null;
      envelope.served = 'none';
      envelope.retry_after_seconds = retryAfterSeconds;
      envelope.runtime_capture = null;
      return envelope;
    },
    missionRefs,
    changedRoster: { removedRef, addedRef, replacedRef },
    members,
  };
}
