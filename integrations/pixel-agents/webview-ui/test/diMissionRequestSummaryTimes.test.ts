/**
 * R12 (the panel-requests half): the overview summary's two OPTIONAL action times, as the webview parser reads them.
 *
 * `attested_at` and `cancelled_at` arrive inside `answer`, which the contract treats as UNTRUSTED adapter data. The six keys `parseSummary` already requires fail the whole summary when
 * they are wrong. These two are OPTIONAL: absent is normal (a request with no recorded action carries none), so they are validated EACH ON ITS OWN and an invalid value means "no recorded time", never
 * "some time". A request with one unreadable time is still listed (a missing or malformed time must not make a row vanish), and its other time is still read.
 *
 * What a time means here: THAT an approval or a withdrawal was recorded, and WHEN. Not who did it, and not that anything was completed.
 */
import { describe, expect, it } from 'vitest';

import { parseOverview } from '../src/components/diMissionRequestModel.ts';

const REF = `lr-${'a'.repeat(32)}`;
const MISSION = `mn-${'b'.repeat(32)}`;
const DIGEST = 'c'.repeat(64);

const base = {
  request_ref: REF,
  mission_id: MISSION,
  revision: 1,
  created_at: 1_800_000_000,
  objective: 'Make it stable',
  proposal_digest_sha256: DIGEST,
  lifecycle: { stage: 'approved', label: 'Approved', terminal: false, actions: [], notice: null },
};
const overviewOf = (...requests: unknown[]) =>
  parseOverview({
    server_now: 1_800_001_000,
    requests_truncated: false,
    capabilities: {
      run_available: true,
      repository_url: null,
      delivery_authority: 'none',
      decision: { transport: 'operator_attested_relay', proof: 'p', reply: 'approve', approval_valid_seconds: 900 },
      scope_meanings: {},
    },
    requests,
    conversations: [],
  })!;
const times = (extra: Record<string, unknown>) => {
  const [only] = overviewOf({ ...base, ...extra }).requests;
  return only === undefined ? null : { attestedAt: only.attestedAt, cancelledAt: only.cancelledAt };
};

describe('the two optional action times of a panel request summary', () => {
  it('carries a recorded approval time and a recorded withdrawal time, kept apart', () => {
    expect(times({ attested_at: 1_800_000_500 })).toEqual({ attestedAt: 1_800_000_500, cancelledAt: null });
    expect(times({ cancelled_at: 1_800_000_900 })).toEqual({ attestedAt: null, cancelledAt: 1_800_000_900 });
    expect(times({ attested_at: 10_000_000_000, cancelled_at: 10_000_000_001 })).toEqual({
      attestedAt: 10_000_000_000,
      cancelledAt: 10_000_000_001,
    });
  });

  it('ABSENT is normal: the row is still listed, with no recorded time (an older adapter sends neither key)', () => {
    expect(times({})).toEqual({ attestedAt: null, cancelledAt: null });
    expect(times({ attested_at: null, cancelled_at: null })).toEqual({ attestedAt: null, cancelledAt: null });
  });

  it('a present-and-MALFORMED time is rejected INDIVIDUALLY: the row stays listed, the value means "no recorded time"', () => {
    const malformed: unknown[] = [
      'yesterday',
      '1800000500',
      1_800_000_500.5,
      Number.NaN,
      Number.POSITIVE_INFINITY,
      true,
      {},
      [],
      0,
      -1,
      -1_800_000_500,
      1e300,
      Number.MAX_SAFE_INTEGER,
      8_640_000_000_001, // one second past the last instant a JavaScript Date can represent
    ];
    for (const bad of malformed) {
      expect(times({ attested_at: bad }), `attested_at ${String(bad)}`).toEqual({ attestedAt: null, cancelledAt: null });
      expect(times({ cancelled_at: bad }), `cancelled_at ${String(bad)}`).toEqual({ attestedAt: null, cancelledAt: null });
    }
  });

  it('one malformed time does not take the other with it, and neither takes the row', () => {
    expect(times({ attested_at: 'bad', cancelled_at: 1_800_000_900 })).toEqual({ attestedAt: null, cancelledAt: 1_800_000_900 });
    expect(times({ attested_at: 1_800_000_500, cancelled_at: {} })).toEqual({ attestedAt: 1_800_000_500, cancelledAt: null });
  });

  it('the last instant a Date can represent is accepted (the bound is the platform\'s, not a guess about the present)', () => {
    expect(times({ attested_at: 8_640_000_000_000 })).toEqual({ attestedAt: 8_640_000_000_000, cancelledAt: null });
  });

  it('the six required keys still fail the WHOLE summary, a valid time notwithstanding', () => {
    const o = overviewOf({ ...base, attested_at: 1_800_000_500, request_ref: undefined }, { ...base, attested_at: 1_800_000_500 });
    expect(o.requests).toHaveLength(1);
    expect(o.requests[0]!.attestedAt).toBe(1_800_000_500);
  });
});
