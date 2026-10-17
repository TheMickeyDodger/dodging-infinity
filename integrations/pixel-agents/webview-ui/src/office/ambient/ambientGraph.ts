/**
 * ONE PEDESTRIAN GRAPH over the whole world, for ambient routing.
 *
 * Hubs sit on a lattice (one per HUB_SPACING x HUB_SPACING block that has walkable floor, at the
 * walkable tile nearest the block centre). A multi-source BFS from every hub assigns each walkable
 * tile to its nearest hub and records the step back toward it (a Voronoi partition with parent
 * pointers). A walkable pocket that won no lattice block (for example a small enclosed room sharing
 * its blocks with a larger area) is never reached by that BFS, so a SWEEP then seeds an extra hub at
 * each still-unassigned walkable tile and continues the BFS, until EVERY walkable tile is assigned. Two hubs are joined where their cells touch; the edge keeps the cheapest
 * touching pair, so its tile path is "back to hub A" + one step + "on to hub B" — no search needed.
 *
 * A route between any two walkable tiles is then: the start's parent chain to its hub, a
 * Dijkstra path over hubs (a few hundred nodes), each edge's tile path, and the goal's chain from
 * its hub — with loops pruned. Building is O(cells); a route query costs O(hubs log hubs) plus the
 * path length. Water, walls, void and furniture are excluded by the walk mask, so bridges and doors
 * are simply the walkable tiles that connect cells. Because every walkable component carries at least
 * one hub, the hub-graph components ARE the walkable components: a pocket unreachable from the rest
 * of the world is reported as its own component (stats.components, componentTiles), never dropped.
 */
import type { WalkMask } from './ambientPath.js';

export interface GraphStats {
  hubs: number;
  /** Hubs placed by the lattice. */
  latticeHubs: number;
  /** Hubs added by the post-BFS sweep for pockets the lattice missed. */
  pocketHubs: number;
  edges: number;
  /** Walkable components (every walkable tile belongs to exactly one). */
  components: number;
  largestComponentHubs: number;
  /** Walkable tiles in the largest component. */
  largestComponentTiles: number;
  /** Walkable tiles in the mask. */
  walkableTiles: number;
  /** Walkable tiles assigned to a hub. Equals walkableTiles by construction (the sweep). */
  assignedTiles: number;
}

export interface GraphRoute {
  status: 'found' | 'same' | 'unwalkable' | 'unreachable' | 'too-long';
  /** Tile indices (row * cols + col), excluding the start, including the goal. */
  path: Int32Array;
  /** Hubs settled by the Dijkstra search (its work measure). */
  hubsSettled: number;
}

export class PedestrianGraph {
  readonly mask: WalkMask;
  readonly spacing: number;
  /** Hub tile index per hub id. */
  readonly hubTile: Int32Array;
  /** Hub id per tile (-1 = not walkable). */
  readonly hubOf: Int32Array;
  /** Next tile toward the tile's hub (-1 at the hub itself or unwalkable). */
  readonly parent: Int32Array;
  /** BFS distance (tiles) to the tile's hub. */
  readonly dist: Int32Array;
  /** Adjacency: for hub h, neighbours in adjStart[h]..adjStart[h+1]. */
  private readonly adjStart: Int32Array;
  private readonly adjHub: Int32Array;
  private readonly adjCost: Int32Array;
  /** Boundary tiles of each adjacency entry: aSide in the source hub's cell, bSide in the other. */
  private readonly adjA: Int32Array;
  private readonly adjB: Int32Array;
  /** Component id per hub. */
  readonly component: Int32Array;
  /** Walkable tiles per component id. */
  readonly componentTiles: Int32Array;
  readonly stats: GraphStats;

  constructor(mask: WalkMask, spacing: number) {
    this.mask = mask;
    this.spacing = spacing;
    const { cols, rows, walkable } = mask;
    const n = cols * rows;

    // ── hubs on a lattice ──
    const hubs: number[] = [];
    for (let br = 0; br < rows; br += spacing)
      for (let bc = 0; bc < cols; bc += spacing) {
        const cr = br + spacing / 2;
        const cc = bc + spacing / 2;
        let best = -1;
        let bestD = Infinity;
        for (let r = br; r < Math.min(rows, br + spacing); r++)
          for (let c = bc; c < Math.min(cols, bc + spacing); c++) {
            const i = r * cols + c;
            if (walkable[i] !== 1) continue;
            const d = (r - cr) ** 2 + (c - cc) ** 2;
            if (d < bestD) {
              bestD = d;
              best = i;
            }
          }
        if (best >= 0) hubs.push(best);
      }
    const latticeHubs = hubs.length;

    // ── multi-source BFS: nearest hub + parent pointer per walkable tile ──
    this.hubOf = new Int32Array(n).fill(-1);
    this.parent = new Int32Array(n).fill(-1);
    this.dist = new Int32Array(n).fill(-1);
    const queue = new Int32Array(n);
    let head = 0;
    let tail = 0;
    for (let h = 0; h < hubs.length; h++) {
      this.hubOf[hubs[h]] = h;
      this.dist[hubs[h]] = 0;
      queue[tail++] = hubs[h];
    }
    let walkableTiles = 0;
    for (let i = 0; i < n; i++) if (walkable[i] === 1) walkableTiles++;
    const runBfs = (): void => {
    while (head < tail) {
      const u = queue[head++];
      const uc = u % cols;
      const ur = (u - uc) / cols;
      for (let k = 0; k < 4; k++) {
        const vc = k === 2 ? uc - 1 : k === 3 ? uc + 1 : uc;
        const vr = k === 0 ? ur - 1 : k === 1 ? ur + 1 : ur;
        if (vc < 0 || vr < 0 || vc >= cols || vr >= rows) continue;
        const v = vr * cols + vc;
        if (walkable[v] !== 1 || this.hubOf[v] !== -1) continue;
        this.hubOf[v] = this.hubOf[u];
        this.parent[v] = u;
        this.dist[v] = this.dist[u] + 1;
        queue[tail++] = v;
      }
    }
    };
    runBfs();
    // ── sweep: every walkable tile the lattice BFS never reached seeds its own hub ──
    for (let i = 0; i < n; i++) {
      if (walkable[i] !== 1 || this.hubOf[i] !== -1) continue;
      this.hubOf[i] = hubs.length;
      this.dist[i] = 0;
      hubs.push(i);
      queue[tail++] = i;
      runBfs();
    }
    this.hubTile = Int32Array.from(hubs);
    let assignedTiles = 0;
    for (let i = 0; i < n; i++) if (this.hubOf[i] >= 0) assignedTiles++;

    // ── edges where cells touch: keep the cheapest boundary pair per hub pair ──
    const best = new Map<number, { cost: number; a: number; b: number }>();
    const H = hubs.length;
    for (let u = 0; u < n; u++) {
      const hu = this.hubOf[u];
      if (hu < 0) continue;
      const uc = u % cols;
      for (const v of [uc + 1 < cols ? u + 1 : -1, u + cols < n ? u + cols : -1]) {
        if (v < 0) continue;
        const hv = this.hubOf[v];
        if (hv < 0 || hv === hu) continue;
        const cost = this.dist[u] + 1 + this.dist[v];
        const lo = Math.min(hu, hv);
        const hi = Math.max(hu, hv);
        const key = lo * H + hi;
        const prev = best.get(key);
        if (!prev || cost < prev.cost) {
          // store with `a` on the lo hub's side
          best.set(key, hu === lo ? { cost, a: u, b: v } : { cost, a: v, b: u });
        }
      }
    }
    const degree = new Int32Array(H + 1);
    for (const key of best.keys()) {
      degree[Math.floor(key / H)]++;
      degree[key % H]++;
    }
    this.adjStart = new Int32Array(H + 1);
    for (let h = 0; h < H; h++) this.adjStart[h + 1] = this.adjStart[h] + degree[h];
    const m = this.adjStart[H];
    this.adjHub = new Int32Array(m);
    this.adjCost = new Int32Array(m);
    this.adjA = new Int32Array(m);
    this.adjB = new Int32Array(m);
    const fill = this.adjStart.slice(0, H);
    for (const [key, e] of best) {
      const lo = Math.floor(key / H);
      const hi = key % H;
      let p = fill[lo]++;
      this.adjHub[p] = hi;
      this.adjCost[p] = e.cost;
      this.adjA[p] = e.a;
      this.adjB[p] = e.b;
      p = fill[hi]++;
      this.adjHub[p] = lo;
      this.adjCost[p] = e.cost;
      this.adjA[p] = e.b;
      this.adjB[p] = e.a;
    }

    // ── components over hubs ──
    this.component = new Int32Array(H).fill(-1);
    let components = 0;
    let largest = 0;
    const stack: number[] = [];
    for (let h = 0; h < H; h++) {
      if (this.component[h] !== -1) continue;
      let size = 0;
      stack.push(h);
      this.component[h] = components;
      while (stack.length) {
        const x = stack.pop()!;
        size++;
        for (let p = this.adjStart[x]; p < this.adjStart[x + 1]; p++) {
          const y = this.adjHub[p];
          if (this.component[y] === -1) {
            this.component[y] = components;
            stack.push(y);
          }
        }
      }
      largest = Math.max(largest, size);
      components++;
    }
    this.componentTiles = new Int32Array(components);
    for (let i = 0; i < n; i++) if (this.hubOf[i] >= 0) this.componentTiles[this.component[this.hubOf[i]]]++;
    let largestTiles = 0;
    for (const t of this.componentTiles) largestTiles = Math.max(largestTiles, t);
    this.stats = {
      hubs: H,
      latticeHubs,
      pocketHubs: H - latticeHubs,
      edges: best.size,
      components,
      largestComponentHubs: largest,
      largestComponentTiles: largestTiles,
      walkableTiles,
      assignedTiles,
    };
  }

  /** The component id of a walkable tile (-1 when not walkable). */
  componentOf(tile: number): number {
    const h = this.hubOf[tile];
    return h >= 0 ? this.component[h] : -1;
  }

  /** True when both tiles are walkable and in the same connected component. */
  connected(a: number, b: number): boolean {
    const ha = this.hubOf[a];
    const hb = this.hubOf[b];
    return ha >= 0 && hb >= 0 && this.component[ha] === this.component[hb];
  }

  /** Tiles from `t` up to (and including) its hub, starting with `t`. */
  private chainToHub(t: number, out: number[]): void {
    for (let x = t; x !== -1; x = this.parent[x]) out.push(x);
  }

  /** A route from tile `from` to tile `to`, at most `maxLength` tiles long. */
  route(from: number, to: number, maxLength: number): GraphRoute {
    if (from === to) return { status: 'same', path: new Int32Array(0), hubsSettled: 0 };
    const ha = this.hubOf[from];
    const hb = this.hubOf[to];
    if (ha < 0 || hb < 0) return { status: 'unwalkable', path: new Int32Array(0), hubsSettled: 0 };
    if (this.component[ha] !== this.component[hb])
      return { status: 'unreachable', path: new Int32Array(0), hubsSettled: 0 };

    // Dijkstra over hubs.
    const H = this.hubTile.length;
    const distH = new Float64Array(H).fill(Infinity);
    const prevH = new Int32Array(H).fill(-1);
    const prevEdge = new Int32Array(H).fill(-1);
    const done = new Uint8Array(H);
    const heap: Array<[number, number]> = [[0, ha]];
    distH[ha] = 0;
    let settled = 0;
    while (heap.length) {
      const [d, h] = heapPop(heap);
      if (done[h]) continue;
      done[h] = 1;
      settled++;
      if (h === hb) break;
      for (let p = this.adjStart[h]; p < this.adjStart[h + 1]; p++) {
        const y = this.adjHub[p];
        const nd = d + this.adjCost[p];
        if (nd < distH[y]) {
          distH[y] = nd;
          prevH[y] = h;
          prevEdge[y] = p;
          heapPush(heap, [nd, y]);
        }
      }
    }
    if (!done[hb]) return { status: 'unreachable', path: new Int32Array(0), hubsSettled: settled };

    // Hub sequence ha .. hb, then stitch tile paths.
    const edges: number[] = [];
    for (let h = hb; h !== ha; h = prevH[h]) edges.push(prevEdge[h]);
    edges.reverse();
    const tiles: number[] = [];
    this.chainToHub(from, tiles); // from .. hubA
    for (const p of edges) {
      // current hub -> boundary a (reverse of a's chain), a -> b, b -> next hub (b's chain)
      const back: number[] = [];
      this.chainToHub(this.adjA[p], back); // a .. hub
      for (let i = back.length - 2; i >= 0; i--) tiles.push(back[i]);
      this.chainToHub(this.adjB[p], tiles); // b .. next hub
    }
    const tail: number[] = [];
    this.chainToHub(to, tail); // to .. hubB
    for (let i = tail.length - 2; i >= 0; i--) tiles.push(tail[i]);

    // Prune loops (a tile visited twice cuts out everything between), drop the start.
    const seen = new Map<number, number>();
    const pruned: number[] = [];
    for (const t of tiles) {
      const at = seen.get(t);
      if (at !== undefined) {
        for (let i = pruned.length - 1; i > at; i--) seen.delete(pruned[i]);
        pruned.length = at + 1;
        continue;
      }
      seen.set(t, pruned.length);
      pruned.push(t);
    }
    pruned.shift();
    if (pruned.length > maxLength)
      return { status: 'too-long', path: new Int32Array(0), hubsSettled: settled };
    return { status: 'found', path: Int32Array.from(pruned), hubsSettled: settled };
  }
}

function heapPush(heap: Array<[number, number]>, item: [number, number]): void {
  heap.push(item);
  let i = heap.length - 1;
  while (i > 0) {
    const p = (i - 1) >> 1;
    if (heap[p][0] <= heap[i][0]) break;
    [heap[p], heap[i]] = [heap[i], heap[p]];
    i = p;
  }
}

function heapPop(heap: Array<[number, number]>): [number, number] {
  const top = heap[0];
  const last = heap.pop()!;
  if (heap.length > 0) {
    heap[0] = last;
    let i = 0;
    for (;;) {
      const l = 2 * i + 1;
      const r = l + 1;
      let m = i;
      if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
      if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
      if (m === i) break;
      [heap[m], heap[i]] = [heap[i], heap[m]];
      i = m;
    }
  }
  return top;
}
