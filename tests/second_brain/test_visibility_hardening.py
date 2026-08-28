"""M2.5C Phase 1 -- Second Brain Visibility / Relationship Access Hardening V1.

Phase 0 reconnaissance found two confirmed leaks:

1. `_entry_summary()` (second_brain_tools.py) rendered relationship
   metadata (related_entry_id/relation_type/status) for a neighbor entry
   regardless of whether the CALLING actor was authorized to see that
   neighbor -- it called the unfiltered `service.get_relationships()`
   directly instead of resolving each neighbor through the same
   principal-scoped access path `second_brain_get`/`second_brain_search`
   already use for the primary entry (Obsidian's `_relationship_line()`
   already demonstrated the correct resolve-and-omit pattern).

2. `second_brain_link` -> `service.create_relationship()` performed no
   visibility check at all (only existence), functioning as an
   existence oracle and an unauthorized-mutation path against entries
   the caller cannot read.

This phase does NOT give TEAM/COMPANY real group-membership semantics
-- no identity/team/org primitive exists anywhere in this codebase
(Phase 0 finding). PRIVATE enforcement (creator-only) is unchanged and
reused as-is; TEAM/COMPANY continue their existing pass-through
behavior, now honestly annotated as not group-membership authorized in
this runtime.

No live DB, no network, no real Second Brain writes -- isolated
tmp_path-backed store, mirroring test_identity.py's fixture pattern.
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

_ALICE = "test-principal:alice"
_BOB = "test-principal:bob"


@pytest.fixture
def service(tmp_path: Path) -> SecondBrainService:
    store = SecondBrainStore(db_path=tmp_path / "test_visibility_hardening.db")
    svc = SecondBrainService(store=store)
    yield svc
    svc.close()


def _tools(service: SecondBrainService, principal: str):
    return {
        "search": SecondBrainSearchTool(service=service, principal=principal),
        "get": SecondBrainGetTool(service=service, principal=principal),
        "propose": SecondBrainProposeEntryTool(service=service, principal=principal),
        "confirm": SecondBrainConfirmEntryTool(service=service, principal=principal),
        "link": SecondBrainLinkTool(service=service, principal=principal),
    }


def _propose_and_confirm(tools, **kwargs) -> str:
    defaults = dict(
        type="PROBLEM",
        title="Test entry",
        summary="Test summary for visibility hardening.",
        provenance="Test provenance.",
        source="conversation",
        trust_status="OBSERVED",
        domains=["test-domain"],
        entities=["test-entity"],
        visibility="PRIVATE",
    )
    defaults.update(kwargs)
    propose_result = tools["propose"].execute(**defaults)
    assert propose_result.success, propose_result.content
    confirm_result = tools["confirm"].execute(proposal_id=propose_result.metadata["proposal_id"])
    assert confirm_result.success, confirm_result.content
    return confirm_result.metadata["entry_id"]


# -- A: search omits a relationship to a neighbor the caller can't see -------


def test_a_search_omits_relationship_to_private_neighbor(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    bob = _tools(service, _BOB)

    hidden_id = _propose_and_confirm(alice, title="Alice hidden problem", visibility="PRIVATE")
    visible_id = _propose_and_confirm(alice, title="Alice visible decision", visibility="TEAM")
    link_result = alice["link"].execute(
        source_entry_id=visible_id, target_entry_id=hidden_id, relation_type="RELATED_TO", source="test"
    )
    assert link_result.success  # Alice linking her own two entries is legitimate

    bob_search = bob["search"].execute(query="Alice visible decision")
    assert bob_search.metadata["num_results"] == 1
    assert hidden_id not in bob_search.content
    assert "RELATED_TO" not in bob_search.content
    entry_dict = bob_search.metadata["entries"][0]
    assert entry_dict["relationships"] == []  # fully omitted, not a placeholder


# -- B: same guarantee through second_brain_get -------------------------------


def test_b_get_omits_relationship_to_private_neighbor(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    bob = _tools(service, _BOB)

    hidden_id = _propose_and_confirm(alice, title="Alice hidden action", visibility="PRIVATE")
    visible_id = _propose_and_confirm(alice, title="Alice visible outcome", visibility="COMPANY")
    link_result = alice["link"].execute(
        source_entry_id=visible_id, target_entry_id=hidden_id, relation_type="RESULTED_IN", source="test"
    )
    assert link_result.success

    bob_get = bob["get"].execute(entry_id=visible_id)
    assert bob_get.success
    assert hidden_id not in bob_get.content
    assert "RESULTED_IN" not in bob_get.content
    assert bob_get.metadata["relationships"] == []


# -- C: relationship between two mutually-visible entries still renders ------


def test_c_authorized_relationship_still_rendered_normally(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    a = _propose_and_confirm(alice, title="A")
    b = _propose_and_confirm(alice, title="B")
    link_result = alice["link"].execute(
        source_entry_id=a, target_entry_id=b, relation_type="RELATED_TO", source="test"
    )
    assert link_result.success

    get_result = alice["get"].execute(entry_id=a)
    assert len(get_result.metadata["relationships"]) == 1
    assert get_result.metadata["relationships"][0]["related_entry_id"] == b
    assert "RELATED_TO" in get_result.content
    assert b in get_result.content


# -- D: second_brain_link denies an unauthorized endpoint --------------------


def test_d_second_brain_link_denies_unauthorized_target(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    bob = _tools(service, _BOB)

    hidden_id = _propose_and_confirm(alice, title="Alice hidden lesson", visibility="PRIVATE")
    bob_own_id = _propose_and_confirm(bob, title="Bob own problem")

    link_result = bob["link"].execute(
        source_entry_id=bob_own_id, target_entry_id=hidden_id, relation_type="RELATED_TO", source="test"
    )
    assert not link_result.success
    # Error must not confirm the target's existence/visibility more than
    # a genuinely-nonexistent id would -- no "PRIVATE"/"unauthorized"
    # wording that would distinguish "exists but hidden" from "does not exist".
    assert "PRIVATE" not in link_result.content
    assert "unauthorized" not in link_result.content.lower()

    # Compare against a genuinely nonexistent id -- same shape of denial.
    nonexistent_result = bob["link"].execute(
        source_entry_id=bob_own_id, target_entry_id="does-not-exist-at-all", relation_type="RELATED_TO", source="test"
    )
    assert not nonexistent_result.success

    # No relationship row was created by the denied attempt.
    rels = service.get_relationships(bob_own_id, direction="both")
    assert rels == []


# -- E: same-actor linking is unaffected --------------------------------------


def test_e_second_brain_link_same_actor_still_works(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    a = _propose_and_confirm(alice)
    b = _propose_and_confirm(alice)
    link_result = alice["link"].execute(
        source_entry_id=a, target_entry_id=b, relation_type="RELATED_TO", source="test"
    )
    assert link_result.success
    rels = service.get_relationships(a, direction="both")
    assert len(rels) == 1


# -- F: TEAM/COMPANY honesty annotation ---------------------------------------


def test_f_team_and_company_get_honesty_annotation_private_does_not(service: SecondBrainService):
    alice = _tools(service, _ALICE)
    team_id = _propose_and_confirm(alice, title="Team-visible entry", visibility="TEAM")
    company_id = _propose_and_confirm(alice, title="Company-visible entry", visibility="COMPANY")
    private_id = _propose_and_confirm(alice, title="Private entry", visibility="PRIVATE")

    team_get = alice["get"].execute(entry_id=team_id)
    assert "no group-membership authorization" in team_get.content
    assert "TEAM" in team_get.content

    company_get = alice["get"].execute(entry_id=company_id)
    assert "no group-membership authorization" in company_get.content
    assert "COMPANY" in company_get.content

    private_get = alice["get"].execute(entry_id=private_id)
    assert "group-membership" not in private_get.content  # PRIVATE behavior unchanged, no noise
