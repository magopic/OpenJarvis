// MAIA Knowledge Graph — visual grammar (FASE 4O.4 STEP 5).
//
// Pure presentation mapping: every function here only READS meaning
// fields (kind, entry_type, lifecycle, status, derived) to pick a
// shape/color/size/glow -- it never adds, removes, or renames a
// meaning field, and never infers a relationship that isn't already a
// real `GraphEdge`. `VisualNode`/`VisualEdge` always keep the original
// backend id, so a caller can always trace a visual object back to its
// exact Second Brain (or derived-navigation) counterpart.
import type { GraphEdge, GraphEntryType, GraphNode, GraphResponse } from '../types/graph';

export type NodeShape = 'icosahedron' | 'sphere' | 'octahedron' | 'tetrahedron' | 'box' | 'cone' | 'cylinder';

export interface VisualStyle {
  colorVar: string;
  shape: NodeShape;
  size: number;
  glow: number;
}

// DOMAIN and ENTITY are structural/derived -- amber and purple keep them
// visually distinct from the cyan "real memory" ENTRY family (STEP 5:
// node kinds must be visually distinguishable at a glance).
const KIND_STYLE: Record<'DOMAIN' | 'ENTITY', VisualStyle> = {
  DOMAIN: { colorVar: '--color-accent-amber', shape: 'icosahedron', size: 1.6, glow: 0.85 },
  ENTITY: { colorVar: '--color-accent-purple', shape: 'sphere', size: 0.42, glow: 0.3 },
};

// One entry per Experience Cycle stage the phase explicitly asked to
// distinguish, plus the remaining EntryTypes with a coherent, less
// emphasized neutral treatment -- not ten unrelated random styles: shape
// communicates the *stage* (problem=alert octahedron, decision=solid
// box, action=directional cone, outcome/lesson=resolved sphere family),
// size communicates weight (resolved/authoritative stages read slightly
// larger), and glow communicates emphasis, all independent axes.
const ENTRY_TYPE_STYLE: Record<GraphEntryType, VisualStyle> = {
  PROBLEM: { colorVar: '--color-error', shape: 'octahedron', size: 0.68, glow: 0.55 },
  HYPOTHESIS: { colorVar: '--color-accent-amber', shape: 'tetrahedron', size: 0.64, glow: 0.4 },
  DECISION: { colorVar: '--color-accent-purple', shape: 'box', size: 0.74, glow: 0.55 },
  ACTION: { colorVar: '--color-accent', shape: 'cone', size: 0.66, glow: 0.5 },
  OUTCOME: { colorVar: '--color-success', shape: 'sphere', size: 0.76, glow: 0.55 },
  LESSON: { colorVar: '--color-accent', shape: 'icosahedron', size: 0.8, glow: 0.75 },
  EVENT: { colorVar: '--color-text-secondary', shape: 'sphere', size: 0.6, glow: 0.25 },
  OBSERVATION: { colorVar: '--color-text-secondary', shape: 'sphere', size: 0.6, glow: 0.25 },
  PROCEDURE: { colorVar: '--color-text-secondary', shape: 'cylinder', size: 0.62, glow: 0.3 },
  MEETING_NOTE: { colorVar: '--color-text-secondary', shape: 'sphere', size: 0.6, glow: 0.25 },
};

const DEFAULT_ENTRY_STYLE: VisualStyle = { colorVar: '--color-text-secondary', shape: 'sphere', size: 0.6, glow: 0.25 };

export function styleForNode(node: GraphNode): VisualStyle {
  if (node.kind === 'DOMAIN') return KIND_STYLE.DOMAIN;
  if (node.kind === 'ENTITY') return KIND_STYLE.ENTITY;
  if (node.entry_type && ENTRY_TYPE_STYLE[node.entry_type]) return ENTRY_TYPE_STYLE[node.entry_type];
  return DEFAULT_ENTRY_STYLE;
}

// Lifecycle is real stored data (archived_at/superseded_by), not an
// invented visual category -- ACTIVE reads at full presence, SUPERSEDED
// and ARCHIVED read progressively quieter so history never masquerades
// as current truth.
export function lifecycleOpacity(node: GraphNode): number {
  if (node.kind !== 'ENTRY') return 1;
  if (node.lifecycle === 'ARCHIVED') return 0.32;
  if (node.lifecycle === 'SUPERSEDED') return 0.5;
  return 1;
}

export interface EdgeStyle {
  colorVar: string;
  opacity: number;
  width: number;
  dashed: boolean;
}

// CONFIRMED vs. PROPOSED is the one distinction the phase repeatedly
// insists must never be blurred -- solid/bright vs. dashed/muted, never
// the same treatment. SUPERSESSION gets its own color (it is a
// structural fact, not a semantic claim). NAVIGATION is deliberately
// the quietest edge on the graph: real, but mechanical, never implying
// causality from mere co-occurrence.
export function styleForEdge(edge: GraphEdge): EdgeStyle {
  if (edge.kind === 'NAVIGATION') {
    return { colorVar: '--color-border', opacity: 0.16, width: 0.5, dashed: false };
  }
  if (edge.kind === 'SUPERSESSION') {
    return { colorVar: '--color-accent-purple', opacity: 0.75, width: 1.6, dashed: false };
  }
  // RELATIONSHIP
  if (edge.status === 'PROPOSED') {
    return { colorVar: '--color-accent-amber', opacity: 0.5, width: 1.0, dashed: true };
  }
  if (edge.status === 'REJECTED') {
    // Not shown by default (server-side filter excludes it); if a
    // caller explicitly opts in, render it unmistakably de-emphasized
    // rather than inventing a "confirmed-looking" style for it.
    return { colorVar: '--color-error', opacity: 0.12, width: 0.5, dashed: true };
  }
  return { colorVar: '--color-accent', opacity: 0.85, width: 1.4, dashed: false };
}

export function edgeLabel(edge: GraphEdge): string {
  if (edge.kind === 'NAVIGATION') return edge.basis === 'SHARED_DOMAIN' ? 'same domain' : 'same entity';
  if (edge.kind === 'SUPERSESSION') return 'supersedes';
  return edge.basis;
}

// -- deterministic id-based color/geometry resolution ------------------

export function resolveCssColor(varName: string, fallback = '#8d8d93'): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  return value || fallback;
}

// -- bounded merge for neighborhood expansion (STEP 8 / STEP 18-E) -----

// Adds `addition` on top of `base`, de-duplicating by id so repeated
// "click node -> expand neighborhood" calls never produce two React
// keys for the same node/edge. Meaning fields are never modified during
// merge -- a node/edge already present keeps its first-seen values.
export function mergeGraphResponses(base: GraphResponse, addition: GraphResponse): GraphResponse {
  const nodeIds = new Set(base.nodes.map((n) => n.id));
  const edgeIds = new Set(base.edges.map((e) => e.id));
  const mergedNodes = [...base.nodes];
  const mergedEdges = [...base.edges];
  for (const node of addition.nodes) {
    if (!nodeIds.has(node.id)) {
      nodeIds.add(node.id);
      mergedNodes.push(node);
    }
  }
  for (const edge of addition.edges) {
    if (!edgeIds.has(edge.id)) {
      edgeIds.add(edge.id);
      mergedEdges.push(edge);
    }
  }
  return {
    nodes: mergedNodes,
    edges: mergedEdges,
    root: addition.root ?? base.root,
    truncated: base.truncated || addition.truncated,
    bounds: addition.bounds ?? base.bounds,
  };
}
