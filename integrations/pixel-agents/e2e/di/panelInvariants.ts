/**
 * feature13 — the LAYOUT INVARIANTS of the DI office panel, as pure named checks over what
 * panelViewports.spec.ts measures in the real built app. Kept apart from the browser driver so the
 * checks themselves can be tested: an element that is ABSENT makes its check FALSE; it never makes
 * the check disappear (that conditional omission is what let a build with no inspector go green).
 * See panelViewports.spec.ts for the two named modes (candidate asserts, baseline records).
 */

/**
 * One action in the Selected dock, measured from the BUILT layout with NO scroll, click, hover or focus
 * first (a click auto-scrolls, a bounding box cannot see an element hidden inside its parent's own scroll
 * area): where it is, whether it is whole inside the viewport and inside the dock's VISIBLE client area
 * (below the dock's bottom fade), and what `document.elementFromPoint` finds at its centre.
 */
export interface DockActionMeasure {
  rect: { top: number; left: number; bottom: number; right: number; width: number; height: number };
  insideViewport: boolean;
  insideDockVisible: boolean;
  clearOfFade: boolean;
  hitIsAction: boolean;
  hit: string | null;
}

/** The Selected dock's own scroll area and the actions it offers on initial selection. */
export interface DockMeasure {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  /** The dock's VISIBLE client rect (padding box without scrollbar), and the px its bottom fades. */
  visible: { top: number; left: number; bottom: number; right: number };
  fadePx: number;
  /** offered: the dock carries the review button; not-offered: it says so; absent: neither (older contract). */
  reviewState: 'offered' | 'not-offered' | 'absent';
  actions: Record<string, DockActionMeasure | null>;
}

/** What measurePanel reports (null where the build has no such element). */
export interface PanelMeasure {
  panel: { overflowPx: number } | null;
  selectedSection: { top: number; bottom: number; withinViewport: boolean } | null;
  inspectorOpen: boolean;
  inspector: { withinViewport: boolean; closeVisible: boolean } | null;
  firstCard: { objective: boolean | null; status: boolean | null; team: boolean | null } | null;
  missionCards: { total: number; readableWithoutScrolling: number; withOpenInspector: number };
  /** The Selected dock's geometry (null: no dock in this build). Asserted in the member-selected state. */
  dock: DockMeasure | null;
}

/** The dock actions a selected team member must offer, whole and in reach, on initial selection. */
export const DOCK_ACTIONS = ['Show details', 'Show in the office', 'Show this Mission in the list'];
/** The review entry: asserted wherever the dock offers one (a fixture that offers a review). */
export const DOCK_REVIEW_ACTION = 'Open code review';

/**
 * The LAYOUT INVARIANTS that decide feature13, as named checks (see the header for the two modes).
 * Every one is evaluated on every state at every viewport, with NO conditional omission: an element
 * that is absent makes its check FALSE, it never makes the check disappear.
 */
export function layoutInvariants(
  m: PanelMeasure,
  vp: string,
  state: string,
): Array<{ name: string; ok: boolean }> {
  const inspectorState = state.startsWith('c-') || state.startsWith('d-');
  const checks: Array<{ name: string; ok: boolean }> = [];
  const add = (name: string, ok: boolean | null | undefined) =>
    checks.push({ name: `${vp} ${state}: ${name}`, ok: ok === true });
  add('the panel exists and never scrolls itself (overflowPx === 0)', m.panel?.overflowPx === 0);
  add('the Selected dock exists and is inside the viewport', m.selectedSection?.withinViewport);
  add(
    inspectorState ? 'the inspector is open' : 'no dialog is open',
    m.inspectorOpen === inspectorState,
  );
  if (inspectorState) {
    add('the inspector fits the viewport', m.inspector?.withinViewport);
    add("the inspector's Close is reachable", m.inspector?.closeVisible);
  }
  add('Mission cards exist', m.missionCards.total > 0);
  add(
    'every Mission card carries its "Show details"',
    m.missionCards.total > 0 && m.missionCards.withOpenInspector === m.missionCards.total,
  );
  if (state === 'b-team-member-selected') {
    // The doorways in the dock, on INITIAL selection: measured with no scrolling and no click first.
    const dock = m.dock;
    add('the dock was measured on initial selection: its scrollTop is 0', dock?.scrollTop === 0);
    const names = [
      ...DOCK_ACTIONS,
      ...(dock?.reviewState === 'offered' ? [DOCK_REVIEW_ACTION] : []),
    ];
    for (const name of names) {
      const a = dock?.actions[name] ?? null;
      add(`dock action "${name}" exists`, a !== null);
      add(`dock action "${name}" is whole inside the viewport`, a?.insideViewport);
      add(`dock action "${name}" is whole inside the dock's visible area`, a?.insideDockVisible);
      add(`dock action "${name}" is clear of the dock's bottom fade`, a?.clearOfFade);
      add(`dock action "${name}" is what is hit at its centre`, a?.hitIsAction);
    }
  }
  if (state === 'a-loaded-nothing-selected') {
    // Section 0: the opening shows the first Mission's objective, status and team (every viewport).
    add('first Mission objective in view', m.firstCard?.objective);
    add('first Mission status in view', m.firstCard?.status);
    add('first Mission team in view', m.firstCard?.team);
    if (vp === '1440x900')
      add(
        'three or more Mission cards readable without scrolling',
        m.missionCards.readableWithoutScrolling >= 3,
      );
  }
  return checks;
}
