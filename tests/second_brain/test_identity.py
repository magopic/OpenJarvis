"""FASE 4N.2A -- stable identity binding tests.

Covers:
 - resolve_runtime_principal() determinism (STEP 2).
 - Cross-invocation PRIVATE retrieval under isolated storage (STEP 4,
   "Invocation A/B": same real principal, fresh tool instances each
   time, simulating separate ``jarvis ask`` processes).
 - Cross-principal denial (STEP 4, "Invocation C": a genuinely
   different principal).
 - Spoofing resistance: a model trying to pass its own actor/
   created_by/principal string in the tool-call payload has zero
   effect -- the runtime-injected principal always wins.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjarvis.second_brain.identity import resolve_runtime_principal
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.store import SecondBrainStore
from openjarvis.tools.second_brain_tools import (
    SecondBrainConfirmEntryTool,
    SecondBrainGetTool,
    SecondBrainProposeEntryTool,
    SecondBrainSearchTool,
)


def test_resolve_runtime_principal_is_deterministic():
    """Same process, same OS account -> same principal every call."""
    first = resolve_runtime_principal()
    second = resolve_runtime_principal()
    assert first == second
    assert first  # never empty


def test_resolve_runtime_principal_honors_override(monkeypatch):
    """The env-var escape hatch (process-controlled, never model-controlled)."""
    monkeypatch.setenv("OPENJARVIS_PRINCIPAL_OVERRIDE", "ci-runner-42")
    assert resolve_runtime_principal() == "ci-runner-42"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_identity.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


def test_cross_invocation_same_principal_retrieves_private(service: SecondBrainService):
    """STEP 4, Invocation A -> B: fresh tool instances each time (simulating
    a new `jarvis ask` process), but the SAME real principal (as
    resolve_runtime_principal() would return on the same machine/account)
    -- the entry must be retrievable, with no actor string for the model
    to have to remember or guess."""
    same_principal = "test-principal:same-machine-same-account"

    # Invocation A: propose + confirm a PRIVATE entry.
    invocation_a = {
        "propose": SecondBrainProposeEntryTool(service=service, principal=same_principal),
        "confirm": SecondBrainConfirmEntryTool(service=service, principal=same_principal),
    }
    propose_result = invocation_a["propose"].execute(
        type="OBSERVATION",
        title="Nota privata invocazione A",
        summary="Creata nella prima invocazione.",
        provenance="Conversazione A.",
        source="conversation",
        trust_status="OBSERVED",
        visibility="PRIVATE",
    )
    confirm_result = invocation_a["confirm"].execute(
        proposal_id=propose_result.metadata["proposal_id"]
    )
    entry_id = confirm_result.metadata["entry_id"]

    # Invocation B: brand new tool instances (a fresh process would
    # construct these from scratch), same principal.
    invocation_b_search = SecondBrainSearchTool(service=service, principal=same_principal)
    invocation_b_get = SecondBrainGetTool(service=service, principal=same_principal)

    search_result = invocation_b_search.execute(query="invocazione A")
    assert search_result.metadata["num_results"] == 1
    assert search_result.metadata["entries"][0]["id"] == entry_id

    get_result = invocation_b_get.execute(entry_id=entry_id)
    assert get_result.success


def test_cross_principal_denial(service: SecondBrainService):
    """STEP 4, Invocation C: a genuinely different principal must be denied."""
    owner_principal = "test-principal:owner"
    other_principal = "test-principal:stranger"

    owner_propose = SecondBrainProposeEntryTool(service=service, principal=owner_principal)
    owner_confirm = SecondBrainConfirmEntryTool(service=service, principal=owner_principal)
    propose_result = owner_propose.execute(
        type="OBSERVATION",
        title="Nota privata del proprietario",
        summary="Solo il proprietario dovrebbe vederla.",
        provenance="Conversazione privata.",
        source="conversation",
        trust_status="OBSERVED",
        visibility="PRIVATE",
    )
    entry_id = owner_confirm.execute(
        proposal_id=propose_result.metadata["proposal_id"]
    ).metadata["entry_id"]

    stranger_get = SecondBrainGetTool(service=service, principal=other_principal)
    stranger_search = SecondBrainSearchTool(service=service, principal=other_principal)

    denied = stranger_get.execute(entry_id=entry_id)
    assert not denied.success

    hidden = stranger_search.execute(query="proprietario")
    assert hidden.metadata["num_results"] == 0


def test_model_cannot_spoof_actor_via_tool_params(service: SecondBrainService):
    """A model attempting to pass its own actor/created_by/principal in
    the tool-call payload must have zero effect -- these are no longer
    accepted arguments at all, and even if slipped in as extra kwargs
    (execute(**params) doesn't reject unknown keys), the code never
    reads them for identity purposes."""
    real_principal = "test-principal:real-runtime-identity"
    propose_tool = SecondBrainProposeEntryTool(service=service, principal=real_principal)
    confirm_tool = SecondBrainConfirmEntryTool(service=service, principal=real_principal)

    # Attempted spoof: the model tries to claim it's someone else.
    propose_result = propose_tool.execute(
        type="OBSERVATION",
        title="Tentativo di spoofing",
        summary="Il modello tenta di impersonare un altro utente.",
        provenance="x",
        source="conversation",
        trust_status="OBSERVED",
        created_by="attacker:spoofed",  # ignored -- not read by the tool anymore
        actor="attacker:spoofed",  # ignored -- not read by the tool anymore
    )
    confirm_result = confirm_tool.execute(
        proposal_id=propose_result.metadata["proposal_id"],
        actor="attacker:spoofed",  # ignored
    )
    entry_id = confirm_result.metadata["entry_id"]

    entry = service.get_entry(entry_id, actor=real_principal)
    assert entry is not None
    assert entry.created_by == real_principal  # NOT "attacker:spoofed"

    # And the spoofed identity genuinely cannot read it.
    spoofed_get = SecondBrainGetTool(service=service, principal="attacker:spoofed")
    assert not spoofed_get.execute(entry_id=entry_id).success


def test_second_brain_tools_schema_has_no_identity_params():
    """The JSON schema the model actually sees must not offer actor/
    created_by/principal as fillable arguments at all."""
    for tool_cls in (
        SecondBrainSearchTool,
        SecondBrainGetTool,
        SecondBrainProposeEntryTool,
        SecondBrainConfirmEntryTool,
    ):
        tool = tool_cls()
        props = tool.spec.parameters.get("properties", {})
        for forbidden in ("actor", "created_by", "principal"):
            assert forbidden not in props, f"{tool_cls.__name__} still exposes {forbidden!r}"
