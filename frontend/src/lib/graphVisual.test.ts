import { describe, expect, it } from 'vitest';

import { lifecycleOpacity, mergeGraphResponses, styleForEdge, styleForNode } from './graphVisual';
import { makeGraph } from './graphTestFixtures';
import type { GraphEdge, GraphNode } from '../types/graph';

const entry = (overrides: Partial<GraphNode> = {}): GraphNode => ({
  id: 'e1',
  kind: 'ENTRY',
  label: 'x',
  entry_type: 'PROBLEM',
  trust_status: 'OBSERVED',
  visibility: 'PRIVATE',
  domains: [],
  entities: [],
  lifecycle: 'ACTIVE',
  created_at: 0,
  derived: false,
  ...overrides,
});

const edge = (overrides: Partial<GraphEdge> = {}): GraphEdge => ({
  id: 'r1',
  source: 'a',
  target: 'b',
  kind: 'RELATIONSHIP',
  status: 'CONFIRMED',
  derived: false,
  basis: 'RELATED_TO',
  ...overrides,
});

describe('styleForNode', () => {
  // FASE 4O.4A: every node is a sphere now (user visual review: distinct
  // 3D primitives per kind read as "unrelated shapes") -- differentiation
  // moved to color/size/halo, so these assertions check *that* instead.
  it('gives DOMAIN, ENTITY, and ENTRY visibly distinct colors and sizes (STEP 5)', () => {
    const domain = styleForNode({ ...entry({ kind: 'DOMAIN', entry_type: null }) });
    const ent = styleForNode({ ...entry({ kind: 'ENTITY', entry_type: null }) });
    const problem = styleForNode(entry({ entry_type: 'PROBLEM' }));
    const colors = new Set([domain.coreColor, ent.coreColor, problem.coreColor]);
    expect(colors.size).toBe(3);
    expect(domain.size).toBeGreaterThan(ent.size);
    expect(domain.size).toBeGreaterThan(problem.size);
  });

  it('gives each Experience Cycle entry type its own color', () => {
    const types = ['PROBLEM', 'HYPOTHESIS', 'DECISION', 'ACTION', 'OUTCOME', 'LESSON'] as const;
    const colors = types.map((t) => styleForNode(entry({ entry_type: t })).coreColor);
    expect(new Set(colors).size).toBe(new Set(types).size);
  });

  it('falls back to a defined neutral style for an unmapped/missing entry_type', () => {
    const style = styleForNode(entry({ entry_type: null }));
    expect(style.coreColor).toBeDefined();
    expect(style.size).toBeGreaterThan(0);
  });

  it('keeps the palette restrained: every ENTRY style stays within the defined color set (STEP 6)', () => {
    const types = ['PROBLEM', 'HYPOTHESIS', 'DECISION', 'ACTION', 'OUTCOME', 'LESSON', 'EVENT', 'OBSERVATION', 'PROCEDURE', 'MEETING_NOTE'] as const;
    const colors = new Set(types.map((t) => styleForNode(entry({ entry_type: t })).coreColor));
    // Restrained means a handful of curated hues, not one per type times
    // arbitrary variation -- 10 entry types collapse to far fewer colors
    // since several share the same neutral treatment.
    expect(colors.size).toBeLessThanOrEqual(7);
  });
});

describe('lifecycleOpacity', () => {
  it('renders ACTIVE at full presence and progressively quieter for historical states', () => {
    const active = lifecycleOpacity(entry({ lifecycle: 'ACTIVE' }));
    const superseded = lifecycleOpacity(entry({ lifecycle: 'SUPERSEDED' }));
    const archived = lifecycleOpacity(entry({ lifecycle: 'ARCHIVED' }));
    expect(active).toBe(1);
    expect(superseded).toBeLessThan(active);
    expect(archived).toBeLessThan(superseded);
  });

  it('never dims a DOMAIN/ENTITY node (lifecycle is not applicable to them)', () => {
    expect(lifecycleOpacity(entry({ kind: 'DOMAIN', lifecycle: null }))).toBe(1);
  });
});

describe('styleForEdge', () => {
  it('visually distinguishes CONFIRMED from PROPOSED (STEP 5/18-G)', () => {
    const confirmed = styleForEdge(edge({ status: 'CONFIRMED' }));
    const proposed = styleForEdge(edge({ status: 'PROPOSED' }));
    expect(confirmed.dashed).toBe(false);
    expect(proposed.dashed).toBe(true);
    expect(confirmed.opacity).toBeGreaterThan(proposed.opacity);
  });

  it('visually distinguishes derived NAVIGATION edges from stored RELATIONSHIP edges (STEP 18-H)', () => {
    const nav = styleForEdge(edge({ kind: 'NAVIGATION', status: null, basis: 'SHARED_DOMAIN' }));
    const rel = styleForEdge(edge({ kind: 'RELATIONSHIP', status: 'CONFIRMED' }));
    expect(nav.color).not.toBe(rel.color);
    expect(nav.opacity).toBeLessThan(rel.opacity);
  });

  it('gives SUPERSESSION its own distinct treatment', () => {
    const supersession = styleForEdge(edge({ kind: 'SUPERSESSION', status: 'CONFIRMED', basis: 'SUPERSEDES' }));
    const rel = styleForEdge(edge({ kind: 'RELATIONSHIP', status: 'CONFIRMED' }));
    expect(supersession.color).not.toBe(rel.color);
  });

  it('de-emphasizes REJECTED far below CONFIRMED if one ever appears', () => {
    const rejected = styleForEdge(edge({ status: 'REJECTED' }));
    const confirmed = styleForEdge(edge({ status: 'CONFIRMED' }));
    expect(rejected.opacity).toBeLessThan(confirmed.opacity);
  });
});

describe('mergeGraphResponses', () => {
  it('de-duplicates nodes and edges by id (STEP 8 / 18-E)', () => {
    const base = makeGraph();
    const addition = makeGraph({
      nodes: [...makeGraph().nodes, { ...entry({ id: 'e-new-1' }) }],
    });
    const merged = mergeGraphResponses(base, addition);
    const ids = merged.nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain('e-new-1');
  });

  it('never modifies the meaning fields of an already-present node', () => {
    const base = makeGraph();
    const mutatedDuplicate = makeGraph({
      nodes: base.nodes.map((n) => (n.id === 'e-problem-1' ? { ...n, label: 'TAMPERED' } : n)),
    });
    const merged = mergeGraphResponses(base, mutatedDuplicate);
    const node = merged.nodes.find((n) => n.id === 'e-problem-1');
    expect(node?.label).toBe('Linea M ferma');
  });

  it('preserves truncated=true if either side was truncated', () => {
    const base = makeGraph({ truncated: true });
    const addition = makeGraph({ truncated: false });
    expect(mergeGraphResponses(base, addition).truncated).toBe(true);
  });
});
