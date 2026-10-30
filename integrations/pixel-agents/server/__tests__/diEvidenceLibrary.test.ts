/**
 * Item 2 (original 10) — the evidence library, server side, against the backend's EMITTED
 * answers: `artifact-content-v1.FIXTURE.json` is exported by tests/test_artifact_content.py
 * (ExportForConsumerTests) from a REAL catalog owner and its /artifact content read over an
 * isolated fixture Mission Core store, NORMALIZED with the D1 conventions (synthetic root,
 * product-recomputed journal digests, the record attached after). Hash-pinned. FIXTURE evidence
 * only — never live acceptance.
 *
 * Proves: the four evidence types stay DISTINCT; empty / not reported / unsupported / stale /
 * refused / unavailable never collapse; nothing is manufactured; content is requested by an
 * admitted reference (never a path) and every backend answer is re-checked before it is relayed.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import { MAX_ARTIFACT_RESPONSE_BYTES, routePath } from '../src/di/backendClient.js';
import type { DiscoveryResult } from '../src/di/backendDiscovery.js';
import { EVIDENCE_LINKS_MAX, parseEnvelope } from '../src/di/catalogProjection.js';
import { DI_HANDLED_MESSAGE_TYPES, handleDiClientMessage } from '../src/di/diClientMessages.js';
import { DiOffice } from '../src/di/diOffice.js';
import {
  admitAgainstView,
  ARTIFACT_CONTENT_PROBLEMS,
  EVIDENCE_MIN_INTERVAL_MS,
  OFFICE_EVIDENCE_PROBLEMS,
  parseArtifactContentAnswer,
  parseEvidenceRead,
} from '../src/di/evidenceContent.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import type { MissionPresentation, OfficeView } from '../src/di/officeView.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

const FIXTURE = path.join(__dirname, 'fixtures/di-backend/v2/artifact-content-v1.FIXTURE.json');
const FIXTURE_SHA256 = '9c5599a7cc3f5fe5c68beaf3023add1437d88f05e1afffd490ff78693770eaff';
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
/** A REAL machine temp root (never the record's `<placeholder>` text): the D1 round-13 form. */
const REAL_ROOT =
  /(?:\/private)?\/var\/folders\/[^/<"\s]+\/[^/<"\s]+\/T\/|\/private\/tmp\/|\/Users\/|\/home\/[a-z]/;

type Raw = Record<string, unknown>;

function exported(): Raw {
  const bytes = fs.readFileSync(FIXTURE);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURE_SHA256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

const catalogAnswer = (raw: Raw): Raw => raw.catalog_answer as Raw;
const missionRaw = (answer: Raw): Raw => ((answer.response as Raw).missions as Raw[])[0];
const reportRaw = (answer: Raw): Raw => (missionRaw(answer).mission_state as Raw).report as Raw;
const contentAnswer = (raw: Raw, name: string): Raw => (raw.content_answers as Raw)[name] as Raw;
const generatedAt = (answer: Raw): number => Number((answer.response as Raw).generated_at);

function viewOf(answer: Raw, now = generatedAt(answer)): OfficeView {
  const parsed = parseEnvelope(answer);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  return buildOfficeView(parsed.envelope, FRESH, now, 2);
}

function theMission(view: OfficeView): MissionPresentation {
  if (view.missions.state !== 'observed') throw new Error(`missions ${view.missions.state}`);
  return view.missions.value[0];
}

function library(edit?: (answer: Raw) => void, now?: number) {
  const answer = clone(catalogAnswer(exported()));
  edit?.(answer);
  const evidence = theMission(viewOf(answer, now)).evidence;
  if (evidence === null) throw new Error('evidence not projected');
  return evidence;
}

function requestFor(raw: Raw, name: string) {
  const a = contentAnswer(raw, name);
  return {
    missionRef: String(a.mission_ref),
    artifactId: String(a.artifact_id),
    cursor: String(a.cursor),
  };
}

describe('the exported fixture is portable (D1 conventions, scanned as written)', () => {
  it('carries no real machine root; the record keeps its literal pattern and counts', () => {
    const text = fs.readFileSync(FIXTURE, 'utf-8');
    expect(REAL_ROOT.test(text)).toBe(false);
    const raw = exported();
    const payload = JSON.stringify({ a: raw.catalog_answer, c: raw.content_answers });
    const record = raw.normalization as Raw;
    expect(String(record.transformation)).toContain('/var/folders/<a>/<b>/T/<dir>');
    expect(payload).not.toContain('/var/folders/');
    expect(payload.split('/__synthetic__/di-fixture-root').length - 1).toBe(record.occurrences);
    expect(record.temporary_roots).toBe(1);
    expect(text).not.toContain('di-fixture-root-2');
    expect(text).not.toContain('TRANSCRIPT-CANARY');
  });
});

describe('the evidence library: four DISTINCT types from one real Mission', () => {
  it('artifacts, verification, reviews and receipts are separate sections', () => {
    const raw = exported();
    const lib = library();
    const report = reportRaw(catalogAnswer(raw));
    expect(lib.cursor).toBe((report.provenance as Raw).journal_digest_sha256);
    expect(lib.currency).toBe('current');
    expect(lib.contentReads).toEqual({ state: 'available', reason: null });

    // Artifacts: every recorded NON-receipt artifact, with its role.
    expect(lib.artifacts.state).toBe('observed');
    expect(lib.artifacts.total).toBe(9);
    const roles = new Set(lib.artifacts.items.map((a) => a.role));
    expect(roles).toEqual(new Set(['PRODUCED', 'VERIFICATION', 'ORIGINAL_INPUT']));
    const opaque = lib.artifacts.items.find((a) => a.locatorKind === 'OPAQUE_REFERENCE')!;
    expect(opaque.content).toEqual({
      state: 'not_requestable',
      reason: 'locator_kind_has_no_content',
    });
    for (const a of lib.artifacts.items.filter((x) => x.locatorKind === 'REPOSITORY_PATH')) {
      // The listing NEVER claims content is safe: the backend decides at read time.
      expect(a.content).toEqual({ state: 'requestable', reason: null });
      // No candidate report was observed: applicability is NOT claimed either way.
      expect(a.candidate).toBe('not_observed');
    }

    // Verification: the accepted record, linked to the artifact it RECORDS (never inferred).
    expect(lib.verification.state).toBe('observed');
    expect(lib.verification.items).toHaveLength(1);
    const record = lib.verification.items[0];
    expect(record).toMatchObject({
      kind: 'VERIFICATION_RECORD',
      accepted: true,
      invalidated: false,
      verifyingKind: true,
      bearsOnClaim: true,
    });
    // The accepted record names TWO artifacts: the test result and (as a recorded reference may)
    // a private Herdr file. The listing shows the link; disclosure is the backend's to refuse.
    const linked = lib.artifacts.items.filter((a) => record.artifactIds.includes(a.artifactId));
    expect(linked.map((a) => a.role).sort()).toEqual(['PRODUCED', 'VERIFICATION']);
    expect(record.artifactIds).toHaveLength(2);

    // Reviews: the admitted canonical preamble (verdict, gate, NOT digest-anchored) and the
    // reviewer-DECLARED public findings the local gate recorded — never transcript text.
    expect(lib.reviews.state).toBe('observed');
    const [review] = lib.reviews.items;
    expect({ ...review, findings: undefined }).toEqual({
      round: 1,
      reviewerLogical: 'reviewer1',
      reviewerAgent: 'pod-reviewer1',
      protocolToken: 'APPROVE',
      gate: 'parsed',
      verdict: 'APPROVE',
      currency: 'current',
      integrity: 'linkage_corroborated',
      candidateApplicability: 'unproven',
      findings: undefined,
    });
    const declared = (missionRaw(catalogAnswer(raw)).admitted_artifacts as Raw)
      .canonical_review as Raw;
    const f = declared.findings as Raw;
    expect(review.findings).toEqual({
      state: 'present',
      problem: null,
      count: 2,
      items: [
        {
          index: 1,
          severity: 'blocker',
          area: 'target_runtime/artifact_content.py',
          line: 381,
          summary: (f.items as Raw[])[0].summary as string,
        },
        {
          index: 2,
          severity: 'non_blocking',
          area: null,
          line: null,
          summary: 'findings render as text: <b>bold</b> <script>alert(1)</script>',
        },
      ],
      candidateSha256: f.candidate_sha256,
      routedAt: f.routed_at,
      provenance: f.provenance,
    });
    expect(review.findings.provenance).toContain('not a cryptographic proof');
    expect(JSON.stringify(lib)).not.toContain('TRANSCRIPT-CANARY');
    expect(JSON.stringify(lib)).not.toContain('private tail');

    // Receipts: none recorded — a POSITIVE empty, never "unsupported".
    expect(lib.receipts).toEqual({ state: 'known_empty', reason: null, total: 0, items: [] });
  });

  it('is serialized on the wire exactly as presented', () => {
    const answer = catalogAnswer(exported());
    const v = viewOf(answer);
    const msg = buildDiOfficeStateMessage(
      v,
      () => null,
      () => null,
    );
    const wire = msg.missions.missions[0].evidence;
    expect(wire).toEqual(theMission(v).evidence);
  });
});

describe('empty, not reported, unsupported, stale, refused and unavailable never collapse', () => {
  it('stale: the answer expired — listing kept, content reads closed', () => {
    const answer = catalogAnswer(exported());
    const lib = library(undefined, generatedAt(answer) + 3600);
    expect(lib.currency).toBe('stale');
    expect(lib.currencyReason).not.toBeNull();
    expect(lib.contentReads).toEqual({ state: 'unavailable', reason: 'office_not_live' });
    expect(lib.artifacts.state).toBe('observed');
  });

  it('a Mission that is not bound: listing kept, content reads closed by name', () => {
    const lib = library((a) => {
      (missionRaw(a).binding as Raw).state = 'disappeared';
      (missionRaw(a).binding as Raw).reason = 'task_not_in_snapshot';
    });
    expect(lib.contentReads).toEqual({ state: 'unavailable', reason: 'mission_binding_not_bound' });
  });

  it('not reported (null value) vs empty (an empty list)', () => {
    const lib = library((a) => {
      (reportRaw(a).evidence as Raw).value = null;
      (reportRaw(a).artifacts as Raw).value = [];
    });
    expect(lib.verification.state).toBe('not_recorded');
    expect(lib.artifacts).toEqual({ state: 'known_empty', reason: null, total: 0, items: [] });
    expect(lib.receipts.state).toBe('known_empty');
  });

  it('unavailable: the Mission State could not be read — reviews are a separate source', () => {
    const lib = library((a) => {
      missionRaw(a).mission_state = {
        state: 'unavailable',
        problem: 'mission_state_source_unavailable',
        detail: 'store missing',
        report: null,
        inputs: null,
      };
    });
    for (const s of [lib.artifacts, lib.verification, lib.receipts]) {
      expect(s.state).toBe('unavailable');
      expect(s.reason).toBe('mission_state_source_unavailable');
      expect(s.items).toEqual([]);
    }
    expect(lib.reviews.state).toBe('observed');
    expect(lib.cursor).toBeNull();
    expect(lib.contentReads.state).toBe('unavailable');
  });

  it('a malformed item is never guessed: the section is unavailable', () => {
    const lib = library((a) => {
      const items = (reportRaw(a).artifacts as Raw).value as Raw[];
      delete items[0].artifact_id;
    });
    expect(lib.artifacts.state).toBe('unavailable');
    expect(lib.artifacts.reason).toBe('report_fact_shape_unexpected');
    expect(lib.receipts.state).toBe('unavailable');
  });

  it('refused: a refused canonical review stays REFUSED, with its problem', () => {
    const lib = library((a) => {
      missionRaw(a).admitted_artifacts = {
        canonical_review: {
          state: 'refused',
          problem: 'review_decision_disagrees',
          record: null,
          gate: null,
          verdict: null,
          currency: 'current',
          integrity: null,
          task_status: 'ACTIVE',
          candidate_applicability: 'unproven',
        },
      };
    });
    expect(lib.reviews).toEqual({
      state: 'refused',
      reason: 'review_decision_disagrees',
      total: null,
      items: [],
    });
  });

  it('a plain Herdr task reports no Mission Core artifact library, and shows the catalog review', () => {
    const p = path.join(
      __dirname,
      'fixtures/di-backend/herdr-task/herdr-task-answer-v2.FIXTURE.json',
    );
    const answer = JSON.parse(fs.readFileSync(p, 'utf-8')) as Raw;
    const v = viewOf(answer);
    if (v.missions.state !== 'observed') throw new Error('missions');
    const herd = v.missions.value.find((m) => m.sourceKind === 'herdr_task')!;
    const lib = herd.evidence!;
    for (const s of [lib.artifacts, lib.verification, lib.receipts]) {
      expect(s.state).toBe('not_recorded');
      expect(s.reason).toBeNull();
    }
    // this fixture's catalog still carries the older kind refusal; the office shows it as said
    expect(lib.reviews).toMatchObject({
      state: 'not_applicable',
      reason: 'source_kind_has_no_canonical_review',
    });
    expect(lib.contentReads).toEqual({
      state: 'unavailable',
      reason: 'herdr_task_has_no_artifact_bodies',
    });
  });

  it('a Herdr task with a recorded review shows that review, and not_recorded stays empty', () => {
    const p = path.join(
      __dirname,
      'fixtures/di-backend/herdr-task/herdr-task-answer-v2.FIXTURE.json',
    );
    const answer = JSON.parse(fs.readFileSync(p, 'utf-8')) as Raw;
    const missions = (answer.response as Raw).missions as Raw[];
    const raw = missions.find((m) => (m.lifecycle as Raw).model === 'herdr.task/1')!;
    const review = (raw.admitted_artifacts as Raw).canonical_review as Raw;
    review.state = 'not_recorded';
    review.problem = null;
    const empty = viewOf(answer);
    if (empty.missions.state !== 'observed') throw new Error('missions');
    const emptyHerd = empty.missions.value.find((m) => m.sourceKind === 'herdr_task')!;
    expect(emptyHerd.evidence!.reviews).toMatchObject({
      state: 'not_recorded',
      reason: null,
      items: [],
    });

    review.state = 'admitted';
    review.record = {
      round: 1,
      reviewer_logical: 'reviewer1',
      reviewer_agent: 'pod-reviewer1',
      protocol_token: 'APPROVE',
    };
    review.gate = 'parsed';
    review.verdict = 'APPROVE';
    review.currency = 'current';
    review.integrity = 'linkage_corroborated';
    review.task_status = 'ACTIVE';
    const admitted = viewOf(answer);
    if (admitted.missions.state !== 'observed') throw new Error('missions');
    const herd = admitted.missions.value.find((m) => m.sourceKind === 'herdr_task')!;
    expect(herd.evidence!.reviews.state).toBe('observed');
    expect(herd.evidence!.reviews.items[0]).toMatchObject({
      round: 1,
      reviewerLogical: 'reviewer1',
      verdict: 'APPROVE',
    });
  });
});

describe('review findings: five states, never collapsed or guessed', () => {
  const findingsOf = (edit: (f: Raw | undefined, r: Raw) => void) =>
    library((a) => {
      const r = (missionRaw(a).admitted_artifacts as Raw).canonical_review as Raw;
      edit(r.findings as Raw | undefined, r);
    }).reviews.items[0].findings;

  it('an older producer (no key) is NOT REPORTED, never empty', () => {
    expect(findingsOf((_f, r) => delete r.findings)).toMatchObject({
      state: 'not_reported',
      items: [],
      count: null,
    });
  });

  it('legacy, refused and empty keep their own states and codes', () => {
    const blank = {
      count: null,
      items: [],
      candidate_sha256: null,
      routed_at: null,
      linkage: null,
      provenance: null,
    };
    expect(
      findingsOf((_f, r) => {
        r.findings = {
          state: 'not_recorded',
          problem: 'findings_legacy_record_predates_field',
          ...blank,
        };
      }),
    ).toMatchObject({ state: 'not_recorded', problem: 'findings_legacy_record_predates_field' });
    expect(
      findingsOf((_f, r) => {
        r.findings = { state: 'refused', problem: 'findings_declaration_ambiguous', ...blank };
      }),
    ).toMatchObject({ state: 'refused', problem: 'findings_declaration_ambiguous', items: [] });
    expect(
      findingsOf((f) => {
        f!.state = 'empty';
        f!.count = 0;
        f!.items = [];
      }),
    ).toMatchObject({ state: 'empty', count: 0, items: [] });
  });

  it('a malformed block is UNEXPECTED, never guessed', () => {
    const cases: Array<(f: Raw) => void> = [
      (f) => (f.count = 3),
      (f) => (f.extra = 1),
      (f) => (f.state = 'invented'),
      (f) => ((f.items as Raw[])[0].severity = 'urgent'),
      (f) => ((f.items as Raw[])[0].summary = 'x'.repeat(281)),
      (f) => ((f.items as Raw[])[1].index = 7),
      (f) => ((f.items as Raw[])[0].script = 'x'),
      (f) => (f.problem = 'something'),
    ];
    for (const edit of cases) {
      const shown = findingsOf((f) => edit(f!));
      expect(shown.state).toBe('unexpected');
      expect(shown.items).toEqual([]);
    }
  });
});

describe('receipts are their own type and are never manufactured', () => {
  function withReceipts(): ReturnType<typeof library> {
    return library((a) => {
      const items = (reportRaw(a).artifacts as Raw).value as Raw[];
      const base = {
        key: null,
        role: 'VERIFICATION',
        locator_kind: 'DELIVERY_RECEIPT_REFERENCE',
        content_digest_sha256: 'a'.repeat(64),
        available_reported: true,
        observed_digest_sha256: null,
        matches_observed: null,
        recorded_at: 1800000000,
      };
      items.push(
        {
          ...base,
          artifact_id: `mf-${'1'.repeat(32)}`,
          attested: true,
          receipt_attestation: {
            delivery_id: 'pd-synthetic',
            step: 'push',
            receipt_state: 'SUCCEEDED',
            step_state: 'COMPLETED',
            authorization_id: 'ma-synthetic',
            effect_completed: true,
          },
        },
        {
          ...base,
          artifact_id: `mf-${'2'.repeat(32)}`,
          attested: false,
          receipt_attestation: null,
        },
      );
    });
  }

  it('attested and unattested receipts are listed apart from artifacts', () => {
    const lib = withReceipts();
    expect(lib.artifacts.total).toBe(9);
    expect(lib.receipts.state).toBe('observed');
    expect(lib.receipts.items.map((r) => [r.attested, r.effectCompleted, r.deliveryId])).toEqual([
      [true, true, 'pd-synthetic'],
      // An unattested reference is an UNVERIFIED claim: no state, no effect, never success.
      [false, null, null],
    ]);
  });
});

describe('a content request names an admitted reference — never a path', () => {
  const ok = {
    type: 'diEvidenceRead',
    missionRef: `mc1:mn-${'a'.repeat(32)}`,
    artifactId: `mf-${'b'.repeat(32)}`,
    cursor: 'c'.repeat(64),
  };

  it('exact keys, fixed grammar', () => {
    expect(parseEvidenceRead(ok)).toEqual({
      missionRef: ok.missionRef,
      artifactId: ok.artifactId,
      cursor: ok.cursor,
    });
    for (const bad of [
      { ...ok, path: '/etc/passwd' },
      { ...ok, locator: 'reports/result.txt' },
      { ...ok, artifactId: '../../etc/passwd' },
      { ...ok, artifactId: 'reports/result.txt' },
      { ...ok, missionRef: 'mc1:/etc/passwd' },
      { ...ok, cursor: 'C'.repeat(64) },
      { ...ok, cursor: 'c'.repeat(63) },
      { ...ok, url: 'file:///etc/passwd' },
      { type: 'diEvidenceRead', missionRef: ok.missionRef, artifactId: ok.artifactId },
      { ...ok, missionRef: 7 },
    ]) {
      expect(parseEvidenceRead(bad as Raw)).toBeNull();
    }
  });

  it('is admitted only for a listed, requestable artifact at the CURRENT listing', () => {
    const raw = exported();
    const v = viewOf(catalogAnswer(raw));
    const good = requestFor(raw, 'admitted_text');
    expect(admitAgainstView(v, good)).toEqual({ ok: true });
    const refused = (req: typeof good, view = v) => {
      const r = admitAgainstView(view, req);
      return r.ok ? null : r.problem;
    };
    expect(refused({ ...good, cursor: 'd'.repeat(64) })).toBe('listing_moved');
    expect(refused({ ...good, artifactId: `mf-${'e'.repeat(32)}` })).toBe('reference_not_listed');
    expect(refused({ ...good, missionRef: `mc1:mn-${'f'.repeat(32)}` })).toBe('mission_not_listed');
    expect(refused(requestFor(raw, 'refused_locator_kind'))).toBe('reference_has_no_content');
    const stale = viewOf(catalogAnswer(raw), generatedAt(catalogAnswer(raw)) + 3600);
    expect(refused(good, stale)).toBe('content_reads_unavailable');
  });
});

describe('every backend answer is re-checked before it is relayed', () => {
  it('parses every exported answer, admitted and refused, against its request', () => {
    const raw = exported();
    for (const name of Object.keys(raw.content_answers as Raw)) {
      const parsed = parseArtifactContentAnswer(contentAnswer(raw, name), requestFor(raw, name));
      expect(parsed.ok, name).toBe(true);
      if (!parsed.ok) continue;
      expect(parsed.answer.state).toBe(name.split('_')[0]);
      if (parsed.answer.state === 'refused') {
        expect(parsed.answer.content).toBeNull();
        expect(ARTIFACT_CONTENT_PROBLEMS).toContain(parsed.answer.problem);
      }
    }
    const html = parseArtifactContentAnswer(
      contentAnswer(raw, 'admitted_html'),
      requestFor(raw, 'admitted_html'),
    );
    expect(html.ok && html.answer.content?.media).toBe('text/html');
    // What the canonical record DECLARED it to be, carried through — never the path's shape.
    expect(html.ok && html.answer.artifact?.admissionBasis).toEqual(['contract_required_artifact']);
    const log = parseArtifactContentAnswer(
      contentAnswer(raw, 'admitted_verification_log'),
      requestFor(raw, 'admitted_verification_log'),
    );
    expect(log.ok && log.answer.artifact?.admissionBasis).toEqual(['accepted_evidence_artifact']);
  });

  it('refuses a mismatched, tampered, oversized or unknown answer', () => {
    const raw = exported();
    const req = requestFor(raw, 'admitted_text');
    const base = contentAnswer(raw, 'admitted_text');
    const bad = (edit: (a: Raw) => void, against = req) => {
      const a = clone(base);
      edit(a);
      const r = parseArtifactContentAnswer(a, against);
      return r.ok ? 'accepted' : r.problem;
    };
    expect(bad(() => undefined, { ...req, artifactId: `mf-${'9'.repeat(32)}` })).toBe(
      'backend_answer_invalid',
    );
    // The bridge re-hashes the text: bytes that are not the recorded digest never pass —
    // even with the SAME length and the backend's digest claims left untouched.
    expect(bad((a) => ((a.content as Raw).text = 'tampered\n'))).toBe('backend_answer_invalid');
    expect(
      bad((a) => {
        const c = a.content as Raw;
        c.text = String(c.text).replace('12 passed', '99 passed');
        expect(Buffer.byteLength(String(c.text))).toBe(c.byte_count);
      }),
    ).toBe('backend_answer_invalid');
    expect(bad((a) => ((a.content as Raw).media = 'text/javascript'))).toBe(
      'backend_answer_invalid',
    );
    expect(bad((a) => (a.extra = 1))).toBe('backend_answer_invalid');
    // An admitted answer must say what the record declared; an unknown basis is never accepted.
    expect(bad((a) => ((a.artifact as Raw).admission_basis = []))).toBe('backend_answer_invalid');
    expect(bad((a) => ((a.artifact as Raw).admission_basis = ['by_extension']))).toBe(
      'backend_answer_invalid',
    );
    expect(bad((a) => ((a.content as Raw).script = 'x'))).toBe('backend_answer_invalid');
    expect(
      bad((a) => {
        a.state = 'refused';
        a.problem = 'something_new';
        a.content = null;
        a.artifact = null;
      }),
    ).toBe('backend_answer_invalid');
    const big = 'x'.repeat(65537);
    expect(
      bad((a) => {
        const c = a.content as Raw;
        c.text = big;
        c.byte_count = big.length;
        c.digest_sha256 = crypto.createHash('sha256').update(big).digest('hex');
        (a.artifact as Raw).content_digest_sha256 = c.digest_sha256;
      }),
    ).toBe('backend_answer_invalid');
  });
});

describe('the backend client names the content route by reference only', () => {
  it('builds exactly one /artifact query of the fixed grammar', () => {
    const q = {
      missionRef: `mc1:mn-${'a'.repeat(32)}`,
      artifactId: `mf-${'b'.repeat(32)}`,
      cursor: 'c'.repeat(64),
    };
    expect(routePath('artifact', undefined, q)).toBe(
      `/artifact?mission_ref=${encodeURIComponent(q.missionRef)}&artifact_id=${q.artifactId}&cursor=${q.cursor}`,
    );
    expect(routePath('artifact', undefined, { ...q, artifactId: '../x' })).toBeNull();
    expect(routePath('artifact')).toBeNull();
    expect(MAX_ARTIFACT_RESPONSE_BYTES).toBeLessThan(1024 * 1024);
  });
});

describe('DiOffice.readEvidence — one bounded read, relayed only to the asking client', () => {
  const ENDPOINT: DiscoveryResult = { ok: true, endpoint: { port: 1, token: 'secret-not-logged' } };
  let clockMs = 0;
  /** What the office's NEXT catalog poll receives (a test may move it mid-read). */
  const feed: { answer: Raw } = { answer: {} };

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function office(
    requestArtifact?: (endpoint: unknown, q: unknown) => Promise<BackendResult>,
    answer = catalogAnswer(exported()),
  ) {
    clockMs = generatedAt(answer) * 1000;
    feed.answer = answer;
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ENDPOINT,
        request: async () => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body: clone(feed.answer),
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
        ...(requestArtifact ? { requestArtifact: vi.fn(requestArtifact) } : {}),
      },
    });
    await o.poll();
    return o;
  }

  const http200 = (body: unknown): BackendResult => ({
    kind: 'http',
    status: 200,
    headers: {},
    body,
    bodyProblem: null,
  });

  it('admitted content reaches only the requesting socket, as inert data', async () => {
    const raw = exported();
    const backend = vi.fn(async (_endpoint: unknown, _ref: unknown) =>
      http200(clone(contentAnswer(raw, 'admitted_html'))),
    );
    const o = await office(backend);
    const sent: Raw[] = [];
    await o.readEvidence({ type: 'diEvidenceRead', ...requestFor(raw, 'admitted_html') }, (m) =>
      sent.push(m),
    );
    expect(backend).toHaveBeenCalledTimes(1);
    expect(backend.mock.calls[0][1]).toEqual(requestFor(raw, 'admitted_html'));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: 'diEvidenceContent',
      state: 'admitted',
      decidedBy: 'backend',
      problem: null,
    });
    expect((sent[0].content as Raw).media).toBe('text/html');
    expect(String((sent[0].content as Raw).text)).toContain('<script>');
    o.stop();
  });

  it('a backend refusal is relayed with its own code, and nothing else', async () => {
    const raw = exported();
    for (const name of [
      'refused_private_location',
      'refused_digest_mismatch',
      'refused_class_not_declared',
    ]) {
      const o = await office(async () => http200(clone(contentAnswer(raw, name))));
      const sent: Raw[] = [];
      // Admission at the office first needs a requestable listed artifact.
      await o.readEvidence({ type: 'diEvidenceRead', ...requestFor(raw, name) }, (m) =>
        sent.push(m),
      );
      expect(sent[0]).toMatchObject({
        state: 'refused',
        decidedBy: 'backend',
        problem: contentAnswer(raw, name).problem,
        content: null,
        artifact: null,
      });
      o.stop();
    }
  });

  it('office refusals never reach the backend', async () => {
    const raw = exported();
    const backend = vi.fn(async () => http200(clone(contentAnswer(raw, 'admitted_text'))));
    const o = await office(backend);
    const sent: Raw[] = [];
    const send = (m: Raw) => sent.push(m);
    await o.readEvidence(
      { type: 'diEvidenceRead', ...requestFor(raw, 'admitted_text'), cursor: 'd'.repeat(64) },
      send,
    );
    await o.readEvidence(
      { type: 'diEvidenceRead', ...requestFor(raw, 'refused_locator_kind') },
      send,
    );
    expect(sent.map((m) => [m.decidedBy, m.problem])).toEqual([
      ['office', 'listing_moved'],
      ['office', 'reference_has_no_content'],
    ]);
    expect(backend).not.toHaveBeenCalled();
    o.stop();
  });

  it('one read at a time, and at most one per interval', async () => {
    const raw = exported();
    let release: (r: BackendResult) => void = () => undefined;
    const backend = vi.fn(
      () =>
        new Promise<BackendResult>((resolve) => {
          release = resolve;
        }),
    );
    const o = await office(backend);
    const sent: Raw[] = [];
    const send = (m: Raw) => sent.push(m);
    const req = { type: 'diEvidenceRead', ...requestFor(raw, 'admitted_text') };
    const first = o.readEvidence(req, send);
    await o.readEvidence(req, send);
    expect(sent.map((m) => m.problem)).toEqual(['read_in_progress']);
    release(http200(clone(contentAnswer(raw, 'admitted_text'))));
    await first;
    await o.readEvidence(req, send);
    expect(sent.map((m) => m.problem)).toEqual(['read_in_progress', null, 'read_rate_limited']);
    clockMs += EVIDENCE_MIN_INTERVAL_MS;
    const third = o.readEvidence(req, send);
    release(http200(clone(contentAnswer(raw, 'admitted_text'))));
    await third;
    expect(sent[3].state).toBe('admitted');
    expect(backend).toHaveBeenCalledTimes(2);
    o.stop();
  });

  it('transport failures and bad answers are honest refusals', async () => {
    const raw = exported();
    const req = { type: 'diEvidenceRead', ...requestFor(raw, 'admitted_text') };
    const cases: Array<[BackendResult, string]> = [
      [{ kind: 'transport_error', problem: 'backend_timeout' }, 'backend_unavailable'],
      [
        { kind: 'http', status: 400, headers: {}, body: {}, bodyProblem: null },
        'backend_refused_request',
      ],
      [
        { kind: 'http', status: 200, headers: {}, body: undefined, bodyProblem: 'body_not_json' },
        'backend_answer_invalid',
      ],
    ];
    for (const [result, problem] of cases) {
      const o = await office(async () => result);
      const sent: Raw[] = [];
      await o.readEvidence(req, (m) => sent.push(m));
      expect(sent[0]).toMatchObject({ state: 'refused', decidedBy: 'office', problem });
      o.stop();
    }
  });

  it('a listing that moved while the backend read is DISCARDED, never relayed', async () => {
    const raw = exported();
    let o: DiOffice | null = null;
    const backend = async () => {
      // The office polls a newer answer (the Mission moved) while the read is out.
      const moved = clone(catalogAnswer(raw));
      (reportRaw(moved).provenance as Raw).journal_digest_sha256 = 'e'.repeat(64);
      feed.answer = moved;
      await o!.poll();
      return http200(clone(contentAnswer(raw, 'admitted_text')));
    };
    o = await office(backend);
    const sent: Raw[] = [];
    await o.readEvidence({ type: 'diEvidenceRead', ...requestFor(raw, 'admitted_text') }, (m) =>
      sent.push(m),
    );
    expect(sent[0]).toMatchObject({
      state: 'refused',
      decidedBy: 'office',
      problem: 'listing_moved_during_read',
      content: null,
    });
    o.stop();
  });

  it('a backend without the content seam (fixture preview) says so', async () => {
    const raw = exported();
    const o = await office(undefined);
    const sent: Raw[] = [];
    await o.readEvidence({ type: 'diEvidenceRead', ...requestFor(raw, 'admitted_text') }, (m) =>
      sent.push(m),
    );
    expect(sent[0].problem).toBe('content_reads_not_supported_by_this_backend');
    o.stop();
  });

  it('every office problem is declared', () => {
    expect(new Set(OFFICE_EVIDENCE_PROBLEMS).size).toBe(OFFICE_EVIDENCE_PROBLEMS.length);
  });
});

/**
 * Round 15 F4 through the CONNECTED office path: the backend's real catalog answer, with ONE
 * recorded verification record linking N artifacts, is polled by a real DiOffice and read back
 * as the exact message it broadcasts. Mission Core admits up to 64 links per record
 * (`mission/state.py` MAX_ARTIFACT_LINKS): all of them must survive, later ones included; a
 * longer list keeps its TRUE total so the cap is never silent.
 */
describe('evidence links are never silently cut (round 15 F4)', () => {
  const hex = (n: number) => n.toString(16).padStart(32, '0');
  async function broadcastWith(extra: number) {
    const answer = clone(catalogAnswer(exported()));
    const record = ((reportRaw(answer).evidence as Raw).value as Raw[])[0];
    const real = record.artifact_ids as string[];
    const ids = [...real, ...Array.from({ length: extra }, (_, i) => `mf-${hex(i + 1)}`)];
    record.artifact_ids = ids;
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async () => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body: answer,
          bodyProblem: null,
        }),
        nowMs: () => generatedAt(answer) * 1000,
        log: () => undefined,
      },
    });
    await o.poll();
    const message = o.officeStateMessage();
    o.stop();
    const items = message.missions.missions[0].evidence!.verification.items;
    expect(items).toHaveLength(1);
    return { ids, item: items[0] as unknown as Raw };
  }

  it("the per-record link cap is exactly Mission Core's own bound (64)", () => {
    expect(EVIDENCE_LINKS_MAX).toBe(64);
  });

  it('64 recorded links (the canonical bound) all reach the wire, later ones included', async () => {
    const { ids, item } = await broadcastWith(62);
    expect(ids).toHaveLength(64);
    expect(item.artifactIds).toEqual(ids);
    expect(item.artifactIdsTotal).toBe(64);
    expect(item.artifactIds as string[]).toContain(ids[40]);
    expect(item.artifactIds as string[]).toContain(ids[63]);
  });

  it('a longer list keeps its true total: the cap is reported, never silent', async () => {
    const { ids, item } = await broadcastWith(68);
    expect(ids).toHaveLength(70);
    expect(item.artifactIds).toEqual(ids.slice(0, 64));
    expect(item.artifactIdsTotal).toBe(70);
  });
});

/**
 * Item 2 (completed-Mission artifacts), office side, against the backend's EMITTED historical
 * export (`artifact-content-historical-v1.FIXTURE.json`: a COMPLETED Mission built through Mission
 * Core's normal APIs, observed through the task-native producer at its own root; hash-pinned;
 * FIXTURE evidence only — seeded lifecycle and isolated principals are TEST DATA, never real
 * execution, verification or authorization).
 */
describe('a COMPLETED Mission reads under its historical recorded execution', () => {
  const HISTORICAL = path.join(
    __dirname,
    'fixtures/di-backend/v2/artifact-content-historical-v1.FIXTURE.json',
  );
  const HISTORICAL_SHA256 = '789533593eb063068f4407c635df45647b7cadf698c013f95ea4870b0aadecfe';
  function historical(): Raw {
    const bytes = fs.readFileSync(HISTORICAL);
    const digest = crypto.createHash('sha256').update(bytes).digest('hex');
    if (digest !== HISTORICAL_SHA256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
    return JSON.parse(bytes.toString('utf-8')) as Raw;
  }
  const canonicalOf = (answer: Raw): Raw =>
    ((answer.response as Raw).missions as Raw[]).find(
      (m) => (m.lifecycle as Raw).model !== 'herdr.task/1',
    )!;
  function presented(edit?: (answer: Raw) => void, now?: number) {
    const answer = clone(catalogAnswer(historical()));
    edit?.(answer);
    const view = viewOf(answer, now);
    if (view.missions.state !== 'observed') throw new Error('missions not observed');
    const ref = canonicalOf(answer).mission_ref as string;
    const mission = view.missions.value.find((m) => m.missionRef === ref)!;
    return { answer, mission, view };
  }
  const identityOf = (answer: Raw): Raw =>
    (canonicalOf(answer).mission_state as Raw).execution_identity as Raw;

  it('is listed as historical: established identity, its own cursor, reads open, no team', () => {
    const { answer, mission, view } = presented();
    const lib = mission.evidence!;
    expect(lib.execution.basis).toBe('historical_completed_execution');
    expect(lib.execution.identityState).toBe('established');
    expect(lib.cursor).toBe(identityOf(answer).cursor_sha256);
    expect(lib.cursor).not.toBe(
      ((canonicalOf(answer).mission_state as Raw).report as Raw).provenance &&
        (((canonicalOf(answer).mission_state as Raw).report as Raw).provenance as Raw)
          .journal_digest_sha256,
    );
    expect(lib.contentReads).toEqual({ state: 'available', reason: null });
    expect(lib.execution.taskId).toBe((canonicalOf(answer).execution as Raw).task_id);
    // The task-native row OBSERVES the same execution; it has no read authority of its own.
    const native =
      view.missions.state === 'observed'
        ? view.missions.value.find((m) => m.missionRef !== mission.missionRef)!
        : null;
    expect(native!.evidence!.execution.basis).toBe('none');
    expect(native!.evidence!.contentReads.state).toBe('unavailable');
  });

  it('every way the identity is not established closes reads, each with its own reason', () => {
    const cases: Array<[string, (i: Raw, a: Raw) => void, string]> = [
      [
        'not reported',
        (_i, a) => delete (canonicalOf(a).mission_state as Raw).execution_identity,
        'historical_identity_not_reported',
      ],
      [
        'not established',
        (i) =>
          Object.assign(i, {
            state: 'not_established',
            problem: 'execution_prepared_binding_absent',
            identity_sha256: null,
            cursor_sha256: null,
          }),
        'historical_identity_not_established',
      ],
      [
        'malformed',
        (i) => Object.assign(i, { cursor_sha256: 'not-a-digest' }),
        'historical_identity_unexpected',
      ],
      [
        'not completed',
        (i) =>
          Object.assign(i, {
            state: 'not_applicable',
            problem: 'execution_mission_not_completed',
            identity_sha256: null,
            cursor_sha256: null,
          }),
        'mission_binding_not_bound',
      ],
    ];
    for (const [name, change, reason] of cases) {
      const { mission } = presented((a) => change(identityOf(a), a));
      expect(mission.evidence!.execution.basis, name).toBe('none');
      expect(mission.evidence!.contentReads, name).toEqual({ state: 'unavailable', reason });
    }
    const notEstablished = presented((a) =>
      Object.assign(identityOf(a), {
        state: 'not_established',
        problem: 'execution_prepared_binding_absent',
        identity_sha256: null,
        cursor_sha256: null,
      }),
    ).mission.evidence!;
    expect(notEstablished.execution.identityProblem).toBe('execution_prepared_binding_absent');
  });

  it('expired: the listing is STALE and reads close, the recorded execution kept apart', () => {
    const answer = catalogAnswer(historical());
    const { mission } = presented(undefined, generatedAt(answer) + 3600);
    const lib = mission.evidence!;
    expect(lib.currency).toBe('stale');
    expect(lib.contentReads.state).toBe('unavailable');
    expect(lib.execution.basis).toBe('historical_completed_execution');
  });

  async function officeOver(
    answer: Raw,
    requestArtifact: (e: unknown, q: unknown) => Promise<BackendResult>,
  ) {
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async () => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body: clone(answer),
          bodyProblem: null,
        }),
        nowMs: () => generatedAt(answer) * 1000,
        log: () => undefined,
        requestArtifact: vi.fn(requestArtifact),
      },
    });
    await o.poll();
    return o;
  }
  const readOf = (raw: Raw, name: string) => {
    const a = contentAnswer(raw, name);
    return {
      type: 'diEvidenceRead',
      missionRef: a.mission_ref as string,
      artifactId: a.artifact_id as string,
      cursor: a.cursor as string,
    };
  };

  it('the office relays a historical answer only under the historical basis it lists', async () => {
    const raw = historical();
    const answer = catalogAnswer(raw);
    const backend = async () => ({
      kind: 'http' as const,
      status: 200,
      headers: {},
      body: clone(contentAnswer(raw, 'admitted_text')),
      bodyProblem: null,
    });
    const o = await officeOver(answer, backend);
    const sent: Raw[] = [];
    await o.readEvidence(readOf(raw, 'admitted_text'), (m) => sent.push(m));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ state: 'admitted', decidedBy: 'backend' });
    expect((sent[0].artifact as Raw).executionBasis).toBe('historical_completed_execution');
    o.stop();
    // The same bytes claimed under the ACTIVE basis are not relayed for a historical listing.
    const wrong = clone(contentAnswer(raw, 'admitted_text'));
    (wrong.artifact as Raw).execution_basis = 'active_binding';
    const o2 = await officeOver(answer, async () => ({
      kind: 'http' as const,
      status: 200,
      headers: {},
      body: wrong,
      bodyProblem: null,
    }));
    const sent2: Raw[] = [];
    await o2.readEvidence(readOf(raw, 'admitted_text'), (m) => sent2.push(m));
    expect(sent2[0]).toMatchObject({
      state: 'refused',
      decidedBy: 'office',
      problem: 'execution_basis_mismatch',
      content: null,
    });
    o2.stop();
  });

  it("the bare journal digest is not a completed Mission's cursor: the office never asks", async () => {
    const raw = historical();
    const backend = vi.fn(async () => ({
      kind: 'http' as const,
      status: 200,
      headers: {},
      body: null,
      bodyProblem: null,
    }));
    const o = await officeOver(catalogAnswer(raw), backend);
    const sent: Raw[] = [];
    await o.readEvidence(readOf(raw, 'refused_cursor_not_listed'), (m) => sent.push(m));
    expect(sent[0]).toMatchObject({
      state: 'refused',
      decidedBy: 'office',
      problem: 'listing_moved',
    });
    o.stop();
  });

  it('every exported historical answer parses, and refusals keep their own causes', () => {
    const raw = historical();
    const answers = raw.content_answers as Record<string, Raw>;
    const problems: Record<string, unknown> = {};
    for (const name of Object.keys(answers).sort()) {
      const a = answers[name];
      const parsed = parseArtifactContentAnswer(a, {
        missionRef: a.mission_ref as string,
        artifactId: a.artifact_id as string,
        cursor: a.cursor as string,
      });
      expect(parsed.ok, name).toBe(true);
      if (parsed.ok) problems[name] = parsed.answer.problem;
    }
    expect(problems).toEqual({
      admitted_html: null,
      admitted_text: null,
      refused_class_not_declared: 'artifact_class_not_declared',
      refused_cursor_not_listed: 'cursor_not_the_listed_cursor',
      refused_digest_mismatch: 'content_changed_since_recorded',
      refused_private_location: 'locator_in_private_location',
    });
  });
});

describe('the DI message allowlist', () => {
  it('handles diEvidenceRead only through the office seam; a malformed one is refused', () => {
    expect(DI_HANDLED_MESSAGE_TYPES).toContain('diEvidenceRead');
    const calls: Raw[] = [];
    const ctx = {
      store: new AgentStateStore(),
      cache: {} as never,
      extensionVersion: '',
      readEvidence: (msg: Raw) => {
        calls.push(msg);
      },
    };
    const good = {
      type: 'diEvidenceRead',
      missionRef: `mc1:mn-${'a'.repeat(32)}`,
      artifactId: `mf-${'b'.repeat(32)}`,
      cursor: 'c'.repeat(64),
    };
    expect(handleDiClientMessage(good, () => undefined, ctx)).toBe('handled');
    expect(calls).toHaveLength(1);
    expect(handleDiClientMessage({ ...good, path: '/etc/passwd' }, () => undefined, ctx)).toEqual({
      refused: 'diEvidenceRead is not exactly an admitted reference',
    });
    expect(calls).toHaveLength(1);
    const noSeam = { ...ctx, readEvidence: undefined };
    expect(handleDiClientMessage(good, () => undefined, noSeam)).toEqual({
      refused: 'no evidence reader in this office',
    });
  });
});
