// MAIA Knowledge Graph — HTTP client for the read-only Graph API
// (`server/second_brain_graph_routes.py`, FASE 4O.4). Every function
// here is a GET against `/v1/second-brain/graph/*` and returns the
// backend's `GraphResponse` verbatim — no client-side reinterpretation
// of node/edge meaning happens in this file; that's `graphVisual.ts`'s
// job, layered on top.
import { apiFetch } from './api';
import type { GraphFilterParams, GraphResponse } from '../types/graph';

export class GraphUnauthorizedError extends Error {}
export class GraphNotFoundError extends Error {}

async function handleGraphResponse(res: Response): Promise<GraphResponse> {
  if (res.status === 401 || res.status === 403) {
    throw new GraphUnauthorizedError('Not authorized to view this part of the graph');
  }
  if (res.status === 404) {
    const body = await res.json().catch(() => ({}));
    throw new GraphNotFoundError(body.detail || 'Graph node not found');
  }
  if (!res.ok) {
    throw new Error(`Graph request failed: ${res.status}`);
  }
  return res.json();
}

function filterParams(filters?: GraphFilterParams): URLSearchParams {
  const params = new URLSearchParams();
  if (!filters) return params;
  if (filters.domains?.length) params.set('domains', filters.domains.join(','));
  if (filters.entities?.length) params.set('entities', filters.entities.join(','));
  if (filters.entryTypes?.length) params.set('entry_types', filters.entryTypes.join(','));
  if (filters.trustStatuses?.length) params.set('trust_statuses', filters.trustStatuses.join(','));
  if (filters.relationshipStatuses?.length) params.set('relationship_statuses', filters.relationshipStatuses.join(','));
  if (filters.includeArchived) params.set('include_archived', 'true');
  if (filters.includeSuperseded) params.set('include_superseded', 'true');
  return params;
}

export interface GraphBoundsParams {
  maxNodes?: number;
  maxEdges?: number;
}

function boundsParams(bounds?: GraphBoundsParams): URLSearchParams {
  const params = new URLSearchParams();
  if (bounds?.maxNodes != null) params.set('max_nodes', String(bounds.maxNodes));
  if (bounds?.maxEdges != null) params.set('max_edges', String(bounds.maxEdges));
  return params;
}

export async function fetchGraphOverview(
  filters?: GraphFilterParams,
  bounds?: GraphBoundsParams,
  entryLimit = 50,
): Promise<GraphResponse> {
  const params = filterParams(filters);
  for (const [k, v] of boundsParams(bounds)) params.set(k, v);
  params.set('entry_limit', String(entryLimit));
  const res = await apiFetch(`/v1/second-brain/graph/overview?${params.toString()}`);
  return handleGraphResponse(res);
}

export async function fetchGraphNeighborhood(
  rootId: string,
  depth = 1,
  filters?: GraphFilterParams,
  bounds?: GraphBoundsParams,
): Promise<GraphResponse> {
  const params = filterParams(filters);
  for (const [k, v] of boundsParams(bounds)) params.set(k, v);
  params.set('depth', String(depth));
  const res = await apiFetch(
    `/v1/second-brain/graph/neighborhood/${encodeURIComponent(rootId)}?${params.toString()}`,
  );
  return handleGraphResponse(res);
}

export async function fetchGraphDomain(domain: string, entryLimit = 100): Promise<GraphResponse> {
  const params = new URLSearchParams({ entry_limit: String(entryLimit) });
  const res = await apiFetch(`/v1/second-brain/graph/domain/${encodeURIComponent(domain)}?${params}`);
  return handleGraphResponse(res);
}

export async function fetchGraphEntity(entity: string, entryLimit = 100): Promise<GraphResponse> {
  const params = new URLSearchParams({ entry_limit: String(entryLimit) });
  const res = await apiFetch(`/v1/second-brain/graph/entity/${encodeURIComponent(entity)}?${params}`);
  return handleGraphResponse(res);
}

export async function fetchGraphExperience(
  anchorEntryId: string,
  maxHops = 4,
  maxEntries = 12,
): Promise<GraphResponse> {
  const params = new URLSearchParams({ max_hops: String(maxHops), max_entries: String(maxEntries) });
  const res = await apiFetch(
    `/v1/second-brain/graph/experience/${encodeURIComponent(anchorEntryId)}?${params}`,
  );
  return handleGraphResponse(res);
}
