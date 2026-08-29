// MAIA Knowledge Graph — deterministic layout (FASE 4O.4 STEP 4 / STEP 14,
// revised FASE 4O.4A visual-review fix #2).
//
// Graph Data != Graph Layout State: this module computes a position map
// keyed by node id, entirely separate from `GraphResponse` itself and
// never mutating it. A future layout mode (organization/plant/process/
// user-custom, STEP 14) is just a different function with this same
// signature (`GraphResponse -> LayoutMap`) swapped in -- nothing about
// the graph, camera, or interaction code needs to know which layout
// produced the positions it's rendering.
//
// Deliberately NOT a live force simulation: positions are computed once
// from the graph's own structure (domain membership, navigation edges)
// using an id-seeded deterministic hash for jitter -- zero
// `Math.random()` anywhere in this file. Re-running this function on an
// unchanged `GraphResponse` always returns the same positions (STEP
// 18-M), and there is nothing to "settle" frame-to-frame, which is also
// how STEP 4's "avoid a constantly vibrating force graph" is satisfied
// by construction rather than by tuning damping constants.
//
// FASE 4O.4A fix #2: the previous version placed DOMAIN nodes on a flat
// XZ ring (constant y=0) and jittered entries/entities with a polar
// angle + a separate, small linear Y offset -- which reads as a flat
// diagram viewed from an angle, not real volume. Domains now sit on a
// small Fibonacci sphere (still index-only, deterministic, no
// randomness) and every cluster member is placed with true spherical
// jitter (independent theta/phi/radius hashes) around its center,
// occupying a shallow 3D volume instead of a disc.
import type { GraphResponse } from '../types/graph';

export interface LayoutPosition {
  x: number;
  y: number;
  z: number;
}

export type LayoutMap = Record<string, LayoutPosition>;

// FASE 4O.4A fix #5 (real-screenshot review): the previous single
// shared scale factor shrank GLOBAL domain spacing and LOCAL cluster
// spacing by the same amount, which produced the opposite of what the
// graph needed -- domain regions still read as separated islands while
// nodes/labels *inside* a cluster crowded into each other. These are
// now two independent concerns (STEP B: "two-level spacing"): domain
// (global) placement uses a smaller baseline radius and shrinks further
// for small graphs; local cluster radius uses a larger baseline and
// stays constant regardless of dataset size, since crowding is a local
// problem that doesn't improve by shrinking everything uniformly.
const DOMAIN_SPHERE_RADIUS = 3.0;
const ENTRY_CLUSTER_RADIUS = 2.4;
const ENTITY_CLUSTER_RADIUS = 1.3;
const UNGROUPED_RADIUS = 6.0;

// Applies only to global domain-nucleus spacing (STEP 4/11, still a
// generic node-count rule, not a special case for the current 22-entry
// test brain): a young/sparse Second Brain pulls domain regions in
// toward a compact, intimate composition; a "medium" graph (~60-120
// nodes) uses the baseline as-is; a large graph grows only modestly,
// since at scale nodes should read as a denser point-like field rather
// than the layout spreading further outward.
export function domainRadiusScaleForNodeCount(nodeCount: number): number {
  if (nodeCount <= 8) return 0.4;
  if (nodeCount >= 120) return 1.15;
  if (nodeCount <= 60) {
    const t = (nodeCount - 8) / (60 - 8);
    return 0.4 + t * 0.6;
  }
  const t = (nodeCount - 60) / (120 - 60);
  return 1.0 + t * 0.15;
}

// FNV-1a -- fast, deterministic, no external dependency. Only used to
// spread siblings within a cluster; never used to decide *whether* two
// nodes are related (that always comes from real domains/edges).
function hash01(seed: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 4294967296;
}

function average(points: LayoutPosition[]): LayoutPosition | null {
  if (points.length === 0) return null;
  const n = points.length;
  return {
    x: points.reduce((s, p) => s + p.x, 0) / n,
    y: points.reduce((s, p) => s + p.y, 0) / n,
    z: points.reduce((s, p) => s + p.z, 0) / n,
  };
}

// Evenly distributes `n` points across a sphere surface using the
// golden-angle Fibonacci lattice -- purely index-based (no hashing, no
// randomness), so domain placement is stable and won't clump even for
// small counts. This is what gives the whole composition a rounded,
// organic silhouette instead of a flat ring.
function fibonacciSpherePoint(i: number, n: number, radius: number): LayoutPosition {
  if (n <= 1) return { x: 0, y: 0, z: 0 };
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (i / (n - 1)) * 2; // 1 .. -1
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = goldenAngle * i;
  return {
    x: Math.cos(theta) * r * radius,
    y: y * radius * 0.7, // slightly flattened so the brain still reads horizontally
    z: Math.sin(theta) * r * radius,
  };
}

// True spherical jitter around `center`: independent hashes for the two
// angles and the radius fraction produce a point spread through a small
// 3D volume (not a flat disc + separate depth term).
function jitterAroundSphere(id: string, salt: string, center: LayoutPosition, maxRadius: number): LayoutPosition {
  const theta = hash01(`${id}:${salt}:theta`) * Math.PI * 2;
  const cosPhi = hash01(`${id}:${salt}:phi`) * 2 - 1; // uniform on sphere, not just the equator
  const sinPhi = Math.sqrt(Math.max(0, 1 - cosPhi * cosPhi));
  const r = maxRadius * (0.35 + 0.65 * hash01(`${id}:${salt}:r`));
  return {
    x: center.x + r * sinPhi * Math.cos(theta),
    y: center.y + r * cosPhi * 0.8, // gently flattened, still a real volume
    z: center.z + r * sinPhi * Math.sin(theta),
  };
}

export function computeGraphLayout(graph: GraphResponse): LayoutMap {
  const positions: LayoutMap = {};

  const domainNodes = graph.nodes
    .filter((n) => n.kind === 'DOMAIN')
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id));
  const entryNodes = graph.nodes.filter((n) => n.kind === 'ENTRY');
  const entityNodes = graph.nodes.filter((n) => n.kind === 'ENTITY');

  // Two-level spacing (STEP B): only the GLOBAL domain-nucleus radius
  // scales with dataset size; LOCAL cluster radii stay at their fixed
  // baseline regardless of how many nodes exist, so a young/sparse
  // brain gets tighter domain regions without its individual clusters
  // crowding into overlapping nodes/labels.
  const domainScale = domainRadiusScaleForNodeCount(graph.nodes.length);
  const domainSphereRadius = DOMAIN_SPHERE_RADIUS * domainScale;
  const entryClusterRadius = ENTRY_CLUSTER_RADIUS;
  const entityClusterRadius = ENTITY_CLUSTER_RADIUS;
  const ungroupedRadius = UNGROUPED_RADIUS;

  domainNodes.forEach((node, i) => {
    positions[node.id] = fibonacciSpherePoint(i, domainNodes.length, domainSphereRadius);
  });

  const domainCentroid = (domainValues: string[]): LayoutPosition | null =>
    average(domainValues.map((d) => positions[`domain:${d}`]).filter(Boolean) as LayoutPosition[]);

  // Entries cluster near the centroid of the domains they belong to (a
  // real field on the entry, not a co-occurrence guess); entries with no
  // domain fall back to a distinct outer "ungrouped" shell so they never
  // silently collapse onto the origin.
  for (const node of entryNodes) {
    const center = domainCentroid(node.domains) ?? { x: 0, y: 0, z: 0 };
    const inCluster = node.domains.length > 0;
    const radius = inCluster ? entryClusterRadius : ungroupedRadius;
    positions[node.id] = jitterAroundSphere(node.id, 'entry', center, radius);
  }

  // Entities cluster near the entries that navigate to them (a real
  // NAVIGATION edge, not an assumption); isolated entities fall back to
  // their own outer shell rather than stacking at the origin.
  const entityConnections: Record<string, string[]> = {};
  for (const edge of graph.edges) {
    if (edge.kind === 'NAVIGATION' && edge.basis === 'SHARED_ENTITY') {
      (entityConnections[edge.target] ??= []).push(edge.source);
    }
  }
  for (const node of entityNodes) {
    const connected = entityConnections[node.id] ?? [];
    const pts = connected.map((id) => positions[id]).filter(Boolean) as LayoutPosition[];
    const center = average(pts) ?? { x: 0, y: 0, z: 0 };
    const radius = pts.length > 0 ? entityClusterRadius : ungroupedRadius * 1.2;
    positions[node.id] = jitterAroundSphere(node.id, 'entity', center, radius);
  }

  // Defensive: any node kind not covered above (should not happen given
  // the frozen ENTRY/ENTITY/DOMAIN vocabulary) still gets a position
  // rather than being silently dropped from rendering.
  for (const node of graph.nodes) {
    if (!positions[node.id]) positions[node.id] = { x: 0, y: 0, z: 0 };
  }

  return positions;
}
