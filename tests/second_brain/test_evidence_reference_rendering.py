"""M2.5B Phase 1 -- Second Brain Evidence Reference Honest Rendering.

Phase 0 reconnaissance found `evidence_references` was persisted but
never rendered back to the model at all (write-only), with zero
code-level validation anywhere -- only advisory tool-schema prose. This
phase does not add validation; it makes the field visible, always
labeled UNVERIFIED, so its mere presence (or the memory's own
trust_status) is never mistaken for independently verified support.

Core authority rule under test: evidence_reference != evidence_verified.

No live DB, no network, no real Second Brain writes -- every test uses
an isolated tmp_path-backed store, mirroring test_capture_workflow.py's
exact fixture pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.agents.operational_evidence import build_evidence
from openjarvis.core.types import ToolResult
from openjarvis.second_brain.projections.obsidian import render_entry_note, render_frontmatter
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.tools.second_brain_tools import (
    SecondBrainConfirmEntryTool,
    SecondBrainFindRelatedExperiencesTool,
    SecondBrainGetTool,
    SecondBrainProposeEntryTool,
    SecondBrainSearchTool,
)

_ALICE = "test-principal:alice"

_REF_A = {
    "capability": "ops.production.get_kpi",
    "domain": "production",
    "metric": "oee",
    "period": "2026-07",
}
_REF_B = {
    "capability": "ops.logistics.get_kpi",
    "domain": "logistics",
    "metric": "saldo_epal",
    "period": "2026-07",
}


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_evidence_ref.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


@pytest.fixture
def tools(service: SecondBrainService):
    return {
        "search": SecondBrainSearchTool(service=service, principal=_ALICE),
        "get": SecondBrainGetTool(service=service, principal=_ALICE),
        "propose": SecondBrainProposeEntryTool(service=service, principal=_ALICE),
        "confirm": SecondBrainConfirmEntryTool(service=service, principal=_ALICE),
        "find_related": SecondBrainFindRelatedExperiencesTool(service=service, principal=_ALICE),
    }


def _propose_and_confirm(tools, **kwargs) -> str:
    defaults = dict(
        type="PROBLEM",
        title="Test entry",
        summary="Test summary for evidence reference rendering.",
        provenance="Test provenance.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["test-domain"],
        entities=["test-entity"],
    )
    defaults.update(kwargs)
    propose_result = tools["propose"].execute(**defaults)
    assert propose_result.success, propose_result.content
    confirm_result = tools["confirm"].execute(proposal_id=propose_result.metadata["proposal_id"])
    assert confirm_result.success, confirm_result.content
    return confirm_result.metadata["entry_id"]


# -- A: one evidence reference -> UNVERIFIED in rendering --------------------


def test_a_single_evidence_reference_renders_unverified(tools):
    entry_id = _propose_and_confirm(tools, evidence_references=[_REF_A])

    search_result = tools["search"].execute(query="Test entry")
    assert "UNVERIFIED" in search_result.content
    assert "ops.production.get_kpi" in search_result.content
    entry_dict = search_result.metadata["entries"][0]
    assert entry_dict["evidence_references"][0]["verification_status"] == "UNVERIFIED"

    get_result = tools["get"].execute(entry_id=entry_id)
    assert "UNVERIFIED" in get_result.content
    assert get_result.metadata["evidence_references"][0]["verification_status"] == "UNVERIFIED"


# -- B: multiple evidence references -> each represented, all UNVERIFIED -----


def test_b_multiple_evidence_references_all_unverified(tools):
    _propose_and_confirm(tools, evidence_references=[_REF_A, _REF_B])

    search_result = tools["search"].execute(query="Test entry")
    refs = search_result.metadata["entries"][0]["evidence_references"]
    assert len(refs) == 2
    assert all(r["verification_status"] == "UNVERIFIED" for r in refs)
    assert "ops.production.get_kpi" in search_result.content
    assert "ops.logistics.get_kpi" in search_result.content
    assert search_result.content.count("UNVERIFIED") >= 2


# -- C: no evidence references -> rendering unchanged, no noise --------------


def test_c_no_evidence_references_rendering_unchanged(tools):
    _propose_and_confirm(tools)  # no evidence_references kwarg at all

    search_result = tools["search"].execute(query="Test entry")
    assert "evidence_references" not in search_result.content
    assert "UNVERIFIED" not in search_result.content
    assert "evidence_references" not in search_result.metadata["entries"][0]


# -- D: VERIFIED trust_status + reference -> reference still UNVERIFIED ------


def test_d_verified_trust_status_does_not_upgrade_reference(tools):
    entry_id = _propose_and_confirm(
        tools,
        type="ACTION",
        trust_status="VERIFIED",
        evidence_references=[_REF_A],
    )
    get_result = tools["get"].execute(entry_id=entry_id)
    assert "trust=VERIFIED" in get_result.content
    assert "UNVERIFIED" in get_result.content  # the reference itself
    ref = get_result.metadata["evidence_references"][0]
    assert ref["verification_status"] == "UNVERIFIED"
    assert get_result.metadata["trust_status"] == "VERIFIED"  # entry trust unaffected


# -- E: LESSON/HYPOTHESIS + reference -> same UNVERIFIED semantics -----------


def test_e_hypothesis_lesson_with_reference_same_unverified_semantics(tools):
    entry_id = _propose_and_confirm(
        tools,
        type="LESSON",
        trust_status="HYPOTHESIS",
        domains=[],
        entities=[],
        evidence_references=[_REF_A],  # satisfies LESSON's grounding OR-gate
    )
    get_result = tools["get"].execute(entry_id=entry_id)
    assert "trust=HYPOTHESIS" in get_result.content
    assert "UNVERIFIED" in get_result.content
    assert get_result.metadata["trust_status"] == "HYPOTHESIS"
    assert get_result.metadata["evidence_references"][0]["verification_status"] == "UNVERIFIED"


# -- F: operational evidence recap preserves UNVERIFIED ----------------------


def test_f_operational_evidence_recap_preserves_unverified(tools):
    _propose_and_confirm(tools, evidence_references=[_REF_A])
    search_result = tools["search"].execute(query="Test entry")

    tr = ToolResult(
        tool_name="second_brain_search",
        content=search_result.content,
        success=True,
        metadata=search_result.metadata,
    )
    evidence = build_evidence([tr])
    assert len(evidence.historical_experience) == 1
    summary = evidence.historical_experience[0].summary
    assert "UNVERIFIED" in summary
    assert "ops.production.get_kpi" in summary


# -- find_related_experiences also renders evidence references --------------


def test_find_related_experiences_renders_unverified(tools, service: SecondBrainService):
    entry_id = _propose_and_confirm(tools, evidence_references=[_REF_A])
    # Link a second entry so find_related_experiences has something to
    # traverse via relationship/domain match (matches its own retrieval
    # discipline -- exact entity match is the simplest reliable path).
    result = tools["find_related"].execute(entities=["test-entity"])
    assert result.success
    assert result.metadata["num_candidates"] >= 1
    assert "UNVERIFIED" in result.content
    assert "ops.production.get_kpi" in result.content
    candidate = next(c for c in result.metadata["candidates"] if c["entry_id"] == entry_id)
    assert candidate["evidence_references"][0]["verification_status"] == "UNVERIFIED"


# -- V1 limitation: two syntactically different refs get identical treatment -


def test_v1_limitation_different_looking_references_receive_same_unverified_treatment(tools):
    """Phase 1 cannot distinguish a plausible-but-fabricated reference
    from a genuinely legitimate one -- both render identically labeled
    UNVERIFIED. This is an intentional, tested V1 limitation, not a gap."""
    entry_id = _propose_and_confirm(tools, evidence_references=[_REF_A, _REF_B])
    get_result = tools["get"].execute(entry_id=entry_id)
    refs = get_result.metadata["evidence_references"]
    assert refs[0]["capability"] != refs[1]["capability"]
    assert refs[0]["verification_status"] == refs[1]["verification_status"] == "UNVERIFIED"


# -- H/I: Obsidian human-facing export ----------------------------------------


def test_h_obsidian_export_populated_reference_carries_unverified(service: SecondBrainService, tools):
    entry_id = _propose_and_confirm(tools, evidence_references=[_REF_A])
    entry = service.get_entry(entry_id, actor=_ALICE)

    frontmatter = render_frontmatter(entry, outcome_backed=None)
    assert "verification_status: UNVERIFIED" in frontmatter

    note = render_entry_note(entry, [], lambda eid: None, outcome_backed=None)
    assert "[UNVERIFIED]" in note
    assert "ops.production.get_kpi" in note


def test_i_obsidian_export_empty_references_unchanged(service: SecondBrainService, tools):
    entry_id = _propose_and_confirm(tools)  # no evidence_references
    entry = service.get_entry(entry_id, actor=_ALICE)

    frontmatter = render_frontmatter(entry, outcome_backed=None)
    assert "evidence_references" not in frontmatter
    assert "UNVERIFIED" not in frontmatter

    note = render_entry_note(entry, [], lambda eid: None, outcome_backed=None)
    assert "## Evidence References" in note  # pre-existing heading, unchanged
    assert "*(none)*" in note  # pre-existing empty-state marker, unchanged
    assert "UNVERIFIED" not in note
