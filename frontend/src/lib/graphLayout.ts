// MAIA Knowledge Graph — deterministic layout (FASE 4O.4 STEP 4 / STEP 14).
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
import type { GraphResponse } from '../types/graph';

export interface LayoutPosition {
  x: number;
  y: number;
  z: number;
}

export type LayoutMap = Record<string, LayoutPosition>;

const DOMAIN_RING_RADIUS = 15;
const ENTRY_CLUSTER_RADIUS = 5.5;
const ENTITY_CLUSTER_RADIUS = 3;
const UNGROUPED_RADIUS = 20;

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

function hashAngle(id: string, salt: string): number {
  return hash01(`${id}:${salt}`) * Math.PI * 2;
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

export function computeGraphLayout(graph: GraphResponse): LayoutMap {
  const positions: LayoutMap = {};

  const domainNodes = graph.nodes
    .filter((n) => n.kind === 'DOMAIN')
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id));
  const entryNodes = graph.nodes.filter((n) => n.kind === 'ENTRY');
  const entityNodes = graph.nodes.filter((n) => n.kind === 'ENTITY');

  if (domainNodes.length === 1) {
    positions[domainNodes[0].id] = { x: 0, y: 0, z: 0 };
  } else {
    domainNodes.forEach((node, i) => {
      const angle = (i / Math.max(domainNodes.length, 1)) * Math.PI * 2;
      positions[node.id] = {
        x: Math.cos(angle) * DOMAIN_RING_RADIUS,
        y: 0,
        z: Math.sin(angle) * DOMAIN_RING_RADIUS,
      };
    });
  }

  const domainCentroid = (domainValues: string[]): LayoutPosition | null =>
    average(domainValues.map((d) => positions[`domain:${d}`]).filter(Boolean) as LayoutPosition[]);

  // Entries cluster near the centroid of the domains they belong to (a
  // real field on the entry, not a co-occurrence guess); entries with no
  // domain fall back to a distinct outer "ungrouped" ring so they never
  // silently collapse onto the origin.
  for (const node of entryNodes) {
    const center = domainCentroid(node.domains) ?? { x: 0, y: 0, z: 0 };
    const inCluster = node.domains.length > 0;
    const angle = hashAngle(node.id, 'entry');
    const radius = (inCluster ? ENTRY_CLUSTER_RADIUS : UNGROUPED_RADIUS) * (0.45 + 0.55 * hash01(`${node.id}:r`));
    const depth = (hash01(`${node.id}:y`) - 0.5) * 4;
    positions[node.id] = {
      x: center.x + Math.cos(angle) * radius,
      y: center.y + depth,
      z: center.z + Math.sin(angle) * radius,
    };
  }

  // Entities cluster near the entries that navigate to them (a real
  // NAVIGATION edge, not an assumption); isolated entities fall back to
  // their own outer ring rather than stacking at the origin.
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
    const angle = hashAngle(node.id, 'entity');
    const radius = (pts.length > 0 ? ENTITY_CLUSTER_RADIUS : UNGROUPED_RADIUS * 1.3) * (0.5 + 0.5 * hash01(`${node.id}:r`));
    const depth = (hash01(`${node.id}:y`) - 0.5) * 3;
    positions[node.id] = {
      x: center.x + Math.cos(angle) * radius,
      y: center.y + depth,
      z: center.z + Math.sin(angle) * radius,
    };
  }

  // Defensive: any node kind not covered above (should not happen given
  // the frozen ENTRY/ENTITY/DOMAIN vocabulary) still gets a position
  // rather than being silently dropped from rendering.
  for (const node of graph.nodes) {
    if (!positions[node.id]) positions[node.id] = { x: 0, y: 0, z: 0 };
  }

  return positions;
}
