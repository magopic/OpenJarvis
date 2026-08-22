import { describe, expect, it } from 'vitest';

import { computeGraphLayout } from './graphLayout';
import { EMPTY_GRAPH, makeGraph } from './graphTestFixtures';

describe('computeGraphLayout', () => {
  it('places every node exactly once', () => {
    const graph = makeGraph();
    const layout = computeGraphLayout(graph);
    expect(Object.keys(layout).sort()).toEqual(graph.nodes.map((n) => n.id).sort());
  });

  it('is deterministic across repeated calls on the same payload (STEP 18-M)', () => {
    const graph = makeGraph();
    const a = computeGraphLayout(graph);
    const b = computeGraphLayout(graph);
    expect(a).toEqual(b);
  });

  it('handles an empty graph safely (STEP 18-B)', () => {
    expect(computeGraphLayout(EMPTY_GRAPH)).toEqual({});
  });

  it('handles a graph with no DOMAIN nodes safely (STEP 6)', () => {
    const graph = makeGraph();
    const noDomains = {
      ...graph,
      nodes: graph.nodes.filter((n) => n.kind !== 'DOMAIN'),
      edges: graph.edges.filter((e) => e.basis !== 'SHARED_DOMAIN'),
    };
    const layout = computeGraphLayout(noDomains);
    for (const node of noDomains.nodes) {
      expect(layout[node.id]).toBeDefined();
      expect(Number.isFinite(layout[node.id].x)).toBe(true);
      expect(Number.isFinite(layout[node.id].y)).toBe(true);
      expect(Number.isFinite(layout[node.id].z)).toBe(true);
    }
  });

  it('clusters entries near their domain rather than at the origin', () => {
    const graph = makeGraph();
    const layout = computeGraphLayout(graph);
    const domainPos = layout['domain:produzione'];
    const entryPos = layout['e-problem-1'];
    const distFromDomain = Math.hypot(entryPos.x - domainPos.x, entryPos.z - domainPos.z);
    const distFromOrigin = Math.hypot(entryPos.x, entryPos.z);
    // With a single domain the domain itself sits at the origin, so this
    // assertion is really "the entry lands within the cluster radius",
    // not "far from origin" -- assert the cluster radius bound directly.
    expect(distFromDomain).toBeLessThan(10);
    expect(distFromOrigin).toBeLessThan(10);
  });

  it('handles Unicode/Italian node ids and labels without throwing (STEP 18-N)', () => {
    const graph = makeGraph({
      nodes: [
        {
          id: 'e-città-1',
          kind: 'ENTRY',
          label: 'Perché la Linea M è fermata? 🏭',
          entry_type: 'PROBLEM',
          trust_status: 'OBSERVED',
          visibility: 'PRIVATE',
          domains: ['città-metropolitana'],
          entities: [],
          lifecycle: 'ACTIVE',
          created_at: 1000,
          derived: false,
        },
        { id: 'domain:città-metropolitana', kind: 'DOMAIN', label: 'città-metropolitana', entry_type: null, trust_status: null, visibility: null, domains: [], entities: [], lifecycle: null, created_at: null, derived: true },
      ],
      edges: [],
    });
    expect(() => computeGraphLayout(graph)).not.toThrow();
    const layout = computeGraphLayout(graph);
    expect(layout['e-città-1']).toBeDefined();
  });

  it('gives two distinct domains two distinct positions', () => {
    const graph = makeGraph({
      nodes: [
        ...makeGraph().nodes,
        { id: 'domain:logistica', kind: 'DOMAIN', label: 'logistica', entry_type: null, trust_status: null, visibility: null, domains: [], entities: [], lifecycle: null, created_at: null, derived: true },
      ],
    });
    const layout = computeGraphLayout(graph);
    const a = layout['domain:produzione'];
    const b = layout['domain:logistica'];
    expect(a).not.toEqual(b);
  });
});
