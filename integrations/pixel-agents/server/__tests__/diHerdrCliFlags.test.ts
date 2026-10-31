/**
 * The office's Herdr flags (A1): --herdr-bin, --herdr-home and --herdr-socket, in the style of --backend-discovery and --delivery-adapter-discovery: explicit, absolute, each failure naming the exact flag.
 *
 * What is shown: all three or none; each a plain absolute path (the route's own guard); the socket inside the home; a value is REFUSED, never repaired (no normalisation turns a bad path into a good one); and
 * with none of them the office is configured with no Herdr route at all (`herdr: null`), which the relay then reports as "not configured".
 *
 * WHAT THIS DOES NOT ESTABLISH: main() is not run here (it starts a server), so the relay's wiring into the dispatcher is covered by diHerdrRequests.test.ts and the closure pin, not by this file.
 */
import { describe, expect, it } from 'vitest';

import type { DiCliArgs } from '../src/di/diCli.js';
import { DiCliArgsError, HERDR_LAUNCHER_DIR_NAME, parseDiArgs } from '../src/di/diCli.js';

const BASE = ['--state-dir', '/abs/state', '--backend-discovery', '/abs/d.json'];
const FLAGS = ['--herdr-bin', '/Users/someone/.local/bin/herdr', '--herdr-home', '/Users/someone', '--herdr-socket', '/Users/someone/.config/herdr/herdr.sock'];

describe('--herdr-bin, --herdr-home and --herdr-socket', () => {
  it('all three, with the backend or with a fixture source, give exactly the configuration named', () => {
    const expected = { bin: '/Users/someone/.local/bin/herdr', home: '/Users/someone', socketPath: '/Users/someone/.config/herdr/herdr.sock' };
    expect((parseDiArgs([...BASE, ...FLAGS]) as DiCliArgs).herdr).toEqual(expected);
    expect((parseDiArgs(['--state-dir', '/abs/state', '--fixture-source', '/abs/f.json', ...FLAGS]) as DiCliArgs).herdr).toEqual(expected);
  });

  it('their order does not matter', () => {
    const [bin, binValue, home, homeValue, socket, socketValue] = FLAGS;
    expect((parseDiArgs([...BASE, socket, socketValue, bin, binValue, home, homeValue]) as DiCliArgs).herdr).toMatchObject({ home: '/Users/someone' });
  });

  it('with none of them the office has no Herdr route (null), and nothing is defaulted from the environment', () => {
    expect((parseDiArgs(BASE) as DiCliArgs).herdr).toBeNull();
  });

  it.each([
    ['--herdr-bin', ['--herdr-home', '/Users/someone', '--herdr-socket', '/Users/someone/.config/herdr/herdr.sock']],
    ['--herdr-home', ['--herdr-bin', '/Users/someone/.local/bin/herdr', '--herdr-socket', '/Users/someone/.config/herdr/herdr.sock']],
    ['--herdr-socket', ['--herdr-bin', '/Users/someone/.local/bin/herdr', '--herdr-home', '/Users/someone']],
  ])('without %s the office refuses to start and names it', (missing, given) => {
    let error: unknown;
    try {
      parseDiArgs([...BASE, ...given]);
    } catch (err) {
      error = err;
    }
    expect(error).toBeInstanceOf(DiCliArgsError);
    expect((error as Error).message).toContain(`missing ${missing}`);
  });

  it.each([
    ['--herdr-bin'],
    ['--herdr-home'],
    ['--herdr-socket'],
  ])('%s with no value is a missing-value error naming it', (flag) => {
    expect(() => parseDiArgs([...BASE, flag])).toThrow(`Missing value for ${flag}`);
  });

  it.each([
    ['a relative executable', ['--herdr-bin', 'herdr'], '--herdr-bin'],
    ['an executable with a space', ['--herdr-bin', '/Users/some one/herdr'], '--herdr-bin'],
    ['an executable reaching through ..', ['--herdr-bin', '/Users/someone/../bin/herdr'], '--herdr-bin'],
    ['a relative home', ['--herdr-home', 'someone'], '--herdr-home'],
    ['a home with a trailing slash (refused, NOT trimmed)', ['--herdr-home', '/Users/someone/'], '--herdr-home'],
    ['a socket with a doubled slash (refused, NOT normalised)', ['--herdr-socket', '/Users/someone//herdr.sock'], '--herdr-socket'],
    ['a socket with a shell character', ['--herdr-socket', '/Users/someone/h;sock'], '--herdr-socket'],
  ])('%s is refused by name, and not repaired', (_name, over, flag) => {
    const merged = [...FLAGS];
    for (let i = 0; i < over.length; i += 2) merged[merged.indexOf(over[i]) + 1] = over[i + 1];
    expect(() => parseDiArgs([...BASE, ...merged])).toThrow(flag);
    expect(() => parseDiArgs([...BASE, ...merged])).toThrow(DiCliArgsError);
  });

  it('a socket outside the home is refused and both flags are named: the route\'s rule is enforced at the door as well', () => {
    const merged = [...FLAGS];
    merged[merged.indexOf('--herdr-socket') + 1] = '/Users/other/.config/herdr/herdr.sock';
    expect(() => parseDiArgs([...BASE, ...merged])).toThrow('--herdr-socket must lie inside --herdr-home');
  });

  it('the launcher directory lives under the state root, under a fixed name', () => {
    expect(HERDR_LAUNCHER_DIR_NAME).toBe('herdr-launchers');
  });
});
