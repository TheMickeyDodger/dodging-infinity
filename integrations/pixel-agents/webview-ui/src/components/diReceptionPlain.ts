/**
 * The reception inbox's PLAIN first layer: what happened, why it needs a person, what they can do
 * next, and what could not be checked — each phrased from the canonical cause or reason the server's
 * projection already carries, never from anything invented here. Pure presentation over
 * `DiReception`: it reads nothing, derives no outcome and adds no action. The exact causes, reason
 * codes and identities stay in the disclosures that already hold them.
 *
 * A cause id this table does not know (a newer producer) falls back to the cause's own label and a
 * generic reason, so a new cause is shown, never dropped.
 */
import type {
  DiReception,
  DiReceptionCategory,
  DiReceptionCategoryId,
  DiReceptionEntry,
} from '../../../core/src/messages.js';
import { formatObserved } from './diOfficeFormat.js';
import { utcStamp } from './diReceptionFormat.js';

/** One canonical cause in plain words: what happened and why it needs the person. */
export interface PlainCause {
  happened: string;
  why: string;
}

/** Every cause id `server/src/di/receptionDesk.ts` emits (a test keeps this in step with it). */
export const PLAIN_CAUSES: Readonly<Record<string, PlainCause>> = {
  mission_core_blockers: {
    happened: 'Mission Core lists active blockers on this Mission.',
    why: 'A blocker is something Mission Core records as standing in the Mission’s way.',
  },
  member_activity_blocked: {
    happened: 'A team member reports that it is blocked.',
    why: 'The member’s own terminal reports blocked; this office does not say what it is waiting for.',
  },
  di_progression_paused: {
    happened: 'DI progression of this Mission is paused.',
    why: 'Mission Core’s pause flag is set, so DI will not advance it until it is resumed. This does not mean external work stopped.',
  },
  workflow_phase_blocked: {
    happened: 'This workflow is blocked and has stopped.',
    why: 'Blocked is a terminal, fail-closed state: the workflow does not continue on its own.',
  },
  completion_holds: {
    happened: 'Mission Core is holding back verified success for this Mission.',
    why: 'Until those holds are cleared the Mission cannot be treated as verified.',
  },
  awaiting_decision: {
    happened: 'This Mission is waiting for a human decision.',
    why: 'Mission Core records that a decision is pending, and only a person can make it.',
  },
  workflow_needs_reauthorization: {
    happened: 'This work needs a fresh authorization before it can continue.',
    why: 'Only a person can authorize it again; it cannot continue until then.',
  },
  completion_contradicted: {
    happened: 'A source’s report contradicts Mission Core’s record of this Mission.',
    why: 'The report and the record disagree, so the reported result is not confirmed.',
  },
  verified_success_withdrawn: {
    happened: 'A verified success of this Mission was withdrawn.',
    why: 'Mission Core had verified it and no longer does.',
  },
  evidence_contradicted_hold: {
    happened: 'The proof evidence is contradicted or does not match.',
    why: 'Mission Core holds the Mission’s success back until the evidence is resolved.',
  },
  evidence_drifted_hold: {
    happened: 'The accepted evidence has drifted from the candidate or baseline.',
    why: 'The evidence no longer matches what was reviewed.',
  },
  review_record_refused: {
    happened: 'The canonical review record was refused (it was not admitted).',
    why: 'There is no admitted review to rely on.',
  },
  review_integrity_not_corroborated: {
    happened: 'The review record could not be linked to the work it reviews.',
    why: 'Its integrity is not corroborated, so it cannot be relied on.',
  },
  review_verdict_reject: {
    happened: 'The review verdict is REJECT.',
    why: 'The reviewer did not approve this work.',
  },
  reported_complete_not_verified: {
    happened: 'The task source reports this Mission complete.',
    why: 'That is a report, not a verification: Mission Core has not verified success, so a person should review it.',
  },
};

const GENERIC_WHY = 'The source reports something here that a person should look at.';

/**
 * Every hold code Mission Core defines (`mission/observation.py` HOLD_*; a test keeps this in step with
 * it), in plain words taken from the code's own name and Mission Core's comments. A code NOT in this
 * table is shown as "reported reason: <code>" — never guessed.
 */
export const PLAIN_HOLDS: Readonly<Record<string, string>> = {
  no_canonical_closure: 'No canonical closure has been recorded for this Mission.',
  proof_not_satisfied: 'The Mission’s proof is not satisfied.',
  evidence_contradicted: 'The proof evidence is contradicted or does not match.',
  candidate_drifted: 'The candidate has drifted from what was accepted.',
  baseline_drifted: 'The baseline has drifted from what was accepted.',
  candidate_unconfirmed:
    'A candidate report was received that does not name a recorded artifact, so the candidate is unconfirmed.',
  baseline_unconfirmed: 'The report does not confirm the anchored baseline.',
  hard_blocker_active: 'A hard blocker is active.',
  prerequisites_not_satisfied: 'Prerequisites for this Mission are not satisfied.',
  review_not_approved: 'The review has not approved this work.',
  review_unknown: 'The review holds no fact about this work.',
  review_unavailable: 'The review source could not be consulted.',
  review_inapplicable: 'The review does not apply to this candidate.',
  contract_not_current: 'The Mission’s contract is not current.',
  task_contradicts: 'The task record contradicts this Mission.',
};

/** The detail label Mission Core's information holds carry (`receptionDesk.ts` INFORMATION_HOLD_LABEL). */
export const INFORMATION_HOLD_LABEL = 'information hold (not blocking, per Mission Core)';
const INFORMATION_ONLY = ' (information only: it does not block by itself)';

/** A blocked member's detail line is `ref · logical (role) · agent · classification (reason)`. */
function plainMember(value: string): string {
  const parts = value.split(' · ');
  return parts.length >= 4 && parts[1] !== ''
    ? `${parts[1]} reports it is blocked.`
    : `Member reporting blocked activity: ${value}`;
}

/** Each cause of an entry in plain words, in the projection's order, each cause once. */
export function plainCauses(entry: DiReceptionEntry): PlainCause[] {
  return entry.causes.map((c) => PLAIN_CAUSES[c.cause] ?? { happened: c.label, why: GENERIC_WHY });
}

/**
 * The source-supported reasons behind an entry, in plain words, each once: its hold codes (an
 * unmapped one reads "reported reason: <code>"), the blocker text the source reported (verbatim), a
 * hard blocker, and a member reporting blocked activity. They are read from the cause details the
 * projection already carries; the codes themselves stay in the entry's exact-record disclosure.
 */
export function plainReasons(entry: DiReceptionEntry): string[] {
  const out: string[] = [];
  const add = (text: string) => {
    if (!out.includes(text)) out.push(text);
  };
  for (const cause of entry.causes)
    for (const d of cause.details) {
      if (d.value === null) continue;
      if (d.label === 'hold' || d.label === INFORMATION_HOLD_LABEL) {
        const base = PLAIN_HOLDS[d.value] ?? `reported reason: ${d.value}`;
        add(d.label === INFORMATION_HOLD_LABEL ? `${base}${INFORMATION_ONLY}` : base);
      } else if (d.label === 'blocker') add(plainBlocker(d.value));
      else if (d.label === 'hard blocker active' && d.value === 'true')
        add(PLAIN_HOLDS['hard_blocker_active']);
      else if (d.label === 'member') add(plainMember(d.value));
    }
  const said = new Set(plainCauses(entry).map((c) => c.happened));
  return out.filter((t) => !said.has(t));
}

/**
 * A reported blocker in plain words. Mission Core's active blockers arrive as a JSON record
 * (`{blocker_id, key, severity, opened_at}`): the plain facts (what it is called, how severe, when it
 * opened) lead, and the record itself — including the blocker id — stays in the exact-record
 * disclosure. Plain text the source wrote is shown verbatim; a record this cannot read says so and
 * never prints raw JSON on the first layer.
 */
export function plainBlocker(value: string): string {
  const text = value.trim();
  if (!text.startsWith('{') && !text.startsWith('[')) return `Reported blocker: ${value}`;
  try {
    const o: unknown = JSON.parse(text);
    if (typeof o === 'object' && o !== null && !Array.isArray(o)) {
      const r = o as Record<string, unknown>;
      if (typeof r.key === 'string' && r.key.trim() !== '') {
        const severity =
          typeof r.severity === 'string' && r.severity.trim() !== '' ? ` (${r.severity})` : '';
        const opened =
          typeof r.opened_at === 'number' && Number.isFinite(r.opened_at) && r.opened_at > 0
            ? `, opened ${utcStamp(r.opened_at > 1e11 ? Math.floor(r.opened_at / 1000) : r.opened_at)}`
            : '';
        return `Reported blocker: ${r.key}${severity}${opened}`;
      }
    }
  } catch {
    // Not readable as a record: said plainly below.
  }
  return 'A blocker is reported; its exact record is in the details below.';
}

/** The reasons why this entry needs the person, each said once. */
export function plainWhy(entry: DiReceptionEntry): string[] {
  return [...new Set(plainCauses(entry).map((c) => c.why))];
}

// ── the FIRST LAYER of a card ─────────────────────────────────────────────────────────────────
//
// Short, source-backed sentences with NO identifiers or codes above the disclosure: no blocker id or key, no raw severity literal, no request/conversation
// ref, no stage literal, no digest, no reason code. Severity appears only as a word and only where the source gave one. Everything exact (the blocker
// record itself, the hold reasons in plain words, the causes with their details, the route text) is inside the card's disclosure.

/** What the blockers cause of an entry says, read from its details: how many, a uniform severity WORD, and the open date of a single one. */
interface BlockerFacts {
  count: number;
  /** a lower-case alphabetic severity word, only when every parsed record gives the same one */
  severity: string | null;
  openedAt: number | null;
  /** a blocker the source wrote as plain text (verbatim), when there is exactly one and it is the only blocker */
  text: string | null;
}

function blockerFacts(entry: DiReceptionEntry): BlockerFacts | null {
  const cause = entry.causes.find((c) => c.cause === 'mission_core_blockers');
  if (cause === undefined) return null;
  const details = cause.details.filter((d) => d.label === 'blocker' && d.value !== null);
  let reported = 0;
  const severities = new Set<string>();
  let sawUnreadable = false;
  let openedAt: number | null = null;
  let text: string | null = null;
  for (const d of details) {
    const value = (d.value as string).trim();
    reported++;
    if (!value.startsWith('{')) {
      text = value;
      continue;
    }
    try {
      const o: unknown = JSON.parse(value);
      if (typeof o !== 'object' || o === null || Array.isArray(o)) {
        sawUnreadable = true;
        continue;
      }
      const r = o as Record<string, unknown>;
      if (typeof r.severity === 'string' && /^[A-Za-z]{2,20}$/.test(r.severity.trim()))
        severities.add(r.severity.trim().toLowerCase());
      else sawUnreadable = true;
      if (typeof r.opened_at === 'number' && Number.isFinite(r.opened_at) && r.opened_at > 0)
        openedAt = r.opened_at > 1e11 ? Math.floor(r.opened_at / 1000) : r.opened_at;
    } catch {
      sawUnreadable = true;
    }
  }
  // the source states the total as "N", "N (showing M; K omitted by the display cap)" or "unknown (not a list)": read the leading integer only
  const statedText = cause.details.find((d) => d.label === 'active blockers')?.value ?? '';
  const stated = /^\d+(?!\d)/.test(statedText) ? Number(/^\d+/.exec(statedText)![0]) : NaN;
  const count = Number.isInteger(stated) && stated >= reported ? stated : reported;
  return {
    count,
    severity: severities.size === 1 && !sawUnreadable && reported === count ? [...severities][0] : null,
    openedAt: count === 1 ? openedAt : null,
    text: count === 1 ? text : null,
  };
}

/** The blockers cause in one sentence from the source's own facts ("Mission Core reports an active hard blocker, opened <date>."). */
export function blockerSentence(entry: DiReceptionEntry): string | null {
  const f = blockerFacts(entry);
  if (f === null) return null;
  if (f.count === 0) return 'Mission Core reports an active blocker.';
  const noun = `${f.severity === null ? '' : `${f.severity} `}blocker${f.count === 1 ? '' : 's'}`;
  if (f.count === 1 && f.text !== null) return `Mission Core reports an active blocker: ${f.text}`;
  const opened = f.openedAt === null ? '' : `, opened ${utcStamp(f.openedAt)}`;
  return f.count === 1
    ? `Mission Core reports an active ${noun}${opened}.`
    : `Mission Core reports ${f.count} active ${noun}.`;
}

/**
 * "What happened" on the first layer: at most two short sentences, one per cause in the projection's order, each cause once; a blockers cause says
 * what the source gave (how many, how severe, when) and never an identifier. More than two causes: the rest are counted, not dropped (all of them are
 * in the card's disclosure).
 */
export function firstLayerHappened(entry: DiReceptionEntry): { lines: string[]; more: number } {
  const all = [
    ...new Set(
      entry.causes.map(
        (c) =>
          (c.cause === 'mission_core_blockers' ? blockerSentence(entry) : null) ??
          PLAIN_CAUSES[c.cause]?.happened ??
          c.label,
      ),
    ),
  ];
  return { lines: all.slice(0, 2), more: Math.max(0, all.length - 2) };
}

/** "Why it needs you" on the first layer: ONE sentence — the first cause's reason; the others are in the disclosure. */
export function firstLayerWhy(entry: DiReceptionEntry): string {
  return plainWhy(entry)[0] ?? GENERIC_WHY;
}

/**
 * The route to act, as the projection words it, without the shared read-only sentence every entry
 * repeats (said once at the top of the inbox; the full route text stays in the entry's disclosure).
 */
export function routeAction(route: string): string {
  const cut = route.replace(/\s*This desk is read-only[^.]*\.(\s*Hiding[^.]*\.)?/i, '').trim();
  return cut === '' ? route : cut;
}

/** The entry's Mission title as the card shows it: the objective's own words, verbatim. */
export function entryGoal(entry: DiReceptionEntry): string {
  return formatObserved(entry.objective);
}

// ── uncertainty ───────────────────────────────────────────────────────────────────────────────

/**
 * A reason code in words a person understands. Only reasons the projection actually emits are
 * mapped; anything else is said as "its record does not say" with the exact code left in the
 * disclosure that already shows it (never guessed).
 */
export function plainReason(reason: string): string {
  if (reason.includes('source_kind_has_no_mission_state'))
    return 'that kind of record carries no Mission Core state';
  if (reason.includes('binding_unverified'))
    return 'no live session confirms its task record’s team';
  if (reason.includes('mission_not_active'))
    return 'the Mission is not active, so there is no live team to read';
  if (reason.includes('covered_by_canonical_record'))
    return 'its canonical record already answers this';
  if (reason.startsWith('not_in_catalog_contract')) return 'no source reports it';
  if (reason.startsWith('catalog_does_not_report')) return 'this catalog does not report it';
  if (reason.startsWith('not_registered')) return 'its source is not set up for this catalog';
  if (reason.startsWith('delivery_presentations_unavailable'))
    return 'its source could not be read just now';
  if (reason.startsWith('contract_v1'))
    return 'records at the older contract version 1 do not carry it';
  if (reason.includes('truncated_by_display_cap'))
    return 'the list was cut off by the display limit';
  if (reason.includes('not_recorded')) return 'its record does not say';
  if (reason.includes('unavailable')) return 'its record could not be read just now';
  if (reason.includes('not_applicable')) return 'this kind of source does not report it';
  return 'its record does not say';
}

const missions = (n: number) => `${n} ${n === 1 ? 'Mission' : 'Missions'}`;

/**
 * The ONE or TWO plain lines above the cards when something could not be fully checked, e.g. "Some Missions couldn’t be fully checked · Clarification
 * isn’t reported by the catalog". Null when nothing is uncertain. The per-category and per-source detail is one disclosure away; material uncertainty is
 * never hidden and a source that was not read is never reported as "none".
 */
export function uncertaintyHeadline(
  reception: DiReception,
  undeterminedDeliveries: number,
): string | null {
  const parts: string[] = [];
  const checkable = reception.categories.filter((c) => !notReported(c) && c.category !== 'delivery_decision');
  if (checkable.some((c) => c.gaps.length > 0)) parts.push('Some Missions couldn’t be fully checked');
  else if (checkable.some((c) => c.outcome === 'unavailable' || c.outcome === 'unsupported'))
    parts.push('Some categories couldn’t be checked');
  const labels = notReportedLabels(reception);
  if (labels.length > 0)
    parts.push(`${labels.join(' and ')} ${labels.length === 1 ? 'isn’t' : 'aren’t'} reported by the catalog`);
  if (undeterminedDeliveries > 0)
    parts.push(
      `${undeterminedDeliveries} delivery ${undeterminedDeliveries === 1 ? 'proposal' : 'proposals'} couldn’t be checked for a recorded authorization`,
    );
  return parts.length === 0 ? null : parts.join(' · ');
}

/**
 * What could not be checked, one plain line per category, from that category's own gaps (its
 * unsupported and unavailable cells) — never from a guess. A category the whole source does not
 * report (clarification, delivery) is NOT here: see {@link notReportedLabels}.
 */
export function uncertaintyLines(reception: DiReception): string[] {
  const lines: string[] = [];
  for (const c of reception.categories) {
    if (notReported(c)) continue;
    // Delivery proposals are a source of their own, with their own coverage line.
    if (c.category === 'delivery_decision') continue;
    if (c.gaps.length > 0) {
      const parts = c.gaps.map(
        (g) => `${missions(g.missions)} couldn’t be checked (${plainReason(g.reason)})`,
      );
      lines.push(`${c.label}: ${parts.join('; ')}.`);
    } else if (c.outcome === 'unavailable' || c.outcome === 'unsupported') {
      lines.push(`${c.label}: couldn’t be checked (${plainReason(c.reason ?? '')}).`);
    }
  }
  return lines;
}

/** A category the whole source does not report at all (no producer record exists to read). */
function notReported(c: DiReceptionCategory): boolean {
  return c.outcome === 'unsupported' && (c.reason ?? '').startsWith('not_in_catalog_contract');
}

/** The categories this source does not report, by label (clarification and delivery today). */
export function notReportedLabels(reception: DiReception): string[] {
  return reception.categories.filter(notReported).map((c) => c.label);
}

// ── search and filter ─────────────────────────────────────────────────────────────────────────

export interface ReceptionFilter {
  query: string;
  category: DiReceptionCategoryId | null;
}

/** What a search matches: the entry's category, goal, identifiers and plain causes — never a verdict. */
function haystack(entry: DiReceptionEntry, categoryLabel: string): string {
  return [
    categoryLabel,
    entryGoal(entry),
    entry.missionRef,
    entry.sourceId,
    entry.nativeId,
    ...entry.causes.map((c) => c.label),
    ...plainCauses(entry).map((c) => c.happened),
  ]
    .join(' ')
    .toLowerCase();
}

/**
 * The entries a search and a category chip leave in view: every whitespace-separated word of the
 * query must match. A filter changes what is SHOWN only; no canonical outcome changes.
 */
export function filterEntries(
  entries: readonly DiReceptionEntry[],
  filter: ReceptionFilter,
  labelOf: (entry: DiReceptionEntry) => string,
): DiReceptionEntry[] {
  const words = filter.query.toLowerCase().split(/\s+/).filter(Boolean);
  return entries.filter(
    (e) =>
      (filter.category === null || e.category === filter.category) &&
      words.every((w) => haystack(e, labelOf(e)).includes(w)),
  );
}

/** One chip per category that has entries in view, with its count, in the desk's order. */
export function categoryChips(
  reception: DiReception,
  entries: readonly DiReceptionEntry[],
): Array<{ category: DiReceptionCategoryId; label: string; count: number }> {
  return reception.categories
    .map((c) => ({
      category: c.category,
      label: c.label,
      count: entries.filter((e) => e.category === c.category).length,
    }))
    .filter((c) => c.count > 0);
}
