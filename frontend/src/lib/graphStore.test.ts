import { beforeEach, describe, expect, it, vi } from 'vitest';

import { EMPTY_GRAPH, makeGraph } from './graphTestFixtures';

vi.mock('./graphApi', () => ({
  fetchGraphOverview: vi.fn(),
  fetchGraphNeighborhood: vi.fn(),
  fetchGraphExperience: vi.fn(),
  GraphUnauthorizedError: class GraphUnauthorizedError extends Error {},
  GraphNotFoundError: class GraphNotFoundError extends Error {},
}));

import * as graphApi from './graphApi';
import { useGraphStore } from './graphStore';

const fetchGraphOverview = vi.mocked(graphApi.fetchGraphOverview);
const fetchGraphNeighborhood = vi.mocked(graphApi.fetchGraphNeighborhood);
const fetchGraphExperience = vi.mocked(graphApi.fetchGraphExperience);

beforeEach(() => {
  vi.clearAllMocks();
  useGraphStore.setState({
    status: 'idle',
    errorMessage: null,
    graph: EMPTY_GRAPH,
    layout: {},
    filters: {},
    selectedNodeId: null,
    mode: { type: 'overview' },
  });
});

describe('useGraphStore.loadOverview', () => {
  it('populates graph+layout and sets status ready on a valid overview (STEP 18-A)', async () => {
    fetchGraphOverview.mockResolvedValue(makeGraph());
    await useGraphStore.getState().loadOverview();
    const state = useGraphStore.getState();
    expect(state.status).toBe('ready');
    expect(state.graph.nodes.length).toBeGreaterThan(0);
    expect(Object.keys(state.layout).length).toBe(state.graph.nodes.length);
  });

  it('sets status empty for a zero-node graph (STEP 18-B)', async () => {
    fetchGraphOverview.mockResolvedValue(EMPTY_GRAPH);
    await useGraphStore.getState().loadOverview();
    expect(useGraphStore.getState().status).toBe('empty');
  });

  it('sets status no-access on an authorization failure (STEP 18-C)', async () => {
    fetchGraphOverview.mockRejectedValue(new graphApi.GraphUnauthorizedError('nope'));
    await useGraphStore.getState().loadOverview();
    expect(useGraphStore.getState().status).toBe('no-access');
  });

  it('sets status error on an unexpected failure without leaking raw errors to graph state', async () => {
    fetchGraphOverview.mockRejectedValue(new Error('boom'));
    await useGraphStore.getState().loadOverview();
    const state = useGraphStore.getState();
    expect(state.status).toBe('error');
    expect(state.errorMessage).toBe('boom');
  });

  it('surfaces truncated=true from the backend response honestly (STEP 18-L)', async () => {
    fetchGraphOverview.mockResolvedValue(makeGraph({ truncated: true }));
    await useGraphStore.getState().loadOverview();
    expect(useGraphStore.getState().graph.truncated).toBe(true);
  });

  it('preserves Unicode labels unchanged end to end (STEP 18-N)', async () => {
    const graph = makeGraph({
      nodes: [
        { id: 'e1', kind: 'ENTRY', label: 'Perché è fermata? 🏭', entry_type: 'PROBLEM', trust_status: 'OBSERVED', visibility: 'PRIVATE', domains: [], entities: [], lifecycle: 'ACTIVE', created_at: 0, derived: false },
      ],
      edges: [],
    });
    fetchGraphOverview.mockResolvedValue(graph);
    await useGraphStore.getState().loadOverview();
    expect(useGraphStore.getState().graph.nodes[0].label).toBe('Perché è fermata? 🏭');
  });
});

describe('useGraphStore.setFilters', () => {
  it('passes filters through to the Graph API rather than filtering client-side (STEP 18-J)', async () => {
    fetchGraphOverview.mockResolvedValue(makeGraph());
    useGraphStore.getState().setFilters({ domains: ['produzione'], includeArchived: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(fetchGraphOverview).toHaveBeenCalledWith({ domains: ['produzione'], includeArchived: true });
  });
});

describe('useGraphStore.selectNode', () => {
  it('merges a neighborhood fetch without producing duplicate ids (STEP 18-E)', async () => {
    fetchGraphOverview.mockResolvedValue(makeGraph());
    await useGraphStore.getState().loadOverview();

    fetchGraphNeighborhood.mockResolvedValue(
      makeGraph({ nodes: [...makeGraph().nodes, { id: 'e-new', kind: 'ENTRY', label: 'new', entry_type: 'ACTION', trust_status: 'OBSERVED', visibility: 'PRIVATE', domains: ['produzione'], entities: [], lifecycle: 'ACTIVE', created_at: 3000, derived: false }] }),
    );
    await useGraphStore.getState().selectNode('e-problem-1');
    const ids = useGraphStore.getState().graph.nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain('e-new');
    expect(useGraphStore.getState().selectedNodeId).toBe('e-problem-1');
  });

  it('does not fetch a neighborhood for a DOMAIN or ENTITY node', async () => {
    fetchGraphOverview.mockResolvedValue(makeGraph());
    await useGraphStore.getState().loadOverview();
    await useGraphStore.getState().selectNode('domain:produzione');
    expect(fetchGraphNeighborhood).not.toHaveBeenCalled();
  });

  it('clears selection on null', async () => {
    useGraphStore.setState({ selectedNodeId: 'e-problem-1' });
    await useGraphStore.getState().selectNode(null);
    expect(useGraphStore.getState().selectedNodeId).toBeNull();
  });
});

describe('useGraphStore.showExperience', () => {
  it('preserves stored relationship type/status in the experience graph (STEP 18-I)', async () => {
    const graph = makeGraph();
    fetchGraphExperience.mockResolvedValue(graph);
    await useGraphStore.getState().showExperience('e-problem-1');
    const state = useGraphStore.getState();
    expect(state.status).toBe('ready');
    expect(state.mode).toEqual({ type: 'experience', anchorId: 'e-problem-1' });
    const relEdge = state.graph.edges.find((e) => e.kind === 'RELATIONSHIP');
    expect(relEdge?.status).toBe('CONFIRMED');
    expect(relEdge?.basis).toBe('RESULTED_IN');
  });

  it('surfaces a 404 as a clean error state, not a raw exception (STEP 18-C-adjacent)', async () => {
    fetchGraphExperience.mockRejectedValue(new graphApi.GraphNotFoundError('No such entry: x'));
    await useGraphStore.getState().showExperience('x');
    expect(useGraphStore.getState().status).toBe('error');
  });
});
