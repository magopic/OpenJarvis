"""FASE 4O.3 STEP 14 -- Knowledge Graph Projection V1 isolated test matrix.

Every test uses an isolated temporary Second Brain database (``tmp_path``)
-- nothing here ever touches a real Second Brain.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openjarvis.second_brain.errors import SecondBrainValidationError
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
from openjarvis.second_brain.store import SecondBrainStore

_A = "test-principal:alice"
_B = "test-principal:bob"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_sb.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


def _entry(service, *, actor=_A, type="OBSERVATION", trust_status="OBSERVED", visibility="PRIVATE",
           domains=None, entities=None, title="t"):
    timestamp = time.time() if (type == "DECISION" or trust_status == "DECISION") else None
    if type == "LESSON" or trust_status == "LEARNED":
        domains = domains or ["default-domain"]
    return service.create_entry(
        type=type, title=title, summary="s", created_by=actor, provenance="p", source="conv",
        trust_status=trust_status, visibility=visibility,
        domains=domains or [], entities=entities or [], timestamp=timestamp,
    )


def _link(service, src, tgt, *, actor=_A, relation_type="RELATED_TO", confirm=True):
    rel = service.create_relationship(
        source_entry_id=src.id, target_entry_id=tgt.id, relation_type=relation_type,
        source="conv", created_by=actor,
    )
    if confirm:
        service.update_relationship_status(rel.id, "CONFIRMED", actor=actor)
    return rel


# -- A: single entry graph ---------------------------------------------------


def test_a_single_entry_graph(service):
    e = _entry(service, domains=["d1"], entities=["ent1"])
    resp = get_overview(service, actor=_A)
    entry_nodes = [n for n in resp.nodes if n.kind == "ENTRY"]
    assert len(entry_nodes) == 1
    assert entry_nodes[0].id == e.id
    assert entry_nodes[0].derived is False


# -- B: stored CONFIRMED relationship ----------------------------------------


def test_b_confirmed_relationship_edge(service):
    a = _entry(service, title="a")
    b = _entry(service, title="b")
    _link(service, a, b, relation_type="CAUSES", confirm=True)
    resp = get_neighborhood(service, a.id, actor=_A, depth=1)
    rel_edges = [e for e in resp.edges if e.kind == "RELATIONSHIP"]
    assert len(rel_edges) == 1
    assert rel_edges[0].status == "CONFIRMED"
    assert rel_edges[0].basis == "CAUSES"
    assert rel_edges[0].derived is False


# -- C: PROPOSED relationship visibly distinct -------------------------------


def test_c_proposed_relationship_distinct_status(service):
    a = _entry(service, title="a")
    b = _entry(service, title="b")
    _link(service, a, b, relation_type="SIMILAR_TO", confirm=False)
    resp = get_neighborhood(service, a.id, actor=_A, depth=1)
    rel_edges = [e for e in resp.edges if e.kind == "RELATIONSHIP"]
    assert len(rel_edges) == 1
    assert rel_edges[0].status == "PROPOSED"
    assert rel_edges[0].status != "CONFIRMED"


# -- D: REJECTED excluded by default -----------------------------------------


def test_d_rejected_excluded_by_default(service):
    a = _entry(service, title="a")
    b = _entry(service, title="b")
    rel = _link(service, a, b, relation_type="DUPLICATES", confirm=False)
    service.update_relationship_status(rel.id, "REJECTED", actor=_A)
    resp = get_neighborhood(service, a.id, actor=_A, depth=1)
    assert not any(e.kind == "RELATIONSHIP" for e in resp.edges)
    assert not any(n.id == b.id for n in resp.nodes)


# -- E: ENTRY -> ENTITY navigation edge --------------------------------------


def test_e_entry_to_entity_navigation_edge(service):
    e = _entry(service, entities=["Linea M"])
    resp = get_overview(service, actor=_A)
    nav = [ed for ed in resp.edges if ed.kind == "NAVIGATION" and ed.basis == "SHARED_ENTITY"]
    assert len(nav) == 1
    assert nav[0].source == e.id
    assert nav[0].target == "entity:Linea M"
    assert nav[0].derived is True
    entity_nodes = [n for n in resp.nodes if n.kind == "ENTITY"]
    assert entity_nodes[0].derived is True


# -- F: ENTRY -> DOMAIN navigation edge ---------------------------------------


def test_f_entry_to_domain_navigation_edge(service):
    e = _entry(service, domains=["produzione"])
    resp = get_overview(service, actor=_A)
    nav = [ed for ed in resp.edges if ed.kind == "NAVIGATION" and ed.basis == "SHARED_DOMAIN"]
    assert len(nav) == 1
    assert nav[0].target == "domain:produzione"
    domain_nodes = [n for n in resp.nodes if n.kind == "DOMAIN"]
    assert domain_nodes[0].derived is True


# -- G: PRIVATE owner visible -------------------------------------------------


def test_g_private_owner_visible(service):
    e = _entry(service, actor=_A, visibility="PRIVATE", title="secret")
    resp = get_overview(service, actor=_A)
    assert any(n.id == e.id for n in resp.nodes)


# -- H: PRIVATE other principal invisible ------------------------------------


def test_h_private_other_principal_invisible(service):
    e = _entry(service, actor=_A, visibility="PRIVATE", title="secret")
    resp = get_overview(service, actor=_B)
    assert not any(n.id == e.id for n in resp.nodes)
    assert len(resp.nodes) == 0


# -- I: unauthorized endpoint removes edge -----------------------------------


def test_i_unauthorized_endpoint_removes_edge(service):
    a = _entry(service, actor=_A, visibility="TEAM", title="a")
    private_b = _entry(service, actor=_A, visibility="PRIVATE", title="secret-b")
    _link(service, a, private_b, relation_type="RELATED_TO", confirm=True)
    resp = get_neighborhood(service, a.id, actor=_B, depth=1)
    assert not any(n.id == private_b.id for n in resp.nodes)
    assert not any(e.target == private_b.id or e.source == private_b.id for e in resp.edges)


# -- J: private-only entity/domain does not leak -----------------------------


def test_j_private_only_entity_domain_no_leak(service):
    _entry(service, actor=_A, visibility="PRIVATE", domains=["only-private-domain"], entities=["only-private-entity"])
    resp = get_overview(service, actor=_B)
    assert not any(n.kind == "DOMAIN" and n.label == "only-private-domain" for n in resp.nodes)
    assert not any(n.kind == "ENTITY" and n.label == "only-private-entity" for n in resp.nodes)
    dump = json.dumps(resp.to_dict())
    assert "only-private-domain" not in dump
    assert "only-private-entity" not in dump


# -- K: superseded excluded by default ---------------------------------------


def test_k_superseded_excluded_by_default(service):
    old = _entry(service, title="old")
    old_after, _rel = service.supersede_entry(
        old.id, actor=_A,
        new_entry_kwargs=dict(
            type="OBSERVATION", title="new", summary="s", created_by=_A, provenance="p",
            source="conv", trust_status="OBSERVED", visibility="PRIVATE",
        ),
    )
    resp = get_overview(service, actor=_A)
    assert not any(n.id == old.id for n in resp.nodes)


# -- L: archived excluded by default -----------------------------------------


def test_l_archived_excluded_by_default(service):
    e = _entry(service, title="to-archive")
    service.archive_entry(e.id, actor=_A)
    resp = get_overview(service, actor=_A)
    assert not any(n.id == e.id for n in resp.nodes)


# -- M: explicit include historical works -------------------------------------


def test_m_include_historical_via_neighborhood(service):
    old = _entry(service, title="old")
    old_after, rel = service.supersede_entry(
        old.id, actor=_A,
        new_entry_kwargs=dict(
            type="OBSERVATION", title="new", summary="s", created_by=_A, provenance="p",
            source="conv", trust_status="OBSERVED", visibility="PRIVATE",
        ),
    )
    new_id = rel.source_entry_id
    resp = get_neighborhood(service, new_id, actor=_A, depth=1, filters=GraphFilters(include_superseded=True))
    assert any(n.id == old.id for n in resp.nodes)
    supersession_edges = [e for e in resp.edges if e.kind == "SUPERSESSION"]
    assert len(supersession_edges) == 1
    assert supersession_edges[0].source == new_id
    assert supersession_edges[0].target == old.id


# -- N: neighborhood depth bounded --------------------------------------------


def test_n_neighborhood_depth_bounded(service):
    chain = [_entry(service, title=f"e{i}") for i in range(4)]
    for i in range(3):
        _link(service, chain[i], chain[i + 1], relation_type="PRECEDES", confirm=True)
    resp = get_neighborhood(service, chain[0].id, actor=_A, depth=1)
    entry_nodes = {n.id for n in resp.nodes if n.kind == "ENTRY"}
    assert chain[1].id in entry_nodes
    assert chain[2].id not in entry_nodes
    assert chain[3].id not in entry_nodes


# -- O: node limit produces truncated=true -------------------------------------


def test_o_node_limit_truncates(service):
    for i in range(10):
        _entry(service, title=f"e{i}", domains=["d"])
    resp = get_overview(service, actor=_A, bounds=GraphBounds(max_nodes=3))
    assert resp.truncated is True
    assert len(resp.nodes) <= 3


# -- P: edge limit produces truncated=true -------------------------------------


def test_p_edge_limit_truncates(service):
    hub = _entry(service, title="hub")
    spokes = [_entry(service, title=f"s{i}") for i in range(6)]
    for s in spokes:
        _link(service, hub, s, relation_type="RELATED_TO", confirm=True)
    resp = get_neighborhood(service, hub.id, actor=_A, depth=1, bounds=GraphBounds(max_nodes=200, max_edges=2))
    assert resp.truncated is True
    assert len(resp.edges) <= 2


# -- Q: deterministic ordering -------------------------------------------------


def test_q_deterministic_ordering(service):
    for i in range(5):
        _entry(service, title=f"e{i}", domains=["d"])
    resp1 = get_overview(service, actor=_A)
    resp2 = get_overview(service, actor=_A)
    ids1 = [n.id for n in resp1.nodes]
    ids2 = [n.id for n in resp2.nodes]
    assert ids1 == ids2
    assert ids1 == sorted(ids1)


# -- R: experience chain graph --------------------------------------------------


def test_r_experience_chain_graph(service):
    problem = _entry(service, type="PROBLEM", title="problem")
    decision = _entry(service, type="DECISION", title="decision")
    action = _entry(service, type="ACTION", title="action")
    outcome = _entry(service, type="OUTCOME", title="outcome")
    lesson = _entry(service, type="LESSON", title="lesson", trust_status="LEARNED")
    _link(service, problem, decision, relation_type="DECIDED_IN", confirm=True)
    _link(service, decision, action, relation_type="RESULTED_IN", confirm=True)
    _link(service, action, outcome, relation_type="RESULTED_IN", confirm=True)
    _link(service, outcome, lesson, relation_type="RESULTED_IN", confirm=True)

    resp = get_experience_graph(service, problem.id, actor=_A)
    node_ids = {n.id for n in resp.nodes}
    assert {problem.id, decision.id, action.id, outcome.id, lesson.id} <= node_ids
    entry_types = {n.id: n.entry_type for n in resp.nodes if n.kind == "ENTRY"}
    assert entry_types[problem.id] == "PROBLEM"
    assert entry_types[lesson.id] == "LESSON"
    assert all(e.status == "CONFIRMED" for e in resp.edges if e.kind == "RELATIONSHIP")


# -- S: empty brain -------------------------------------------------------------


def test_s_empty_brain(service):
    resp = get_overview(service, actor=_A)
    assert resp.nodes == ()
    assert resp.edges == ()
    assert resp.truncated is False


# -- T: special Unicode labels ---------------------------------------------------


def test_t_unicode_labels_preserved(service):
    e = _entry(service, title="Perché la Linea M è fermata? 🏭", domains=["città-metropolitana"], entities=["Città"])
    resp = get_overview(service, actor=_A)
    entry_node = next(n for n in resp.nodes if n.id == e.id)
    assert entry_node.label == "Perché la Linea M è fermata? 🏭"
    dumped = json.dumps(resp.to_dict(), ensure_ascii=False)
    assert "città-metropolitana" in dumped


# -- U: repeated query byte-equivalent JSON --------------------------------------


def test_u_repeated_query_byte_equivalent(service):
    a = _entry(service, title="a", domains=["d"], entities=["ent"])
    b = _entry(service, title="b", domains=["d"])
    _link(service, a, b, relation_type="RELATED_TO", confirm=True)

    resp1 = get_neighborhood(service, a.id, actor=_A, depth=2)
    resp2 = get_neighborhood(service, a.id, actor=_A, depth=2)
    json1 = json.dumps(resp1.to_dict(), sort_keys=True, ensure_ascii=False)
    json2 = json.dumps(resp2.to_dict(), sort_keys=True, ensure_ascii=False)
    assert json1 == json2


# -- V: projection causes zero Second Brain mutations ----------------------------


def test_v_projection_zero_mutations(service):
    _entry(service, title="a", domains=["d"])
    _entry(service, title="b", domains=["d"])
    before = len(service.list_entries(actor=_A, include_archived=True, limit=1_000_000))
    ok_before, _ = service.verify_audit_chain()

    get_overview(service, actor=_A)
    get_neighborhood(service, next(iter(service.list_entries(actor=_A, limit=1))).id, actor=_A, depth=2)
    get_domain_graph(service, "d", actor=_A)
    get_entity_graph(service, "nonexistent-entity", actor=_A)

    after = len(service.list_entries(actor=_A, include_archived=True, limit=1_000_000))
    ok_after, _ = service.verify_audit_chain()
    assert before == after
    assert ok_before is True and ok_after is True


# -- neighborhood on nonexistent/unauthorized root -------------------------------


def test_neighborhood_nonexistent_root_raises(service):
    with pytest.raises(SecondBrainValidationError):
        get_neighborhood(service, "does-not-exist", actor=_A, depth=1)


# -- filters: entry_types / trust_statuses ---------------------------------------


def test_filters_entry_types_and_trust_status(service):
    _entry(service, type="PROBLEM", title="p", trust_status="OBSERVED")
    _entry(service, type="DECISION", title="d", trust_status="DECISION")
    resp = get_overview(service, actor=_A, filters=GraphFilters(entry_types=("DECISION",)))
    entry_nodes = [n for n in resp.nodes if n.kind == "ENTRY"]
    assert len(entry_nodes) == 1
    assert entry_nodes[0].entry_type == "DECISION"


# -- STEP 16: Obsidian/Graph identity consistency --------------------------------


def test_step16_obsidian_graph_identity_agreement(service):
    from openjarvis.second_brain.projections.obsidian import note_folder

    e = _entry(service, type="DECISION", title="agree", domains=["d"], entities=["ent"])
    b = _entry(service, title="b")
    rel = _link(service, e, b, relation_type="AFFECTS", confirm=True)

    resp = get_neighborhood(service, e.id, actor=_A, depth=1)
    node = next(n for n in resp.nodes if n.id == e.id)

    # Same entry id, same type, same lifecycle-equivalent state.
    assert node.id == e.id
    assert node.entry_type == e.type.value
    assert node.lifecycle == "ACTIVE"
    assert "Decisions" in note_folder(e)  # Obsidian's own folder-precedence source of truth

    # Same relationship type/status on both sides.
    rel_edge = next(ed for ed in resp.edges if ed.kind == "RELATIONSHIP")
    assert rel_edge.status == "CONFIRMED"
    assert rel_edge.basis == "AFFECTS"

    # Same authorization behavior: a stranger sees neither.
    resp_stranger = get_overview(service, actor=_B)
    assert not any(n.id == e.id for n in resp_stranger.nodes)
