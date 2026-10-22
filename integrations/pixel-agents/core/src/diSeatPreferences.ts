/**
 * THE DI SEAT PREFERENCES — the ONE definition of what the DI office may save about WHERE each real character likes to sit, shared by the server
 * (checked before anything is written, and again when read) and the webview (checked again before anything is applied).
 *
 * A preference is keyed by the character's CANONICAL IDENTITY — the same explicit tuple the office already uses for its characters:
 * [coverage, sourceId, missionRef, taskId | null, memberRef] — never by the process-local character NUMBER, which restarts at 1 with every server
 * process and could attach a saved seat to a different agent. The value is a seat id of the loaded layout. A key is NEVER truncated, normalised or
 * case-folded: it is the identity exactly, or the entry is refused.
 *
 * EVERY FIELD'S BOUND IS THE EXISTING BOUND ALONG THE REAL PATH (catalog contract → server DI projection → identity tuple), not a new one:
 * - coverage: the closed set the DI identity map accepts (server/src/di/identityMap.ts COVERAGES).
 * - sourceId, missionRef, taskId, and a runtime agent's rootRef/agentRef: the catalog contract's text rule `require_text` — a non-empty `str` without NUL,
 *   at most MAX_SNAPSHOT_TEXT_CHARS = 1024 (mission_sources/contract.py:197, :233-245), applied to mission_ref (contract_v2.py:1442), task_id / logical
 *   (contract_v2.py:1282-1283) and the runtime agent's source_id / root_ref / agent_ref (contract.py:569-571). The source registry's own source id is
 *   shorter still (registry.py MAX_SOURCE_ID_CHARS = 64), so every registered id fits.
 * - memberRef of a team member: `<mission_ref>#<task_id>#<logical>` (contract_v2.py:1284, checked there by equality, not by a separate text rule), so it is
 *   at most 3 × 1024 + 2 characters.
 * - the seat id: the layout's own id bound, `isBoundedId` / LAYOUT_ID_MAX_LENGTH = 128 (core/src/layoutLimits.ts), the same bound the existing
 *   `saveAgentSeats` check applies to a seat id (server/src/constants.ts DI_ID_MAX_LENGTH).
 * The character rules are those same rules: empty and NUL are refused, nothing else (no extra control-character ban: the contract has none).
 *
 * "Characters" are CODE POINTS, as Python's `len` counts them (a UTF-16 unit count would wrongly refuse a valid identity made of astral characters); the count
 * stops as soon as it passes the bound, so it never reads further into a hostile string than the bound allows.
 *
 * The WHOLE record stays bounded: at most DI_SEAT_PREFERENCES_MAX_ENTRIES entries and DI_SEAT_PREFERENCES_MAX_BYTES bytes, oldest first (the newest is last).
 * Two separate concerns:
 * - EXTERNAL INTAKE (`intakeDiSeatPreferences`, what a received save passes through): cheap and bounded before anything is built or copied — more than MAX_ENTRIES
 *   entries, any field outside its bound, a repeated identity, or an estimated size over DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES is REFUSED WHOLE with a closed,
 *   non-echoing reason, and a refused intake writes nothing. A correct client already fits; an in-bound save a little over the byte budget is accepted and its
 *   OLDEST entries are evicted until it fits.
 * - INTERNAL FITTING (`fitDiSeatPreferences`, `sanitizeDiSeatPreferences`: the webview's own map, and the reader of a file that is already byte-bounded on read):
 *   the OLDEST entries are evicted, deterministically, until the record fits; an entry that breaks its own bounds is refused for that entry only.
 * It holds no position, time, count, Mission state or anything canonical: it is local seating preference, grants nothing and decides nothing about any Mission.
 */
import { isBoundedId, LAYOUT_ID_MAX_LENGTH } from './layoutLimits.js';

export const DI_SEAT_PREFERENCES_VERSION = 1;
export const DI_SEAT_PREFERENCES_MAX_ENTRIES = 512;
/** Hard ceiling of a saved record: its UTF-8 compact JSON. */
export const DI_SEAT_PREFERENCES_MAX_BYTES = 131_072;
/**
 * The most a RECEIVED save may weigh (an estimate over its entries, stopped at the first violation) before it is refused unread: four times the record budget,
 * so a client slightly over budget is trimmed, not refused, while no frame, however large, makes the intake do more than this much work.
 */
export const DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES = 4 * DI_SEAT_PREFERENCES_MAX_BYTES;
/** mission_sources/contract.py MAX_SNAPSHOT_TEXT_CHARS: the text bound of every identity component the catalog contract validates. */
export const DI_SEAT_PREFERENCE_TEXT_MAX_CHARS = 1024;
/** `<mission_ref>#<task_id>#<logical>`: three contract-bounded texts and two separators. */
export const DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS = 3 * DI_SEAT_PREFERENCE_TEXT_MAX_CHARS + 2;
/** The layout's own id bound (also DI_ID_MAX_LENGTH): what a seat id may be. */
export const DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS = LAYOUT_ID_MAX_LENGTH;

/** The closed set of coverages a character identity may have (the DI identity map's own set). */
export const DI_SEAT_PREFERENCE_COVERAGES = ['direct', 'child', 'runtime'] as const;

export interface DiSeatPreferenceEntry {
  /** [coverage, sourceId, missionRef, taskId | null, memberRef] */
  i: [string, string, string, string | null, string];
  /** The preferred seat id in the layout. */
  s: string;
}

export interface DiSeatPreferences {
  v: typeof DI_SEAT_PREFERENCES_VERSION;
  entries: DiSeatPreferenceEntry[];
}

/** The empty record: what a first load starts from. */
export const EMPTY_DI_SEAT_PREFERENCES: Readonly<DiSeatPreferences> = Object.freeze({
  v: DI_SEAT_PREFERENCES_VERSION,
  entries: [],
});

/**
 * The existing `require_text` rule: a non-empty string without NUL within `max` CODE POINTS (Python's `len`). One pass that stops at the first NUL or as
 * soon as the count passes `max`; a string longer than `2 * max` UTF-16 units is over the bound without being read at all (a code point is at most two
 * units). Nothing is changed, trimmed or normalised.
 */
function text(v: unknown, max: number): v is string {
  if (typeof v !== 'string' || v.length === 0 || v.length > 2 * max) return false;
  let count = 0;
  for (let i = 0; i < v.length; i++) {
    const c = v.charCodeAt(i);
    if (c === 0) return false;
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < v.length) {
      const d = v.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) i++; // a surrogate pair is ONE code point
    }
    if (++count > max) return false;
  }
  return true;
}

/** UTF-8 byte length of a string (surrogate pairs count as four). */
function utf8Bytes(s: string): number {
  let bytes = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) bytes += 1;
    else if (c < 0x800) bytes += 2;
    else if (c >= 0xd800 && c <= 0xdbff) {
      bytes += 4;
      i++;
    } else bytes += 3;
  }
  return bytes;
}

/** UTF-8 byte length of the compact JSON of `state` (Infinity when it cannot be serialised). */
export function diSeatPreferencesBytes(state: unknown): number {
  try {
    return utf8Bytes(JSON.stringify(state));
  } catch {
    return Infinity;
  }
}

/** null when the entry is admissible (its identity components and seat within their existing bounds), else why it is refused. */
export function diSeatPreferenceEntryProblem(entry: unknown): string | null {
  if (typeof entry !== 'object' || entry === null || Array.isArray(entry))
    return 'a seat preference is not an object';
  const e = entry as Record<string, unknown>;
  const ek = Object.keys(e);
  if (ek.length !== 2 || !ek.includes('i') || !ek.includes('s'))
    return 'a seat preference has keys outside the contract';
  if (!Array.isArray(e.i) || e.i.length !== 5) return 'a seat preference identity is not five fields';
  const [coverage, sourceId, missionRef, taskId, memberRef] = e.i as unknown[];
  if (!DI_SEAT_PREFERENCE_COVERAGES.includes(coverage as never))
    return 'a seat preference identity has an unsupported coverage';
  if (!text(sourceId, DI_SEAT_PREFERENCE_TEXT_MAX_CHARS))
    return 'a seat preference sourceId is empty, holds NUL or is over its bound';
  if (!text(missionRef, DI_SEAT_PREFERENCE_TEXT_MAX_CHARS))
    return 'a seat preference missionRef is empty, holds NUL or is over its bound';
  if (taskId !== null && !text(taskId, DI_SEAT_PREFERENCE_TEXT_MAX_CHARS))
    return 'a seat preference taskId is not null, or is empty, holds NUL or is over its bound';
  if (!text(memberRef, DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS))
    return 'a seat preference memberRef is empty, holds NUL or is over its bound';
  if (!isBoundedId(e.s, DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS))
    return 'a seat preference seat is empty or over the layout id bound';
  return null;
}

/** Bytes one entry adds to the record's compact JSON (without the separating comma). */
export function diSeatPreferenceEntryBytes(entry: DiSeatPreferenceEntry): number {
  return utf8Bytes(JSON.stringify(entry));
}

/** Bytes of the record around its entries: `{"v":1,"entries":[]}`. */
const RECORD_OVERHEAD_BYTES = utf8Bytes(JSON.stringify({ v: DI_SEAT_PREFERENCES_VERSION, entries: [] }));

/**
 * The entries trimmed to the record budget: while there are more than MAX_ENTRIES or the record would exceed MAX_BYTES, the OLDEST entry (the first) is
 * evicted. Deterministic; the newest entries are kept; nothing is truncated or rewritten. `evicted` counts what was dropped.
 */
export function fitDiSeatPreferences<T extends DiSeatPreferenceEntry>(
  entries: readonly T[],
): { entries: T[]; evicted: number } {
  const sizes = entries.map((e) => diSeatPreferenceEntryBytes(e));
  let total = RECORD_OVERHEAD_BYTES + sizes.reduce((a, b) => a + b, 0) + Math.max(0, entries.length - 1);
  let first = 0;
  while (
    first < entries.length &&
    (entries.length - first > DI_SEAT_PREFERENCES_MAX_ENTRIES || total > DI_SEAT_PREFERENCES_MAX_BYTES)
  ) {
    total -= sizes[first] + (entries.length - first > 1 ? 1 : 0);
    first++;
  }
  return { entries: entries.slice(first), evicted: first };
}

export interface SanitizedDiSeatPreferences {
  state: DiSeatPreferences;
  /** Why each refused entry was refused (that entry only), in input order. */
  refused: string[];
  /** Entries evicted (oldest first) to fit the record budget. */
  evicted: number;
}

/**
 * INTERNAL FITTING for inputs that are already bounded (a file read after its byte-size check, the webview's own map): the admissible content of a record: null when it is not a record at all (not an object, wrong keys or version, entries not a list);
 * otherwise every entry that satisfies its bounds, a repeated identity keeping its LAST (newest) occurrence, trimmed to the budget oldest-first. An entry
 * that breaks its bounds is refused for that entry only and the refusal is reported — the rest of the record stands.
 */
export function sanitizeDiSeatPreferences(state: unknown): SanitizedDiSeatPreferences | null {
  if (typeof state !== 'object' || state === null || Array.isArray(state)) return null;
  const o = state as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== 2 || !keys.includes('v') || !keys.includes('entries')) return null;
  if (o.v !== DI_SEAT_PREFERENCES_VERSION || !Array.isArray(o.entries)) return null;
  const refused: string[] = [];
  const lastOf = new Map<string, number>();
  o.entries.forEach((entry, index) => {
    if (diSeatPreferenceEntryProblem(entry) === null)
      lastOf.set(JSON.stringify((entry as DiSeatPreferenceEntry).i), index);
  });
  const kept: DiSeatPreferenceEntry[] = [];
  o.entries.forEach((entry, index) => {
    const problem = diSeatPreferenceEntryProblem(entry);
    if (problem !== null) {
      refused.push(`entry ${index}: ${problem}`);
      return;
    }
    const e = entry as DiSeatPreferenceEntry;
    if (lastOf.get(JSON.stringify(e.i)) !== index) {
      refused.push(`entry ${index}: its identity is listed again later; the later entry stands`);
      return;
    }
    kept.push({ i: [...e.i] as DiSeatPreferenceEntry['i'], s: e.s });
  });
  const fitted = fitDiSeatPreferences(kept);
  return { state: { v: DI_SEAT_PREFERENCES_VERSION, entries: fitted.entries }, refused, evicted: fitted.evicted };
}

/** Why a received save is refused — a CLOSED vocabulary: never an echo of anything the sender wrote. */
export const DI_SEAT_PREFERENCES_REFUSALS = {
  notARecord: 'seat preferences are not a record of the contract',
  tooMany: 'seat preferences hold more entries than the record allows',
  duplicate: 'a seat preference identity is listed twice',
  tooLarge: 'seat preferences are over the intake size bound',
} as const;

export type DiSeatPreferencesIntake =
  | { ok: true; entries: DiSeatPreferenceEntry[]; evicted: number }
  | { ok: false; reason: string };

/**
 * EXTERNAL INTAKE of a received save. Cheap and bounded: the record shape, then the entry COUNT (before any entry is read), then each entry's bounds and a
 * running size estimate, stopping at the first violation; nothing is built until every check has passed. A refusal is whole (nothing may be written) and its
 * reason is closed. An accepted save is copied once (at most MAX_ENTRIES entries) and its oldest entries are evicted if it is over the byte budget.
 */
export function intakeDiSeatPreferences(state: unknown): DiSeatPreferencesIntake {
  const refuse = (reason: string): DiSeatPreferencesIntake => ({ ok: false, reason });
  if (typeof state !== 'object' || state === null || Array.isArray(state)) return refuse(DI_SEAT_PREFERENCES_REFUSALS.notARecord);
  const o = state as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== 2 || !keys.includes('v') || !keys.includes('entries') || o.v !== DI_SEAT_PREFERENCES_VERSION || !Array.isArray(o.entries))
    return refuse(DI_SEAT_PREFERENCES_REFUSALS.notARecord);
  const entries = o.entries as unknown[];
  if (entries.length > DI_SEAT_PREFERENCES_MAX_ENTRIES) return refuse(DI_SEAT_PREFERENCES_REFUSALS.tooMany);
  let estimate = RECORD_OVERHEAD_BYTES;
  const seen = new Set<string>();
  for (const entry of entries) {
    const problem = diSeatPreferenceEntryProblem(entry);
    if (problem !== null) return refuse(problem); // a fixed sentence naming the rule, never the value
    const e = entry as DiSeatPreferenceEntry;
    estimate += diSeatPreferenceEntryBytes(e) + 1;
    if (estimate > DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES) return refuse(DI_SEAT_PREFERENCES_REFUSALS.tooLarge);
    const identity = JSON.stringify(e.i);
    if (seen.has(identity)) return refuse(DI_SEAT_PREFERENCES_REFUSALS.duplicate);
    seen.add(identity);
  }
  const copied = (entries as DiSeatPreferenceEntry[]).map((e) => ({ i: [...e.i] as DiSeatPreferenceEntry['i'], s: e.s }));
  const fitted = fitDiSeatPreferences(copied);
  return { ok: true, entries: fitted.entries, evicted: fitted.evicted };
}

/** null when the value is an admissible record exactly as it stands (every entry valid, no repeat, within the record budget), else the closed reason. */
export function checkDiSeatPreferences(state: unknown): string | null {
  const r = intakeDiSeatPreferences(state);
  if (!r.ok) return r.reason;
  return r.evicted > 0 ? 'seat preferences are over the record budget' : null;
}
