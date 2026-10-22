/**
 * The webview's identity-keyed SEAT PREFERENCES (DOM-free, unit-tested): which seat each real character (by canonical identity, never by the
 * process-local number) last sat in. Loaded from the server at the handshake, merged with where characters sit now, saved when it changed.
 * Bounded by the shared contract (core/src/diSeatPreferences.ts): the map keeps insertion order, oldest first, and forgets the longest-away
 * identity first (by entry count AND by record bytes). An entry that breaks its bounds is refused for that entry only. A key is the identity exactly:
 * never truncated, normalised or case-folded. Entries for identities that are away are KEPT, so a team member that leaves and rejoins gets its seat back.
 */
import type { DiSeatPreferenceEntry, DiSeatPreferences } from '../../../../core/src/diSeatPreferences.js';
import {
  DI_SEAT_PREFERENCES_VERSION,
  diSeatPreferenceEntryProblem,
  fitDiSeatPreferences,
  sanitizeDiSeatPreferences,
} from '../../../../core/src/diSeatPreferences.js';

/** identity key (JSON of the five-field tuple) → seat id; insertion order = oldest first. */
export type SeatPrefs = Map<string, string>;

/** The admissible preferences in a server record (each entry that satisfies its bounds), or null when it is not a record at all (ignored whole). */
export function seatPrefsFromRecord(state: unknown, onRefused?: (why: string) => void): SeatPrefs | null {
  const clean = sanitizeDiSeatPreferences(state);
  if (clean === null) return null;
  for (const why of clean.refused) onRefused?.(why);
  const prefs: SeatPrefs = new Map();
  for (const entry of clean.state.entries) prefs.set(JSON.stringify(entry.i), entry.s);
  return prefs;
}

/** The contract record for these preferences. */
export function seatPrefsToRecord(prefs: ReadonlyMap<string, string>): DiSeatPreferences {
  return {
    v: DI_SEAT_PREFERENCES_VERSION,
    entries: [...prefs].map(([key, seat]) => ({
      i: JSON.parse(key) as DiSeatPreferences['entries'][number]['i'],
      s: seat,
    })),
  };
}

export interface SeatPrefsMerge {
  prefs: SeatPrefs;
  /** Absent identities (history) evicted, oldest first, to fit the record budget. */
  historyEvicted: number;
  /**
   * Identities PRESENT now that do not fit the record budget even with no history left: a deterministic subset (ascending stable identity key) is kept and this
   * many were dropped. Zero in every ordinary case; whenever it is not zero it is reported, never silent.
   */
  activeDropped: number;
}

/**
 * `prev` updated with where characters sit now (`current`: identity key -> seat id; the identities HELD NOW).
 * EVICTION PRIORITY: the identities present in `current` are PROTECTED — a continuously present agent is never forgotten for being unchanged. Absent history is
 * evicted first, oldest first (its order is the order it was last present or changed). The serialised order is well defined: history first, then the present
 * identities (those that were already known, in their own order, then new or changed ones), so "newest last" means "most relevant last". Only if the present
 * identities ALONE exceed the record budget (entries or bytes) is a deterministic subset kept — the first by ascending stable identity key, independent of
 * discovery order — and the number dropped is returned (`activeDropped`) for the caller to report. An entry that breaks its bounds is refused (`onRefused`),
 * nothing stored; its previous entry, if any, stays as history.
 */
export function mergeSeatPrefsReport(
  prev: ReadonlyMap<string, string>,
  current: ReadonlyMap<string, string>,
  onRefused?: (why: string) => void,
): SeatPrefsMerge {
  const admissible = new Map<string, string>();
  let changed = false;
  for (const [key, seat] of current) {
    if (prev.get(key) === seat) {
      admissible.set(key, seat);
      continue;
    }
    let tuple: unknown;
    try {
      tuple = JSON.parse(key);
    } catch {
      onRefused?.('a seat preference key is not an identity tuple');
      continue;
    }
    const problem = diSeatPreferenceEntryProblem({ i: tuple, s: seat });
    if (problem !== null) {
      onRefused?.(problem);
      continue;
    }
    admissible.set(key, seat);
    changed = true;
  }
  const prevOrder = new Map([...prev.keys()].map((k, n) => [k, n] as const));
  const history = [...prev].filter(([key]) => !admissible.has(key));
  const active = [...admissible]
    .map(([key, seat], n) => ({ key, seat, isNew: prev.get(key) !== seat, was: prevOrder.get(key) ?? Infinity, n }))
    .sort((a, b) => Number(a.isNew) - Number(b.isNew) || a.was - b.was || a.n - b.n)
    .map(({ key, seat }) => [key, seat] as const);
  const ordered = [...history, ...active];
  const asEntry = ([key, seat]: readonly [string, string]): DiSeatPreferenceEntry => ({
    i: JSON.parse(key) as DiSeatPreferenceEntry['i'],
    s: seat,
  });
  // nothing new and nothing absent beyond what prev already held within its budget: only the order is refreshed
  if (!changed && ordered.length <= prev.size) return { prefs: new Map(ordered), historyEvicted: 0, activeDropped: 0 };
  const fitted = fitDiSeatPreferences(ordered.map(asEntry));
  if (fitted.evicted <= history.length)
    return { prefs: new Map(ordered.slice(fitted.evicted)), historyEvicted: fitted.evicted, activeDropped: 0 };
  // the present identities alone are over the budget: keep a deterministic subset by ascending stable key and say how many were dropped
  const byKey = [...active].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  // the fitter keeps the TAIL of a list, so fitting the descending list keeps exactly the lowest keys that fit both budgets
  const keep = fitDiSeatPreferences([...byKey].reverse().map(asEntry)).entries.length;
  return {
    prefs: new Map(byKey.slice(0, keep)),
    historyEvicted: history.length,
    activeDropped: byKey.length - keep,
  };
}

/** `mergeSeatPrefsReport(...).prefs`: the merged preferences, for callers that do not report the budget. */
export function mergeSeatPrefs(
  prev: ReadonlyMap<string, string>,
  current: ReadonlyMap<string, string>,
  onRefused?: (why: string) => void,
): SeatPrefs {
  return mergeSeatPrefsReport(prev, current, onRefused).prefs;
}

/** Same identities with the same seats (recency order is not content). */
export function sameSeatPrefs(a: ReadonlyMap<string, string>, b: ReadonlyMap<string, string>): boolean {
  if (a.size !== b.size) return false;
  for (const [key, seat] of a) if (b.get(key) !== seat) return false;
  return true;
}
