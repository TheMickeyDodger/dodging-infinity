/**
 * (c), owner decision: the office relays the person's DECLINE, REJECTION or CHANGE REQUEST, each on EXACTLY the offer shown, to
 * the request panel's own route for it. No socket and no process: the closed grammar and the reply mapping only, as
 * diMissionRequests.test.ts tests them for `decide`.
 *
 *  - `decline`, `reject` and `requestChanges` take the decision's offer fields and NO typed word; `requestChanges` adds the
 *    person's reason, relayed UNCHANGED (whether it is readable is the adapter's rule too, checked there before anything is read
 *    or written; here it is bounded and well formed);
 *  - each becomes exactly one POST to /requests/<ref>/decline, /reject or /request-changes, a route the panel client allows;
 *  - each is a WRITE: a failure after it may have been sent leaves its outcome UNKNOWN, never a refusal or "nothing changed".
 */
import { describe, expect, it } from 'vitest';

import type { PanelCall, PanelResult } from '../src/di/missionPanelClient.js';
import { isPanelRoute } from '../src/di/missionPanelClient.js';
import { parseMissionRequest, replyFromPanel } from '../src/di/missionRequests.js';

const REF = `lr-${'a'.repeat(32)}`;
const MISSION = `mn-${'d'.repeat(32)}`;
const DIGEST = 'e'.repeat(64);
const OFFER_DIGEST = '9'.repeat(64);
const REASON = 'Narrow the scope to the readiness probe.\nAdd a regression test for the flake.';

const OUTCOMES = [
  ['decline', 'decline'],
  ['reject', 'reject'],
  ['requestChanges', 'request-changes'],
] as const;

const offerFields = {
  requestRef: REF,
  missionId: MISSION,
  revision: 3,
  digest: DIGEST,
  offerDigest: OFFER_DIGEST,
  expiresAt: 1_000_900,
  scope: ['engineering_change'],
  deliveryTargets: [],
  repositoryUrl: 'https://github.com/Example/Repo',
};
const offerBody = {
  mission_id: MISSION,
  revision: 3,
  proposal_digest_sha256: DIGEST,
  offer_digest_sha256: OFFER_DIGEST,
  expires_at: 1_000_900,
  approved_action_scope: ['engineering_change'],
  approved_delivery_targets: [],
  repository_url: 'https://github.com/Example/Repo',
};
const message = (op: string, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  type: 'diMissionRequest',
  seq: 7,
  op,
  ...offerFields,
  ...(op === 'requestChanges' ? { reason: REASON } : {}),
  ...extra,
});
const refusal = (msg: Record<string, unknown>): string => {
  const parsed = parseMissionRequest(msg);
  expect(parsed.ok, JSON.stringify(msg)).toBe(false);
  return (parsed as { reason: string }).reason;
};

describe('the three non-approval outcomes: closed grammar', () => {
  it('there are exactly three, and each becomes exactly its one call to the panel', () => {
    expect(OUTCOMES.map(([op]) => op)).toEqual(['decline', 'reject', 'requestChanges']);
    for (const [op, route] of OUTCOMES) {
      const parsed = parseMissionRequest(message(op));
      const body = op === 'requestChanges' ? { ...offerBody, reason: REASON } : offerBody;
      expect(parsed, op).toEqual({
        ok: true,
        seq: 7,
        op,
        call: { method: 'POST', path: `/requests/${REF}/${route}`, body },
      });
      expect(isPanelRoute((parsed as { call: PanelCall }).call), op).toBe(true);
    }
  });

  it('no outcome carries a typed word, and only a change request carries a reason', () => {
    for (const [op] of OUTCOMES) {
      expect(refusal(message(op, { reply: 'approve' })), op).toBe(`${op} does not take "reply"`);
    }
    for (const op of ['decline', 'reject']) {
      expect(refusal(message(op, { reason: REASON })), op).toBe(`${op} does not take "reason"`);
    }
    expect(refusal(message('decide', { reason: REASON }))).toBe('decide does not take "reason"');
  });

  it('a change request needs its reason: present, not blank, at most 2000 characters, well formed, no control character', () => {
    const { reason: _omitted, ...withoutReason } = message('requestChanges');
    expect(refusal(withoutReason)).toBe('requestChanges needs "reason"');
    const bad: Array<[unknown, string]> = [
      ['', 'reason must not be empty'],
      ['   \n\t', 'reason must not be empty'],
      [5, 'reason must be text'],
      [null, 'reason must be text'],
      ['x'.repeat(2001), 'reason is longer than 2000 characters'],
      ['a\u0000b', 'reason carries a control character'],
      ['\ud800', 'reason is not well-formed Unicode text'],
    ];
    expect(bad).toHaveLength(7);
    for (const [reason, said] of bad) {
      expect(refusal(message('requestChanges', { reason })), JSON.stringify(reason)).toBe(said);
    }
    // The longest readable reason is relayed unchanged.
    const longest = 'y'.repeat(2000);
    const parsed = parseMissionRequest(message('requestChanges', { reason: longest }));
    expect((parsed as { call: PanelCall }).call.body).toMatchObject({ reason: longest });
  });

  it('every outcome binds the exact offer: each malformed offer field is refused, as for decide', () => {
    const cases: Array<[string, unknown, string]> = [
      ['requestRef', 'lr-nope', 'requestRef is not an lr- reference'],
      ['missionId', 'mn-nope', 'missionId is not an mn- id'],
      ['revision', 0, 'revision must be a positive integer'],
      ['digest', 'f'.repeat(63), 'digest is not 64 lowercase hex characters'],
      ['offerDigest', 'nope', 'offerDigest is not 64 lowercase hex characters'],
      ['expiresAt', 0, 'expiresAt must be the displayed absolute expiry'],
      ['scope', ['Not A Name'], 'scope must be a short list of names'],
      ['deliveryTargets', 'github_pr', 'deliveryTargets must be a short list of names'],
      [
        'repositoryUrl',
        'https://example.com/x/y',
        'repositoryUrl must be null or a github.com repository URL',
      ],
    ];
    expect(cases).toHaveLength(9);
    for (const [op] of OUTCOMES) {
      for (const [key, value, said] of cases) {
        expect(refusal(message(op, { [key]: value })), `${op} ${key}`).toBe(said);
      }
      const { requestRef: _r, ...missing } = message(op);
      expect(refusal(missing), op).toBe(`${op} needs "requestRef"`);
    }
  });
});

describe('the three non-approval outcomes are WRITES', () => {
  const http200 = (body: unknown): PanelResult => ({
    kind: 'http',
    status: 200,
    body,
    bodyProblem: null,
  });
  const parsedAs = (op: string) => {
    const parsed = parseMissionRequest(message(op));
    expect(parsed.ok, `${op} parses`).toBe(true);
    return parsed as Extract<typeof parsed, { ok: true }>;
  };

  it('a failure after one may have been sent leaves its outcome UNKNOWN', () => {
    const problems = ['panel_timeout', 'panel_network_error', 'panel_response_too_large'] as const;
    expect(problems).toHaveLength(3);
    for (const [op] of OUTCOMES) {
      for (const problem of problems) {
        const out = replyFromPanel(parsedAs(op), { kind: 'transport_error', problem });
        expect(out, `${op}, ${problem}`).toMatchObject({
          seq: 7,
          op,
          state: 'unknown',
          problem,
          answer: null,
        });
        expect(out.reason, `${op}, ${problem}`).toMatch(/not known/);
      }
      const unreadable = replyFromPanel(parsedAs(op), http200({}));
      expect(unreadable, op).toMatchObject({
        state: 'unknown',
        problem: 'panel_answer_unreadable',
      });
    }
  });

  it("the adapter's own refusal is relayed as a refusal with its code", () => {
    for (const [op] of OUTCOMES) {
      const out = replyFromPanel(
        parsedAs(op),
        http200({
          ok: false,
          problem: 'local_request_change_request_reason',
          reason: 'a change request needs a readable reason',
          details: {},
        }),
      );
      expect(out, op).toMatchObject({
        state: 'refused',
        problem: 'local_request_change_request_reason',
      });
    }
  });
});
