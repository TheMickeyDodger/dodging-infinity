import assert from 'node:assert/strict';

import { test } from 'vitest';

import { installMapKeyGuard } from '../src/office/mapKeyGuard.ts';

/** A keydown as the browser dispatches it (Node has Event/EventTarget, not KeyboardEvent). */
function keydown(key: string): Event {
  const event = new Event('keydown', { cancelable: true });
  Object.defineProperty(event, 'key', { value: key });
  return event;
}

test('while the Map is open, NO key reaches a handler behind it (editor shortcuts included)', () => {
  // Node's EventTarget has no propagation path and calls listeners in registration order, so the
  // guard is registered first here. In a browser it is a WINDOW CAPTURE listener, which runs
  // before any window bubble listener whatever the order — proven by the real-browser
  // interaction probe on the built office (evidence), not by this unit test.
  const target = new EventTarget();
  const editorSaw: string[] = [];
  let closed = 0;
  const dispose = installMapKeyGuard(target, () => closed++);
  target.addEventListener('keydown', (e) =>
    editorSaw.push((e as Event & { key?: string }).key ?? ''),
  );
  for (const key of ['Delete', 'Backspace', 'r', 'R', 't', 'T', 'z', 'y', 'ArrowLeft', 'Escape']) {
    target.dispatchEvent(keydown(key));
  }
  assert.deepEqual(editorSaw, []); // nothing deleted, rotated, toggled, undone or closed behind it
  assert.equal(closed, 1); // Escape closes the Map — and only the Map
  dispose();
  target.dispatchEvent(keydown('Delete'));
  assert.deepEqual(editorSaw, ['Delete']); // once closed, the keyboard is the editor's again
});

test('Escape is consumed (default prevented) so nothing else treats it as its own', () => {
  const target = new EventTarget();
  installMapKeyGuard(target, () => {});
  const esc = keydown('Escape');
  target.dispatchEvent(esc);
  assert.equal(esc.defaultPrevented, true);
});
