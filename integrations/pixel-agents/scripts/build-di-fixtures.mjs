#!/usr/bin/env node
/**
 * Builds the labelled DI FIXTURE scripts in packaging/di/fixtures/ for `--fixture-source`.
 *
 *   node scripts/build-di-fixtures.mjs           # (re)write the fixtures
 *   node scripts/build-di-fixtures.mjs --check   # exit 1 if any written fixture differs
 *
 * Every fixture is DERIVED from a hash-pinned test fixture and records that hash: the canonical
 * CONTRACT-v3 fixture (server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json),
 * or — for the reception-desk fixtures at contract v2 — the emitted v2 answer
 * (server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json). A fixture
 * meant for a contract-v2 office declares `contract_version` (fixture-viewer passes it on).
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

/**
 * The reception-desk fixtures at contract v2 derive from the child's EMITTED v2 catalog answer
 * (serializer-emitted, validator-checked, SYNTHETIC ids), hash-pinned exactly as its test pins it.
 */
const V2_ANSWER = join(
  ROOT,
  'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_ANSWER_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const v2Bytes = readFileSync(V2_ANSWER);
const v2Digest = createHash('sha256').update(v2Bytes).digest('hex');
if (v2Digest !== V2_ANSWER_SHA256) {
  console.error(`v2 answer hash mismatch: ${v2Digest} (pinned ${V2_ANSWER_SHA256})`);
  process.exit(1);
}
const v2Answer = JSON.parse(v2Bytes.toString('utf-8'));
const derivedFromV2 = { path: relative(ROOT, V2_ANSWER), sha256: V2_ANSWER_SHA256 };

/**
 * reception-v2's edits (shared by reception-v2-partial): Mission Core 0002 carries a HARD blocker, a contradiction with an
 * evidence_contradicted hold and a COMPLETE task report; workflow wf-0005 declares NEEDS_REAUTHORIZATION.
 */
function receptionV2Edits({ m2, wf }) {
  const report = m2.mission_state.report;
  report.blockers.value = {
    active: [
      {
        blocker_id: 'bk-fixture-0001',
        key: 'synthetic-fixture-blocker',
        severity: 'HARD',
        opened_at: 999990,
      },
    ],
    hard_active: true,
  };
  Object.assign(report.completion, {
    reported_complete: true,
    contradicted: true,
    holds: [
      'evidence_contradicted',
      'hard_blocker_active',
      'no_canonical_closure',
      'proof_not_satisfied',
    ],
  });
  wf.lifecycle.state = 'NEEDS_REAUTHORIZATION';
}

/**
 * The emitted v2 answer with its three Missions (Mission Core 0002, 0009; workflow_store wf-0005),
 * edited, then given `missions[].workflow_phase` EXACTLY as the current producer declares it
 * (mission_sources/contract_v2.py `workflow_phase`): that historical emission predates the field.
 */
function v2Envelope(edit) {
  const e = clone(v2Answer);
  const [m2, m9, wf] = e.response.missions;
  edit({ e, m2, m9, wf });
  for (const m of e.response.missions) {
    m.workflow_phase =
      m.lifecycle.model === 'workflow_authority.workflow_phase/2'
        ? { state: 'observed', problem: null, phase: m.lifecycle.state }
        : {
            state: 'not_applicable',
            problem: 'lifecycle_model_has_no_workflow_phase',
            phase: null,
          };
  }
  return e;
}

/**
 * item 07: the producer emissions of the mixed 92-root registry fixture
 * (tests/test_herdr_task_source.py ExportForConsumerTests), NORMALIZED AT EXPORT for publication
 * (D1: the run's machine temp root -> /__synthetic__/di-fixture-root, every path-derived root key
 * recomputed with the product's own root_key; `normalize_export`), hash-pinned exactly as the
 * consumer test pins them. SYNTHETIC ids and paths; no real registry, Mission or agent.
 */
const HERDR = {
  1: {
    path: 'server/__tests__/fixtures/di-backend/herdr-task/herdr-task-answer-v1.FIXTURE.json',
    sha256: '608b1feb802b538712635721b7802a9b616f3486c5eb46e23515b79465e76eab',
  },
  2: {
    path: 'server/__tests__/fixtures/di-backend/herdr-task/herdr-task-answer-v2.FIXTURE.json',
    sha256: 'e432a3a9c083815d8d6c5799e2961091b765a6a856535088099052b4eef9c841',
  },
};
function herdrAnswer(version) {
  const { path, sha256 } = HERDR[version];
  const raw = readFileSync(join(ROOT, path));
  const got = createHash('sha256').update(raw).digest('hex');
  if (got !== sha256) {
    console.error(`herdr-task answer v${version} hash mismatch: ${got} (pinned ${sha256})`);
    process.exit(1);
  }
  return JSON.parse(raw.toString('utf-8'));
}
const HERDR_NOTE =
  'Its pinned input is the producer emission normalized at export for publication (temporary ' +
  'root -> /__synthetic__/di-fixture-root; path-derived root keys recomputed by the product). ' +
  'It evidences rendering of plain Herdr tasks and of runtime agents (a verified runtime ' +
  'identity that is not a team member, in its own zone) only; it is SYNTHETIC and evidences ' +
  'nothing about any live registry, team or agent.';

const RECEPTION_NOTE =
  'It evidences rendering, layout, keyboard behaviour and the contract-version difference ' +
  'only; it is SYNTHETIC and evidences nothing about live Mission data.';

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
  {
    fixture_id: 'reception-v2',
    label:
      'FIXTURE — reception-v2: synthetic attention records at contract v2 — SYNTHETIC VISUAL TEST',
    description:
      'The emitted v2 answer, edited so every implemented reception-desk category is present: ' +
      'Mission Core 0002 carries a HARD blocker, a contradiction with an evidence_contradicted ' +
      'hold and a COMPLETE task report; 0009 awaits a decision (as emitted); workflow wf-0005 ' +
      'DECLARES the phase NEEDS_REAUTHORIZATION (missions[].workflow_phase, added as the current ' +
      'producer emits it). Run it with --contract-version 2. ' +
      RECEPTION_NOTE,
    contract_version: 2,
    derived_from: derivedFromV2,
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: v2Envelope(receptionV2Edits),
      },
    ],
  },
  {
    // pass C (section 7): the same attention records PLUS one Mission the desk lists nothing for, so a Needs-you count
    // is a FLOOR ("N+") the way partial real data makes it: some Missions known to need the person, one not known.
    fixture_id: 'reception-v2-partial',
    label:
      'FIXTURE — reception-v2-partial: attention records for three Missions and none for a fourth, at contract v2 — ' +
      'SYNTHETIC VISUAL TEST',
    description:
      'reception-v2 (the same three attention records), plus a fourth Mission the reception desk lists nothing for: ' +
      'workflow wf-0006, a copy of wf-0005 whose declared phase is PREPARED (its run not started, nothing to ' +
      'reauthorize). This catalog does not report clarification or delivery decisions, so whether wf-0006 needs the ' +
      'person is NOT KNOWN: a Needs-you count over these four Missions is a floor ("3+"), never 3 and never 4. ' +
      'Run it with --contract-version 2. ' +
      RECEPTION_NOTE,
    contract_version: 2,
    derived_from: derivedFromV2,
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: v2Envelope((parts) => {
          receptionV2Edits(parts);
          const { e, wf } = parts;
          const w6 = clone(wf);
          w6.native_id = 'wf-0006';
          w6.mission_ref = 'synthetic_workflow_store:wf-0006';
          w6.objective = 'SYNTHETIC v2 fixture workflow with nothing listed for you';
          w6.lifecycle.state = 'PREPARED';
          e.response.missions.push(w6);
          const store = e.response.sources.find((src) => src.source_id === wf.source_id);
          store.record_count += 1;
        }),
      },
    ],
  },
  {
    fixture_id: 'reception-v2-unavailable',
    label:
      'FIXTURE — reception-v2-unavailable: a fixture-induced unavailable runtime observation — ' +
      'SYNTHETIC VISUAL TEST',
    description:
      'The emitted v2 answer with its runtime capture reported unavailable ' +
      '(capture_not_finished_within_wait). This condition is FIXTURE-INDUCED, not an observed ' +
      'live condition: member activity must read as unavailable with its reason, never as ' +
      '"none" or healthy. It carries missions[].workflow_phase as the current producer emits it. ' +
      'Run it with --contract-version 2. ' +
      RECEPTION_NOTE,
    contract_version: 2,
    derived_from: derivedFromV2,
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: v2Envelope(({ e }) => {
          Object.assign(e.runtime_capture, {
            available: false,
            problem: 'capture_not_finished_within_wait',
          });
        }),
      },
    ],
  },
  {
    fixture_id: 'reception-v1',
    label: 'FIXTURE — reception-v1: the attention desk at contract v1 — SYNTHETIC VISUAL TEST',
    description:
      'The canonical v1 answer with its Mission Core member reporting blocked activity, and its ' +
      "workflow_store workflow's DISPLAY literal set to NEEDS_REAUTHORIZATION: contract v1 " +
      'declares no workflow phase, so a v1 office must report authorization unsupported rather ' +
      'than read that literal. Run it with --contract-version 1. ' +
      RECEPTION_NOTE,
    contract_version: 1,
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: (() => {
          const e = clone(canonical);
          e.response.missions[0].members[0].activity = 'blocked';
          e.response.missions[1].lifecycle.state = 'NEEDS_REAUTHORIZATION';
          return e;
        })(),
      },
    ],
  },
  ...[1, 2].map((version) => ({
    fixture_id: `herdr-task-crowded-v${version}`,
    label: `FIXTURE — herdr-task-crowded-v${version}: a realistic crowded DI registry at contract v${version} — SYNTHETIC VISUAL TEST`,
    description:
      'The producer emission of the mixed registry fixture: 98 aliases over 92 paths ' +
      '(4 shared-path groups, 19 missing), 15 ACTIVE / 29 COMPLETE / 9 ABORTED / 39 no-task ' +
      'records, 4 live workspaces: two bound teams (8 members) and 8 verified runtime agents ' +
      '(a bootstrap-only team and a team whose task is COMPLETE), plus 8 unverified runtime ' +
      `entries (dead sessions on reused names). Not edited here. Run it with --contract-version ${version}. ` +
      HERDR_NOTE,
    contract_version: version,
    derived_from: { path: HERDR[version].path, sha256: HERDR[version].sha256 },
    after_last: 'hold',
    frames: [{ kind: 'catalog', timing: 'fresh', envelope: herdrAnswer(version) }],
  })),
  {
    // C1 (items 08/09): the unverified listing and navigation against the live mismatch shape.
    fixture_id: 'herdr-task-session-mismatch-v2',
    label:
      'FIXTURE — herdr-task-session-mismatch-v2: four foreign-role session mismatches (unverified, not drawn) at contract v2 — SYNTHETIC VISUAL TEST',
    description:
      'The v2 producer emission of the mixed registry, EDITED: the four runtime entries of root ' +
      'act01 keep their names and recorded sessions, but their identity reason is set to ' +
      "'session_replaced' (a live native agent carries the role's name while the recorded role " +
      'binding names a different session: the shape of a foreign team whose sessions were ' +
      "replaced). The four entries of root act00 keep 'native_identity_degraded'. Nothing else " +
      'is changed. Neither a name, a pane, a workspace nor a timestamp makes these verified. Run ' +
      'it with --contract-version 2. ' +
      HERDR_NOTE,
    contract_version: 2,
    derived_from: { path: HERDR[2].path, sha256: HERDR[2].sha256 },
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: (() => {
          const e = herdrAnswer(2);
          for (const a of e.response.runtime_agents.agents) {
            if (a.identity.state === 'unverified' && a.root_aliases.includes('act01'))
              a.identity.reason = 'session_replaced';
          }
          return e;
        })(),
      },
    ],
  },
  {
    fixture_id: 'herdr-task-ordinary-v1',
    label:
      'FIXTURE — herdr-task-ordinary-v1: two bound teams only (8 members) at contract v1 — SYNTHETIC VISUAL TEST',
    description:
      'The v1 producer emission of the mixed registry, EDITED: every mission except the two ' +
      "bound teams (the Mission Core record and the plain Herdr task) and the Mission Core root's " +
      'deferring alias is removed, and runtime_agents lists no agent (its counts are left as ' +
      'emitted). Run it with --contract-version 1. ' +
      HERDR_NOTE,
    contract_version: 1,
    derived_from: { path: HERDR[1].path, sha256: HERDR[1].sha256 },
    after_last: 'hold',
    frames: [
      {
        kind: 'catalog',
        timing: 'fresh',
        envelope: (() => {
          const e = herdrAnswer(1);
          e.response.missions = e.response.missions.filter(
            (m) =>
              m.binding.state === 'bound' || m.binding.reason === 'covered_by_canonical_record',
          );
          e.response.runtime_agents.agents = [];
          return e;
        })(),
      },
    ],
  },
];

/**
 * D1 publication cleanup: a machine-specific temporary root (a macOS TMPDIR directory,
 * `/var/folders/<a>/<b>/T/<dir>`, or its realpath under `/private`) carried by a pinned input is
 * replaced in the GENERATED output by a synthetic, portable root — one per distinct temporary
 * root, numbered in order of first appearance, so distinct roots stay distinct — and nothing else
 * changes. The pinned inputs are never edited (two are external, base-identical pins whose paths
 * are an inherited baseline limit); `derived_from` keeps each input's EXACT sha256 and
 * `normalization` records the transformation. Inputs here carry no identity derived from these
 * paths (the herdr-task answers were normalized at export, with their root keys recomputed).
 */
const TEMP_ROOT = /(?:\/private)?\/var\/folders\/[^/"\\]+\/[^/"\\]+\/T\/[^/"\\]+/g;
const SYNTHETIC_ROOT = '/__synthetic__/di-fixture-root';

function normalizeTempRoots(text) {
  const roots = new Map();
  let occurrences = 0;
  const out = text.replace(TEMP_ROOT, (match) => {
    // The realpath and the plain form of ONE directory are one root.
    const key = match.startsWith('/private/') ? match.slice('/private'.length) : match;
    if (!roots.has(key))
      roots.set(key, roots.size === 0 ? SYNTHETIC_ROOT : `${SYNTHETIC_ROOT}-${roots.size + 1}`);
    occurrences++;
    return roots.get(key);
  });
  return { text: out, occurrences, roots: roots.size };
}

const check = process.argv.includes('--check');
if (!check) mkdirSync(OUT_DIR, { recursive: true });
let differ = 0;
for (const f of fixtures) {
  let doc = {
    fixture: true,
    ...f,
    derived_from: f.derived_from ?? derivedFrom,
    generator: 'scripts/build-di-fixtures.mjs',
  };
  // The PAYLOAD is normalized once; the normalization record is attached AFTERWARDS, so no pass
  // ever rewrites it (round 13: a second pass over the record mangled its own pattern text and
  // invented a second root). The record counts only roots found in the payload.
  const found = normalizeTempRoots(JSON.stringify(doc, null, 2));
  if (found.occurrences > 0)
    doc = {
      ...JSON.parse(found.text),
      normalization: {
        transformation:
          'machine-specific temporary roots (/var/folders/<a>/<b>/T/<dir>, also as its /private ' +
          `realpath) replaced by ${SYNTHETIC_ROOT} (one per distinct root, numbered in order of ` +
          'first appearance); no other byte of the derived emission changed; derived_from keeps ' +
          "the input's exact sha256",
        synthetic_root: SYNTHETIC_ROOT,
        temporary_roots: found.roots,
        occurrences: found.occurrences,
      },
    };
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
