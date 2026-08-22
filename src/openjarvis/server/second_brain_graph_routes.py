"""Read-only HTTP surface for the frozen Knowledge Graph projection (FASE 4O.4).

This is a thin transport wrapper, not new graph logic: every endpoint
calls straight into the already-certified, already-bounded, already
principal-aware functions in
``second_brain/projections/graph.py`` (FASE 4O.3, frozen) and returns
``GraphResponse.to_dict()`` verbatim. No route here can write to the
Second Brain -- every handler opens a ``SecondBrainService``, makes one
read call, and closes it in a ``finally`` block, the same lifecycle the
CLI (`cli/second_brain_cmd.py`) already uses.

The actor is always resolved server-side via
``resolve_runtime_principal()`` -- identical to every Second Brain tool
and CLI command -- and is never accepted as a request parameter. This
app is a single-user local desktop server (Tauri companion); there is
no multi-tenant session concept to thread through, and accepting a
client-supplied principal would let a compromised frontend impersonate
a different identity, defeating the authorization model FASE 4N.2A
built. The existing global auth middleware (Bearer key, when
configured) already gates every ``/v1/*`` route including this one --
no route-local auth logic is added here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from openjarvis.second_brain.errors import SecondBrainValidationError
from openjarvis.second_brain.identity import resolve_runtime_principal
from openjarvis.second_brain.projections.graph import (
    GraphBounds,
    GraphFilters,
    get_domain_graph,
    get_entity_graph,
    get_experience_graph,
    get_neighborhood,
    get_overview,
)
from openjarvis.second_brain.service import SecondBrainService

router = APIRouter(prefix="/v1/second-brain/graph", tags=["second-brain-graph"])


def _csv(value: Optional[str]) -> Optional[tuple]:
    if not value:
        return None
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _filters(
    *,
    domains: Optional[str] = None,
    entities: Optional[str] = None,
    entry_types: Optional[str] = None,
    trust_statuses: Optional[str] = None,
    relationship_statuses: Optional[str] = None,
    include_archived: bool = False,
    include_superseded: bool = False,
) -> GraphFilters:
    kwargs = {
        "domains": _csv(domains),
        "entities": _csv(entities),
        "entry_types": _csv(entry_types),
        "trust_statuses": _csv(trust_statuses),
        "include_archived": include_archived,
        "include_superseded": include_superseded,
    }
    if relationship_statuses:
        kwargs["relationship_statuses"] = _csv(relationship_statuses)
    return GraphFilters(**kwargs)


def _bounds(max_nodes: Optional[int], max_edges: Optional[int], max_depth: Optional[int] = None) -> GraphBounds:
    kwargs = {}
    if max_nodes is not None:
        kwargs["max_nodes"] = max_nodes
    if max_edges is not None:
        kwargs["max_edges"] = max_edges
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    return GraphBounds(**kwargs)


@router.get("/overview")
async def graph_overview(
    domains: Optional[str] = None,
    entities: Optional[str] = None,
    entry_types: Optional[str] = None,
    trust_statuses: Optional[str] = None,
    relationship_statuses: Optional[str] = None,
    include_archived: bool = False,
    include_superseded: bool = False,
    entry_limit: int = Query(50, ge=1, le=1000),
    max_nodes: Optional[int] = None,
    max_edges: Optional[int] = None,
):
    principal = resolve_runtime_principal()
    filters = _filters(
        domains=domains, entities=entities, entry_types=entry_types, trust_statuses=trust_statuses,
        relationship_statuses=relationship_statuses,
        include_archived=include_archived, include_superseded=include_superseded,
    )
    bounds = _bounds(max_nodes, max_edges)
    service = SecondBrainService()
    try:
        response = get_overview(service, actor=principal, filters=filters, bounds=bounds, entry_limit=entry_limit)
    finally:
        service.close()
    return response.to_dict()


@router.get("/neighborhood/{root_id}")
async def graph_neighborhood(
    root_id: str,
    depth: int = Query(1, ge=0, le=5),
    include_archived: bool = False,
    include_superseded: bool = False,
    relationship_statuses: Optional[str] = None,
    max_nodes: Optional[int] = None,
    max_edges: Optional[int] = None,
):
    principal = resolve_runtime_principal()
    filters = _filters(
        include_archived=include_archived, include_superseded=include_superseded,
        relationship_statuses=relationship_statuses,
    )
    bounds = _bounds(max_nodes, max_edges, depth)
    service = SecondBrainService()
    try:
        response = get_neighborhood(service, root_id, actor=principal, depth=depth, filters=filters, bounds=bounds)
    except SecondBrainValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        service.close()
    return response.to_dict()


@router.get("/domain/{domain}")
async def graph_domain(domain: str, entry_limit: int = Query(100, ge=1, le=1000)):
    principal = resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_domain_graph(service, domain, actor=principal, entry_limit=entry_limit)
    finally:
        service.close()
    return response.to_dict()


@router.get("/entity/{entity}")
async def graph_entity(entity: str, entry_limit: int = Query(100, ge=1, le=1000)):
    principal = resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_entity_graph(service, entity, actor=principal, entry_limit=entry_limit)
    finally:
        service.close()
    return response.to_dict()


@router.get("/experience/{anchor_entry_id}")
async def graph_experience(
    anchor_entry_id: str,
    max_hops: int = Query(4, ge=1, le=10),
    max_entries: int = Query(12, ge=1, le=50),
):
    principal = resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_experience_graph(
            service, anchor_entry_id, actor=principal, max_hops=max_hops, max_entries=max_entries
        )
    except SecondBrainValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        service.close()
    return response.to_dict()


__all__ = ["router"]
