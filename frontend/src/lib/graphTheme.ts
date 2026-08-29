// MAIA Knowledge Graph — visual identity palette (FASE 4O.4A).
//
// Deliberately hardcoded, not read from the app's `--color-*` CSS
// variables: the graph is meant to always render as MAIA's own
// immersive dark environment regardless of the surrounding app's
// light/dark theme setting (the earlier CSS-var approach silently
// broke into a white canvas whenever the app theme resolved to light,
// since `getComputedStyle(document.documentElement)` reads the root
// theme class, not a local wrapper). A small, curated, mostly cyan/
// cold-blue/white palette also directly satisfies "reduce the palette
// dramatically" (STEP 6) -- semantic hues (PROBLEM/HYPOTHESIS) are the
// only deliberate departures, used sparingly.
//
// Graph Data != Graph Layout != Graph Visual State (STEP 17): nothing
// in this file reads or writes a `GraphNode`/`GraphEdge` -- it is pure
// color constants, consumed by `graphVisual.ts`'s mapping functions.

export const GRAPH_BG = '#05070c';
export const GRAPH_FOG_NEAR_COLOR = '#0a1420';
export const GRAPH_VIGNETTE = '#020304';

export const COLOR_CYAN = '#22d3ee';
export const COLOR_CYAN_BRIGHT = '#a5f3fc';
export const COLOR_COLD_BLUE = '#3b82f6';
export const COLOR_COLD_BLUE_DIM = '#1e3a5f';
export const COLOR_COOL_WHITE = '#e6f4ff';
export const COLOR_MUTED_CORAL = '#e8917f'; // PROBLEM -- the one warm departure, desaturated
export const COLOR_MUTED_GOLD = '#d9b26a'; // HYPOTHESIS -- desaturated, not neon amber
export const COLOR_VIOLET_COLD = '#8ea4f0'; // DECISION / SUPERSESSION -- cold-family violet, not saturated purple
export const COLOR_MINT = '#7fe6c8'; // OUTCOME -- cyan-adjacent, barely green

export const COLOR_ARCHIVED_GREY = '#4a5563';

export const EDGE_NAVIGATION_COLOR = '#3a4a5c';
