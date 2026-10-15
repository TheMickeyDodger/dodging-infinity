/**
 * REDUCED MOTION — the OS preference, read safely and followed live. DOM-free like the rest of
 * office/ (typed structurally against the one slice of MediaQueryList it uses). An unavailable
 * or throwing matchMedia is NO preference, never 'reduce'.
 */
import { DI_REDUCED_MOTION_QUERY } from '../constants.js';

/** The part of a MediaQueryList this module uses. */
export interface PreferenceQuery {
  matches: boolean;
  addEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
  removeEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
}
export type MatchMedia = (query: string) => PreferenceQuery;

function platformMatchMedia(): MatchMedia | undefined {
  const scope = globalThis as { matchMedia?: MatchMedia };
  return typeof scope.matchMedia === 'function' ? scope.matchMedia.bind(globalThis) : undefined;
}

/** Calls `onChange` now and on every change of the preference; returns the unsubscribe. */
export function subscribeReducedMotion(
  onChange: (reduce: boolean) => void,
  matchMedia: MatchMedia | undefined = platformMatchMedia(),
): () => void {
  let query: PreferenceQuery | null;
  try {
    query = matchMedia ? matchMedia(DI_REDUCED_MOTION_QUERY) : null;
  } catch {
    query = null;
  }
  onChange(query?.matches === true);
  if (!query) return () => {};
  const listener = (event: { matches: boolean }) => onChange(event.matches);
  try {
    query.addEventListener('change', listener);
  } catch {
    return () => {};
  }
  const subscribed = query;
  return () => {
    try {
      subscribed.removeEventListener('change', listener);
    } catch {
      // nothing to undo
    }
  };
}
