"""FASE 4P.3 STEP 21 -- model-facing governed-action tool surface tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

import openjarvis.tools.governed_action_tools  # noqa: F401 -- triggers @ToolRegistry.register
from openjarvis.core.registry import ToolRegistry
from openjarvis.governed_actions.service import GovernedActionService
from openjarvis.governed_actions.store import GovernedActionStore


def _svc() -> GovernedActionService:
    return GovernedActionService(
        store=GovernedActionStore(tempfile.mktemp(suffix=".db")),
        test_notes_path=Path(tempfile.mktemp(suffix=".txt")),
    )


class TestRegistration:
    def test_five_tools_registered(self):
        import importlib

        import openjarvis.tools.governed_action_tools as mod

        importlib.reload(mod)
        for name in (
            "maia_actions_list",
            "maia_action_get",
            "maia_action_prepare",
            "maia_action_request_approval",
            "maia_action_reject",
        ):
            assert ToolRegistry.contains(name), name

    def test_no_approve_no_execute_tool_registered(self):
        import importlib

        import openjarvis.tools.governed_action_tools as mod

        importlib.reload(mod)
        for name in ("maia_action_approve", "maia_action_execute", "maia_execute_action"):
            assert not ToolRegistry.contains(name), name


class TestPrepareListGetReject:
    def test_prepare_creates_proposed_action(self):
        from openjarvis.tools.governed_action_tools import GovernedActionPrepareTool

        svc = _svc()
        result = GovernedActionPrepareTool(service=svc).execute(
            capability="maia_test_write_note", arguments={"note": "hi"}, rationale="test"
        )
        assert result.success is True
        payload = json.loads(result.content)
        assert payload["status"] == "PROPOSED"

    def test_prepare_unknown_capability_fails_honestly(self):
        from openjarvis.tools.governed_action_tools import GovernedActionPrepareTool

        result = GovernedActionPrepareTool(service=_svc()).execute(
            capability="not_real", arguments={}, rationale="test"
        )
        assert result.success is False

    def test_prepare_then_list_then_get(self):
        from openjarvis.tools.governed_action_tools import (
            GovernedActionGetTool,
            GovernedActionPrepareTool,
            GovernedActionsListTool,
        )

        svc = _svc()
        created = GovernedActionPrepareTool(service=svc).execute(
            capability="maia_test_write_note", arguments={"note": "hi"}, rationale="test"
        )
        action_id = json.loads(created.content)["id"]

        listed = GovernedActionsListTool(service=svc).execute()
        assert any(a["id"] == action_id for a in json.loads(listed.content))

        got = GovernedActionGetTool(service=svc).execute(action_id=action_id)
        assert got.success is True
        assert json.loads(got.content)["arguments"]["note"] == "hi"

    def test_request_approval_transitions_to_pending(self):
        from openjarvis.tools.governed_action_tools import (
            GovernedActionPrepareTool,
            GovernedActionRequestApprovalTool,
        )

        svc = _svc()
        created = GovernedActionPrepareTool(service=svc).execute(
            capability="maia_test_write_note", arguments={"note": "hi"}, rationale="test"
        )
        action_id = json.loads(created.content)["id"]
        result = GovernedActionRequestApprovalTool(service=svc).execute(action_id=action_id)
        assert result.success is True
        assert json.loads(result.content)["status"] == "PENDING_APPROVAL"

    def test_request_approval_from_wrong_state_fails(self):
        """Cannot request approval twice, or on an action not yet prepared."""
        from openjarvis.tools.governed_action_tools import GovernedActionRequestApprovalTool

        result = GovernedActionRequestApprovalTool(service=_svc()).execute(action_id="nonexistent")
        assert result.success is False

    def test_reject_transitions_to_rejected(self):
        from openjarvis.tools.governed_action_tools import (
            GovernedActionPrepareTool,
            GovernedActionRejectTool,
            GovernedActionRequestApprovalTool,
        )

        svc = _svc()
        created = GovernedActionPrepareTool(service=svc).execute(
            capability="maia_test_write_note", arguments={"note": "hi"}, rationale="test"
        )
        action_id = json.loads(created.content)["id"]
        GovernedActionRequestApprovalTool(service=svc).execute(action_id=action_id)
        result = GovernedActionRejectTool(service=svc).execute(action_id=action_id)
        assert result.success is True
        assert json.loads(result.content)["status"] == "REJECTED"

    def test_get_unknown_action_fails_honestly(self):
        from openjarvis.tools.governed_action_tools import GovernedActionGetTool

        result = GovernedActionGetTool(service=_svc()).execute(action_id="nonexistent")
        assert result.success is False
