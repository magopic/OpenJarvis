// MAIA Knowledge Graph — Zustand store (FASE 4O.4).
//
// Owns exactly three things kept deliberately separate: the graph
// truth (`graph`, straight from the Graph API), the derived layout
// (`layout`, computed by `graphLayout.ts` -- see STEP 14's Graph
// Data != Graph Layout State boundary), and UI/interaction state
// (`status`, `selectedNodeId`, `filters`, `mode`). No action in this
// store ever calls a write endpoint -- every one of them is a GET
// through `graphApi.ts`.
import { create } from 'zustand';
import type { GraphFilterParams, GraphResponse } from '../types/graph';
import {
  fetchGraphExperience,
  fetchGraphNeighborhood,
  fetchGraphOverview,
  GraphNotFoundError,
  GraphUnauthorizedError,
} from './graphApi';
import { computeGraphLayout, type LayoutMap } from './graphLayout';
import { mergeGraphResponses } from './graphVisual';

export type GraphViewStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'no-access' | 'error';

export type GraphMode = { type: 'overview' } | { type: 'experience'; anchorId: string };

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [], root: null, truncated: false, bounds: {} };

interface GraphStoreState {
  status: GraphViewStatus;
  errorMessage: string | null;
  graph: GraphResponse;
  layout: LayoutMap;
  filters: GraphFilterParams;
  selectedNodeId: string | null;
  mode: GraphMode;

  loadOverview: () => Promise<void>;
  setFilters: (filters: GraphFilterParams) => void;
  selectNode: (nodeId: string | null) => Promise<void>;
  showExperience: (anchorEntryId: string) => Promise<void>;
  backToOverview: () => Promise<void>;
}

function classifyError(err: unknown, set: (partial: Partial<GraphStoreState>) => void, fallback: string) {
  if (err instanceof GraphUnauthorizedError) {
    set({ status: 'no-access', errorMessage: err.message });
  } else if (err instanceof GraphNotFoundError) {
    set({ status: 'error', errorMessage: err.message });
  } else {
    set({ status: 'error', errorMessage: err instanceof Error ? err.message : fallback });
  }
}

export const useGraphStore = create<GraphStoreState>((set, get) => ({
  status: 'idle',
  errorMessage: null,
  graph: EMPTY_GRAPH,
  layout: {},
  filters: {},
  selectedNodeId: null,
  mode: { type: 'overview' },

  loadOverview: async () => {
    set({ status: 'loading', errorMessage: null, mode: { type: 'overview' }, selectedNodeId: null });
    try {
      const graph = await fetchGraphOverview(get().filters);
      set({ graph, layout: computeGraphLayout(graph), status: graph.nodes.length === 0 ? 'empty' : 'ready' });
    } catch (err) {
      classifyError(err, set, 'Failed to load the knowledge graph');
    }
  },

  setFilters: (filters) => {
    set({ filters });
    void get().loadOverview();
  },

  selectNode: async (nodeId) => {
    set({ selectedNodeId: nodeId });
    if (!nodeId) return;
    const node = get().graph.nodes.find((n) => n.id === nodeId);
    // Only ENTRY nodes have a stored-relationship neighborhood worth
    // fetching -- DOMAIN/ENTITY nodes are already fully represented by
    // the navigation edges already in the loaded graph.
    if (!node || node.kind !== 'ENTRY') return;
    try {
      const chunk = await fetchGraphNeighborhood(nodeId, 1, get().filters);
      const merged = mergeGraphResponses(get().graph, chunk);
      set({ graph: merged, layout: computeGraphLayout(merged) });
    } catch {
      // Best-effort expansion -- the detail panel still works off the
      // already-loaded graph even when this fetch fails.
    }
  },

  showExperience: async (anchorEntryId) => {
    set({ status: 'loading', errorMessage: null, mode: { type: 'experience', anchorId: anchorEntryId } });
    try {
      const graph = await fetchGraphExperience(anchorEntryId);
      set({
        graph,
        layout: computeGraphLayout(graph),
        status: graph.nodes.length === 0 ? 'empty' : 'ready',
        selectedNodeId: anchorEntryId,
      });
    } catch (err) {
      classifyError(err, set, 'Failed to load the experience chain');
    }
  },

  backToOverview: async () => {
    await get().loadOverview();
  },
}));
