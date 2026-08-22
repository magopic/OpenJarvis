"""FASE 4N.2 STEP 9 -- conversational capture workflow scenarios.

Each test exercises the tool layer directly (calling ``.execute()`` the
same way the orchestrator's tool-calling loop would), simulating what a
model should do at each conversational turn -- without needing a live
LLM in the loop. Live model integration is verified separately (STEP
10). All tests use an isolated temporary database.

FASE 4N.2A: tools no longer accept ``actor``/``created_by`` as
execute() arguments -- identity is a constructor-injected
``principal`` (see ``second_brain/identity.py``), never something the
caller passes in the tool-call payload. Tests that need to simulate
two different real users construct two separate sets of tools with
different explicit ``principal=`` values (this is what the runtime,
never the model, is responsible for supplying).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.tools.second_brain_tools import (
    SecondBrainArchiveTool,
    SecondBrainConfirmEntryTool,
    SecondBrainGetTool,
    SecondBrainLinkTool,
    SecondBrainProposeEntryTool,
    SecondBrainSearchTool,
)

_ALICE = "test-principal:alice"
_BOB = "test-principal:bob"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_capture.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


def _make_tools(service: SecondBrainService, principal: str):
    return {
        "search": SecondBrainSearchTool(service=service, principal=principal),
        "get": SecondBrainGetTool(service=service, principal=principal),
        "propose": SecondBrainProposeEntryTool(service=service, principal=principal),
        "confirm": SecondBrainConfirmEntryTool(service=service, principal=principal),
        "link": SecondBrainLinkTool(service=service, principal=principal),
        "archive": SecondBrainArchiveTool(service=service, principal=principal),
    }


@pytest.fixture
def tools(service: SecondBrainService):
    return _make_tools(service, _ALICE)


# -- A: propose only, never auto-saved ---------------------------------


def test_a_propose_does_not_auto_save(tools):
    """User: 'Ricordati che abbiamo avuto un problema sulla linea M.'"""
    result = tools["propose"].execute(
        type="PROBLEM",
        title="Problema sulla linea M",
        summary="Abbiamo avuto un problema sulla linea M.",
        provenance="Segnalato dall'utente in conversazione.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["production"],
        entities=["linea M"],
    )
    assert result.success
    proposal_id = result.metadata["proposal_id"]
    assert proposal_id

    # Nothing searchable/persisted yet -- MAIA must not have auto-saved.
    search_result = tools["search"].execute(query="linea M")
    assert search_result.metadata["num_results"] == 0


# -- B: HYPOTHESIS stays HYPOTHESIS, no upgrade -----------------------------


def test_b_hypothesis_only_not_verified(tools):
    """User correction: 'Era un'ipotesi, non una causa verificata.'"""
    result = tools["propose"].execute(
        type="HYPOTHESIS",
        title="Ipotesi: cambio formato",
        summary="Il problema sulla linea M potrebbe essere il cambio formato (non verificato).",
        provenance="L'utente ha specificato che è un'ipotesi, non una causa verificata.",
        source="conversation",
        trust_status="HYPOTHESIS",
        domains=["production"],
        entities=["linea M"],
    )
    assert result.success
    assert result.metadata["proposal_id"]


# -- C: confirm persists exactly once -----------------------------------


def test_c_confirm_persists_once(tools):
    propose_result = tools["propose"].execute(
        type="PROBLEM",
        title="Problema sulla linea M",
        summary="Fermo macchina sulla linea M.",
        provenance="Segnalato in conversazione.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["production"],
        entities=["linea M"],
    )
    proposal_id = propose_result.metadata["proposal_id"]

    confirm_result = tools["confirm"].execute(proposal_id=proposal_id)
    assert confirm_result.success
    entry_id = confirm_result.metadata["entry_id"]

    # Now searchable.
    search_result = tools["search"].execute(query="linea M")
    assert search_result.metadata["num_results"] == 1
    assert search_result.metadata["entries"][0]["id"] == entry_id

    # A second confirm on the same proposal must fail -- persisted once.
    second_confirm = tools["confirm"].execute(proposal_id=proposal_id)
    assert not second_confirm.success

    search_again = tools["search"].execute(query="linea M")
    assert search_again.metadata["num_results"] == 1  # still exactly one


# -- D: correction creates SUPERSEDES, preserves old -------------------


def test_d_correction_supersedes_preserves_old(tools, service: SecondBrainService):
    original_propose = tools["propose"].execute(
        type="OBSERVATION",
        title="Causa del fermo linea M",
        summary="Causa non ancora determinata.",
        provenance="Prima osservazione in conversazione.",
        source="conversation",
        trust_status="HYPOTHESIS",
        domains=["production"],
        entities=["linea M"],
    )
    original_confirm = tools["confirm"].execute(
        proposal_id=original_propose.metadata["proposal_id"]
    )
    original_id = original_confirm.metadata["entry_id"]

    # User: "Correggi: la causa verificata era il cambio formato."
    correction_propose = tools["propose"].execute(
        type="OBSERVATION",
        title="Causa del fermo linea M (verificata)",
        summary="La causa verificata era il cambio formato.",
        provenance="Correzione confermata dall'utente in conversazione.",
        source="conversation",
        trust_status="VERIFIED",
        domains=["production"],
        entities=["linea M"],
    )
    correction_confirm = tools["confirm"].execute(
        proposal_id=correction_propose.metadata["proposal_id"],
        supersedes_entry_id=original_id,
    )
    assert correction_confirm.success
    new_id = correction_confirm.metadata["entry_id"]
    assert new_id != original_id

    # Old entry preserved, unmutated content, now points at the new one.
    old_entry = service.get_entry(original_id, actor=_ALICE)
    assert old_entry is not None
    assert old_entry.summary == "Causa non ancora determinata."  # untouched
    assert old_entry.superseded_by == new_id

    rels = service.get_relationships(new_id)
    assert any(r.relation_type.value == "SUPERSEDES" and r.status.value == "CONFIRMED" for r in rels)


# -- E: retrieval of a past problem --------------------------------------


def test_e_retrieval_of_past_problem(tools):
    """Query: 'Abbiamo già avuto problemi sulla linea M?'"""
    propose = tools["propose"].execute(
        type="PROBLEM",
        title="Fermo linea M",
        summary="Fermo macchina rilevato sulla linea M.",
        provenance="Conversazione precedente.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["production"],
        entities=["linea M"],
    )
    tools["confirm"].execute(proposal_id=propose.metadata["proposal_id"])

    result = tools["search"].execute(query="linea M", entity="linea M")
    assert result.success
    assert result.metadata["num_results"] >= 1
    assert any("linea M" in e["title"] or "linea M" in e["entities"] for e in result.metadata["entries"])


# -- F: relationship-aware retrieval, no invented solution -------------


def test_f_relationship_aware_no_invented_solution(tools):
    """Query: 'Come avevamo risolto?' -- must use the RESOLVED_BY
    relationship, never invent a solution when none is linked."""
    problem_propose = tools["propose"].execute(
        type="PROBLEM", title="Fermo linea M", summary="Fermo macchina.",
        provenance="x", source="conversation",
        trust_status="OBSERVED", domains=["production"], entities=["linea M"],
    )
    problem_id = tools["confirm"].execute(
        proposal_id=problem_propose.metadata["proposal_id"]
    ).metadata["entry_id"]

    # No resolution linked yet.
    rels_before = tools["get"].execute(entry_id=problem_id).metadata["relationships_summary"]
    assert rels_before == "none"

    outcome_propose = tools["propose"].execute(
        type="OUTCOME", title="Fermo risolto", summary="Risolto sostituendo il cambio formato.",
        provenance="x", source="conversation",
        trust_status="OUTCOME", domains=["production"],
    )
    outcome_id = tools["confirm"].execute(
        proposal_id=outcome_propose.metadata["proposal_id"]
    ).metadata["entry_id"]

    link_result = tools["link"].execute(
        source_entry_id=problem_id, target_entry_id=outcome_id,
        relation_type="RESOLVED_BY", source="conversation",
    )
    assert link_result.success
    assert link_result.metadata["status"] == "PROPOSED"  # not auto-confirmed

    rels_after = tools["get"].execute(entry_id=problem_id).metadata["relationships_summary"]
    assert "PROPOSED" in rels_after


# -- G: no match, honest refusal -----------------------------------------


def test_g_no_match_honest_refusal(tools):
    result = tools["search"].execute(query="stabilimento Z problema mai successo")
    assert result.success
    assert result.metadata["num_results"] == 0
    assert "no matching" in result.content.lower()


# -- H: private entry, unauthorized denied (+ archive) ---------------------


def test_h_private_entry_unauthorized_denied(service: SecondBrainService):
    alice_tools = _make_tools(service, _ALICE)
    bob_tools = _make_tools(service, _BOB)

    propose = alice_tools["propose"].execute(
        type="OBSERVATION",
        title="Nota privata",
        summary="Nota personale.",
        provenance="Conversazione privata.",
        source="conversation",
        trust_status="OBSERVED",
        visibility="PRIVATE",
    )
    confirm = alice_tools["confirm"].execute(proposal_id=propose.metadata["proposal_id"])
    entry_id = confirm.metadata["entry_id"]

    # Owner can read it.
    owner_get = alice_tools["get"].execute(entry_id=entry_id)
    assert owner_get.success

    # A different real principal is denied.
    other_get = bob_tools["get"].execute(entry_id=entry_id)
    assert not other_get.success

    # And it never leaks into another principal's search results.
    other_search = bob_tools["search"].execute(query="privata")
    assert other_search.metadata["num_results"] == 0

    # Nor can a different principal archive it.
    other_archive = bob_tools["archive"].execute(entry_id=entry_id)
    assert not other_archive.success

    # The owner can.
    owner_archive = alice_tools["archive"].execute(entry_id=entry_id)
    assert owner_archive.success
