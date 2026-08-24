"""FASE 4P.3 STEP 11/12 -- the model-facing governed-action surface.

Deliberately excludes any tool that could approve or execute:
- No `maia_action_approve` -- STEP 11's own audit concluded approval must
  NOT be model-callable. It happens only inside orchestrator.py's
  deterministic runtime-detection path (STEP 10), which independently
  verifies "exactly one pending action + an explicit human affirmative
  in THIS turn's original input" before ever calling
  GovernedActionService.approve() -- the model is never in that call
  chain and cannot influence it via any tool call.
- No generic execute tool of any kind (STEP 12) -- execution happens only
  as part of that same runtime path, immediately after runtime-detected
  approval, never on model request.

`maia_action_reject` IS model-callable: rejection only ever narrows what
could happen, so relaying a user's "no"/"cancel" through the model is
safe in a way approval is not.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.governed_actions.service import GovernedActionError, GovernedActionService
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _action_brief(a: Any) -> dict:
    return {
        "id": a.id,
        "capability": a.capability,
        "status": a.status,
        "rationale": a.rationale,
        "created_at": a.created_at,
        "expires_at": a.expires_at,
    }


def _action_full(a: Any) -> dict:
    d = _action_brief(a)
    d.update(
        {
            "arguments": a.arguments,
            "arguments_hash": a.arguments_hash,
            "principal": a.principal,
            "proposal_id": a.proposal_id,
            "supporting_evidence": a.supporting_evidence,
            "approved_at": a.approved_at,
            "approved_by": a.approved_by,
            "executed_at": a.executed_at,
            "execution_result": a.execution_result,
            "failure": a.failure,
        }
    )
    return d


@ToolRegistry.register("maia_actions_list")
class GovernedActionsListTool(BaseTool):
    tool_id = "maia_actions_list"

    def __init__(self, service: Optional[GovernedActionService] = None) -> None:
        self._service = service or GovernedActionService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_actions_list",
            description="List governed actions (proposed/pending-approval/approved/executed/etc), optionally filtered by status.",
            parameters={
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.second_brain.identity import resolve_runtime_principal

        actions = self._service.list_actions(
            principal=resolve_runtime_principal(), status=params.get("status")
        )
        return ToolResult(
            tool_name="maia_actions_list",
            content=json.dumps([_action_brief(a) for a in actions]),
            success=True,
            metadata={"num_actions": len(actions)},
        )


@ToolRegistry.register("maia_action_get")
class GovernedActionGetTool(BaseTool):
    tool_id = "maia_action_get"

    def __init__(self, service: Optional[GovernedActionService] = None) -> None:
        self._service = service or GovernedActionService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_get",
            description="Get full detail for one governed action by id.",
            parameters={
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        action = self._service.get_action(params.get("action_id", ""))
        if action is None:
            return ToolResult(tool_name="maia_action_get", content="Governed action not found.", success=False)
        return ToolResult(tool_name="maia_action_get", content=json.dumps(_action_full(action)), success=True)


@ToolRegistry.register("maia_action_prepare")
class GovernedActionPrepareTool(BaseTool):
    tool_id = "maia_action_prepare"

    def __init__(self, service: Optional[GovernedActionService] = None) -> None:
        self._service = service or GovernedActionService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_prepare",
            description=(
                "Draft a governed action for a specific, allowlisted capability with "
                "specific arguments. This does NOT execute anything and does NOT ask "
                "for approval yet -- it only prepares a proposal the user can review. "
                "You must show the user exactly what this would do before requesting "
                "approval; never request approval silently."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "capability": {"type": "string"},
                    "arguments": {"type": "object"},
                    "rationale": {"type": "string"},
                    "proposal_id": {
                        "type": "string",
                        "description": (
                            "Optional: the id of a prior ProactiveInsight's ProposedAction "
                            "this governed action originates from, if any."
                        ),
                    },
                },
                "required": ["capability", "arguments", "rationale"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            action = self._service.prepare_action(
                params.get("capability", ""),
                params.get("arguments") or {},
                rationale=params.get("rationale", ""),
                proposal_id=params.get("proposal_id"),
            )
        except GovernedActionError as exc:
            return ToolResult(tool_name="maia_action_prepare", content=str(exc), success=False)
        return ToolResult(tool_name="maia_action_prepare", content=json.dumps(_action_full(action)), success=True)


@ToolRegistry.register("maia_action_request_approval")
class GovernedActionRequestApprovalTool(BaseTool):
    tool_id = "maia_action_request_approval"

    def __init__(self, service: Optional[GovernedActionService] = None) -> None:
        self._service = service or GovernedActionService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_request_approval",
            description=(
                "Formally ask the user to approve a previously prepared governed "
                "action. This does NOT approve or execute anything itself -- it only "
                "marks the action as awaiting the user's explicit decision. You must "
                "have already shown the user exactly what the action would do."
            ),
            parameters={
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            action = self._service.request_approval(params.get("action_id", ""))
        except GovernedActionError as exc:
            return ToolResult(tool_name="maia_action_request_approval", content=str(exc), success=False)
        return ToolResult(
            tool_name="maia_action_request_approval", content=json.dumps(_action_full(action)), success=True
        )


@ToolRegistry.register("maia_action_reject")
class GovernedActionRejectTool(BaseTool):
    tool_id = "maia_action_reject"

    def __init__(self, service: Optional[GovernedActionService] = None) -> None:
        self._service = service or GovernedActionService()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="maia_action_reject",
            description="Reject a governed action the user declined. Safe to call on the user's behalf when they say no/cancel/don't do that.",
            parameters={
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            action = self._service.reject(params.get("action_id", ""))
        except GovernedActionError as exc:
            return ToolResult(tool_name="maia_action_reject", content=str(exc), success=False)
        return ToolResult(tool_name="maia_action_reject", content=json.dumps(_action_full(action)), success=True)


__all__ = [
    "GovernedActionsListTool",
    "GovernedActionGetTool",
    "GovernedActionPrepareTool",
    "GovernedActionRequestApprovalTool",
    "GovernedActionRejectTool",
]
