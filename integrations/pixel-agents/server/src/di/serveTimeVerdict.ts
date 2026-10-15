/**
 * The backend's serve-time freshness verdict, read from response headers.
 *
 * The verdict is an INPUT this consumer receives — computed by the backend's
 * canonical `response_freshness` immediately before it wrote the body. The consumer
 * never reimplements that rule. This is the single parsing boundary for the two
 * headers (CONTRACT-v3 "Headers"): retarget here if their semantics change.
 *
 * Header convention (deliberately the OPPOSITE of the body's): headers have no
 * null, so `X-DI-Freshness-Problem` is OMITTED when the problem is null. An absent
 * problem header therefore means `problem: null`. An absent HEALTH header is not
 * "fresh" — it is `health: null` (no verdict), which never renders as live.
 */

import type { IncomingHttpHeaders } from 'http';

import type { Classified, Health } from './catalogContract.js';
import { classify, HEALTH_STATES } from './catalogContract.js';

export const FRESHNESS_HEALTH_HEADER = 'x-di-freshness-health';
export const FRESHNESS_PROBLEM_HEADER = 'x-di-freshness-problem';

export interface ServeTimeVerdict {
  /** null = the header was absent: no verdict at all. */
  health: Classified<Health> | null;
  /** null = the header was omitted, which is how the backend encodes a null problem. */
  problem: string | null;
}

function single(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value.length === 1 ? value[0] : undefined;
  return value;
}

export function readServeTimeVerdict(headers: IncomingHttpHeaders): ServeTimeVerdict {
  const health = single(headers[FRESHNESS_HEALTH_HEADER]);
  const problem = single(headers[FRESHNESS_PROBLEM_HEADER]);
  return {
    health: health === undefined ? null : classify(HEALTH_STATES, health),
    problem: problem === undefined ? null : problem,
  };
}

/** True only for an explicit, recognized `fresh` verdict. */
export function verdictIsFresh(verdict: ServeTimeVerdict): boolean {
  return verdict.health !== null && verdict.health.known && verdict.health.value === 'fresh';
}
