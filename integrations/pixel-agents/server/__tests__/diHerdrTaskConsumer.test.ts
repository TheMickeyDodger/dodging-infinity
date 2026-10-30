/**
 * item 07 consumer: the plain Herdr task records and the RUNTIME AGENTS the task-native source
 * adds, read from REAL producer emissions (tests/test_herdr_task_source.py ExportForConsumerTests:
 * the mixed 92-root registry fixture, contracts 1 and 2), hash-pinned. SYNTHETIC fixture evidence
 * only — never a live observation.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DI_RUNTIME_ZONE_REF } from '../../core/src/constants.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { DI_RUNTIME_AGENTS_MAX_UNVERIFIED } from '../src/constants.js';
import type { BackendResult } from '../src/di/backendClient.js';
import type { QueryContractVersion } from '../src/di/catalogContract.js';
import { BINDING_REASONS, LIFECYCLE_MODELS } from '../src/di/catalogContract.js';
import { parseEnvelope, projectResponse } from '../src/di/catalogProjection.js';
import { DiOffice } from '../src/di/diOffice.js';
import type { FixtureScript } from '../src/di/fixtureSource.js';
import { createFixtureSource, readFixtureScript } from '../src/di/fixtureSource.js';
import { DiIdentityMap } from '../src/di/identityMap.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import { deriveReception } from '../src/di/receptionDesk.js';
import { readServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

const FIXTURES: Record<QueryContractVersion, { file: string; sha256: string }> = {
  1: {
    file: 'herdr-task-answer-v1.FIXTURE.json',
    sha256: '608b1feb802b538712635721b7802a9b616f3486c5eb46e23515b79465e76eab',
  },
  2: {
    file: 'herdr-task-answer-v2.FIXTURE.json',
    sha256: 'e432a3a9c083815d8d6c5799e2961091b765a6a856535088099052b4eef9c841',
  },
};

function emitted(version: QueryContractVersion): Record<string, unknown> {
  const p = path.join(__dirname, 'fixtures/di-backend/herdr-task', FIXTURES[version].file);
  const bytes = fs.readFileSync(p);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURES[version].sha256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Record<string, unknown>;
}

function response(body: Record<string, unknown>): Record<string, unknown> {
  return body.response as Record<string, unknown>;
}

function runtimeAgents(body: Record<string, unknown>): Record<string, unknown> {
  return response(body).runtime_agents as Record<string, unknown>;
}

const FRESH = readServeTimeVerdict({ 'x-di-freshness-health': 'fresh' });

function view(body: Record<string, unknown>, version: QueryContractVersion, now?: number) {
  const parsed = parseEnvelope(body);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  const at = now ?? Number(response(body).generated_at);
  return buildOfficeView(parsed.envelope, FRESH, at, version);
}

describe.each([1, 2] as const)('item 07 producer→consumer compatibility (contract v%i)', (v) => {
  it('the REAL emission projects: runtime_agents strict and reported, herdr tasks typed', () => {
    const body = emitted(v);
    const projected = projectResponse(response(body), v);
    expect(projected.ok).toBe(true);
    if (!projected.ok) return;
    const r = projected.view.runtimeAgents;
    expect(r.reported).toBe(true);
    expect(r.state).toEqual({ known: true, value: 'observed' });
    expect(r.caveat).toBe('two_pass_agreement_not_atomic_aba_not_detected');
    const verified = r.agents.filter(
      (a) => a.identity.state.known && a.identity.state.value === 'verified',
    );
    expect(verified).toHaveLength(8);
    expect(r.agents).toHaveLength(16);
    const herdr = projected.view.missions.filter(
      (m) => m.lifecycle.model.known && m.lifecycle.model.value === 'herdr.task/1',
    );
    expect(herdr.length).toBeGreaterThan(50);
    for (const m of herdr) {
      expect(m.native.kind).toBe('herdr_task');
      expect(m.producerRecorded.kind).toBe('herdr_task');
      expect(m.revision).toBeNull();
      expect(m.nativeId.startsWith('mn-')).toBe(false);
    }
    // Every binding reason the producer emitted is a known one.
    for (const m of projected.view.missions) expect(m.binding.reason.known).toBe(true);
    // The locator account rides on the task-native source block.
    const source = projected.view.sources.find((s) => s.kind === 'task_native');
    expect(source?.locators?.registeredAliases).toBe(98);
    expect(source?.locators?.excluded).toHaveLength(19);
  });

  it('the vocabulary carries the herdr model and the five deferral reasons', () => {
    expect(LIFECYCLE_MODELS).toContain('herdr.task/1');
    for (const reason of [
      'covered_by_canonical_record',
      'canonical_claim_conflict',
      'canonical_source_not_fresh',
      'covered_by_parent_chain',
      'task_record_not_corroborated_by_a_live_session',
    ])
      expect(BINDING_REASONS).toContain(reason);
  });

  it('runtime_agents is STRICT when present: a shape outside the contract refuses the answer', () => {
    const cases: Array<[string, (r: Record<string, unknown>) => void]> = [
      ['extra key', (r) => (r.extra = 1)],
      ['missing count', (r) => delete (r.counts as Record<string, unknown>).verified],
      ['no caveat', (r) => (r.caveat = null)],
      [
        'agents while unavailable',
        (r) => {
          r.state = 'unavailable';
          r.problem = 'x';
          r.caveat = null;
        },
      ],
      ['undeclared state', (r) => (r.state = 'partially_observed')],
      [
        'agent key added',
        (r) => ((r.agents as Array<Record<string, unknown>>)[0].membership = 'member'),
      ],
    ];
    for (const [name, edit] of cases) {
      const body = clone(emitted(v));
      edit(runtimeAgents(body));
      const projected = projectResponse(response(body), v);
      expect({ name, ok: projected.ok }).toEqual({ name, ok: false });
    }
  });

  it('an older producer without runtime_agents: NOT REPORTED, never "none"', () => {
    const body = clone(emitted(v));
    delete response(body).runtime_agents;
    const office = view(body, v);
    expect(office.runtimeAgents.reported).toBe(false);
    expect(office.runtimeAgents.plainStatement).toContain('not reported');
    expect(office.runtimeAgents.agents).toEqual([]);
  });

  it('only a VERIFIED identity in a LIVE office is drawn; plain text separates identity from membership', () => {
    const office = view(emitted(v), v);
    expect(office.live).toBe(true);
    const drawn = office.runtimeAgents.agents.filter((a) => a.presence.live);
    expect(drawn).toHaveLength(8);
    for (const a of drawn) {
      expect(a.identityVerified).toBe(true);
      expect(a.plainText).toMatch(/^Runtime identity verified · unassigned DI runtime/);
    }
    for (const a of office.runtimeAgents.agents.filter((x) => !x.identityVerified)) {
      expect(a.presence.live).toBe(false);
      expect(a.plainText).toBe('Runtime identity not verified; not drawn');
    }
    const complete = drawn.filter((a) => a.taskStatus === 'COMPLETE');
    expect(complete).toHaveLength(4);
    for (const a of complete)
      expect(a.plainText).toBe(
        'Runtime identity verified · unassigned DI runtime (no current task; last task record COMPLETE)',
      );
    // Plain words only: no reason code (snake_case) ever reaches the plain line.
    for (const a of office.runtimeAgents.agents) expect(a.plainText).not.toMatch(/[a-z]_[a-z]/);
    expect(office.runtimeAgents.plainStatement).toBe(
      '8 runtime agent(s) with a verified identity, not team members; 8 listed without a verified identity.',
    );
  });

  it('an expired answer draws NO runtime character', () => {
    const body = emitted(v);
    const office = view(body, v, Number(response(body).valid_until) + 1);
    expect(office.live).toBe(false);
    expect(office.runtimeAgents.agents.some((a) => a.presence.live)).toBe(false);
  });

  it('a plain Herdr task carries its provenance and NO Mission Core facts', () => {
    const office = view(emitted(v), v);
    if (office.missions.state !== 'observed') throw new Error('missions not observed');
    const herdr = office.missions.value.filter((m) => m.sourceKind === 'herdr_task');
    expect(herdr.length).toBeGreaterThan(50);
    for (const m of herdr) {
      expect(m.herdrTask).not.toBeNull();
      expect(m.herdrTask?.membership).toBe('derived_from_current_task_record');
      expect(m.paused.state).toBe('not_applicable');
      expect(m.cancelRequested.state).toBe('not_applicable');
      expect(m.updatedAt.state).toBe('not_applicable');
    }
    const shared = herdr.find((m) => (m.herdrTask?.rootAliases.length ?? 0) === 3);
    expect(shared?.herdrTask?.rootAliases).toEqual(['abrt00', 'abrt00-b', 'abrt00-c']);
    const core = office.missions.value.filter((m) => m.sourceKind === 'mission_core');
    for (const m of core) expect(m.herdrTask).toBeNull();
  });

  it('the office message: runtimeAgents with characterIds for verified agents only, never animated', () => {
    const office = view(emitted(v), v);
    const ids = new DiIdentityMap();
    const message = buildDiOfficeStateMessage(
      office,
      () => null,
      (a) =>
        ids.numberFor({
          coverage: 'runtime',
          sourceId: a.sourceId,
          missionRef: a.rootRef,
          taskId: null,
          memberRef: a.agentRef,
        }),
    );
    const r = message.runtimeAgents!;
    expect(r.reported).toBe(true);
    expect(r.agentsTotal).toBe(16);
    expect(r.agentsCap).toBe(8 + DI_RUNTIME_AGENTS_MAX_UNVERIFIED);
    expect(r.agents.filter((a) => a.characterId !== null)).toHaveLength(8);
    for (const a of r.agents) {
      expect(a.characterId !== null).toBe(a.identityVerified);
      expect(a).not.toHaveProperty('animateActive');
      expect(a).not.toHaveProperty('workspaceRealpath');
    }
    expect(r.counts.find((c) => c.name === 'verified')?.value).toBe(8);
    expect(r.locators?.registeredAliases).toBe(98);
    expect(r.locators?.excludedByReason).toEqual([{ name: 'registered_root_absent', value: 19 }]);
    const herdrMissions = message.missions.missions.filter((m) => m.sourceKind === 'herdr_task');
    expect(herdrMissions.every((m) => m.herdrTask !== undefined)).toBe(true);
    expect(
      message.missions.missions
        .filter((m) => m.sourceKind !== 'herdr_task')
        .every((m) => m.herdrTask === undefined),
    ).toBe(true);
  });
});

describe('item 07 round 7: a live role a bound team does not hold', () => {
  it('reads "task membership not verified (not in its task\'s team roster)" in plain words (EDITED clone)', () => {
    const body = clone(emitted(1));
    const entry = (runtimeAgents(body).agents as Array<Record<string, unknown>>).find(
      (a) => (a.identity as Record<string, unknown>).state === 'verified',
    ) as Record<string, unknown>;
    entry.assignment = {
      state: 'unverified_reference',
      reason: 'role_not_in_the_bound_team_roster',
      task_ref: 'herd:t-1@aaaa',
      task_id: 't-1',
      task_status: 'ACTIVE',
    };
    const office = view(body, 1);
    const shown = office.runtimeAgents.agents.find((a) => a.agentRef === entry.agent_ref);
    expect(shown?.plainText).toBe(
      "Runtime identity verified; task membership not verified (not in its task's team roster)",
    );
    expect(shown?.presence.live).toBe(true);
  });
});

describe('item 07: the office draws each physical agent once', () => {
  let clockMs = 0;
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function office(body: Record<string, unknown>, version: QueryContractVersion) {
    clockMs = Number(response(body).generated_at) * 1000;
    const store = new AgentStateStore();
    const o = new DiOffice({
      store,
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: version,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async (): Promise<BackendResult> => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    return { o, store };
  }

  it.each([1, 2] as const)(
    'contract v%i: 8 team members + 8 verified runtime agents = 16 characters, runtime in its own zone',
    async (version) => {
      const { o, store } = office(emitted(version), version);
      await o.poll();
      const characters = [...o.liveCharacters().entries()];
      expect(characters).toHaveLength(16);
      const runtime = characters.filter(
        ([id]) => store.get(id)?.folderName === DI_RUNTIME_ZONE_REF,
      );
      expect(runtime).toHaveLength(8);
      const message = o.officeStateMessage();
      const drawn = message.runtimeAgents!.agents.filter((a) => a.characterId !== null);
      expect(drawn.map((a) => a.characterId).sort()).toEqual(runtime.map(([id]) => id).sort());
      o.stop();
    },
  );

  it('a runtime entry naming a LIVE member’s exact identity is never a second character', async () => {
    const body = clone(emitted(1));
    const r = runtimeAgents(body);
    const missions = response(body).missions as Array<Record<string, unknown>>;
    const member = missions
      .flatMap((m) => m.members as Array<Record<string, unknown>>)
      .find((m) => m.classification === 'present') as Record<string, unknown>;
    const entry = (r.agents as Array<Record<string, unknown>>).find(
      (a) => (a.identity as Record<string, unknown>).state === 'verified',
    ) as Record<string, unknown>;
    entry.agent = member.agent;
    entry.session = (member.session as Record<string, unknown>).recorded;
    const { o } = office(body, 1);
    await o.poll();
    expect([...o.liveCharacters().values()]).toHaveLength(15);
    o.stop();
  });
});

describe('item 07: identity map', () => {
  it('runtime is a coverage of its own: never merged with a member of the same refs', () => {
    const ids = new DiIdentityMap();
    const base = { sourceId: 's', missionRef: 'm', taskId: null, memberRef: 'r' };
    const runtime = ids.numberFor({ ...base, coverage: 'runtime' });
    const direct = ids.numberFor({ ...base, coverage: 'direct' });
    expect(runtime).not.toBe(direct);
  });
});

describe('item 07: the packaged FIXTURE scripts (built from the pinned emissions)', () => {
  const PACKAGED = path.join(__dirname, '../../packaging/di/fixtures');
  type Doc = {
    label: string;
    contract_version: 1 | 2;
    derived_from: { path: string; sha256: string };
  };
  const docOf = (id: string): Doc =>
    JSON.parse(fs.readFileSync(path.join(PACKAGED, `${id}.FIXTURE.json`), 'utf-8')) as Doc;

  async function serve(id: string) {
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
    office.stop();
    return { doc, office, message };
  }

  it.each([
    ['herdr-task-crowded-v1', 1, 16, 8],
    ['herdr-task-crowded-v2', 2, 16, 8],
    ['herdr-task-ordinary-v1', 1, 8, 0],
  ] as const)(
    '%s: labelled, derived from the pinned emission, %i characters / %i runtime',
    async (id, version, characters, runtime) => {
      const { doc, office, message } = await serve(id);
      expect(doc.label).toMatch(/^FIXTURE .*SYNTHETIC VISUAL TEST/);
      expect(doc.contract_version).toBe(version);
      expect(doc.derived_from.sha256).toBe(FIXTURES[version].sha256);
      expect(message.fixtureMode).toBe(true);
      expect(message.live).toBe(true);
      expect([...office.liveCharacters().keys()]).toHaveLength(characters);
      expect(message.runtimeAgents?.agents.filter((a) => a.characterId !== null)).toHaveLength(
        runtime,
      );
    },
  );
});

/**
 * D1 publication cleanup. Both halves are pinned: the two EXTERNAL base-identical inputs stay
 * byte-for-byte as pinned (their machine temp roots are a disclosed inherited baseline limit),
 * while every GENERATED packaged fixture and this Mission's own herdr-task answers carry no
 * machine temp root — the answers with every path-derived root key recomputed (sha256 of the
 * path, the product's root_key), the packaged outputs with provenance that keeps the exact input
 * hash and records the transformation.
 */
describe('D1: publication cleanup — inputs preserved, outputs normalized and coherent', () => {
  const ROOT = path.join(__dirname, '..', '..');
  // A REAL machine temporary root (not the record's literal `/var/folders/<a>/<b>/T/<dir>` text).
  const TEMP =
    /(?:\/private)?\/var\/folders\/[^/<"\s]+\/[^/<"\s]+\/T\/|\/private\/tmp\/|\/Users\/|\/home\/[a-z]/;
  const SYNTHETIC = '/__synthetic__/di-fixture-root';
  const sha = (file: string) =>
    crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

  it('the two external pinned inputs are preserved exactly (not normalized)', () => {
    const pins: Record<string, string> = {
      'server/__tests__/fixtures/di-backend/v3-canonical-fixture.FIXTURE.json':
        'c7dcd7284ee5566f0dee9d20084074e8aa96aeb274b2bc93d159cbe7d815b823',
      'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json':
        'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556',
    };
    // Exactly the base 84f28e2b bytes: the CONTRACT-v3 pin and the filename digest stay valid.
    for (const [file, pinned] of Object.entries(pins))
      expect(sha(path.join(ROOT, file))).toBe(pinned);
  });

  it("this Mission's herdr-task answers: no temp root, every root key derived from its own path", () => {
    for (const version of [1, 2] as const) {
      const text = fs.readFileSync(
        path.join(__dirname, 'fixtures/di-backend/herdr-task', FIXTURES[version].file),
        'utf-8',
      );
      expect(text).not.toMatch(TEMP);
      expect(text).toContain(SYNTHETIC);
      const r = (JSON.parse(text) as { response: Record<string, unknown> }).response as {
        missions: Array<{
          mission_ref: string;
          source_id: string;
          execution: { workspace_realpath: string | null };
        }>;
        runtime_agents: { agents: Array<{ agent_ref: string; workspace_realpath: string }> };
      };
      const key = (p: string) => crypto.createHash('sha256').update(p).digest('hex').slice(0, 12);
      let checked = 0;
      for (const m of r.missions) {
        const p = m.execution.workspace_realpath;
        if (m.source_id !== 'herd' || !p) continue;
        expect(m.mission_ref.endsWith(`@${key(p)}`)).toBe(true);
        checked++;
      }
      for (const a of r.runtime_agents.agents) {
        expect(a.agent_ref.split(':')[1].split('#')[0]).toBe(key(a.workspace_realpath));
        checked++;
      }
      expect(checked).toBeGreaterThan(60);
      const refs = r.missions.map((m) => m.mission_ref);
      expect(refs).toEqual([...refs].sort()); // the product's order, by the recomputed refs
      expect(new Set(refs).size).toBe(refs.length); // distinct records stay distinct
    }
  });

  it('every generated packaged fixture: no temp root; exact input hash kept; normalization recorded', () => {
    const dir = path.join(ROOT, 'packaging/di/fixtures');
    for (const name of fs.readdirSync(dir).filter((f) => f.endsWith('.FIXTURE.json'))) {
      const text = fs.readFileSync(path.join(dir, name), 'utf-8');
      expect(text, name).not.toMatch(TEMP);
      const doc = JSON.parse(text) as {
        derived_from: { path: string; sha256: string };
        normalization?: {
          occurrences: number;
          temporary_roots: number;
          synthetic_root: string;
          transformation: string;
        };
      };
      // Provenance names the input's EXACT current bytes.
      expect(sha(path.join(ROOT, doc.derived_from.path)), name).toBe(doc.derived_from.sha256);
      const { normalization, ...body } = doc;
      const synthetic = JSON.stringify(body).split(SYNTHETIC).length - 1;
      const input = fs.readFileSync(path.join(ROOT, doc.derived_from.path), 'utf-8');
      if (TEMP.test(input) && synthetic > 0) {
        // The generator normalized a temporary root its input carries: the transformation is
        // recorded and accounts for every synthetic root in the output.
        expect(normalization, name).toBeDefined();
        expect(normalization!.synthetic_root).toBe(SYNTHETIC);
        expect(normalization!.occurrences, name).toBe(synthetic);
        // Round 13: the record counts ONLY roots of the payload, and is never itself rewritten.
        const labels = new Set(
          JSON.stringify(body).match(/\/__synthetic__\/di-fixture-root(?:-\d+)?(?=[/"])/g) ?? [],
        );
        expect(normalization!.temporary_roots, name).toBe(labels.size);
        expect(normalization!.transformation, name).toContain('/var/folders/<a>/<b>/T/<dir>');
        for (const label of JSON.stringify(normalization).match(/di-fixture-root-\d+/g) ?? [])
          expect(
            labels.has(`/__synthetic__/${label}`),
            `${name}: ${label} not in the payload`,
          ).toBe(true);
      } else {
        // Nothing normalized HERE: the output carries none of its input's paths (malformed), or
        // its input was already normalized at export (this Mission's herdr-task answers).
        expect(normalization, name).toBeUndefined();
        if (synthetic > 0) expect(input, name).toContain(SYNTHETIC);
      }
    }
  });
});

describe('Herdr reception from the task record', () => {
  it('FAILED is a blocker and COMPLETE with no approving review is ready for review', () => {
    const body = clone(emitted(2));
    const missions = (body.response as { missions: Array<Record<string, unknown>> }).missions;
    const herd = missions.filter(
      (m) => (m.lifecycle as { model: string }).model === 'herdr.task/1',
    );
    expect(herd.length).toBeGreaterThan(1);
    const failed = herd[0]!;
    const ready = herd[1]!;
    (failed.lifecycle as { state: string; standing: string }).state = 'FAILED';
    (failed.lifecycle as { standing: string }).standing = 'terminal';
    const failedReview = (
      failed.admitted_artifacts as { canonical_review: Record<string, unknown> }
    ).canonical_review;
    failedReview.state = 'not_recorded';
    failedReview.problem = null;
    (ready.lifecycle as { state: string; standing: string }).state = 'COMPLETE';
    (ready.lifecycle as { standing: string }).standing = 'terminal';
    const readyReview = (ready.admitted_artifacts as { canonical_review: Record<string, unknown> })
      .canonical_review;
    readyReview.state = 'not_recorded';
    readyReview.problem = null;
    const office = view(body, 2);
    const reception = deriveReception(office);
    const causes = (missionRef: string) =>
      reception.entries
        .filter((e) => e.missionRef === missionRef)
        .flatMap((e) => e.causes.map((c) => c.cause));
    expect(causes(String(failed.mission_ref))).toContain('herdr_task_failed');
    expect(causes(String(ready.mission_ref))).toContain('herdr_task_complete_unreviewed');
    const blockers = reception.categories.find((c) => c.category === 'blockers');
    expect(blockers?.reason ?? '').not.toMatch(/source_kind_has_no_mission/);
  });
});
