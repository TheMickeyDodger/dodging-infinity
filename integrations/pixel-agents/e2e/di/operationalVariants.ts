/**
 * feature13 — OWNED, LABELLED FIXTURE VARIANTS for the built-app regressions the realistic fixture
 * does not hold. SYNTHETIC, never live evidence. Every variant is derived from a HASH-PINNED fixture
 * (the pin is verified at load, as realisticFixture.ts does) and changes ONLY fields the existing
 * contract already has — no field is added, no contract is extended:
 *
 *  - `buildCasesV1()`: contract v1, from the realistic fixture. A live, confirmed member reporting
 *    `blocked` (the Mission reads Blocked); a `replaced` member reporting `blocked` (it must NOT);
 *    DUPLICATE member names across three Missions and two Missions with an IDENTICAL objective (lookalike
 *    records that must stay distinct and source-qualified); and an objective exported as the bare
 *    section header `OBJECTIVE`, on a Mission whose team and status still render.
 *  - `buildCasesV2()`: contract v2, from the pinned v2 answer. "Failed" is only representable here: a
 *    Mission State whose completion REPORTS complete without success (`mission_state.report.completion`),
 *    which contract v1 cannot carry. This answer also holds duplicate names INSIDE one Mission (a direct
 *    and a child `executor1`, `lead1`).
 *  - `buildCrowdedV2()`: the pinned herdr-task v2 answer unchanged but for its timestamps — 64 Missions,
 *    16 runtime agents (8 not verified): the largest load server/__tests__/diHerdrTaskConsumer.test.ts
 *    exercises ('herdr-task-crowded-v2'), named as the CROWDED load for local measurements.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

import { buildRealisticFixture } from './realisticFixture';

type Raw = Record<string, unknown>;

const FIXTURES_DIR = path.join(__dirname, '../../server/__tests__/fixtures/di-backend');
/** Same pins as server/__tests__/diCatalogV2.test.ts V2_SHA256 and diHerdrTaskConsumer.test.ts FIXTURES. */
const V2_SMALL = {
  file: 'v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
  sha256: 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556',
};
const HERDR_V2 = {
  file: 'herdr-task/herdr-task-answer-v2.FIXTURE.json',
  sha256: 'e432a3a9c083815d8d6c5799e2961091b765a6a856535088099052b4eef9c841',
};

function loadPinned(fixture: { file: string; sha256: string }): Raw {
  const bytes = fs.readFileSync(path.join(FIXTURES_DIR, fixture.file));
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== fixture.sha256)
    throw new Error(`FIXTURE hash mismatch: ${fixture.file}: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

/** Shift every integer in [lo, hi] by `delta` (a deep copy): the fixture's epochs move to "now". */
function shiftEpochs<T>(value: T, lo: number, hi: number, delta: number): T {
  if (typeof value === 'number') {
    return (Number.isInteger(value) && value >= lo && value <= hi ? value + delta : value) as T;
  }
  if (Array.isArray(value)) return value.map((v) => shiftEpochs(v, lo, hi, delta)) as T;
  if (value !== null && typeof value === 'object') {
    const out: Raw = {};
    for (const [k, v] of Object.entries(value as Raw)) out[k] = shiftEpochs(v, lo, hi, delta);
    return out as T;
  }
  return value;
}

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export interface CasesV1 {
  envelopeAt(now: number): Raw;
  refs: {
    blocked: string;
    replacedBlocked: string;
    header: string;
    dupA: string;
    dupB: string;
    dupC: string;
  };
  /** The three Missions whose first member is named executor1, in list order. */
  executor1Refs: string[];
  liveMemberCount: number;
}

/** Rename a Mission's member in place, keeping every identity field consistent and unique. */
function renameMember(member: Raw, missionRef: string, logical: string, sessionTag: string): void {
  const recorded = `sess-${logical}-${sessionTag}-1`;
  member.member_ref = `${missionRef}#${logical}`;
  member.logical = logical;
  member.agent = `pod-${logical}`;
  member.session = {
    recorded,
    live: member.classification === 'present' ? recorded : `sess-${logical}-${sessionTag}-2`,
  };
}

export function buildCasesV1(): CasesV1 {
  const base = buildRealisticFixture();
  const refs = base.missionRefs;
  const build = (now: number): Raw => {
    const envelope = base.envelopeAt(now);
    const missions = (envelope.response as Raw).missions as Raw[];
    const members = (i: number) => missions[i].members as Raw[];
    // BLOCKED, promoting: a live, confirmed member (Mission 0's lead1) reports blocked.
    members(0).find((m) => m.logical === 'lead1')!.activity = 'blocked';
    // DUPLICATE names + an IDENTICAL objective: Mission 1's team is renamed to share Mission 0's
    // executor1, and its objective is Mission 0's, word for word.
    missions[1].objective = missions[0].objective;
    const dupB = members(1).find((m) => m.logical === 'executor2')!;
    renameMember(dupB, refs[1], 'executor1', 'm2');
    // BLOCKED, NOT promoting: the same Mission's replaced reviewer2 reports blocked (not this member).
    members(1).find((m) => m.logical === 'reviewer2')!.activity = 'blocked';
    // A third executor1, and the HEADER-ONLY objective: Mission 5 exports only "OBJECTIVE".
    renameMember(members(5)[0], refs[5], 'executor1', 'm6');
    missions[5].objective = 'OBJECTIVE';
    return envelope;
  };
  return {
    envelopeAt: build,
    refs: {
      blocked: refs[0],
      replacedBlocked: refs[1],
      header: refs[5],
      dupA: refs[0],
      dupB: refs[1],
      dupC: refs[5],
    },
    executor1Refs: [refs[0], refs[1], refs[5]],
    // present members: Mission 0's four, Mission 1's executor1, Mission 5's executor1.
    liveMemberCount: 6,
  };
}

export interface CasesV2 {
  envelopeAt(now: number): Raw;
  refs: { duplicates: string; failed: string };
}

export function buildCasesV2(): CasesV2 {
  const base = loadPinned(V2_SMALL);
  const baseTime = (base.response as Raw).generated_at as number;
  const missionsOf = (envelope: Raw) => (envelope.response as Raw).missions as Raw[];
  const refs = {
    duplicates: missionsOf(base)[0].mission_ref as string,
    failed: missionsOf(base)[1].mission_ref as string,
  };
  return {
    envelopeAt: (now) => {
      // The fixture's synthetic epoch (1000000 + small offsets) moves to "now"; the 60 s validity moves with it.
      const envelope = shiftEpochs(base, baseTime, baseTime + 999, now - baseTime);
      // FAILED, as REPORTED by the source (not verified): completion reported without success.
      const completion = ((missionsOf(envelope)[1].mission_state as Raw).report as Raw)
        .completion as Raw;
      completion.reported_complete = true;
      completion.reported_success = false;
      return envelope;
    },
    refs,
  };
}

export interface CrowdedV2 {
  envelopeAt(now: number): Raw;
  missionCount: number;
}

export function buildCrowdedV2(): CrowdedV2 {
  const base = loadPinned(HERDR_V2);
  const baseTime = (base.response as Raw).generated_at as number;
  return {
    // The recorded epochs sit at 1.8e9 (+ a minute for valid_until); only they move.
    envelopeAt: (now) => shiftEpochs(clone(base), 1_700_000_000, 1_900_000_000, now - baseTime),
    missionCount: ((base.response as Raw).missions as Raw[]).length,
  };
}
