/**
 * The number <-> canonical identity map is where identity corruption would hide:
 * two different agents silently becoming one character. Identities are explicit
 * tuples; refs are opaque and never parsed.
 */
import { describe, expect, it } from 'vitest';

import type { CharacterIdentity } from '../src/di/identityMap.js';
import { DiIdentityMap, IdentityMapError } from '../src/di/identityMap.js';

function id(overrides: Partial<CharacterIdentity> = {}): CharacterIdentity {
  return {
    coverage: 'direct',
    sourceId: 'pixel_live_office',
    missionRef: 'pixel_live_office:mn-1',
    taskId: 'task-a',
    memberRef: 'pixel_live_office:mn-1#executor1',
    ...overrides,
  };
}

describe('DiIdentityMap', () => {
  it('assigns one stable number per identity', () => {
    const map = new DiIdentityMap();
    const first = map.numberFor(id());
    expect(map.numberFor(id())).toBe(first);
    expect(map.identityFor(first)).toEqual(id());
  });

  it('the SAME logical name in two different tasks is two characters (collision hazard)', () => {
    const map = new DiIdentityMap();
    const a = map.numberFor(id({ taskId: 'task-a' }));
    const b = map.numberFor(id({ taskId: 'task-b' }));
    expect(a).not.toBe(b);
  });

  it('the same memberRef under a different task or mission is a different identity', () => {
    const map = new DiIdentityMap();
    const base = map.numberFor(id());
    expect(map.numberFor(id({ missionRef: 'pixel_live_office:mn-2' }))).not.toBe(base);
    expect(map.numberFor(id({ taskId: null }))).not.toBe(base);
  });

  it('never merges across sources even when native ids and logical names match', () => {
    const map = new DiIdentityMap();
    const a = map.numberFor(
      id({ sourceId: 'pixel_live_office', missionRef: 'x:1', memberRef: 'x:1#e1' }),
    );
    const b = map.numberFor(
      id({ sourceId: 'di_v2_store', missionRef: 'x:1', memberRef: 'x:1#e1' }),
    );
    expect(a).not.toBe(b);
  });

  it('is case-sensitive: identities are not normalized', () => {
    const map = new DiIdentityMap();
    expect(map.numberFor(id({ memberRef: 'S:M#Executor1' }))).not.toBe(
      map.numberFor(id({ memberRef: 'S:M#executor1' })),
    );
  });

  it('treats refs as opaque — any non-empty string works, nothing is parsed', () => {
    const map = new DiIdentityMap();
    const n = map.numberFor(id({ memberRef: 'no colon, no hash, spaces and ":#" anywhere' }));
    expect(map.identityFor(n)?.memberRef).toBe('no colon, no hash, spaces and ":#" anywhere');
  });

  it('never reuses a number for a different identity', () => {
    const map = new DiIdentityMap();
    const numbers = new Set<number>();
    for (let i = 0; i < 50; i++) numbers.add(map.numberFor(id({ memberRef: `m#${i}` })));
    expect(numbers.size).toBe(50);
    expect(map.size).toBe(50);
  });

  it('rejects malformed identities instead of guessing', () => {
    const map = new DiIdentityMap();
    expect(() => map.numberFor(id({ memberRef: '' }))).toThrow(IdentityMapError);
    expect(() => map.numberFor(id({ sourceId: '' }))).toThrow(IdentityMapError);
    expect(() => map.numberFor(id({ taskId: '' }))).toThrow(IdentityMapError);
    expect(() => map.numberFor({ ...id(), coverage: 'sideways' as unknown as 'direct' })).toThrow(
      IdentityMapError,
    );
  });

  it('contract v2: a linked-child identity gets its OWN number; direct and child never merge', () => {
    const map = new DiIdentityMap();
    const direct = map.numberFor(id());
    const child = map.numberFor({ ...id(), coverage: 'child' });
    expect(child).not.toBe(direct);
    expect(map.numberFor({ ...id(), coverage: 'child' })).toBe(child); // stable
    expect(map.identityFor(child)?.coverage).toBe('child');
  });

  it('returned identities are copies (the map cannot be mutated from outside)', () => {
    const map = new DiIdentityMap();
    const n = map.numberFor(id());
    const copy = map.identityFor(n)!;
    copy.memberRef = 'tampered';
    expect(map.identityFor(n)?.memberRef).toBe(id().memberRef);
  });
});
