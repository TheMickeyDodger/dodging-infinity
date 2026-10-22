/**
 * Display helpers for the Mission reception desk (item 06). The desk is projected server-side;
 * these only format it. Every outcome has its own words AND its own leading shape, so status never
 * rests on colour alone, and only `known_empty` ever says "none".
 */
import type {
  DiReception,
  DiReceptionCategory,
  DiReceptionEntry,
  DiReceptionGap,
  DiReceptionOutcome,
} from '../../../core/src/messages.js';

export const RECEPTION_GLYPH: Record<DiReceptionOutcome, string> = {
  present: '▲',
  known_empty: '○',
  unsupported: '⊘',
  unavailable: '?',
  partial: '◐',
};

export function formatCategoryHeadline(c: DiReceptionCategory): string {
  const glyph = RECEPTION_GLYPH[c.outcome];
  const why = c.reason ?? 'reason not given';
  switch (c.outcome) {
    case 'present': {
      const also =
        c.counts.unsupported + c.counts.unavailable > 0
          ? ` · ${c.counts.unsupported} cannot report, ${c.counts.unavailable} unknown`
          : '';
      return `${glyph} NEEDS ATTENTION: ${c.entriesTotal} entr${c.entriesTotal === 1 ? 'y' : 'ies'}${also}`;
    }
    case 'known_empty':
      return `${glyph} none reported (read succeeded)`;
    case 'unsupported':
      return `${glyph} NOT SUPPORTED: ${why}`;
    case 'unavailable':
      return `${glyph} UNKNOWN: ${why}`;
    case 'partial':
      return (
        `${glyph} NOT FULLY KNOWN: ${c.counts.knownEmpty} report none · ` +
        `${c.counts.unsupported} cannot report · ${c.counts.unavailable} unknown`
      );
  }
}

/**
 * The inbox's plain first-layer words for a category, with its shape. Only `known_empty` says
 * "nothing reported" (a successful read); an unsupported or unreadable category never reads as empty.
 * The exact reason code stays one disclosure away (`formatCategoryHeadline`).
 */
export function plainCategoryHeadline(c: DiReceptionCategory): string {
  const glyph = RECEPTION_GLYPH[c.outcome];
  switch (c.outcome) {
    case 'present': {
      const n = c.entriesTotal;
      const also = c.counts.unsupported + c.counts.unavailable;
      return `${glyph} ${n} ${n === 1 ? 'needs' : 'need'} attention${also > 0 ? ` · ${also} couldn’t be checked` : ''}`;
    }
    case 'known_empty':
      return `${glyph} nothing reported (read succeeded)`;
    case 'unsupported':
      return `${glyph} not reported by this source`;
    case 'unavailable':
      return `${glyph} can’t be read just now`;
    case 'partial':
      return `${glyph} only partly known`;
  }
}

export function outcomeClass(outcome: DiReceptionOutcome): string {
  switch (outcome) {
    case 'known_empty':
      return 'text-text';
    case 'unsupported':
      return 'text-text-muted italic';
    case 'present':
    case 'unavailable':
    case 'partial':
      return 'text-warning';
  }
}

/** `YYYY-MM-DD HH:MM UTC` for an epoch-seconds value: absolute, so it reads the same everywhere. */
export function utcStamp(epochSeconds: number): string {
  return `${new Date(epochSeconds * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

export function formatGap(g: DiReceptionGap): string {
  return `${g.missions} Mission(s) ${g.outcome === 'unsupported' ? 'cannot report' : 'unknown'}: ${g.reason}`;
}

/** What a local hide is keyed on: the canonical identity AND its canonical content. */
export function hideKey(e: DiReceptionEntry): string {
  return `${e.key}\u0000${e.signature}`;
}

export function visibleEntries(
  entries: DiReceptionEntry[],
  hidden: ReadonlySet<string>,
): { visible: DiReceptionEntry[]; hidden: number } {
  const visible = entries.filter((e) => !hidden.has(hideKey(e)));
  return { visible, hidden: entries.length - visible.length };
}

/** Roving focus through the entry list; null when the key is not a list key. */
export function nextEntryIndex(current: number, key: string, length: number): number | null {
  if (length === 0) return null;
  switch (key) {
    case 'ArrowDown':
      return Math.min(current + 1, length - 1);
    case 'ArrowUp':
      return Math.max(current - 1, 0);
    case 'Home':
      return 0;
    case 'End':
      return length - 1;
    default:
      return null;
  }
}

export function formatTruncation(r: DiReception): string | null {
  return r.entriesTotal > r.entries.length
    ? `showing ${r.entries.length} of ${r.entriesTotal} entries (display cap ${r.entriesCap})`
    : null;
}

export function formatEntryCurrency(e: DiReceptionEntry): string {
  const reasons = [...new Set(e.causes.flatMap((c) => c.staleReasons))];
  if (e.current && reasons.length === 0) return 'current';
  return `STALE: ${reasons.length > 0 ? reasons.join('; ') : 'reason not given'}`;
}

/**
 * The desk as the client may honestly show it once it can no longer vouch for the office state:
 * every entry is stale with the client's reason, and no category may say "none".
 */
export function invalidateReception(r: DiReception, reason: string): DiReception {
  const why = `client:${reason}`;
  return {
    ...r,
    entries: r.entries.map((e) => ({
      ...e,
      current: false,
      causes: e.causes.map((c) => ({ ...c, staleReasons: [...c.staleReasons, why] })),
    })),
    categories: r.categories.map((c) =>
      c.outcome === 'known_empty' ? { ...c, outcome: 'unavailable', reason: why } : c,
    ),
    // Source-keyed entries (delivery proposals) are no more current than the Mission entries.
    ...(r.sourceEntries === undefined
      ? {}
      : {
          sourceEntries: r.sourceEntries.map((e) => ({
            ...e,
            current: false,
            staleReasons: [...e.staleReasons, why],
          })),
        }),
    ...(r.sourceCoverage === undefined
      ? {}
      : {
          sourceCoverage: r.sourceCoverage.map((c) =>
            c.outcome === 'known_empty'
              ? { ...c, outcome: 'unavailable' as const, reason: why }
              : c,
          ),
        }),
    notices: [
      ...r.notices,
      {
        reason: why,
        statement: 'This view can no longer vouch for the office state: nothing is current.',
      },
    ],
  };
}
