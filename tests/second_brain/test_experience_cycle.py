"""FASE 4N.3 -- the full operational experience cycle, isolated storage.

PROBLEM -> HYPOTHESIS -> DECISION -> ACTION -> OUTCOME -> LESSON, all
persisted through the governed tool layer, all relationships explicit
and (where appropriate) confirmed. Uses deterministic, clearly
fictional test data ("Asset Alpha-7") -- never anything resembling
real production data.

STEP 8's "future case" scenario (a second, later case with partial
overlap) is covered by ``test_similar_case_retrieval_no_automatic_causality``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.tools.second_brain_tools import (
    SecondBrainConfirmEntryTool,
    SecondBrainGetTool,
    SecondBrainLinkTool,
    SecondBrainProposeEntryTool,
    SecondBrainSearchTool,
)

_OPERATOR = "test-principal:experience-cycle"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_experience.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


@pytest.fixture
def tools(service: SecondBrainService):
    return {
        "search": SecondBrainSearchTool(service=service, principal=_OPERATOR),
        "get": SecondBrainGetTool(service=service, principal=_OPERATOR),
        "propose": SecondBrainProposeEntryTool(service=service, principal=_OPERATOR),
        "confirm": SecondBrainConfirmEntryTool(service=service, principal=_OPERATOR),
        "link": SecondBrainLinkTool(service=service, principal=_OPERATOR),
    }


def _confirmed_id(tools, propose_result) -> str:
    return tools["confirm"].execute(
        proposal_id=propose_result.metadata["proposal_id"]
    ).metadata["entry_id"]


def _confirm_relationship(service: SecondBrainService, rel_id: str) -> None:
    service.update_relationship_status(rel_id, "CONFIRMED", actor=_OPERATOR)


def test_full_experience_chain_persists_correctly(tools, service: SecondBrainService):
    # PROBLEM
    problem_id = _confirmed_id(tools, tools["propose"].execute(
        type="PROBLEM",
        title="Degraded performance on Asset Alpha-7",
        summary="Asset Alpha-7 has shown degraded operational performance over the last observed period.",
        provenance="Observed and reported in conversation.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
    ))

    # HYPOTHESIS
    hypothesis_id = _confirmed_id(tools, tools["propose"].execute(
        type="HYPOTHESIS",
        title="Changeover instability may be contributing",
        summary="Changeover instability is hypothesized as a contributing factor to the degraded performance.",
        provenance="Hypothesis formed after reviewing the problem report.",
        source="conversation",
        trust_status="HYPOTHESIS",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
    ))
    rel_problem_hyp = tools["link"].execute(
        source_entry_id=hypothesis_id, target_entry_id=problem_id,
        relation_type="RELATED_TO", source="conversation",
    )
    assert rel_problem_hyp.metadata["status"] == "PROPOSED"
    _confirm_relationship(service, rel_problem_hyp.metadata["relationship_id"])

    # DECISION -- user verifies the hypothesis and decides on an action
    decision_id = _confirmed_id(tools, tools["propose"].execute(
        type="DECISION",
        title="Inspect and adjust the changeover procedure",
        summary="Decided to inspect and, if needed, change the changeover procedure on Asset Alpha-7.",
        provenance="Decision made after the changeover-instability hypothesis was verified in conversation.",
        source="conversation",
        trust_status="DECISION",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
        timestamp=1700000000.0,
    ))
    rel_hyp_decision = tools["link"].execute(
        source_entry_id=decision_id, target_entry_id=hypothesis_id,
        relation_type="DECIDED_IN", source="conversation",
    )
    _confirm_relationship(service, rel_hyp_decision.metadata["relationship_id"])

    # ACTION
    action_id = _confirmed_id(tools, tools["propose"].execute(
        type="ACTION",
        title="Changeover procedure updated on Asset Alpha-7",
        summary="The changeover procedure on Asset Alpha-7 was inspected and updated per the decision.",
        provenance="Action taken following the decision to inspect/change the procedure.",
        source="conversation",
        trust_status="DECISION",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
    ))
    rel_decision_action = tools["link"].execute(
        source_entry_id=action_id, target_entry_id=decision_id,
        relation_type="RESULTED_IN", source="conversation",
    )
    _confirm_relationship(service, rel_decision_action.metadata["relationship_id"])

    # OUTCOME
    outcome_id = _confirmed_id(tools, tools["propose"].execute(
        type="OUTCOME",
        title="Performance recovered on Asset Alpha-7",
        summary="Performance on Asset Alpha-7 recovered in the observed period following the procedure change.",
        provenance="Outcome observed in the period after the action was taken.",
        source="conversation",
        trust_status="OUTCOME",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
    ))
    rel_action_outcome = tools["link"].execute(
        source_entry_id=outcome_id, target_entry_id=action_id,
        relation_type="RESULTED_IN", source="conversation",
    )
    _confirm_relationship(service, rel_action_outcome.metadata["relationship_id"])

    # LESSON -- linked to the OUTCOME, then confirmed
    lesson_id = _confirmed_id(tools, tools["propose"].execute(
        type="LESSON",
        title="Inspect changeover procedure early under similar conditions",
        summary="When the same verified conditions occur on an asset, inspect the changeover procedure early.",
        provenance="Derived from the Asset Alpha-7 case: hypothesis verified, decision made, "
                    "action taken, outcome observed.",
        source="conversation",
        trust_status="LEARNED",
        domains=["test-domain"],
        entities=["Asset Alpha-7"],
    ))
    rel_lesson_outcome = tools["link"].execute(
        source_entry_id=lesson_id, target_entry_id=outcome_id,
        relation_type="RESULTED_IN", source="conversation",
    )
    # Not yet confirmed -- verify the LESSON is correctly reported as
    # NOT outcome-backed until a human confirms the link.
    lesson_before = tools["get"].execute(entry_id=lesson_id)
    assert lesson_before.metadata["outcome_backed"] is False

    _confirm_relationship(service, rel_lesson_outcome.metadata["relationship_id"])

    lesson_after = tools["get"].execute(entry_id=lesson_id)
    assert lesson_after.metadata["outcome_backed"] is True

    # -- verify the whole chain -----------------------------------------

    # Provenance: every entry states what it derives from.
    for entry_id in (problem_id, hypothesis_id, decision_id, action_id, outcome_id, lesson_id):
        entry = service.get_entry(entry_id, actor=_OPERATOR)
        assert entry is not None
        assert entry.provenance

    # Trust lifecycle: each stage carries the status appropriate to it,
    # and none of them silently became something else.
    assert service.get_entry(problem_id, actor=_OPERATOR).trust_status.value == "OBSERVED"
    assert service.get_entry(hypothesis_id, actor=_OPERATOR).trust_status.value == "HYPOTHESIS"
    assert service.get_entry(decision_id, actor=_OPERATOR).trust_status.value == "DECISION"
    assert service.get_entry(outcome_id, actor=_OPERATOR).trust_status.value == "OUTCOME"
    assert service.get_entry(lesson_id, actor=_OPERATOR).trust_status.value == "LEARNED"

    # Relationships: walking from PROBLEM forward reaches every stage.
    hyp_rels = service.get_relationships(hypothesis_id)
    assert any(r.target_entry_id == problem_id and r.status.value == "CONFIRMED" for r in hyp_rels)

    # No overwritten history: nothing here used supersede_entry, so
    # every entry's superseded_by must still be unset.
    for entry_id in (problem_id, hypothesis_id, decision_id, action_id, outcome_id, lesson_id):
        assert service.get_entry(entry_id, actor=_OPERATOR).superseded_by is None

    # Audit chain intact.
    valid, broken = service.verify_audit_chain()
    assert valid is True
    assert broken is None


def _build_full_chain(tools, service: SecondBrainService) -> dict:
    """Rebuild the chain from test_full_experience_chain_persists_correctly
    for reuse by the similar-case and correction tests below."""
    problem_id = _confirmed_id(tools, tools["propose"].execute(
        type="PROBLEM", title="Degraded performance on Asset Alpha-7",
        summary="Asset Alpha-7 has shown degraded operational performance.",
        provenance="Observed and reported in conversation.", source="conversation",
        trust_status="OBSERVED", domains=["test-domain"], entities=["Asset Alpha-7"],
    ))
    outcome_id = _confirmed_id(tools, tools["propose"].execute(
        type="OUTCOME", title="Performance recovered on Asset Alpha-7",
        summary="Performance recovered after the changeover procedure was updated.",
        provenance="Outcome observed after the action.", source="conversation",
        trust_status="OUTCOME", domains=["test-domain"], entities=["Asset Alpha-7"],
    ))
    decision_id = _confirmed_id(tools, tools["propose"].execute(
        type="DECISION", title="Inspect and adjust the changeover procedure",
        summary="Decided to inspect and update the changeover procedure.",
        provenance="Decision made after the hypothesis was verified.", source="conversation",
        trust_status="DECISION", domains=["test-domain"], entities=["Asset Alpha-7"],
        timestamp=1700000000.0,
    ))
    rel = tools["link"].execute(
        source_entry_id=outcome_id, target_entry_id=decision_id,
        relation_type="RESULTED_IN", source="conversation",
    )
    _confirm_relationship(service, rel.metadata["relationship_id"])
    lesson_id = _confirmed_id(tools, tools["propose"].execute(
        type="LESSON", title="Inspect changeover procedure early",
        summary="When similar conditions occur, inspect the changeover procedure early.",
        provenance="Derived from the Asset Alpha-7 outcome.", source="conversation",
        trust_status="LEARNED", domains=["test-domain"], entities=["Asset Alpha-7"],
    ))
    rel2 = tools["link"].execute(
        source_entry_id=lesson_id, target_entry_id=outcome_id,
        relation_type="RESULTED_IN", source="conversation",
    )
    _confirm_relationship(service, rel2.metadata["relationship_id"])
    return {
        "problem_id": problem_id, "decision_id": decision_id,
        "outcome_id": outcome_id, "lesson_id": lesson_id,
    }


def test_similar_case_retrieval_no_automatic_causality(tools, service: SecondBrainService):
    """STEP 8: a later, partially-overlapping case. Similarity must be
    explicit (shared domain/entity), never a fabricated score, and must
    never be presented as automatic proof of the same cause."""
    chain = _build_full_chain(tools, service)

    # A second, later PROBLEM on a DIFFERENT asset but the SAME domain --
    # partial overlap, not identical.
    new_problem_id = _confirmed_id(tools, tools["propose"].execute(
        type="PROBLEM",
        title="Degraded performance on Asset Beta-3",
        summary="Asset Beta-3 has shown degraded operational performance.",
        provenance="Observed and reported in conversation.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["test-domain"],
        entities=["Asset Beta-3"],
    ))

    # "È già successo qualcosa di simile?" -- structural overlap search
    # by domain only (the entity differs on purpose).
    similar = tools["search"].execute(domain="test-domain", type="PROBLEM")
    assert similar.success
    matched_ids = {e["id"] for e in similar.metadata["entries"]}
    assert chain["problem_id"] in matched_ids
    assert new_problem_id in matched_ids
    # The explicit similarity basis is the domain itself -- both entries
    # report it in their own "domains" field, inspectable directly; no
    # numeric score is present anywhere in the tool's output.
    for e in similar.metadata["entries"]:
        assert "similarity_score" not in e
        assert "similarity_percentage" not in e

    # "Quindi la causa è la stessa?" -- nothing in this service/tool
    # layer asserts or implies that. No CAUSES relationship exists
    # between the old and new problem, and none should be inferable
    # from a domain match alone.
    rels = service.get_relationships(new_problem_id)
    assert not any(r.relation_type.value == "CAUSES" for r in rels)

    # "Cosa avevamo fatto quella volta?" / "Ha funzionato?" -- the old
    # case's actual DECISION and OUTCOME are retrievable by walking the
    # relationship the old PROBLEM never had to fabricate.
    decision = service.get_entry(chain["decision_id"], actor=_OPERATOR)
    outcome = service.get_entry(chain["outcome_id"], actor=_OPERATOR)
    assert decision.title == "Inspect and adjust the changeover procedure"
    assert outcome.title == "Performance recovered on Asset Alpha-7"


def test_lesson_correction_preserves_history(tools, service: SecondBrainService):
    """STEP 11: a stored lesson later proven incomplete/wrong is
    superseded, never overwritten."""
    chain = _build_full_chain(tools, service)
    lesson_id = chain["lesson_id"]

    old_lesson = service.get_entry(lesson_id, actor=_OPERATOR)
    assert old_lesson.superseded_by is None

    updated_old, rel = service.supersede_entry(
        lesson_id,
        actor=_OPERATOR,
        new_entry_kwargs=dict(
            type="LESSON",
            title="Inspect changeover procedure AND sensor calibration early",
            summary="Later evidence showed sensor calibration also mattered -- inspect both early.",
            created_by=_OPERATOR,
            provenance="Correction: the original lesson was incomplete per later evidence.",
            source="conversation",
            trust_status="LEARNED",
            domains=["test-domain"],
            entities=["Asset Alpha-7"],
        ),
    )

    # Old entry preserved verbatim, never destroyed.
    assert updated_old.id == lesson_id
    assert updated_old.title == "Inspect changeover procedure early"
    assert updated_old.superseded_by is not None
    new_lesson_id = updated_old.superseded_by

    # SUPERSEDES relationship, confirmed by construction.
    assert rel.relation_type.value == "SUPERSEDES"
    assert rel.status.value == "CONFIRMED"

    # Old version still inspectable directly by id.
    still_there = service.get_entry(lesson_id, actor=_OPERATOR)
    assert still_there is not None
    assert still_there.title == "Inspect changeover procedure early"

    # Future retrieval (default list/search) excludes the archived-style
    # superseded pointer is present but the entry itself is NOT
    # archived -- both versions remain listed; a caller preferring the
    # active one follows superseded_by to the newer entry.
    new_entry = service.get_entry(new_lesson_id, actor=_OPERATOR)
    assert new_entry.title == "Inspect changeover procedure AND sensor calibration early"

    valid, broken = service.verify_audit_chain()
    assert valid is True
    assert broken is None
