#!/usr/bin/env node
/**
 * Builds the six labelled DI FIXTURE scripts in packaging/di/fixtures/ for `--fixture-source`.
 *
 *   node scripts/build-di-fixtures.mjs           # (re)write the fixtures
 *   node scripts/build-di-fixtures.mjs --check   # exit 1 if any written fixture differs
 *
 * Every fixture is DERIVED from the hash-pinned canonical CONTRACT-v3 test fixture
 * (server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json) and records that hash.
 * Output is deterministic: no clock, no randomness. Each script carries `"fixture": true` and a
 * label beginning with FIXTURE; the office additionally labels every view FIXTURE / VISUAL TEST.
 * These are synthetic: they describe no real Mission, agent or store.
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CANONICAL = join(ROOT, 'server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json');
const CANONICAL_SHA256 = 'c7dcd7284ee5566f0dee9d20084074e8aa96aeb274b2bc93d159cbe7d815b823';
const OUT_DIR = join(ROOT, 'packaging/di/fixtures');

const bytes = readFileSync(CANONICAL);
const digest = createHash('sha256').update(bytes).digest('hex');
if (digest !== CANONICAL_SHA256) {
  console.error(`canonical fixture hash mismatch: ${digest} (pinned ${CANONICAL_SHA256})`);
  process.exit(1);
}
const canonical = JSON.parse(bytes.toString('utf-8'));
const clone = (v) => JSON.parse(JSON.stringify(v));
const derivedFrom = {
  path: relative(ROOT, CANONICAL),
  sha256: CANONICAL_SHA256,
};

/** The canonical envelope's first (bound, live) mission, as a template. */
const MISSION_TEMPLATE = canonical.response.missions[0];
const MEMBER_TEMPLATE = MISSION_TEMPLATE.members[0];

function member(missionRef, taskId, logical, role, n) {
  const m = clone(MEMBER_TEMPLATE);
  m.logical = logical;
  m.role = role;
  m.agent = `pod-${logical}`;
  m.member_ref = `${missionRef}#${logical}`;
  m.mission_ref = missionRef;
  m.task_id = taskId;
  m.pane_id = `w1:p${n}`;
  m.session = { live: `sess-${logical}-${n}`, recorded: `sess-${logical}-${n}` };
  return m;
}

/** A bound, consistent synthetic mission with the given members (logical, role). */
function mission(tag, members) {
  const m = clone(MISSION_TEMPLATE);
  const nativeId = `mn-fixture-${tag}`;
  const missionRef = `synthetic-core:${nativeId}`;
  const taskId = `synthetic-task-fixture-${tag}`;
  m.native_id = nativeId;
  m.mission_ref = missionRef;
  m.binding.task_id = taskId;
  m.execution.task_id = taskId;
  m.execution.workspace_realpath = `/synthetic/ws/fixture-${tag}`;
  m.objective = `SYNTHETIC FIXTURE mission (${tag}) - not a real Mission`;
  m.members = members.map(([logical, role], i) => member(missionRef, taskId, logical, role, i + 1));
  return m;
}

/** An envelope holding exactly these missions, with counts and capture kept consistent. */
function envelope(missions) {
  const e = clone(canonical);
  e.response.missions = missions;
  const agents = missions.reduce((n, m) => n + m.members.length, 0);
  e.response.runtime_snapshot.agent_count = agents;
  e.response.runtime_snapshot.task_count = missions.length;
  e.response.unmatched_runtime_agent_count = 0;
  for (const s of e.response.sources) {
    s.record_count = missions.filter((m) => m.source_id === s.source_id).length;
  }
  const roots = missions.map((m) => m.execution.workspace_realpath);
  e.runtime_capture.discovery.missions_by_root = Object.fromEntries(
    missions.map((m) => [m.execution.workspace_realpath, m.mission_ref]),
  );
  e.runtime_capture.discovery.roots = roots;
  e.runtime_capture.roots.captured = roots;
  e.runtime_capture.roots.discovered = roots;
  return e;
}

const ROLES = [
  ['lead', 'lead'],
  ['executor', 'executor'],
  ['reviewer', 'reviewer'],
];

const fixtures = [
  {
    fixture_id: 'zero-agent',
    label: 'FIXTURE — zero-agent: a fresh, healthy catalog with ZERO Missions and ZERO agents',
    description:
      'The backend answers fresh and healthy but reports no Missions, so no real agent exists. ' +
      'The world must still be visibly inhabited by Ambient residents.',
    after_last: 'hold',
    frames: [{ kind: 'catalog', timing: 'fresh', envelope: envelope([]) }],
  },
  {
    fixture_id: 'crowded',
    label: 'FIXTURE — crowded: 8 synthetic Missions, 48 synthetic live members',
    description:
      'Many live members at once: seat pressure, label pressure and render load with real ' +
      'agents and Ambient residents together. All synthetic.',
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: envelope(
          Array.from({ length: 8 }, (_, i) =>
            mission(
              `crowded-${i + 1}`,
              Array.from({ length: 6 }, (_, j) => [
                `${ROLES[j % 3][0]}${Math.floor(j / 3) + 1}`,
                ROLES[j % 3][1],
              ]),
            ),
          ),
        ),
      },
    ],
  },
  {
    fixture_id: 'stale',
    label: 'FIXTURE — stale: a catalog whose freshness lapsed 120 s ago',
    description:
      'The answer was observed but is past valid_until and its serve-time verdict is stale: ' +
      'nothing may render as live and no character may appear working.',
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'stale',
        stale_seconds: 120,
        envelope: envelope([mission('stale', [['executor1', 'executor']])]),
      },
    ],
  },
  {
    fixture_id: 'malformed',
    label: 'FIXTURE — malformed: a body that is not JSON, then JSON that is not a catalog',
    description:
      'Malformed input must be refused into an honest unavailable state, never partially shown.',
    after_last: 'hold',
    frames: [
      { kind: 'body_not_json', repeat: 2 },
      { kind: 'json_body', body: { endpoint: 'di.mission_catalog', response: 'not-an-object' } },
    ],
  },
  {
    fixture_id: 'replacement',
    label: 'FIXTURE — replacement: executor1 is replaced by a new identity after 3 polls',
    description:
      'The same Mission keeps lead1 while executor1 leaves and executor2 arrives: the old ' +
      'character must go, a new one must appear, and identities must never be reused.',
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        repeat: 3,
        envelope: envelope([
          mission('replacement', [
            ['lead1', 'lead'],
            ['executor1', 'executor'],
          ]),
        ]),
      },
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: envelope([
          mission('replacement', [
            ['lead1', 'lead'],
            ['executor2', 'executor'],
          ]),
        ]),
      },
    ],
  },
  {
    fixture_id: 'reconnect',
    label: 'FIXTURE — reconnect: live, then the backend becomes unreachable, then it returns',
    description:
      'Two fresh answers, two unreachable polls, then fresh again: characters disappear while ' +
      'unavailable and the same identities return afterwards.',
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        repeat: 2,
        envelope: envelope([
          mission('reconnect', [
            ['lead1', 'lead'],
            ['executor1', 'executor'],
          ]),
        ]),
      },
      { kind: 'transport_error', problem: 'backend_unreachable', repeat: 2 },
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: envelope([
          mission('reconnect', [
            ['lead1', 'lead'],
            ['executor1', 'executor'],
          ]),
        ]),
      },
    ],
  },
];

const check = process.argv.includes('--check');
if (!check) mkdirSync(OUT_DIR, { recursive: true });
let differ = 0;
for (const f of fixtures) {
  const doc = { fixture: true, ...f, derived_from: derivedFrom, generator: 'scripts/build-di-fixtures.mjs' };
  const text = JSON.stringify(doc, null, 2) + '\n';
  const file = join(OUT_DIR, `${f.fixture_id}.FIXTURE.json`);
  const sha = createHash('sha256').update(text).digest('hex');
  if (check) {
    const same = existsSync(file) && readFileSync(file, 'utf-8') === text;
    if (!same) differ++;
    console.log(`${same ? 'ok     ' : 'DIFFERS'} ${relative(ROOT, file)} ${sha}`);
  } else {
    writeFileSync(file, text);
    console.log(`wrote ${relative(ROOT, file)} ${sha} ${Buffer.byteLength(text)} bytes`);
  }
}
process.exit(differ ? 1 : 0);
