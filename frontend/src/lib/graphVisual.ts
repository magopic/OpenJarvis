// MAIA Knowledge Graph — visual grammar (FASE 4O.4 STEP 5, revised
// FASE 4O.4A art-direction pass).
//
// Pure presentation mapping: every function here only READS meaning
// fields (kind, entry_type, lifecycle, status, derived) to pick a
// color/size/glow -- it never adds, removes, or renames a meaning
// field, and never infers a relationship that isn't already a real
// `GraphEdge`. `VisualStyle`/`EdgeStyle` never carry an id -- callers
// always keep the original backend id for the actual node/edge.
//
// FASE 4O.4A revision: the original grammar used a different 3D
// primitive per node kind/type (icosahedron/octahedron/box/cone/...),
// which read as "a collection of unrelated geometric shapes" rather
// than one coherent system (user visual review). Every node is now a
// luminous sphere; differentiation moved entirely to color (from the
// restrained palette in `graphTheme.ts`), size, and glow/halo
// intensity -- shape no longer carries meaning.
import type { GraphEdge, GraphEntryType, GraphNode, GraphResponse } from '../types/graph';
import {
  COLOR_ARCHIVED_GREY,
  COLOR_COLD_BLUE,
  COLOR_COOL_WHITE,
  COLOR_CYAN,
  COLOR_CYAN_BRIGHT,
  COLOR_MINT,
  COLOR_MUTED_CORAL,
  COLOR_MUTED_GOLD,
  COLOR_VIOLET_COLD,
  EDGE_NAVIGATION_COLOR,
} from './graphTheme';

const COLOR_NEUTRAL_ENTRY = '#6b8299';

export interface VisualStyle {
  coreColor: string;
  haloColor: string;
  size: number;
  haloScale: number;
  glow: number;
  /** LOD: only DOMAIN and the more attention-worthy entry stages get a
   * halo mesh -- keeps draw calls bounded at large node counts (STEP 16). */
  showHalo: boolean;
}

// FASE 4O.4A visual-review fix #2: explicit relative-scale hierarchy
// off one shared baseline (`ENTRY_BASE`), replacing ad-hoc per-type
// numbers -- DOMAIN 1.5x, "major" Experience Cycle stages 1.15-1.25x,
// ordinary ENTRY 1.0x, ENTITY 0.65-0.75x (user visual review: "visual
// hierarchy relies too much on physical node size" / "DOMAIN nodes
// still look like large opaque planets"). DOMAIN's outsized presence
// now comes from brighter core + glow + label + cluster density, not
// raw radius. `haloScale` sizes the additive glow sprite (see
// `NodeMesh.tsx`/`graphGlowTexture.ts`) -- with a real radial falloff
// texture instead of a flat disc, "modestly beyond the core" (per
// review) can stay small without reading as a hard-edged shape.
// FASE 4O.4A fix #3: bumped modestly (0.34 -> 0.4) after visual review
// found nodes read as "almost invisible dots" once the camera-fit bug
// was corrected and the graph sat farther back than intended -- this
// scales every kind/type uniformly, so the relative hierarchy below is
// unchanged (STEP 5: "do not disproportionately enlarge DOMAIN").
const ENTRY_BASE = 0.4;

const KIND_STYLE: Record<'DOMAIN' | 'ENTITY', VisualStyle> = {
  DOMAIN: { coreColor: COLOR_COOL_WHITE, haloColor: COLOR_CYAN, size: ENTRY_BASE * 1.5, haloScale: 2.0, glow: 1.0, showHalo: true },
  ENTITY: { coreColor: COLOR_COLD_BLUE, haloColor: COLOR_COLD_BLUE, size: ENTRY_BASE * 0.7, haloScale: 1.6, glow: 0.22, showHalo: false },
};

// One entry per Experience Cycle stage the phase asks to distinguish,
// plus the remaining EntryTypes with one shared neutral treatment --
// differentiated by color/size/glow only, all drawn from the same
// restrained cyan/cold-blue palette except the two deliberate semantic
// departures (PROBLEM's coral, HYPOTHESIS's gold). PROBLEM/DECISION/
// OUTCOME/LESSON ("major" stages, still the ones carrying a glow) sit
// at 1.15-1.25x baseline; the rest sit at 1.0x.
const ENTRY_TYPE_STYLE: Record<GraphEntryType, VisualStyle> = {
  PROBLEM: { coreColor: COLOR_MUTED_CORAL, haloColor: COLOR_MUTED_CORAL, size: ENTRY_BASE * 1.15, haloScale: 1.7, glow: 0.55, showHalo: true },
  HYPOTHESIS: { coreColor: COLOR_MUTED_GOLD, haloColor: COLOR_MUTED_GOLD, size: ENTRY_BASE * 1.0, haloScale: 1.5, glow: 0.35, showHalo: false },
  DECISION: { coreColor: COLOR_VIOLET_COLD, haloColor: COLOR_VIOLET_COLD, size: ENTRY_BASE * 1.2, haloScale: 1.7, glow: 0.5, showHalo: true },
  ACTION: { coreColor: COLOR_CYAN, haloColor: COLOR_CYAN, size: ENTRY_BASE * 1.0, haloScale: 1.5, glow: 0.42, showHalo: false },
  OUTCOME: { coreColor: COLOR_MINT, haloColor: COLOR_MINT, size: ENTRY_BASE * 1.2, haloScale: 1.7, glow: 0.5, showHalo: true },
  LESSON: { coreColor: COLOR_CYAN_BRIGHT, haloColor: COLOR_CYAN_BRIGHT, size: ENTRY_BASE * 1.25, haloScale: 1.8, glow: 0.85, showHalo: true },
  EVENT: { coreColor: COLOR_NEUTRAL_ENTRY, haloColor: COLOR_NEUTRAL_ENTRY, size: ENTRY_BASE * 1.0, haloScale: 1.4, glow: 0.2, showHalo: false },
  OBSERVATION: { coreColor: COLOR_NEUTRAL_ENTRY, haloColor: COLOR_NEUTRAL_ENTRY, size: ENTRY_BASE * 1.0, haloScale: 1.4, glow: 0.2, showHalo: false },
  PROCEDURE: { coreColor: COLOR_NEUTRAL_ENTRY, haloColor: COLOR_NEUTRAL_ENTRY, size: ENTRY_BASE * 1.0, haloScale: 1.4, glow: 0.2, showHalo: false },
  MEETING_NOTE: { coreColor: COLOR_NEUTRAL_ENTRY, haloColor: COLOR_NEUTRAL_ENTRY, size: ENTRY_BASE * 1.0, haloScale: 1.4, glow: 0.2, showHalo: false },
};

const DEFAULT_ENTRY_STYLE: VisualStyle = {
  coreColor: COLOR_NEUTRAL_ENTRY, haloColor: COLOR_NEUTRAL_ENTRY, size: ENTRY_BASE * 1.0, haloScale: 1.4, glow: 0.2, showHalo: false,
};

export function styleForNode(node: GraphNode): VisualStyle {
  if (node.kind === 'DOMAIN') return KIND_STYLE.DOMAIN;
  if (node.kind === 'ENTITY') return KIND_STYLE.ENTITY;
  if (node.entry_type && ENTRY_TYPE_STYLE[node.entry_type]) return ENTRY_TYPE_STYLE[node.entry_type];
  return DEFAULT_ENTRY_STYLE;
}

// Lifecycle is real stored data (archived_at/superseded_by), not an
// invented visual category -- ACTIVE reads at full presence, SUPERSEDED
// and ARCHIVED read progressively quieter and shift toward a neutral
// grey so history never masquerades as current, colorful truth.
export function lifecycleOpacity(node: GraphNode): number {
  if (node.kind !== 'ENTRY') return 1;
  if (node.lifecycle === 'ARCHIVED') return 0.28;
  if (node.lifecycle === 'SUPERSEDED') return 0.48;
  return 1;
}

export function lifecycleColorOverride(node: GraphNode): string | null {
  if (node.kind === 'ENTRY' && node.lifecycle === 'ARCHIVED') return COLOR_ARCHIVED_GREY;
  return null;
}

export interface EdgeStyle {
  color: string;
  opacity: number;
  width: number;
  dashed: boolean;
}

// CONFIRMED vs. PROPOSED is now differentiated by strength/pattern, not
// hue (STEP 6: "color must communicate state, not decorate") -- both
// stay in the cyan family, PROPOSED simply weaker and dashed. Only
// SUPERSESSION (a structurally different kind of edge) and NAVIGATION
// (deliberately the quietest, most desaturated edge) get their own hue.
// FASE 4O.4A visual-review fix #2: default opacities/widths lowered
// across the board -- edges should read as a fine neural web at rest,
// visible enough to establish structure, brightening only in response
// to selection (`EdgeLine.tsx` multiplies these on emphasis).
export function styleForEdge(edge: GraphEdge): EdgeStyle {
  if (edge.kind === 'NAVIGATION') {
    return { color: EDGE_NAVIGATION_COLOR, opacity: 0.08, width: 0.3, dashed: false };
  }
  if (edge.kind === 'SUPERSESSION') {
    return { color: COLOR_VIOLET_COLD, opacity: 0.45, width: 0.8, dashed: false };
  }
  // RELATIONSHIP
  if (edge.status === 'PROPOSED') {
    return { color: COLOR_CYAN, opacity: 0.2, width: 0.35, dashed: true };
  }
  if (edge.status === 'REJECTED') {
    // Not shown by default (server-side filter excludes it); if a
    // caller explicitly opts in, render it unmistakably de-emphasized
    // rather than inventing a "confirmed-looking" style for it.
    return { color: COLOR_MUTED_CORAL, opacity: 0.08, width: 0.3, dashed: true };
  }
  return { color: COLOR_CYAN_BRIGHT, opacity: 0.42, width: 0.5, dashed: false };
}

export function edgeLabel(edge: GraphEdge): string {
  if (edge.kind === 'NAVIGATION') return edge.basis === 'SHARED_DOMAIN' ? 'same domain' : 'same entity';
  if (edge.kind === 'SUPERSESSION') return 'supersedes';
  return edge.basis;
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
