"""Retrieval Intelligence V1 (FASE 4N.4) -- deterministic progressive
broadening for historical Second Brain retrieval.

FASE 4N.3 found, live, that structured retrieval (domain/entity/type/
FTS filters -- all frozen since 4N.1/4N.2) works correctly whenever a
model chooses to call it, but a model given one complex multi-part
question does not reliably choose to *broaden* a narrow search that
comes back empty. This module removes that dependency: broadening is
now a fixed algorithm the runtime executes deterministically, not a
sequence of tool calls the model has to invent.

No embeddings, no vector index, no synthetic similarity score anywhere
in this file -- every candidate's match is derived from the same
structured filters/FTS the frozen store already had (FASE 4N.1/4N.2),
just tried in a fixed order instead of left to chance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjarvis.second_brain.types import SecondBrainEntry

# Fixed, bounded broadening order -- STEP 2. Each level is strictly
# broader than the last; a level only runs if the caller supplied the
# input it needs (e.g. LEVEL 1 needs an explicit entity/id, LEVEL 3
# needs free text). None of this is configurable per call -- the whole
# point is that it does not depend on what a model decides to try.
LEVEL_EXACT = "EXACT"
LEVEL_STRUCTURED = "STRUCTURED"
LEVEL_TERM = "TERM"
LEVEL_RELATIONSHIP = "RELATIONSHIP"

_LEVEL_PRIORITY = {LEVEL_EXACT: 0, LEVEL_STRUCTURED: 1, LEVEL_TERM: 2, LEVEL_RELATIONSHIP: 3}

# Bounds -- STEP 12 ("no unbounded graph walk", "no full database
# dump"). Each level's own store query is already capped
# (_PER_LEVEL_LIMIT); the RELATIONSHIP level additionally only expands
# from a small number of top seed candidates, not every candidate, so
# the total amount of work stays flat even as the store grows.
_PER_LEVEL_LIMIT = 10
_MAX_RELATIONSHIP_SEEDS = 5
_DEFAULT_MAX_CANDIDATES = 15

# Experience bundle bounds -- a bounded BFS, not a graph traversal
# engine. 8 hops comfortably covers the 6-stage PROBLEM..LESSON cycle
# (FASE 4N.3) plus slack for branching (e.g. a HYPOTHESIS that RELATES_TO
# more than one PROBLEM); it is not "however many hops the graph has."
_DEFAULT_MAX_BUNDLE_HOPS = 8
_DEFAULT_MAX_BUNDLE_ENTRIES = 12


@dataclass(slots=True)
class RetrievalCandidate:
    """One historical entry found during progressive retrieval, with an
    explicit, inspectable reason it matched. Never a similarity score --
    see module docstring."""

    entry_id: str
    retrieval_level: str
    matched_domains: List[str] = field(default_factory=list)
    matched_entities: List[str] = field(default_factory=list)
    matched_terms: List[str] = field(default_factory=list)
    relationship_basis: List[str] = field(default_factory=list)
    active_or_superseded: str = "ACTIVE"  # or "SUPERSEDED"
    historical_entry_type: str = ""
    title: str = ""
    summary: str = ""
    trust_status: str = ""


@dataclass(slots=True)
class ExperienceBundleItem:
    """One stage of a bundled historical experience -- full detail
    preserved, never collapsed into a generated summary (STEP 5)."""

    entry_id: str
    type: str
    title: str
    summary: str
    trust_status: str
    provenance: str
    relationship_basis: str


@dataclass(slots=True)
class ExperienceBundle:
    anchor_entry_id: str
    stages: List[ExperienceBundleItem] = field(default_factory=list)
    truncated: bool = False


def _to_candidate(entry: SecondBrainEntry, *, level: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        entry_id=entry.id,
        retrieval_level=level,
        active_or_superseded="SUPERSEDED" if entry.superseded_by else "ACTIVE",
        historical_entry_type=entry.type.value,
        title=entry.title,
        summary=entry.summary,
        trust_status=entry.trust_status.value,
    )


def _merge_into(existing: RetrievalCandidate, **fields: List[str]) -> None:
    for key, values in fields.items():
        current = getattr(existing, key)
        for v in values:
            if v not in current:
                current.append(v)


__all__ = [
    "LEVEL_EXACT",
    "LEVEL_RELATIONSHIP",
    "LEVEL_STRUCTURED",
    "LEVEL_TERM",
    "ExperienceBundle",
    "ExperienceBundleItem",
    "RetrievalCandidate",
    "_DEFAULT_MAX_BUNDLE_ENTRIES",
    "_DEFAULT_MAX_BUNDLE_HOPS",
    "_DEFAULT_MAX_CANDIDATES",
    "_LEVEL_PRIORITY",
    "_MAX_RELATIONSHIP_SEEDS",
    "_PER_LEVEL_LIMIT",
    "_merge_into",
    "_to_candidate",
]
