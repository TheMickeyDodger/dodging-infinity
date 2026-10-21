/**
 * feature13: the layout invariants are only worth having if they FAIL when the feature is broken.
 * Pure (no browser): each case takes a measurement that satisfies every invariant and breaks ONE
 * thing — removes the dock, removes the inspector, lets the panel scroll, drops a doorway — and
 * asserts that exactly the matching check turns false. An ABSENT element must be a false check, never
 * an omitted one (the hole that let a build without the inspector pass).
 */
import { expect, test } from '@playwright/test';

import {
  DOCK_ACTIONS,
  DOCK_REVIEW_ACTION,
  type DockActionMeasure,
  type DockMeasure,
  layoutInvariants,
  type PanelMeasure,
} from './panelInvariants';

const WHOLE: DockActionMeasure = {
  rect: { top: 480, left: 20, bottom: 520, right: 160, width: 140, height: 40 },
  insideViewport: true,
  insideDockVisible: true,
  clearOfFade: true,
  hitIsAction: true,
  hit: 'button[x]',
};
const GOOD_DOCK: DockMeasure = {
  scrollTop: 0,
  scrollHeight: 300,
  clientHeight: 300,
  visible: { top: 440, left: 0, bottom: 740, right: 420 },
  fadePx: 28,
  reviewState: 'not-offered',
  actions: Object.fromEntries([...DOCK_ACTIONS, DOCK_REVIEW_ACTION].map((n) => [n, WHOLE])),
};
const GOOD: PanelMeasure = {
  panel: { overflowPx: 0 },
  selectedSection: { top: 790, bottom: 900, withinViewport: true },
  inspectorOpen: false,
  inspector: null,
  firstCard: { objective: true, status: true, team: true },
  missionCards: { total: 11, readableWithoutScrolling: 3, withOpenInspector: 11 },
  dock: GOOD_DOCK,
};
const GOOD_INSPECTOR: PanelMeasure = {
  ...GOOD,
  inspectorOpen: true,
  inspector: { withinViewport: true, closeVisible: true },
};

const failing = (m: PanelMeasure, vp: string, state: string): string[] =>
  layoutInvariants(m, vp, state)
    .filter((c) => !c.ok)
    .map((c) => c.name.replace(`${vp} ${state}: `, ''));
const count = (m: PanelMeasure, vp: string, state: string): number =>
  layoutInvariants(m, vp, state).length;

test('a build that satisfies everything passes every check, in every state and viewport', () => {
  for (const vp of ['1440x900', '1280x800', '1024x768', '1100x680']) {
    expect(failing(GOOD, vp, 'a-loaded-nothing-selected')).toEqual([]);
    expect(failing(GOOD, vp, 'b-team-member-selected')).toEqual([]);
    expect(failing(GOOD_INSPECTOR, vp, 'c-inspector-from-mission-card')).toEqual([]);
    expect(failing(GOOD_INSPECTOR, vp, 'd-inspector-from-selected-dock')).toEqual([]);
  }
});

test('a MISSING Selected dock fails the check (it is not skipped)', () => {
  const m = { ...GOOD, selectedSection: null };
  expect(failing(m, '1440x900', 'b-team-member-selected')).toEqual([
    'the Selected dock exists and is inside the viewport',
  ]);
  // ...and the same number of checks ran: nothing was omitted.
  expect(count(m, '1440x900', 'b-team-member-selected')).toBe(
    count(GOOD, '1440x900', 'b-team-member-selected'),
  );
});

test('a dock OUTSIDE the viewport fails (the 1100x680 overflow this defends)', () => {
  const m = { ...GOOD, selectedSection: { top: 422, bottom: 705, withinViewport: false } };
  expect(failing(m, '1100x680', 'b-team-member-selected')).toEqual([
    'the Selected dock exists and is inside the viewport',
  ]);
});

test('a MISSING inspector after invoking it fails, and so does one that does not fit or hides Close', () => {
  const none = { ...GOOD, inspectorOpen: false, inspector: null };
  const state = 'c-inspector-from-mission-card';
  expect(failing(none, '1024x768', state)).toEqual([
    'the inspector is open',
    'the inspector fits the viewport',
    "the inspector's Close is reachable",
  ]);
  const big = { ...GOOD_INSPECTOR, inspector: { withinViewport: false, closeVisible: true } };
  expect(failing(big, '1024x768', state)).toEqual(['the inspector fits the viewport']);
  const noClose = { ...GOOD_INSPECTOR, inspector: { withinViewport: true, closeVisible: false } };
  expect(failing(noClose, '1024x768', state)).toEqual(["the inspector's Close is reachable"]);
});

test('a stray dialog in a state that should have none fails', () => {
  expect(failing(GOOD_INSPECTOR, '1440x900', 'a-loaded-nothing-selected')).toContain(
    'no dialog is open',
  );
});

test('a panel that scrolls itself fails; a missing panel fails', () => {
  expect(
    failing({ ...GOOD, panel: { overflowPx: 256 } }, '1440x900', 'a-loaded-nothing-selected'),
  ).toEqual(['the panel exists and never scrolls itself (overflowPx === 0)']);
  expect(failing({ ...GOOD, panel: null }, '1440x900', 'a-loaded-nothing-selected')).toEqual([
    'the panel exists and never scrolls itself (overflowPx === 0)',
  ]);
});

test('a Mission card WITHOUT its "Open inspector" fails, and so does a list with no cards', () => {
  const one = { ...GOOD, missionCards: { ...GOOD.missionCards, withOpenInspector: 10 } };
  expect(failing(one, '1440x900', 'a-loaded-nothing-selected')).toEqual([
    'every Mission card carries its "Open inspector"',
  ]);
  const empty = {
    ...GOOD,
    missionCards: { total: 0, readableWithoutScrolling: 0, withOpenInspector: 0 },
    firstCard: null,
  };
  const names = failing(empty, '1440x900', 'a-loaded-nothing-selected');
  expect(names).toContain('Mission cards exist');
  expect(names).toContain('every Mission card carries its "Open inspector"');
  expect(names).toContain('first Mission objective in view');
});

test('section 0: the first Mission objective, status and team must each be in view; 1440x900 wants three cards', () => {
  for (const key of ['objective', 'status', 'team'] as const) {
    const m = { ...GOOD, firstCard: { ...GOOD.firstCard!, [key]: false } };
    expect(failing(m, '1280x800', 'a-loaded-nothing-selected')).toEqual([
      `first Mission ${key} in view`,
    ]);
    // an ABSENT element (null) is false too, not skipped
    const absent = { ...GOOD, firstCard: { ...GOOD.firstCard!, [key]: null } };
    expect(failing(absent, '1280x800', 'a-loaded-nothing-selected')).toEqual([
      `first Mission ${key} in view`,
    ]);
  }
  const two = { ...GOOD, missionCards: { ...GOOD.missionCards, readableWithoutScrolling: 2 } };
  expect(failing(two, '1440x900', 'a-loaded-nothing-selected')).toEqual([
    'three or more Mission cards readable without scrolling',
  ]);
  // the three-card target applies at the desktop opening only
  expect(failing(two, '1024x768', 'a-loaded-nothing-selected')).toEqual([]);
});

// -- the dock's OWN actions: measured with no scroll and no click, against the dock's visible area ----------

const STATE_B = 'b-team-member-selected';
/** The 1100x680 defect: the doorway sits below the bottom of the dock's own scroll area. */
const HIDDEN: DockActionMeasure = {
  rect: { top: 700, left: 20, bottom: 736, right: 160, width: 140, height: 36 },
  insideViewport: true, // the viewport is 680 tall in the real case; the bbox of the DOCK is in view
  insideDockVisible: false,
  clearOfFade: false,
  hitIsAction: false,
  hit: 'div[di-selected-member]',
};

test("an action hidden inside the dock's own scroll area FAILS, though the dock's bounding box is in the viewport", () => {
  const m: PanelMeasure = {
    ...GOOD,
    dock: { ...GOOD_DOCK, actions: { ...GOOD_DOCK.actions, 'Open inspector': HIDDEN } },
  };
  // The existing bbox invariant is satisfied (this is what the old suite checked)...
  expect(failing(m, '1100x680', STATE_B)).not.toContain(
    'the Selected dock exists and is inside the viewport',
  );
  // ...and the new invariant is what catches the hidden doorway.
  expect(failing(m, '1100x680', STATE_B)).toEqual([
    'dock action "Open inspector" is whole inside the dock\'s visible area',
    'dock action "Open inspector" is clear of the dock\'s bottom fade',
    'dock action "Open inspector" is what is hit at its centre',
  ]);
});

test("an action that is only partly in the dock's visible area fails too", () => {
  const half: DockActionMeasure = { ...WHOLE, insideDockVisible: false, clearOfFade: false };
  const m: PanelMeasure = {
    ...GOOD,
    dock: { ...GOOD_DOCK, actions: { ...GOOD_DOCK.actions, 'Show in the office': half } },
  };
  expect(failing(m, '1024x768', STATE_B)).toEqual([
    'dock action "Show in the office" is whole inside the dock\'s visible area',
    'dock action "Show in the office" is clear of the dock\'s bottom fade',
  ]);
});

test('a MISSING dock action is a failed check, never an omitted one', () => {
  const m: PanelMeasure = {
    ...GOOD,
    dock: {
      ...GOOD_DOCK,
      actions: { ...GOOD_DOCK.actions, 'Show this Mission in the list': null },
    },
  };
  expect(failing(m, '1280x800', STATE_B)).toEqual([
    'dock action "Show this Mission in the list" exists',
    'dock action "Show this Mission in the list" is whole inside the viewport',
    'dock action "Show this Mission in the list" is whole inside the dock\'s visible area',
    'dock action "Show this Mission in the list" is clear of the dock\'s bottom fade',
    'dock action "Show this Mission in the list" is what is hit at its centre',
  ]);
  expect(count(m, '1280x800', STATE_B)).toBe(count(GOOD, '1280x800', STATE_B));
});

test('a dock with NO measurement fails every dock check; none is dropped', () => {
  const m: PanelMeasure = { ...GOOD, dock: null };
  const failed = failing(m, '1440x900', STATE_B);
  expect(failed).toContain('the dock was measured on initial selection: its scrollTop is 0');
  expect(failed.filter((n) => n.startsWith('dock action')).length).toBe(DOCK_ACTIONS.length * 5);
  expect(count(m, '1440x900', STATE_B)).toBe(count(GOOD, '1440x900', STATE_B));
});

test('a dock that was scrolled before it was measured fails: the measurement must be on INITIAL selection', () => {
  const m: PanelMeasure = { ...GOOD, dock: { ...GOOD_DOCK, scrollTop: 24 } };
  expect(failing(m, '1100x680', STATE_B)).toEqual([
    'the dock was measured on initial selection: its scrollTop is 0',
  ]);
});

test('an OFFERED review entry is asserted like the other actions; a not-offered dock is not asked for a button', () => {
  const offered = (review: DockActionMeasure | null): PanelMeasure => ({
    ...GOOD,
    dock: {
      ...GOOD_DOCK,
      reviewState: 'offered',
      actions: { ...GOOD_DOCK.actions, [DOCK_REVIEW_ACTION]: review },
    },
  });
  expect(failing(offered(WHOLE), '1100x680', STATE_B)).toEqual([]);
  expect(failing(offered(HIDDEN), '1100x680', STATE_B)).toEqual([
    'dock action "Open code review" is whole inside the dock\'s visible area',
    'dock action "Open code review" is clear of the dock\'s bottom fade',
    'dock action "Open code review" is what is hit at its centre',
  ]);
  expect(failing(offered(null), '1100x680', STATE_B)).toContain(
    'dock action "Open code review" exists',
  );
  // Not offered: the same hidden measurement is not an invariant violation (there is no button to reach).
  expect(
    failing(
      {
        ...GOOD,
        dock: { ...GOOD_DOCK, actions: { ...GOOD_DOCK.actions, [DOCK_REVIEW_ACTION]: HIDDEN } },
      },
      '1100x680',
      STATE_B,
    ),
  ).toEqual([]);
  expect(count(offered(WHOLE), '1100x680', STATE_B)).toBe(count(GOOD, '1100x680', STATE_B) + 5);
});

test('the dock-action checks apply to the member-selected state only', () => {
  const broken: PanelMeasure = { ...GOOD, dock: null };
  expect(failing(broken, '1440x900', 'a-loaded-nothing-selected')).toEqual([]);
  expect(count(broken, '1440x900', 'a-loaded-nothing-selected')).toBe(
    count(GOOD, '1440x900', 'a-loaded-nothing-selected'),
  );
});
