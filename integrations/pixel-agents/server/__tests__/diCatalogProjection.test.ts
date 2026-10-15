/**
 * FIXTURE-BASED TESTS — the reference artifact is the backend's serializer-generated
 * canonical fixture (CONTRACT-v3, sha256 pinned below). It is SYNTHETIC fixture
 * evidence only: never live acceptance, never a substitute for the live adapter tests.
 * A swapped or edited fixture fails the hash pin loudly.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import { describe, expect, it } from 'vitest';

import {
  ACTIVITIES,
  BINDING_REASONS,
  BINDING_STATES,
  ENVELOPE_KEYS,
  ENVELOPE_PROBLEMS,
  HEALTH_STATES,
  MEMBER_CLASSIFICATIONS,
  MEMBER_REASONS,
  RESPONSE_KEYS,
  STANDINGS,
} from '../src/di/catalogContract.js';
import { parseEnvelope, projectResponse, readRuntimeCapture } from '../src/di/catalogProjection.js';
import {
  clone,
  FIXTURE_BYTES,
  FIXTURE_PATH,
  FIXTURE_SHA256,
  loadFixture,
} from './helpers/diFixture.js';

describe('FIXTURE: CONTRACT-v3 canonical fixture is the pinned bytes', () => {
  it('matches the sha256 named by CONTRACT-v3 (6861 bytes)', () => {
    const bytes = fs.readFileSync(FIXTURE_PATH);
    expect(bytes.length).toBe(FIXTURE_BYTES);
    expect(crypto.createHash('sha256').update(bytes).digest('hex')).toBe(FIXTURE_SHA256);
  });
});

describe('closed enumerations bound verbatim from CONTRACT-v3', () => {
  it('carries every value v3 counts, and nothing else', () => {
    expect(BINDING_REASONS).toHaveLength(16);
    expect(MEMBER_REASONS).toHaveLength(12);
    expect([...BINDING_STATES]).toEqual([
      'bound',
      'unbound',
      'disappeared',
      'ambiguous',
      'unverified',
      'not_applicable',
    ]);
    expect([...MEMBER_CLASSIFICATIONS]).toEqual([
      'present',
      'replaced',
      'disappeared',
      'ambiguous',
    ]);
    expect([...ACTIVITIES]).toEqual([
      'busy',
      'idle',
      'blocked',
      'done',
      'unknown',
      'unknown_future',
    ]);
    expect([...STANDINGS]).toEqual(['active', 'inactive', 'terminal', 'unknown_future']);
    expect([...HEALTH_STATES]).toEqual([
      'fresh',
      'stale',
      'unavailable',
      'malformed',
      'incompatible',
    ]);
  });

  it('does NOT treat catalog_no_runtime_root as an envelope problem', () => {
    expect(ENVELOPE_PROBLEMS as readonly string[]).not.toContain('catalog_no_runtime_root');
  });
});

describe('FIXTURE: envelope', () => {
  it('parses the exact nine-key envelope without restamping anything', () => {
    const fixture = loadFixture();
    expect(Object.keys(fixture).sort()).toEqual([...ENVELOPE_KEYS].sort());
    const parsed = parseEnvelope(fixture);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.envelope.requestedAt).toBe(1800000000);
    expect(parsed.envelope.served).toEqual({ known: true, value: 'refreshed' });
    expect(parsed.envelope.problem).toBeNull();
    expect(parsed.envelope.detail).toBeNull();
    expect(parsed.envelope.retryAfterSeconds).toBeNull();
  });

  it('body null convention: keys are present with JSON null — a missing key is malformed, never "null"', () => {
    const missing = clone(loadFixture());
    delete missing.detail;
    expect(parseEnvelope(missing)).toMatchObject({
      ok: false,
      verdict: { health: 'malformed', problem: 'response_shape_invalid' },
    });
    const extra = { ...clone(loadFixture()), surprise: 1 };
    expect(parseEnvelope(extra)).toMatchObject({ ok: false, verdict: { health: 'malformed' } });
  });

  it('an unsupported endpoint_version is incompatible, not best-effort parsed', () => {
    const bumped = { ...clone(loadFixture()), endpoint_version: 2 };
    expect(parseEnvelope(bumped)).toMatchObject({
      ok: false,
      verdict: { health: 'incompatible', problem: 'response_contract_unsupported' },
    });
  });

  it('runtime_capture is diagnostics: parsed permissively, never required, extras tolerated', () => {
    expect(readRuntimeCapture({ configured: false })).toEqual({
      configured: false,
      available: null,
      problem: null,
      detail: null,
      observedAt: null,
    });
    expect(readRuntimeCapture(null).configured).toBeNull();
    expect(
      readRuntimeCapture({ configured: true, available: false, problem: 'x', extra: [1] }),
    ).toMatchObject({
      configured: true,
      available: false,
      problem: 'x',
    });
    const withOddCapture = { ...clone(loadFixture()), runtime_capture: { roots: 'not an object' } };
    expect(parseEnvelope(withOddCapture).ok).toBe(true);
  });
});

describe('FIXTURE: response projection (CONTRACT-v3 key sets verbatim)', () => {
  function response(): Record<string, unknown> {
    return clone(loadFixture().response as Record<string, unknown>);
  }

  it('projects the eleven-key response; max_age_seconds is a DURATION', () => {
    const raw = response();
    expect(Object.keys(raw).sort()).toEqual([...RESPONSE_KEYS].sort());
    const result = projectResponse(raw);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const { view } = result;
    expect(view.generatedAt).toBe(1800000000);
    expect(view.validUntil).toBe(1800000060);
    expect(view.maxAgeSeconds).toBe(60);
    expect(view.validUntil - view.generatedAt).toBe(view.maxAgeSeconds);
    expect(view.minPollIntervalSeconds).toBe(5); // read from limits, not hardcoded
  });

  it('keeps registry health, snapshot health and source health as separate values', () => {
    const result = projectResponse(response());
    if (!result.ok) throw new Error('fixture failed to project');
    expect(result.view.registry.health).toEqual({ known: true, value: 'fresh' });
    expect(result.view.runtimeSnapshot.health).toEqual({ known: true, value: 'fresh' });
    expect(result.view.runtimeSnapshot.observedAt).toBe(1800000000);
    expect(result.view.sources.map((s) => [s.sourceId, s.kind])).toEqual([
      ['synthetic-core', 'mission_core'],
      ['synthetic-legacy', 'workflow_store'],
    ]);
  });

  it('keeps the two lifecycle models distinct and case-preserving (no normalization)', () => {
    const result = projectResponse(response());
    if (!result.ok) throw new Error('fixture failed to project');
    const [core, legacy] = result.view.missions;
    expect(core.lifecycle.model).toEqual({ known: true, value: 'mission_core.mission_state/1' });
    expect(core.lifecycle.state).toBe('RUNNING');
    expect(legacy.lifecycle.model).toEqual({
      known: true,
      value: 'workflow_authority.workflow_phase/2',
    });
    expect(legacy.lifecycle.state).toBe('DISPATCHED');
    expect(core.lifecycle.standing).toEqual({ known: true, value: 'active' });
    expect(legacy.binding.state).toEqual({ known: true, value: 'disappeared' });
    expect(legacy.binding.reason).toEqual({ known: true, value: 'task_not_in_snapshot' });
    expect(core.members[0].classification).toEqual({ known: true, value: 'present' });
    expect(core.members[0].reason).toEqual({ known: true, value: 'session_confirmed' });
    expect(core.members[0].activity).toEqual({ known: true, value: 'busy' });
  });

  it('models native and producer.recorded PER SOURCE KIND', () => {
    const result = projectResponse(response());
    if (!result.ok) throw new Error('fixture failed to project');
    const [core, legacy] = result.view.missions;
    expect(core.native).toEqual({ kind: 'mission_core', cancelRequested: false, paused: false });
    expect(legacy.native).toEqual({ kind: 'workflow_store', ambiguity: 'none' });
    expect(core.producerRecorded).toEqual({
      kind: 'mission_core',
      principalKind: 'unauthenticated_local_caller',
      transport: 'local_request',
    });
    expect(legacy.producerRecorded).toEqual({
      kind: 'workflow_store',
      originTransport: 'grok_bot',
      telegramBound: false,
    });
    expect(core.producerDeclared).toBe('synthetic-producer-core'); // declared provenance only
  });

  it('the LITERAL string "unknown" is distinct from an absent key and from null', () => {
    const raw = response();
    const result = projectResponse(raw);
    if (!result.ok) throw new Error('fixture failed to project');
    const member = result.view.missions[0].members[0];
    for (const fact of ['model', 'cost', 'tokens', 'progress'] as const) {
      expect(member.unreported[fact]).toEqual({ state: 'unknown_literal' });
    }
    const missions = raw.missions as Array<Record<string, unknown>>;
    const m0 = (missions[0].members as Array<Record<string, unknown>>)[0];
    delete m0.model;
    m0.cost = null;
    const varied = projectResponse(raw);
    if (!varied.ok) throw new Error('varied fixture failed to project');
    const v = varied.view.missions[0].members[0].unreported;
    expect(v.model).toEqual({ state: 'absent' });
    expect(v.cost).toEqual({ state: 'null' });
    expect(v.tokens).toEqual({ state: 'unknown_literal' });
  });

  it('an unrecognized enumeration value surfaces AS unrecognized — never throws, never defaults', () => {
    const raw = response();
    const missions = raw.missions as Array<Record<string, unknown>>;
    (missions[0].lifecycle as Record<string, unknown>).standing = 'brand_new_standing';
    ((missions[0].members as unknown[])[0] as Record<string, unknown>).activity = 'Busy';
    const result = projectResponse(raw);
    if (!result.ok) throw new Error('should still project');
    expect(result.view.missions[0].lifecycle.standing).toEqual({
      known: false,
      raw: 'brand_new_standing',
    });
    // Case matters: 'Busy' is NOT 'busy'.
    expect(result.view.missions[0].members[0].activity).toEqual({ known: false, raw: 'Busy' });
  });

  it('contract_version is checked against a SUPPORTED SET; others are incompatible', () => {
    expect(projectResponse({ ...response(), contract_version: 2 })).toMatchObject({
      ok: false,
      verdict: { health: 'incompatible', problem: 'response_contract_unsupported' },
    });
    expect(projectResponse({ ...response(), contract: 'something.else' })).toMatchObject({
      ok: false,
      verdict: { health: 'incompatible' },
    });
  });

  it('a different top-level key set is malformed/response_shape_invalid', () => {
    expect(projectResponse({ ...response(), added_key: 1 })).toMatchObject({
      ok: false,
      verdict: { health: 'malformed', problem: 'response_shape_invalid' },
    });
  });

  it('valid_until outside [generated_at, generated_at + max_age_seconds] is malformed', () => {
    expect(projectResponse({ ...response(), valid_until: 1800000061 }).ok).toBe(false);
    expect(projectResponse({ ...response(), valid_until: 1799999999 }).ok).toBe(false);
  });

  it('refs are opaque: a member is tied to its mission by field equality, never by parsing', () => {
    const raw = response();
    const missions = raw.missions as Array<Record<string, unknown>>;
    const member = (missions[0].members as Array<Record<string, unknown>>)[0];
    member.member_ref = 'opaque value with no # and no colon';
    const ok = projectResponse(raw);
    expect(ok.ok).toBe(true);
    member.mission_ref = 'some-other-mission';
    expect(projectResponse(raw)).toMatchObject({ ok: false, verdict: { health: 'malformed' } });
  });
});
