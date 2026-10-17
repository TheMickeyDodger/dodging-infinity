/**
 * PRESENTATION GENERATION: bumped whenever the art a STATIC render depends on is replaced — floor,
 * wall and carpet sprite sets and the furniture catalogue (asset loads and reloads). Caches of a
 * static render (the world Map's static layers) compare it to know their pixels are stale. Layout
 * edits are tracked separately, by the identity of the layout / furniture / tile arrays.
 */
let generation = 0;

export function bumpPresentationGeneration(): void {
  generation++;
}

export function presentationGeneration(): number {
  return generation;
}
