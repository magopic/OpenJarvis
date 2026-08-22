// MAIA Knowledge Graph — types mirroring the frozen backend JSON contract
// (`second_brain/projections/graph.py::GraphNode/GraphEdge/GraphResponse`,
// FASE 4O.3, frozen). These fields are the graph's *meaning* — never
// rename, drop, or reinterpret them here. Presentation-only concerns
// (position, visual category, selection) live in `graphVisual.ts`
// instead, layered on top without touching these.

export type GraphNodeKind = 'ENTRY' | 'ENTITY' | 'DOMAIN';

export type GraphEntryLifecycle = 'ACTIVE' | 'SUPERSEDED' | 'ARCHIVED';

export type GraphEntryType =
  | 'EVENT'
  | 'PROBLEM'
  | 'OBSERVATION'
  | 'HYPOTHESIS'
  | 'DECISION'
  | 'ACTION'
  | 'OUTCOME'
  | 'LESSON'
  | 'PROCEDURE'
  | 'MEETING_NOTE';

export type GraphEdgeKind = 'RELATIONSHIP' | 'NAVIGATION' | 'SUPERSESSION';

export type GraphRelationshipStatus = 'CONFIRMED' | 'PROPOSED' | 'REJECTED';

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  entry_type: GraphEntryType | null;
  trust_status: string | null;
  visibility: string | null;
  domains: string[];
  entities: string[];
  lifecycle: GraphEntryLifecycle | null;
  created_at: number | null;
  derived: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: GraphEdgeKind;
  status: GraphRelationshipStatus | null;
  derived: boolean;
  basis: string;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  root: string | null;
  truncated: boolean;
  bounds: { max_nodes: number; max_edges: number; max_depth: number } | Record<string, number>;
}

export interface GraphFilterParams {
  domains?: string[];
  entities?: string[];
  entryTypes?: string[];
  trustStatuses?: string[];
  relationshipStatuses?: GraphRelationshipStatus[];
  includeArchived?: boolean;
  includeSuperseded?: boolean;
}
