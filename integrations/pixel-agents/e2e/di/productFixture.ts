/**
 * Product experience — OWNED, LABELLED FIXTURE VARIANTS whose objectives look like real ones.
 * SYNTHETIC, never live evidence; served with `--fixture-preview`, so the product itself labels
 * every frame ("LIVE - FIXTURE DATA").
 *
 * The realistic fixture's objectives all open with the same generic sentence ("FIXTURE objective N
 * ..."), which cannot show whether a Mission's title carries its meaning. These variants change ONLY
 * the existing `objective` strings of the pinned fixtures (every pin is still verified at load by the
 * builders they call; no field is added, no contract is extended), using the shapes real objectives
 * take: a plain goal, a goal under an `OBJECTIVE` heading, `AUTHORIZED MISSION <uuid>:` before the
 * goal, and the boilerplate-only cases a title must NOT be made from (a bare id, `AUTHORIZED MISSION
 * <task id>`, a bare `OBJECTIVE` heading). The goals are illustrative ("the sample ..."): no real
 * project, person or repository is named.
 *
 *  - `buildProductFixture()`: the realistic fixture (contract v1, eleven Missions, teams and states
 *    unchanged) with these objectives, in order.
 *  - `buildProductCrowded()`: the pinned crowded herdr-task answer (contract v2: 64 Missions, 16
 *    runtime agents) with its two bound Missions given goals; everything else unchanged.
 */
import { buildCrowdedV2 } from './operationalVariants';
import { buildRealisticFixture } from './realisticFixture';

type Raw = Record<string, unknown>;

/** One objective per realistic-fixture Mission, in its order (eleven Missions). */
export const PRODUCT_OBJECTIVES = [
  'OBJECTIVE\nMove the sample ingestion service from nightly batches to streaming without losing late records.\n\nCONSTRAINTS\n- Keep the public API stable.\n- Record cold-start latency before and after the change.',
  'AUTHORIZED MISSION 7f3c2a90-1b4e-4c8f-9d2a-0e5b6c7d8e9f: Add contract tests that pin every documented response of the sample API.',
  'Write the rollback guide for the sample release and rehearse it once in staging.',
  "Tidy the sample project's error messages so each one says what to do next.",
  // Boilerplate only: no title can honestly be made from it (an honest placeholder instead).
  'AUTHORIZED MISSION 20000101-000000-sample0',
  'Speed up the sample test suite by running the slow integration group in parallel.',
  'Document the sample import flow, including the two legacy file formats.',
  // An id and nothing else.
  '7f3c2a90-1b4e-4c8f-9d2a-0e5b6c7d8e9f',
  'Review the sample backup schedule against the new retention rule.',
  // A bare heading: the source gave no goal at all.
  'OBJECTIVE',
  'Check the sample cache expiry rules.',
] as const;

export interface ProductFixture {
  /** Optionally a shorter max age (seconds), as realisticFixture allows: a frozen answer expires. */
  envelopeAt(now: number, maxAgeSeconds?: number): Raw;
  missionRefs: string[];
}

export function buildProductFixture(): ProductFixture {
  const base = buildRealisticFixture();
  if (base.missionRefs.length !== PRODUCT_OBJECTIVES.length)
    throw new Error(
      `product fixture expects ${PRODUCT_OBJECTIVES.length} Missions, the realistic fixture has ${base.missionRefs.length}`,
    );
  return {
    missionRefs: base.missionRefs,
    envelopeAt: (now, maxAgeSeconds) => {
      const envelope = base.envelopeAt(now, maxAgeSeconds);
      ((envelope.response as Raw).missions as Raw[]).forEach((mission, index) => {
        mission.objective = PRODUCT_OBJECTIVES[index];
      });
      return envelope;
    },
  };
}

/**
 * The product fixture with NOTHING needing attention: its only attention entry is the paused third
 * Mission's progression pause flag (Mission Core `paused`), cleared here. One existing field changed.
 */
export function buildProductZeroAttention(): ProductFixture {
  const base = buildProductFixture();
  return {
    missionRefs: base.missionRefs,
    envelopeAt: (now, maxAgeSeconds) => {
      const envelope = base.envelopeAt(now, maxAgeSeconds);
      const mission = ((envelope.response as Raw).missions as Raw[])[2];
      const native = mission.native as Raw;
      if (native.paused !== true)
        throw new Error('product zero-attention fixture: Mission 3 not paused');
      native.paused = false;
      return envelope;
    },
  };
}

/** The product fixture with NO Missions at all (an empty office), its sources saying so. */
export function buildProductEmpty(): { envelopeAt(now: number): Raw } {
  const base = buildProductFixture();
  return {
    envelopeAt: (now) => {
      const envelope = base.envelopeAt(now);
      const response = envelope.response as Raw;
      response.missions = [];
      for (const source of response.sources as Raw[]) source.record_count = 0;
      return envelope;
    },
  };
}

/** The crowded answer's two bound Missions (the ones with a team), and the goals they get. */
const CROWDED_GOALS: Record<string, string> = {
  'herd:20261017-150121-aaaaaa@5a4574665441':
    'OBJECTIVE\nAdd a health check to the sample worker and alert when it fails twice.',
  'mc:mn-1aa604085c36ca55d01b57634ee13f41':
    'AUTHORIZED MISSION 3b9e6f10-7c2d-4a8e-b5f1-2d4c6e8a0b1c: Split the sample report into smaller parts that load on demand.',
};

/**
 * The crowded variant plus a SECOND SOURCE (`herd-b`) whose runtime agents carry the SAME root ref
 * string and the same role names as the `done00` herd: real cross-source twins, which must stay two
 * herds with source-qualified rows. Only existing runtime-agent fields are copied and changed.
 */
export function buildProductCrossSource(): { envelopeAt(now: number): Raw } {
  const crowded = buildProductCrowded();
  return {
    envelopeAt: (now) => {
      const envelope = crowded.envelopeAt(now);
      const runtime = (envelope.response as Raw).runtime_agents as Raw;
      const agents = runtime.agents as Raw[];
      const twins = agents
        .filter((a) => (a.root_aliases as string[])[0] === 'done00')
        .map((a) => ({
          ...(JSON.parse(JSON.stringify(a)) as Raw),
          source_id: 'herd-b',
          agent_ref: `herd-b:${a.agent_ref as string}`,
        }));
      if (twins.length === 0) throw new Error('product cross-source fixture: no done00 herd');
      runtime.agents = [...agents, ...twins];
      return envelope;
    },
  };
}

/**
 * The crowded variant with ONE verified runtime agent (the first of the `boot00` herd) whose
 * assignment state is a value the contract does not define (`suspended`): the server projects it as
 * `unrecognized:suspended`, and the panel must say that whether it has a task is unknown — never
 * that a task is on record, nor that none is. One existing field changed; nothing added.
 */
export function buildProductAssignmentUnknown(): { envelopeAt(now: number): Raw } {
  const crowded = buildProductCrowded();
  return {
    envelopeAt: (now) => {
      const envelope = crowded.envelopeAt(now);
      const agents = ((envelope.response as Raw).runtime_agents as Raw).agents as Raw[];
      const agent = agents.find(
        (a) =>
          (a.root_aliases as string[])[0] === 'boot00' &&
          ((a.identity as Raw).state as string) === 'verified',
      );
      if (!agent) throw new Error('product assignment-unknown fixture: no verified boot00 agent');
      (agent.assignment as Raw).state = 'suspended';
      return envelope;
    },
  };
}

/**
 * CONNECTED AND VERIFIED, WITH NO BUSY EVIDENCE: the crowded variant with the bound herd Mission's
 * team reporting `idle`, the `done00` runtime herd reporting `idle` and the `boot00` herd reporting
 * no activity at all (null). Identity and presence are untouched: the panel must say "terminal not
 * busy" / "activity not reported", never "busy" — and never "idle" from an assignment record.
 */
export function buildProductNoActivity(): { envelopeAt(now: number): Raw } {
  const crowded = buildProductCrowded();
  return {
    envelopeAt: (now) => {
      const envelope = crowded.envelopeAt(now);
      const response = envelope.response as Raw;
      const team = (response.missions as Raw[]).find(
        (m) => m.mission_ref === 'herd:20261017-150121-aaaaaa@5a4574665441',
      );
      if (!team) throw new Error('product no-activity fixture: no bound herd Mission');
      for (const member of team.members as Raw[]) member.activity = 'idle';
      for (const agent of (response.runtime_agents as Raw).agents as Raw[]) {
        const herd = (agent.root_aliases as string[])[0];
        if (herd === 'done00') agent.activity = 'idle';
        if (herd === 'boot00') agent.activity = null;
      }
      return envelope;
    },
  };
}

export function buildProductCrowded(): { envelopeAt(now: number): Raw; missionCount: number } {
  const base = buildCrowdedV2();
  return {
    missionCount: base.missionCount,
    envelopeAt: (now) => {
      const envelope = base.envelopeAt(now);
      const missions = (envelope.response as Raw).missions as Raw[];
      let applied = 0;
      for (const mission of missions) {
        const goal = CROWDED_GOALS[mission.mission_ref as string];
        if (goal !== undefined) {
          mission.objective = goal;
          applied += 1;
        }
      }
      if (applied !== Object.keys(CROWDED_GOALS).length)
        throw new Error(`product crowded fixture: ${applied} of the bound Missions found`);
      return envelope;
    },
  };
}
