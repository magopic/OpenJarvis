"""FASE 4N.4 STEP 9 -- Retrieval Intelligence V1 isolated test matrix.

Covers scenarios A-L against ``SecondBrainService.find_related_experiences()``
and ``get_experience_bundle()`` using deterministic synthetic data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore

_A = "test-principal:alice"
_B = "test-principal:bob"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_retrieval.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


def _confirm(service: SecondBrainService, rel_id: str, actor: str = _A) -> None:
    service.update_relationship_status(rel_id, "CONFIRMED", actor=actor)


# -- A: exact same entity -------------------------------------------------


def test_a_exact_entity_match_preferred(service: SecondBrainService):
    exact = service.create_entry(
        type="PROBLEM", title="Problem on Kappa-4", summary="x",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    other_domain_same_entity_absent = service.create_entry(
        type="PROBLEM", title="Unrelated problem", summary="x",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["other-domain"], entities=["Zeta-1"],
    )
    candidates = service.find_related_experiences(actor=_A, entities=["Kappa-4"])
    assert len(candidates) == 1
    assert candidates[0].entry_id == exact.id
    assert candidates[0].retrieval_level == "EXACT"
    assert candidates[0].matched_entities == ["Kappa-4"]


# -- B: different entity, same domain --------------------------------------


def test_b_broader_domain_match_found(service: SecondBrainService):
    historical = service.create_entry(
        type="PROBLEM", title="Problem on Kappa-4", summary="x",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    # Query for a NEW entity never seen before, but same domain.
    candidates = service.find_related_experiences(actor=_A, entities=["Lambda-2"], domains=["test-domain"])
    ids = {c.entry_id for c in candidates}
    assert historical.id in ids
    matched = next(c for c in candidates if c.entry_id == historical.id)
    assert matched.retrieval_level == "STRUCTURED"
    assert matched.matched_domains == ["test-domain"]


# -- C: same term, different domain -----------------------------------------


def test_c_term_match_explicit_basis(service: SecondBrainService):
    entry = service.create_entry(
        type="OBSERVATION", title="Calibration drift observed", summary="Sensor calibration drifted over time.",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["unrelated-domain"], entities=["Widget-9"],
    )
    candidates = service.find_related_experiences(actor=_A, query="calibration drift")
    assert len(candidates) == 1
    assert candidates[0].entry_id == entry.id
    assert candidates[0].retrieval_level == "TERM"
    assert candidates[0].matched_terms == ["calibration drift"]
    # Basis is explicit -- domain was NOT the reason, and the candidate
    # doesn't claim a domain match it didn't have.
    assert candidates[0].matched_domains == []


# -- D: unrelated case -------------------------------------------------------


def test_d_unrelated_case_no_false_match(service: SecondBrainService):
    service.create_entry(
        type="PROBLEM", title="Problem on Kappa-4", summary="Performance degraded.",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    candidates = service.find_related_experiences(
        actor=_A, entities=["Totally-Different-Asset"], domains=["completely-other-domain"],
        query="something entirely unrelated xyz123",
    )
    assert candidates == []


def test_term_query_with_one_unmatched_word_still_matches(service: SecondBrainService):
    """FASE 4N.4, found live: a query combining a brand-new identifier
    with real descriptive terms (e.g. "Sigma-8 performance degradate",
    where only "performance degradate" exists in stored content) must
    not return zero results just because one word doesn't match --
    exactly backwards for a tool whose purpose is broadening. LEVEL_TERM
    uses OR-joined FTS (search_entries_fts_broad) specifically so this
    doesn't happen; second_brain_search's own AND-joined FTS
    (search_entries_fts, unchanged) is a separate, frozen code path."""
    entry = service.create_entry(
        type="PROBLEM", title="Problem", summary="Performance degradate on the line.",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    candidates = service.find_related_experiences(
        actor=_A, query="Brand-New-Asset-Never-Seen performance degradate"
    )
    assert any(c.entry_id == entry.id for c in candidates)


# -- E: superseded lesson ----------------------------------------------------


def test_e_superseded_replaced_by_active(service: SecondBrainService):
    old_lesson = service.create_entry(
        type="LESSON", title="Old incomplete lesson", summary="x",
        created_by=_A, provenance="x", source="conv", trust_status="LEARNED",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    updated_old, rel = service.supersede_entry(
        old_lesson.id, actor=_A,
        new_entry_kwargs=dict(
            type="LESSON", title="New corrected lesson", summary="x",
            created_by=_A, provenance="correction", source="conv", trust_status="LEARNED",
            domains=["test-domain"], entities=["Kappa-4"],
        ),
    )
    new_id = updated_old.superseded_by

    candidates = service.find_related_experiences(actor=_A, domains=["test-domain"])
    ids = [c.entry_id for c in candidates]
    assert new_id in ids
    assert old_lesson.id not in ids
    matched = next(c for c in candidates if c.entry_id == new_id)
    assert matched.active_or_superseded == "ACTIVE"
    assert any("supersedes" in b for b in matched.relationship_basis)

    # Direct get-by-id still allows historical inspection.
    still_there = service.get_entry(old_lesson.id, actor=_A)
    assert still_there is not None
    assert still_there.title == "Old incomplete lesson"


# -- F: experience chain -----------------------------------------------------


def test_f_coherent_bundle_returned(service: SecondBrainService):
    problem = service.create_entry(
        type="PROBLEM", title="Problem", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=["Kappa-4"],
    )
    decision = service.create_entry(
        type="DECISION", title="Decision", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="DECISION", domains=["test-domain"], entities=["Kappa-4"],
        timestamp=1700000000.0,
    )
    action = service.create_entry(
        type="ACTION", title="Action", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="DECISION", domains=["test-domain"], entities=["Kappa-4"],
    )
    outcome = service.create_entry(
        type="OUTCOME", title="Outcome", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OUTCOME", domains=["test-domain"], entities=["Kappa-4"],
    )
    lesson = service.create_entry(
        type="LESSON", title="Lesson", summary="x", created_by=_A, provenance="x derived from outcome",
        source="conv", trust_status="LEARNED", domains=["test-domain"], entities=["Kappa-4"],
    )
    r1 = service.create_relationship(source_entry_id=decision.id, target_entry_id=problem.id, relation_type="RELATED_TO", source="conv", created_by=_A)
    _confirm(service, r1.id)
    r2 = service.create_relationship(source_entry_id=action.id, target_entry_id=decision.id, relation_type="RESULTED_IN", source="conv", created_by=_A)
    _confirm(service, r2.id)
    r3 = service.create_relationship(source_entry_id=outcome.id, target_entry_id=action.id, relation_type="RESULTED_IN", source="conv", created_by=_A)
    _confirm(service, r3.id)
    r4 = service.create_relationship(source_entry_id=lesson.id, target_entry_id=outcome.id, relation_type="RESULTED_IN", source="conv", created_by=_A)
    _confirm(service, r4.id)

    bundle = service.get_experience_bundle(problem.id, actor=_A)
    ids_in_bundle = {s.entry_id for s in bundle.stages}
    assert ids_in_bundle == {problem.id, decision.id, action.id, outcome.id, lesson.id}
    # Each stage keeps its own type/summary/trust_status/provenance --
    # not collapsed into one generated summary.
    for stage in bundle.stages:
        assert stage.type
        assert stage.trust_status
        assert stage.provenance
    assert bundle.truncated is False


# -- G: multiple candidates, bounded deterministic ordering -----------------


def test_g_bounded_deterministic_ordering(service: SecondBrainService):
    created = []
    for i in range(5):
        e = service.create_entry(
            type="PROBLEM", title=f"Problem {i}", summary="x", created_by=_A, provenance="x",
            source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=[f"Asset-{i}"],
        )
        created.append(e)

    run1 = service.find_related_experiences(actor=_A, domains=["test-domain"])
    run2 = service.find_related_experiences(actor=_A, domains=["test-domain"])
    assert [c.entry_id for c in run1] == [c.entry_id for c in run2]
    assert len(run1) == 5  # all found, none dropped, none duplicated

    # Bounded: max_candidates caps the returned list even if more exist.
    bounded = service.find_related_experiences(actor=_A, domains=["test-domain"], max_candidates=2)
    assert len(bounded) == 2


# -- H: archived entries excluded by default ---------------------------------


def test_h_archived_excluded_by_default(service: SecondBrainService):
    entry = service.create_entry(
        type="PROBLEM", title="To be archived", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=["Kappa-4"],
    )
    service.archive_entry(entry.id, actor=_A)
    candidates = service.find_related_experiences(actor=_A, domains=["test-domain"])
    assert candidates == []


# -- I: PRIVATE entry authorization -------------------------------------------


def test_i_private_authorization_preserved(service: SecondBrainService):
    private_entry = service.create_entry(
        type="PROBLEM", title="Private problem", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", visibility="PRIVATE",
        domains=["test-domain"], entities=["Kappa-4"],
    )
    owner_candidates = service.find_related_experiences(actor=_A, domains=["test-domain"])
    assert any(c.entry_id == private_entry.id for c in owner_candidates)

    stranger_candidates = service.find_related_experiences(actor=_B, domains=["test-domain"])
    assert not any(c.entry_id == private_entry.id for c in stranger_candidates)

    # Also enforced in relationship expansion and bundle traversal.
    outcome = service.create_entry(
        type="OUTCOME", title="Private outcome", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OUTCOME", visibility="PRIVATE", domains=["test-domain"],
    )
    rel = service.create_relationship(source_entry_id=outcome.id, target_entry_id=private_entry.id, relation_type="RESULTED_IN", source="conv", created_by=_A)
    _confirm(service, rel.id)

    with pytest.raises(Exception):
        service.get_experience_bundle(private_entry.id, actor=_B)


# -- J: special FTS characters ------------------------------------------------


def test_j_fts5_special_characters_safe(service: SecondBrainService):
    entry = service.create_entry(
        type="PROBLEM", title="Problem on Zeta-9", summary="Calibration issue on Zeta-9.",
        created_by=_A, provenance="x", source="conv", trust_status="OBSERVED",
        domains=["test-domain"], entities=["Zeta-9"],
    )
    for q in ("Zeta-9", "title:hack OR 1=1", "NOT AND OR", 'foo"bar'):
        candidates = service.find_related_experiences(actor=_A, query=q)
        assert isinstance(candidates, list)  # must not raise
    candidates = service.find_related_experiences(actor=_A, query="Zeta-9")
    assert len(candidates) == 1
    assert candidates[0].entry_id == entry.id


# -- K: no match --------------------------------------------------------------


def test_k_no_match_honest_empty(service: SecondBrainService):
    candidates = service.find_related_experiences(actor=_A, query="nothing exists like this at all")
    assert candidates == []


# -- L: relationship traversal, no fabricated relationship -------------------


def test_l_no_fabricated_relationship(service: SecondBrainService):
    a = service.create_entry(
        type="PROBLEM", title="A", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OBSERVED", domains=["test-domain"], entities=["Kappa-4"],
    )
    b = service.create_entry(
        type="OUTCOME", title="B", summary="x", created_by=_A, provenance="x",
        source="conv", trust_status="OUTCOME", domains=["test-domain"], entities=["Kappa-4"],
    )
    # An unconfirmed (PROPOSED) relationship exists between A and B --
    # it must NOT appear in the bundle or as a RELATIONSHIP-level match,
    # since only CONFIRMED structure is certified.
    service.create_relationship(source_entry_id=b.id, target_entry_id=a.id, relation_type="RESULTED_IN", source="conv", created_by=_A)

    bundle = service.get_experience_bundle(a.id, actor=_A)
    assert len(bundle.stages) == 1  # only the anchor -- no PROPOSED edge followed

    candidates = service.find_related_experiences(actor=_A, entities=["Kappa-4"])
    rel_level_ids = {c.entry_id for c in candidates if c.retrieval_level == "RELATIONSHIP"}
    assert b.id not in rel_level_ids
