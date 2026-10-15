/**
 * The DI office's stable `number <-> canonical identity` map.
 *
 * The renderer keys characters by NUMBER. A member's canonical identity is an
 * EXPLICIT TUPLE of fields the backend projected — never a parsed ref:
 *   coverage   'direct' (the Mission's own task) or 'child' (contract v2: a linked child
 *              task's chain) — an explicit field, set by the projection, never inferred
 *   sourceId   the registered source
 *   missionRef the backend's mission identity (opaque)
 *   taskId     the task the member belongs to (task-qualified; null when not recorded)
 *   memberRef  the backend's member identity (opaque)
 * Logical names collide across tasks (two chains can both have an `executor1`), so
 * the task and coverage are part of the identity, and no ref is ever string-parsed to
 * decide what something is. Two tuples that differ in ANY field are different
 * characters; nothing is merged, case-folded or normalized.
 *
 * One identity -> one number, stable for the process lifetime; a number is never
 * reassigned to a different identity, so a character can never silently become
 * someone else.
 */

/** `direct`: the Mission's own task; `child` (contract v2): a linked child task's chain. */
export type Coverage = 'direct' | 'child';
const COVERAGES: readonly string[] = ['direct', 'child'];

export interface CharacterIdentity {
  coverage: Coverage;
  sourceId: string;
  missionRef: string;
  taskId: string | null;
  memberRef: string;
}

export class IdentityMapError extends Error {}

function canonicalKey(identity: CharacterIdentity): string {
  const { coverage, sourceId, missionRef, taskId, memberRef } = identity;
  // A closed set: v2's linked-child members are characters too; anything else is refused.
  if (!COVERAGES.includes(coverage)) throw new IdentityMapError('unsupported coverage');
  for (const value of [sourceId, missionRef, memberRef]) {
    if (typeof value !== 'string' || value.length === 0) {
      throw new IdentityMapError('identity fields must be non-empty strings');
    }
  }
  if (taskId !== null && (typeof taskId !== 'string' || taskId.length === 0)) {
    throw new IdentityMapError('taskId must be a non-empty string or null');
  }
  // A structured encoding of the tuple: unambiguous for any field contents.
  return JSON.stringify([coverage, sourceId, missionRef, taskId, memberRef]);
}

export class DiIdentityMap {
  private readonly byKey = new Map<string, number>();
  private readonly byNumber = new Map<number, CharacterIdentity>();
  private next: number;

  constructor(firstNumber = 1) {
    if (!Number.isInteger(firstNumber) || firstNumber < 1) {
      throw new IdentityMapError('firstNumber must be a positive integer');
    }
    this.next = firstNumber;
  }

  /** The number for `identity`, assigning the next unused one on first sight. */
  numberFor(identity: CharacterIdentity): number {
    const key = canonicalKey(identity);
    const existing = this.byKey.get(key);
    if (existing !== undefined) return existing;
    const assigned = this.next++;
    this.byKey.set(key, assigned);
    this.byNumber.set(assigned, { ...identity });
    return assigned;
  }

  identityFor(number: number): CharacterIdentity | undefined {
    const found = this.byNumber.get(number);
    return found ? { ...found } : undefined;
  }

  get size(): number {
    return this.byKey.size;
  }
}
