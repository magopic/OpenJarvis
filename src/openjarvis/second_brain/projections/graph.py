"""Governed, bounded, principal-aware Knowledge Graph projection (FASE 4O.3).

Read-only, exactly like the Obsidian projection (``projections/obsidian.py``):
every function here reads through ``SecondBrainService`` -- never
``SecondBrainStore``, never raw SQL -- so authorization is enforced
*before* a node or edge can be built, not filtered afterward. No writes
back to the Second Brain happen anywhere in this module.

The graph is a derived view, not a second source of truth. Nothing in
this module invents nodes or edges: every ``ENTRY`` node is a real
``SecondBrainEntry``, every ``RELATIONSHIP``/``SUPERSESSION`` edge is a
real stored ``Relationship`` row, and every ``NAVIGATION`` edge is a
mechanical consequence of an entry's own ``domains``/``entities``
fields (co-occurrence, never inferred causality or similarity). The
dormant ``tools/storage/knowledge_graph.py::KnowledgeGraphMemory`` was
evaluated (FASE 4O.1/4O.3 audit) and deliberately not reused: it is a
free-form entity/relation store with its own SQLite backend, no
visibility model, and no connection to the frozen Second Brain
vocabulary -- reusing it would reintroduce exactly the "LLM-invented
graph edges" risk this phase exists to avoid.

Bounded by construction: every query takes a ``GraphBounds`` (max
nodes/edges/depth, hard-capped regardless of what a caller requests)
and reports ``truncated=True`` the moment a cap is hit rather than
silently dropping data. There is no "dump the whole brain" entry
point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.second_brain.errors import SecondBrainAuthorizationError, SecondBrainValidationError
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import Relationship, RelationshipStatus, RelationshipType, SecondBrainEntry

# -- bounds --------------------------------------------------------------

_DEFAULT_MAX_NODES = 200
_DEFAULT_MAX_EDGES = 400
_DEFAULT_MAX_DEPTH = 2

# Hard caps: no caller-supplied value, however large, can exceed these.
# Chosen to keep a single query comfortably interactive (STEP 15) rather
# than to fit any particular UI -- the future 3D graph UI can always
# issue more bounded queries (STEP 8's "merge incremental chunks").
_HARD_MAX_NODES = 1000
_HARD_MAX_EDGES = 2000
_HARD_MAX_DEPTH = 5

# REJECTED relationships never appear in a graph response even when a
# caller's filters technically permit RELATIONSHIP_STATUSES to include
# them at some future call site -- matching STEP 4 exactly, and kept as
# a separate constant so the exclusion is a single grep-able point, not
# scattered through every builder function.
_HARD_EXCLUDED_STATUSES = frozenset({RelationshipStatus.REJECTED.value})

_DEFAULT_RELATIONSHIP_STATUSES: Tuple[str, ...] = (
    RelationshipStatus.CONFIRMED.value,
    RelationshipStatus.PROPOSED.value,
)


@dataclass(frozen=True, slots=True)
class GraphBounds:
    max_nodes: int = _DEFAULT_MAX_NODES
    max_edges: int = _DEFAULT_MAX_EDGES
    max_depth: int = _DEFAULT_MAX_DEPTH

    def clamped(self) -> "GraphBounds":
        return GraphBounds(
            max_nodes=min(max(1, self.max_nodes), _HARD_MAX_NODES),
            max_edges=min(max(1, self.max_edges), _HARD_MAX_EDGES),
            max_depth=min(max(0, self.max_depth), _HARD_MAX_DEPTH),
        )


@dataclass(frozen=True, slots=True)
class GraphFilters:
    """Every field narrows what can appear; nothing here widens access
    beyond what ``actor`` is already authorized to read."""

    domains: Optional[Tuple[str, ...]] = None
    entities: Optional[Tuple[str, ...]] = None
    entry_types: Optional[Tuple[str, ...]] = None
    trust_statuses: Optional[Tuple[str, ...]] = None
    relationship_statuses: Tuple[str, ...] = _DEFAULT_RELATIONSHIP_STATUSES
    since: Optional[float] = None
    until: Optional[float] = None
    include_archived: bool = False
    include_superseded: bool = False

    def allowed_relationship_statuses(self) -> frozenset:
        return frozenset(self.relationship_statuses) - _HARD_EXCLUDED_STATUSES


# -- JSON contract (STEP 2 / STEP 13) -------------------------------------


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    kind: str  # "ENTRY" | "ENTITY" | "DOMAIN"
    label: str
    entry_type: Optional[str] = None
    trust_status: Optional[str] = None
    visibility: Optional[str] = None
    domains: Tuple[str, ...] = ()
    entities: Tuple[str, ...] = ()
    lifecycle: Optional[str] = None  # "ACTIVE" | "SUPERSEDED" | "ARCHIVED"
    created_at: Optional[float] = None
    derived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "entry_type": self.entry_type,
            "trust_status": self.trust_status,
            "visibility": self.visibility,
            "domains": list(self.domains),
            "entities": list(self.entities),
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "derived": self.derived,
        }


@dataclass(frozen=True, slots=True)
class GraphEdge:
    id: str
    source: str
    target: str
    kind: str  # "RELATIONSHIP" | "NAVIGATION" | "SUPERSESSION"
    status: Optional[str] = None  # CONFIRMED/PROPOSED for RELATIONSHIP+SUPERSESSION; None for NAVIGATION
    derived: bool = False
    basis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "status": self.status,
            "derived": self.derived,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class GraphResponse:
    nodes: Tuple[GraphNode, ...]
    edges: Tuple[GraphEdge, ...]
    root: Optional[str] = None
    truncated: bool = False
    bounds: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "root": self.root,
            "truncated": self.truncated,
            "bounds": dict(self.bounds),
        }


# -- node/edge construction from real stored structure --------------------


def _entry_lifecycle(entry: SecondBrainEntry) -> str:
    """Archived takes precedence over superseded (both can be true at
    once) -- same precedence rule as ``obsidian.py::note_folder``, kept
    identical on purpose (STEP 16: the two projections must agree)."""
    if entry.archived_at is not None:
        return "ARCHIVED"
    if entry.superseded_by is not None:
        return "SUPERSEDED"
    return "ACTIVE"


def _entry_node(entry: SecondBrainEntry) -> GraphNode:
    return GraphNode(
        id=entry.id,
        kind="ENTRY",
        label=entry.title,
        entry_type=entry.type.value,
        trust_status=entry.trust_status.value,
        visibility=entry.visibility.value,
        domains=tuple(sorted(entry.domains)),
        entities=tuple(sorted(entry.entities)),
        lifecycle=_entry_lifecycle(entry),
        created_at=entry.created_at,
        derived=False,
    )


def _entity_node(entity: str) -> GraphNode:
    return GraphNode(id=f"entity:{entity}", kind="ENTITY", label=entity, derived=True)


def _domain_node(domain: str) -> GraphNode:
    return GraphNode(id=f"domain:{domain}", kind="DOMAIN", label=domain, derived=True)


def _relationship_edge(rel: Relationship) -> GraphEdge:
    """A ``SUPERSEDES`` relationship (always CONFIRMED -- see
    ``service.supersede_entry``) is rendered as its own edge kind so
    the future UI can distinguish "this replaced that" from an ordinary
    semantic link, even though both come from the exact same stored
    ``Relationship`` row -- no separate "supersession" data exists or
    is invented."""
    kind = "SUPERSESSION" if rel.relation_type is RelationshipType.SUPERSEDES else "RELATIONSHIP"
    return GraphEdge(
        id=f"rel:{rel.id}",
        source=rel.source_entry_id,
        target=rel.target_entry_id,
        kind=kind,
        status=rel.status.value,
        derived=False,
        basis=rel.relation_type.value,
    )


class _GraphAccumulator:
    """Shared bounded-insertion logic for every query function below --
    one place enforces ``max_nodes``/``max_edges`` and sets
    ``truncated``, so no individual query can silently drop data
    without reporting it."""

    def __init__(self, bounds: GraphBounds) -> None:
        self.bounds = bounds
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}
        self.truncated = False

    def add_node(self, node: GraphNode) -> bool:
        if node.id in self._nodes:
            return True
        if len(self._nodes) >= self.bounds.max_nodes:
            self.truncated = True
            return False
        self._nodes[node.id] = node
        return True

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def add_edge(self, edge: GraphEdge) -> bool:
        if edge.id in self._edges:
            return True
        if edge.source not in self._nodes or edge.target not in self._nodes:
            return False
        if len(self._edges) >= self.bounds.max_edges:
            self.truncated = True
            return False
        self._edges[edge.id] = edge
        return True

    def node_ids(self) -> List[str]:
        return list(self._nodes)

    def to_response(self, *, root: Optional[str] = None) -> GraphResponse:
        nodes = tuple(sorted(self._nodes.values(), key=lambda n: n.id))
        edges = tuple(sorted(self._edges.values(), key=lambda e: e.id))
        return GraphResponse(
            nodes=nodes,
            edges=edges,
            root=root,
            truncated=self.truncated,
            bounds={
                "max_nodes": self.bounds.max_nodes,
                "max_edges": self.bounds.max_edges,
                "max_depth": self.bounds.max_depth,
            },
        )


def _resolve_entry(service: SecondBrainService, actor: Optional[str], entry_id: str) -> Optional[SecondBrainEntry]:
    """Principal-scoped lookup that never raises -- identical pattern to
    ``ObsidianProjection._resolve_entry``. An entry this principal
    cannot read (or that no longer exists) is silently unavailable to
    the graph, never surfaced as an error or a placeholder node that
    would itself leak the target's existence (STEP 5)."""
    try:
        return service.get_entry(entry_id, actor=actor)
    except SecondBrainAuthorizationError:
        return None


def _add_navigation(acc: _GraphAccumulator, entry: SecondBrainEntry) -> None:
    for ent in entry.entities:
        node = _entity_node(ent)
        acc.add_node(node)
        acc.add_edge(
            GraphEdge(
                id=f"nav:entity:{entry.id}:{node.id}",
                source=entry.id,
                target=node.id,
                kind="NAVIGATION",
                status=None,
                derived=True,
                basis="SHARED_ENTITY",
            )
        )
    for dom in entry.domains:
        node = _domain_node(dom)
        acc.add_node(node)
        acc.add_edge(
            GraphEdge(
                id=f"nav:domain:{entry.id}:{node.id}",
                source=entry.id,
                target=node.id,
                kind="NAVIGATION",
                status=None,
                derived=True,
                basis="SHARED_DOMAIN",
            )
        )


def _add_relationships_among(
    service: SecondBrainService, acc: _GraphAccumulator, actor: Optional[str], entry_ids: List[str], filters: GraphFilters
) -> None:
    """Stored relationships whose *both* endpoints are already in the
    accumulated node set. Iterated via ``direction="out"`` from every
    id so each relationship is visited exactly once (from its source),
    never twice from each end."""
    id_set = set(entry_ids)
    allowed = filters.allowed_relationship_statuses()
    for eid in sorted(id_set):
        for rel in service.get_relationships(eid, direction="out"):
            if rel.status.value not in allowed:
                continue
            if rel.target_entry_id not in id_set:
                continue
            acc.add_edge(_relationship_edge(rel))


def _apply_entry_filters(entries: List[SecondBrainEntry], filters: GraphFilters) -> List[SecondBrainEntry]:
    out = []
    for e in entries:
        if filters.entry_types and e.type.value not in filters.entry_types:
            continue
        if filters.trust_statuses and e.trust_status.value not in filters.trust_statuses:
            continue
        if not filters.include_superseded and e.superseded_by is not None:
            continue
        out.append(e)
    return out


def _fetch_candidate_entries(
    service: SecondBrainService, actor: Optional[str], filters: GraphFilters, entry_limit: int
) -> List[SecondBrainEntry]:
    """Structural filters (``domains``/``entities``) go through the
    store's own indexed lookups (mirroring ``find_related_experiences``'s
    LEVEL_EXACT/LEVEL_STRUCTURED pattern); ``entry_types``/
    ``trust_statuses`` narrow the result client-side afterward. Always
    bounded by ``entry_limit`` per underlying call -- never an unbounded
    table scan."""
    by_id: Dict[str, SecondBrainEntry] = {}
    if filters.domains:
        for d in filters.domains:
            for e in service.list_entries(
                actor=actor,
                domain=d,
                include_archived=filters.include_archived,
                since=filters.since,
                until=filters.until,
                limit=entry_limit,
            ):
                by_id[e.id] = e
    if filters.entities:
        for ent in filters.entities:
            for e in service.list_entries(
                actor=actor,
                entity=ent,
                include_archived=filters.include_archived,
                since=filters.since,
                until=filters.until,
                limit=entry_limit,
            ):
                by_id[e.id] = e
    if not filters.domains and not filters.entities:
        for e in service.list_entries(
            actor=actor,
            include_archived=filters.include_archived,
            since=filters.since,
            until=filters.until,
            limit=entry_limit,
        ):
            by_id[e.id] = e

    entries = _apply_entry_filters(list(by_id.values()), filters)
    entries.sort(key=lambda e: (-(e.created_at or 0.0), e.id))
    return entries[:entry_limit]


# -- bounded query entry points (STEP 6/7/8/9) -----------------------------


def get_overview(
    service: SecondBrainService,
    *,
    actor: Optional[str],
    filters: Optional[GraphFilters] = None,
    bounds: Optional[GraphBounds] = None,
    entry_limit: int = 50,
) -> GraphResponse:
    """Lightweight aggregate/navigation view (STEP 7) -- domain/entity
    structure and the most recently active authorized entries, not a
    dump of every memory. No importance score is invented: "recent"
    means sorted strictly by ``created_at``, and any count the future
    UI derives comes directly from the returned, already-authorized
    node set, never from a separate unauthorized-inclusive count."""
    filters = filters or GraphFilters()
    b = (bounds or GraphBounds()).clamped()
    acc = _GraphAccumulator(b)

    entries = _fetch_candidate_entries(service, actor, filters, entry_limit)
    for entry in entries:
        if acc.add_node(_entry_node(entry)):
            _add_navigation(acc, entry)

    included_entry_ids = [n for n in acc.node_ids() if not n.startswith(("entity:", "domain:"))]
    _add_relationships_among(service, acc, actor, included_entry_ids, filters)
    return acc.to_response()


def get_neighborhood(
    service: SecondBrainService,
    root_id: str,
    *,
    actor: Optional[str],
    depth: int = 1,
    filters: Optional[GraphFilters] = None,
    bounds: Optional[GraphBounds] = None,
) -> GraphResponse:
    """Deterministic bounded BFS from ``root_id`` over stored
    relationships (STEP 8): root -> stored relationships -> linked
    entries -> entities -> domains. Frontier ids are sorted and each
    entry's relationships are sorted before expansion, so two
    back-to-back calls against an unchanged brain return byte-identical
    JSON (STEP 14-U)."""
    filters = filters or GraphFilters()
    b = (bounds or GraphBounds()).clamped()
    depth = max(0, min(depth, b.max_depth))
    acc = _GraphAccumulator(b)

    root = service.get_entry(root_id, actor=actor)
    if root is None:
        raise SecondBrainValidationError(f"No such entry: {root_id}")

    acc.add_node(_entry_node(root))
    _add_navigation(acc, root)

    allowed = filters.allowed_relationship_statuses()
    visited = {root.id}
    frontier = [root.id]
    hop = 0
    while frontier and hop < depth:
        hop += 1
        next_frontier: List[str] = []
        for current_id in sorted(frontier):
            rels = sorted(
                service.get_relationships(current_id, direction="both"),
                key=lambda r: (r.relation_type.value, r.source_entry_id, r.target_entry_id, r.id),
            )
            for rel in rels:
                if rel.status.value not in allowed:
                    continue
                other_id = rel.target_entry_id if rel.source_entry_id == current_id else rel.source_entry_id
                if other_id in visited:
                    acc.add_edge(_relationship_edge(rel))
                    continue
                other = _resolve_entry(service, actor, other_id)
                if other is None:
                    continue
                if not filters.include_archived and other.archived_at is not None:
                    continue
                if not filters.include_superseded and other.superseded_by is not None:
                    continue
                if not acc.add_node(_entry_node(other)):
                    continue
                acc.add_edge(_relationship_edge(rel))
                _add_navigation(acc, other)
                visited.add(other_id)
                next_frontier.append(other_id)
        frontier = next_frontier

    return acc.to_response(root=root.id)


def get_domain_graph(
    service: SecondBrainService,
    domain: str,
    *,
    actor: Optional[str],
    filters: Optional[GraphFilters] = None,
    bounds: Optional[GraphBounds] = None,
    entry_limit: int = 100,
) -> GraphResponse:
    base_filters = filters or GraphFilters()
    scoped_filters = GraphFilters(
        domains=(domain,),
        entities=base_filters.entities,
        entry_types=base_filters.entry_types,
        trust_statuses=base_filters.trust_statuses,
        relationship_statuses=base_filters.relationship_statuses,
        since=base_filters.since,
        until=base_filters.until,
        include_archived=base_filters.include_archived,
        include_superseded=base_filters.include_superseded,
    )
    b = (bounds or GraphBounds()).clamped()
    acc = _GraphAccumulator(b)
    acc.add_node(_domain_node(domain))

    entries = _fetch_candidate_entries(service, actor, scoped_filters, entry_limit)
    for entry in entries:
        if acc.add_node(_entry_node(entry)):
            _add_navigation(acc, entry)

    included_entry_ids = [e.id for e in entries if acc.has_node(e.id)]
    _add_relationships_among(service, acc, actor, included_entry_ids, scoped_filters)
    return acc.to_response(root=f"domain:{domain}")


def get_entity_graph(
    service: SecondBrainService,
    entity: str,
    *,
    actor: Optional[str],
    filters: Optional[GraphFilters] = None,
    bounds: Optional[GraphBounds] = None,
    entry_limit: int = 100,
) -> GraphResponse:
    base_filters = filters or GraphFilters()
    scoped_filters = GraphFilters(
        domains=base_filters.domains,
        entities=(entity,),
        entry_types=base_filters.entry_types,
        trust_statuses=base_filters.trust_statuses,
        relationship_statuses=base_filters.relationship_statuses,
        since=base_filters.since,
        until=base_filters.until,
        include_archived=base_filters.include_archived,
        include_superseded=base_filters.include_superseded,
    )
    b = (bounds or GraphBounds()).clamped()
    acc = _GraphAccumulator(b)
    acc.add_node(_entity_node(entity))

    entries = _fetch_candidate_entries(service, actor, scoped_filters, entry_limit)
    for entry in entries:
        if acc.add_node(_entry_node(entry)):
            _add_navigation(acc, entry)

    included_entry_ids = [e.id for e in entries if acc.has_node(e.id)]
    _add_relationships_among(service, acc, actor, included_entry_ids, scoped_filters)
    return acc.to_response(root=f"entity:{entity}")


def get_experience_graph(
    service: SecondBrainService,
    anchor_entry_id: str,
    *,
    actor: Optional[str],
    max_hops: int = 4,
    max_entries: int = 12,
    bounds: Optional[GraphBounds] = None,
) -> GraphResponse:
    """Graph view of the frozen Experience Cycle (STEP 9) -- built
    directly on top of the already-certified, already-bounded, already
    privacy-safe ``SecondBrainService.get_experience_bundle`` (FASE
    4N.3/4N.4), so this function invents nothing about *which* entries
    belong: it only adds structured ``GraphEdge`` objects (real
    relation type + status, not the bundle's human-readable
    ``relationship_basis`` string) for the CONFIRMED relationships
    connecting the bundle's own entries."""
    bundle = service.get_experience_bundle(anchor_entry_id, actor=actor, max_hops=max_hops, max_entries=max_entries)
    b = (bounds or GraphBounds()).clamped()
    acc = _GraphAccumulator(b)

    included_ids: List[str] = []
    for stage in bundle.stages:
        entry = _resolve_entry(service, actor, stage.entry_id)
        if entry is None:
            continue
        if acc.add_node(_entry_node(entry)):
            included_ids.append(entry.id)

    confirmed_only = GraphFilters(relationship_statuses=(RelationshipStatus.CONFIRMED.value,))
    _add_relationships_among(service, acc, actor, included_ids, confirmed_only)

    response = acc.to_response(root=anchor_entry_id)
    if bundle.truncated and not response.truncated:
        response = GraphResponse(
            nodes=response.nodes, edges=response.edges, root=response.root, truncated=True, bounds=response.bounds
        )
    return response


__all__ = [
    "GraphBounds",
    "GraphFilters",
    "GraphNode",
    "GraphEdge",
    "GraphResponse",
    "get_overview",
    "get_neighborhood",
    "get_domain_graph",
    "get_entity_graph",
    "get_experience_graph",
]
