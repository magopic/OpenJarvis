// Shared synthetic GraphResponse fixtures for graph* unit tests only.
// Never imported by production code.
import type { GraphResponse } from '../types/graph';

export function makeGraph(overrides: Partial<GraphResponse> = {}): GraphResponse {
  return {
    nodes: [
      {
        id: 'e-problem-1',
        kind: 'ENTRY',
        label: 'Linea M ferma',
        entry_type: 'PROBLEM',
        trust_status: 'OBSERVED',
        visibility: 'PRIVATE',
        domains: ['produzione'],
        entities: ['Linea M'],
        lifecycle: 'ACTIVE',
        created_at: 1000,
        derived: false,
      },
      {
        id: 'e-outcome-1',
        kind: 'ENTRY',
        label: 'Risolto',
        entry_type: 'OUTCOME',
        trust_status: 'OUTCOME',
        visibility: 'PRIVATE',
        domains: ['produzione'],
        entities: ['Linea M'],
        lifecycle: 'ACTIVE',
        created_at: 2000,
        derived: false,
      },
      { id: 'domain:produzione', kind: 'DOMAIN', label: 'produzione', entry_type: null, trust_status: null, visibility: null, domains: [], entities: [], lifecycle: null, created_at: null, derived: true },
      { id: 'entity:Linea M', kind: 'ENTITY', label: 'Linea M', entry_type: null, trust_status: null, visibility: null, domains: [], entities: [], lifecycle: null, created_at: null, derived: true },
    ],
    edges: [
      { id: 'rel:1', source: 'e-problem-1', target: 'e-outcome-1', kind: 'RELATIONSHIP', status: 'CONFIRMED', derived: false, basis: 'RESULTED_IN' },
      { id: 'nav:domain:e-problem-1:domain:produzione', source: 'e-problem-1', target: 'domain:produzione', kind: 'NAVIGATION', status: null, derived: true, basis: 'SHARED_DOMAIN' },
      { id: 'nav:domain:e-outcome-1:domain:produzione', source: 'e-outcome-1', target: 'domain:produzione', kind: 'NAVIGATION', status: null, derived: true, basis: 'SHARED_DOMAIN' },
      { id: 'nav:entity:e-problem-1:entity:Linea M', source: 'e-problem-1', target: 'entity:Linea M', kind: 'NAVIGATION', status: null, derived: true, basis: 'SHARED_ENTITY' },
    ],
    root: null,
    truncated: false,
    bounds: { max_nodes: 200, max_edges: 400, max_depth: 2 },
    ...overrides,
  };
}

export const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [], root: null, truncated: false, bounds: {} };
