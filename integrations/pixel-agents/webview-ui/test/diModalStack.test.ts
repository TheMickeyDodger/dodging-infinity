/**
 * The shared ownership of the app root's `inert` guard (src/components/diModalStack.ts), proven at the module level.
 *
 * Each scenario is labelled:
 *  - REACHABLE: a path a person or the runtime can take in the product today;
 *  - DEFENSIVE: a sequence NOT reachable through the product UI today, exercised so the ownership holds if it ever
 *    becomes reachable. Today a minimized dialog cannot close while hidden: the review's invalidation dispatches
 *    'clear' (never 'close'), every close route of the review and of reception starts from a control inside the
 *    hidden dialog, and the office panel never unmounts after its first office state.
 *
 * The invariants: #root is inert if and only if at least one dialog is in front; a dialog changes the guard only
 * through its own claim and release; only the dialog that was foremost at close gives focus back.
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import {
  claimFront,
  CLOSE_FOCUS_FALLBACKS,
  frontCount,
  isForemost,
  releaseFront,
  returnFocusOnClose,
} from '../src/components/diModalStack.ts';

/** A stand-in for the app root: the module reads it through `document.getElementById('root')`. */
const root = (() => {
  const attrs = new Set<string>();
  return {
    setAttribute: (name: string) => void attrs.add(name),
    removeAttribute: (name: string) => void attrs.delete(name),
    hasAttribute: (name: string) => attrs.has(name),
  };
})();
/** Stand-ins for page controls: each says whether it is connected, disabled, hidden or inside an inert subtree. */
interface FakeControl {
  name: string;
  isConnected: boolean;
  disabled: boolean;
  hidden: boolean;
  inert: boolean;
  focus: () => void;
  closest: (selector: string) => unknown;
  matches: (selector: string) => boolean;
  getClientRects: () => { length: number };
  checkVisibility: () => boolean;
}
const page = { active: null as FakeControl | null, bySelector: new Map<string, FakeControl>() };
function control(name: string, state: Partial<Pick<FakeControl, 'isConnected' | 'disabled' | 'hidden' | 'inert'>> = {}) {
  const c: FakeControl = {
    name,
    isConnected: state.isConnected ?? true,
    disabled: state.disabled ?? false,
    hidden: state.hidden ?? false,
    inert: state.inert ?? false,
    focus: () => {
      page.active = c;
    },
    closest: (selector: string) => (selector === '[inert]' && c.inert ? {} : null),
    matches: (selector: string) => selector === ':disabled' && c.disabled,
    getClientRects: () => ({ length: c.hidden ? 0 : 1 }),
    checkVisibility: () => !c.hidden,
  };
  return c;
}
const FIND = '[data-testid="di-find-input"]';
const OFFICE_RESTORE = '[data-testid="di-office-panel-collapsed"]';

beforeAll(() => {
  (globalThis as unknown as { document: unknown }).document = {
    getElementById: (id: string) => (id === 'root' ? root : null),
    querySelector: (selector: string) => page.bySelector.get(selector) ?? null,
    get activeElement() {
      return page.active;
    },
  };
});

/** A dialog's lifecycle as the hooks drive it, recording whether its close gave focus back. */
function dialog(name: string) {
  const owner = { name };
  const self = {
    focusReturned: null as boolean | null,
    open: () => claimFront(owner),
    minimize: () => void releaseFront(owner),
    restore: () => claimFront(owner),
    close: () => {
      self.focusReturned = releaseFront(owner);
    },
    foremost: () => isForemost(owner),
  };
  return self;
}
const inert = () => root.hasAttribute('inert');

afterEach(() => {
  // every scenario leaves nothing in front, so no state leaks between them
  expect(frontCount()).toBe(0);
  expect(inert()).toBe(false);
});

describe('REACHABLE paths', () => {
  it('one dialog: inert while open; its close gives focus back and leaves nothing inert', () => {
    const a = dialog('details');
    a.open();
    expect(inert()).toBe(true);
    expect(a.foremost()).toBe(true);
    a.close();
    expect(a.focusReturned).toBe(true);
    expect(inert()).toBe(false);
  });

  it('minimize and restore: the app is usable while minimized, modal again on restore, and the close still gives focus back', () => {
    const a = dialog('reception');
    a.open();
    a.minimize();
    expect(inert()).toBe(false);
    expect(a.foremost()).toBe(false);
    a.restore();
    expect(inert()).toBe(true);
    a.close();
    expect(a.focusReturned).toBe(true);
  });

  it('a dialog minimized, another opens and closes in front, then the first is restored and closed', () => {
    const a = dialog('reception');
    const b = dialog('details');
    a.open();
    a.minimize();
    b.open();
    expect(inert()).toBe(true);
    expect(b.foremost()).toBe(true);
    b.close();
    expect(b.focusReturned).toBe(true);
    expect(inert()).toBe(false);
    a.restore();
    expect(inert()).toBe(true);
    a.close();
    expect(a.focusReturned).toBe(true);
  });

  it('two surfaces minimized at once, then each restored and closed in turn', () => {
    const a = dialog('reception');
    const r = dialog('review');
    a.open();
    a.minimize();
    r.open();
    r.minimize();
    expect(inert()).toBe(false);
    r.restore();
    expect(inert()).toBe(true);
    r.close();
    expect(r.focusReturned).toBe(true);
    expect(inert()).toBe(false);
    a.restore();
    a.close();
    expect(a.focusReturned).toBe(true);
  });
});

describe('DEFENSIVE exercises (not reachable through the product UI today)', () => {
  it('ORDER 1: a hidden dialog closes while another is in front: the front dialog keeps its guard and its focus', () => {
    const a = dialog('reception');
    const b = dialog('details');
    a.open();
    a.minimize();
    b.open();
    a.close();
    expect(a.focusReturned, 'the hidden dialog does not give focus back').toBe(false);
    expect(inert(), 'the front dialog is still guarded').toBe(true);
    expect(b.foremost()).toBe(true);
    b.close();
    expect(b.focusReturned).toBe(true);
    expect(inert()).toBe(false);
  });

  it('ORDER 2: the front dialog closes first, then the hidden one: nothing is left inert and the hidden close moves no focus', () => {
    const a = dialog('reception');
    const b = dialog('details');
    a.open();
    a.minimize();
    b.open();
    b.close();
    expect(b.focusReturned).toBe(true);
    a.close();
    expect(a.focusReturned).toBe(false);
    expect(inert()).toBe(false);
  });

  it('a BACKGROUND dialog (in front but not foremost) closes: no focus moves and the foremost keeps the guard', () => {
    const a = dialog('reception');
    const b = dialog('details');
    a.open();
    b.open(); // b in front of a (not reachable today: the first is modal)
    a.close();
    expect(a.focusReturned).toBe(false);
    expect(inert()).toBe(true);
    expect(b.foremost()).toBe(true);
    b.close();
    expect(b.focusReturned).toBe(true);
  });

  it('teardown of several surfaces: two hidden and one in front, closed in every order, keep the guard exactly while one is in front', () => {
    const orders = [
      ['hidden1', 'hidden2', 'front'],
      ['hidden1', 'front', 'hidden2'],
      ['front', 'hidden1', 'hidden2'],
      ['hidden2', 'front', 'hidden1'],
    ];
    for (const order of orders) {
      const ds = { hidden1: dialog('reception'), hidden2: dialog('review'), front: dialog('details') };
      ds.hidden1.open();
      ds.hidden1.minimize();
      ds.hidden2.open();
      ds.hidden2.minimize();
      ds.front.open();
      let frontOpen = true;
      for (const name of order) {
        ds[name as keyof typeof ds].close();
        if (name === 'front') frontOpen = false;
        expect(inert(), `${order.join(' > ')} after ${name}`).toBe(frontOpen);
      }
      expect(ds.front.focusReturned).toBe(true);
      expect(ds.hidden1.focusReturned).toBe(false);
      expect(ds.hidden2.focusReturned).toBe(false);
    }
  });

  it('claiming twice keeps one place; releasing a dialog that is not in front changes nothing', () => {
    const a = dialog('reception');
    a.open();
    a.restore(); // a second claim
    expect(frontCount()).toBe(1);
    a.minimize();
    a.minimize(); // releasing again
    expect(frontCount()).toBe(0);
    expect(inert()).toBe(false);
    a.close();
    expect(a.focusReturned).toBe(false);
  });
});

/**
 * Close-time focus, the twin of the restore fallback: the dialog that was foremost gives focus back to its opener when
 * the opener can still take it; when the opener is absent, disconnected, disabled or hidden (REACHABLE: a cleared
 * review's opener leaves with its agent), focus goes to the office Find box, then the office panel's restore control;
 * never the page body. A dialog that was not foremost moves no focus.
 */
describe('close-time focus fallback (the twin of the restore fallback)', () => {
  const setup = (find: FakeControl | null, officeRestore: FakeControl | null) => {
    page.active = null;
    page.bySelector.clear();
    if (find) page.bySelector.set(FIND, find);
    if (officeRestore) page.bySelector.set(OFFICE_RESTORE, officeRestore);
  };

  it('the fallbacks are named once, in order: the office Find box, then the office panel restore control', () => {
    expect(CLOSE_FOCUS_FALLBACKS).toEqual([FIND, OFFICE_RESTORE]);
  });

  it('REACHABLE: an opener that can take focus gets it back', () => {
    setup(control('find'), null);
    const opener = control('opener');
    expect(returnFocusOnClose(true, opener as unknown as HTMLElement)).toBe(opener);
    expect(page.active?.name).toBe('opener');
  });

  it('REACHABLE: an opener that is absent, disconnected, disabled or hidden falls back to the Find box, never nothing', () => {
    const openers: Array<[string, FakeControl | null]> = [
      ['absent', null],
      ['disconnected', control('opener', { isConnected: false })],
      ['disabled', control('opener', { disabled: true })],
      ['hidden', control('opener', { hidden: true })],
      ['inert', control('opener', { inert: true })],
    ];
    for (const [why, opener] of openers) {
      setup(control('find'), control('office restore'));
      const took = returnFocusOnClose(true, opener as unknown as HTMLElement | null) as unknown as FakeControl | null;
      expect(took?.name ?? null, why).toBe('find');
      expect(page.active?.name, why).toBe('find');
    }
  });

  it('REACHABLE: with the office panel minimized (Find box hidden), focus goes to the office panel restore control', () => {
    setup(control('find', { hidden: true }), control('office restore'));
    expect((returnFocusOnClose(true, null) as unknown as FakeControl | null)?.name).toBe('office restore');
  });

  it('a dialog that was NOT foremost moves no focus, whatever its opener', () => {
    setup(control('find'), control('office restore'));
    const opener = control('opener');
    expect(returnFocusOnClose(false, opener as unknown as HTMLElement)).toBeNull();
    expect(page.active).toBeNull();
  });

  it('when nothing can take focus it says so (null) and moves nothing', () => {
    setup(control('find', { hidden: true }), control('office restore', { isConnected: false }));
    expect(returnFocusOnClose(true, null)).toBeNull();
    expect(page.active).toBeNull();
  });
});
