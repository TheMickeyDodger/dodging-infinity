/**
 * BOUNDED path search for ambient residents.
 *
 * The office's own findPath is an unbounded BFS, fine for a room but not for a 352x224 world
 * crossed by dozens of walkers. This is A* (4-connected, Manhattan heuristic) over a walkability
 * mask, with two hard limits: at most `maxExpansions` nodes expanded per search, and no result
 * longer than `maxLength` tiles. A search that hits either limit reports why and returns no path,
 * so the cost of one search is bounded no matter how large the world is. Buffers are allocated
 * once per grid and reused (generation stamps), so a search allocates only its result.
 */

export interface WalkMask {
  cols: number;
  rows: number;
  /** 1 = walkable for ambient residents. */
  walkable: Uint8Array;
}

export type PathStatus = 'found' | 'same' | 'blocked' | 'budget' | 'too-long' | 'unreachable';

export interface PathResult {
  status: PathStatus;
  /** Excludes the start, includes the goal; empty unless status is 'found'. */
  path: Array<{ col: number; row: number }>;
  expanded: number;
}

export class AmbientPathfinder {
  readonly mask: WalkMask;
  private readonly g: Int32Array;
  private readonly from: Int32Array;
  private readonly seen: Uint32Array;
  private readonly closed: Uint32Array;
  private generation = 0;
  /** Binary heap of node indices keyed by f-score. */
  private heapNodes: Int32Array;
  private heapF: Int32Array;
  private heapSize = 0;

  constructor(mask: WalkMask) {
    this.mask = mask;
    const n = mask.cols * mask.rows;
    this.g = new Int32Array(n);
    this.from = new Int32Array(n);
    this.seen = new Uint32Array(n);
    this.closed = new Uint32Array(n);
    this.heapNodes = new Int32Array(1024);
    this.heapF = new Int32Array(1024);
  }

  isWalkable(col: number, row: number): boolean {
    const { cols, rows, walkable } = this.mask;
    return col >= 0 && row >= 0 && col < cols && row < rows && walkable[row * cols + col] === 1;
  }

  search(
    startCol: number,
    startRow: number,
    goalCol: number,
    goalRow: number,
    maxExpansions: number,
    maxLength: number,
  ): PathResult {
    if (startCol === goalCol && startRow === goalRow) return { status: 'same', path: [], expanded: 0 };
    if (!this.isWalkable(goalCol, goalRow)) return { status: 'blocked', path: [], expanded: 0 };
    if (Math.abs(goalCol - startCol) + Math.abs(goalRow - startRow) > maxLength)
      return { status: 'too-long', path: [], expanded: 0 };

    const { cols, rows, walkable } = this.mask;
    this.generation = (this.generation + 1) >>> 0;
    if (this.generation === 0) {
      this.seen.fill(0);
      this.closed.fill(0);
      this.generation = 1;
    }
    const gen = this.generation;
    const start = startRow * cols + startCol;
    const goal = goalRow * cols + goalCol;
    this.heapSize = 0;
    this.g[start] = 0;
    this.from[start] = -1;
    this.seen[start] = gen;
    this.push(start, manhattan(startCol, startRow, goalCol, goalRow));

    let expanded = 0;
    while (this.heapSize > 0) {
      const node = this.pop();
      if (this.closed[node] === gen) continue;
      this.closed[node] = gen;
      if (node === goal) {
        if (this.g[node] > maxLength) return { status: 'too-long', path: [], expanded };
        return { status: 'found', path: this.reconstruct(goal, start, cols), expanded };
      }
      if (++expanded > maxExpansions) return { status: 'budget', path: [], expanded };
      const col = node % cols;
      const row = (node - col) / cols;
      const gNext = this.g[node] + 1;
      if (gNext > maxLength) continue;
      // Fixed neighbour order (up, down, left, right): deterministic tie-breaking.
      for (let k = 0; k < 4; k++) {
        const nc = k === 2 ? col - 1 : k === 3 ? col + 1 : col;
        const nr = k === 0 ? row - 1 : k === 1 ? row + 1 : row;
        if (nc < 0 || nr < 0 || nc >= cols || nr >= rows) continue;
        const next = nr * cols + nc;
        if (walkable[next] !== 1 || this.closed[next] === gen) continue;
        if (this.seen[next] === gen && this.g[next] <= gNext) continue;
        this.seen[next] = gen;
        this.g[next] = gNext;
        this.from[next] = node;
        this.push(next, gNext + manhattan(nc, nr, goalCol, goalRow));
      }
    }
    return { status: 'unreachable', path: [], expanded };
  }

  private reconstruct(goal: number, start: number, cols: number): Array<{ col: number; row: number }> {
    const path: Array<{ col: number; row: number }> = [];
    for (let node = goal; node !== start; node = this.from[node]) {
      path.push({ col: node % cols, row: Math.floor(node / cols) });
    }
    return path.reverse();
  }

  private push(node: number, f: number): void {
    if (this.heapSize === this.heapNodes.length) {
      const nodes = new Int32Array(this.heapNodes.length * 2);
      const fs = new Int32Array(this.heapF.length * 2);
      nodes.set(this.heapNodes);
      fs.set(this.heapF);
      this.heapNodes = nodes;
      this.heapF = fs;
    }
    let i = this.heapSize++;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.heapF[parent] <= f) break;
      this.heapNodes[i] = this.heapNodes[parent];
      this.heapF[i] = this.heapF[parent];
      i = parent;
    }
    this.heapNodes[i] = node;
    this.heapF[i] = f;
  }

  private pop(): number {
    const top = this.heapNodes[0];
    const lastNode = this.heapNodes[--this.heapSize];
    const lastF = this.heapF[this.heapSize];
    let i = 0;
    for (;;) {
      let child = 2 * i + 1;
      if (child >= this.heapSize) break;
      if (child + 1 < this.heapSize && this.heapF[child + 1] < this.heapF[child]) child++;
      if (this.heapF[child] >= lastF) break;
      this.heapNodes[i] = this.heapNodes[child];
      this.heapF[i] = this.heapF[child];
      i = child;
    }
    this.heapNodes[i] = lastNode;
    this.heapF[i] = lastF;
    return top;
  }
}

function manhattan(c1: number, r1: number, c2: number, r2: number): number {
  return Math.abs(c1 - c2) + Math.abs(r1 - r2);
}
