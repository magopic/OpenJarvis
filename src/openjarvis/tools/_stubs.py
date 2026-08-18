"""ABC for tool implementations and the ToolExecutor dispatch engine.

Follows the same registry pattern as ``engine/_stubs.py`` and ``memory/_stubs.py``.
Each tool is registered via ``@ToolRegistry.register("name")`` and implements
``BaseTool`` with a ``spec`` property and ``execute()`` method.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult

logger = logging.getLogger(__name__)

# Bound on how long a Web/API tool call will block waiting for a human
# Approve/Deny click before treating it as a timeout. Local to this
# mechanism only — not a new global config entry (Fase 3B.6 authorization
# scope). No existing approval-timeout setting was found to reuse (verified
# by search before implementing).
_WEB_APPROVAL_TIMEOUT_SECONDS = 300.0
_WEB_APPROVAL_POLL_INTERVAL_SECONDS = 1.5

# ---------------------------------------------------------------------------
# ToolSpec — metadata describing a tool's interface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolSpec:
    """Declarative description of a tool's interface and characteristics."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    cost_estimate: float = 0.0
    latency_estimate: float = 0.0
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    required_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BaseTool ABC
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Base class for all tool implementations.

    Subclasses must be registered via
    ``@ToolRegistry.register("name")`` to become discoverable.
    """

    tool_id: str
    is_local: bool = True

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""

    @abstractmethod
    def execute(self, **params: Any) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        from openjarvis.tools.description_loader import (
            get_tool_description_override,
        )

        s = self.spec
        desc = get_tool_description_override(s.name) or s.description
        return {
            "type": "function",
            "function": {
                "name": s.name,
                "description": desc,
                "parameters": s.parameters,
            },
        }


# ---------------------------------------------------------------------------
# ToolExecutor — dispatch engine for tool calls
# ---------------------------------------------------------------------------


class ToolExecutor:
    """Dispatch tool calls to registered tools with event bus integration.

    Parameters
    ----------
    tools:
        List of tool instances to make available.
    bus:
        Optional event bus for publishing ``TOOL_CALL_START``/``TOOL_CALL_END``.
    """

    def __init__(
        self,
        tools: List[BaseTool],
        bus: Optional[EventBus] = None,
        *,
        interactive: bool = False,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        default_timeout: float = 30.0,
        capability_policy: Optional[Any] = None,
        agent_id: str = "",
        boundary_guard: Optional[Any] = None,
    ) -> None:
        self._tools: Dict[str, BaseTool] = {t.spec.name: t for t in tools}
        self._bus = bus
        self._interactive = interactive
        self._confirm_callback = confirm_callback
        self._default_timeout = default_timeout
        self._capability_policy = capability_policy
        self._agent_id = agent_id
        self._boundary_guard = boundary_guard

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Parse arguments, dispatch to tool, measure latency, emit events."""
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Unknown tool: {tool_call.name}",
                success=False,
            )

        # Parse arguments
        try:
            params = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except json.JSONDecodeError as exc:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Invalid arguments JSON: {exc}",
                success=False,
            )
        if not isinstance(params, dict):
            return ToolResult(
                tool_name=tool_call.name,
                content=(
                    "Invalid arguments: expected a JSON object, "
                    f"got {type(params).__name__}."
                ),
                success=False,
            )

        # Boundary guard: scan external tool arguments
        if self._boundary_guard is not None and not getattr(tool, "is_local", True):
            try:
                tool_call = self._boundary_guard.check_outbound(tool_call)
                # Re-parse arguments after potential redaction
                params = json.loads(tool_call.arguments) if tool_call.arguments else {}
                if not isinstance(params, dict):
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            "Invalid arguments: expected a JSON object, "
                            f"got {type(params).__name__}."
                        ),
                        success=False,
                    )
            except Exception as exc:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Security block: {exc}",
                    success=False,
                )

        # RBAC capability check
        if self._capability_policy and tool.spec.required_capabilities:
            for cap in tool.spec.required_capabilities:
                if not self._capability_policy.check(
                    self._agent_id,
                    cap,
                    tool_call.name,
                ):
                    if self._bus:
                        self._bus.publish(
                            EventType.CAPABILITY_DENIED,
                            {
                                "agent_id": self._agent_id,
                                "capability": cap,
                                "tool": tool_call.name,
                            },
                        )
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            f"Capability '{cap}' denied for"
                            f" agent '{self._agent_id}'"
                            f" on tool '{tool_call.name}'."
                        ),
                        success=False,
                    )

        # Taint checking (sink policy)
        taint_set = params.get("_taint") if isinstance(params, dict) else None
        if taint_set is not None:
            try:
                from openjarvis.security.taint import TaintSet, check_taint

                if isinstance(taint_set, TaintSet):
                    violation = check_taint(tool_call.name, taint_set)
                    if violation:
                        if self._bus:
                            self._bus.publish(
                                EventType.TAINT_VIOLATION,
                                {
                                    "tool": tool_call.name,
                                    "violation": violation,
                                },
                            )
                        return ToolResult(
                            tool_name=tool_call.name,
                            content=f"Taint violation: {violation}",
                            success=False,
                        )
            except ImportError:
                pass
            # Remove internal taint key before passing to tool
            if isinstance(params, dict):
                params.pop("_taint", None)

        # Emit start event up front — covers the confirmation wait (if any)
        # as well as execution, so a Web-approval-gated call is visible in
        # trace from the moment it's requested, not only once it runs.
        # ``agent`` carries the managed-agent UUID so the AgentExecutor's
        # trace subscriber (which filters by agent_id) can actually match
        # this event — without it, every tool call is silently dropped from
        # traces.
        t0 = time.time()
        if self._bus:
            self._bus.publish(
                EventType.TOOL_CALL_START,
                {
                    "tool": tool_call.name,
                    "arguments": params,
                    "agent": self._agent_id,
                },
            )

        # Confirmation check for sensitive tools. Two independent paths:
        #  - CLI (``interactive`` + a real ``confirm_callback``): unchanged,
        #    a synchronous terminal y/N prompt.
        #  - Everything else (Web/API, Managed Agents): route through the
        #    existing Approval queue/UI (ApprovalBell) instead of an
        #    unconditional silent rejection.
        result: Optional[ToolResult] = None
        approval_action_id: Optional[str] = None
        if tool.spec.requires_confirmation:
            if self._interactive and self._confirm_callback is not None:
                prompt = (
                    f"Allow execution of tool '{tool_call.name}' with args {params}?"
                )
                if not self._confirm_callback(prompt):
                    result = ToolResult(
                        tool_name=tool_call.name,
                        content=f"Tool '{tool_call.name}' execution denied by user.",
                        success=False,
                    )
            else:
                approval_action_id, result = self._await_web_approval(
                    tool_call, params
                )

        if result is None:
            # Not gated, or gated and approved — execute with timeout.
            timeout = tool.spec.timeout_seconds or self._default_timeout
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(tool.execute, **params)
                    result = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                if self._bus:
                    self._bus.publish(
                        EventType.TOOL_TIMEOUT,
                        {"tool": tool_call.name, "timeout": timeout},
                    )
                result = ToolResult(
                    tool_name=tool_call.name,
                    content=(
                        f"Tool '{tool_call.name}' timed out after {timeout:.0f}s."
                    ),
                    success=False,
                )
            except Exception as exc:
                result = ToolResult(
                    tool_name=tool_call.name,
                    content=f"Tool execution error: {exc}",
                    success=False,
                )
            if approval_action_id is not None:
                # Approved-then-executed: tag the result so the trace/API
                # can tell it apart from a tool that never needed gating,
                # matching the denied/timeout path's own "approval" tag.
                result.metadata["approval"] = {
                    "action_id": approval_action_id,
                    "decision": "approved",
                }
        latency = time.time() - t0
        result.latency_seconds = latency
        result.metadata["arguments"] = params

        # Auto-detect taints in results
        if result.success:
            try:
                from openjarvis.security.taint import auto_detect_taint

                detected = auto_detect_taint(result.content)
                if detected and detected.labels:
                    result.metadata["_taint"] = detected
            except ImportError:
                pass

        # Emit end event
        if self._bus:
            result_text = str(result.content)[:10240] if result.content else ""
            # Pass through ToolResult.metadata so downstream consumers
            # (TraceCollector → TraceStep.metadata → SkillOptimizer) can
            # see skill-tagged invocations.  Filter to JSON-serializable
            # values only — internal objects like TaintSet (added by the
            # taint auto-detect above) must not leak to event subscribers
            # since the trace store will JSON-serialize them later.
            event_metadata = self._json_safe_metadata(result.metadata)
            self._bus.publish(
                EventType.TOOL_CALL_END,
                {
                    "tool": tool_call.name,
                    "success": result.success,
                    "latency": latency,
                    "result": result_text,
                    "metadata": event_metadata,
                    "agent": self._agent_id,
                },
            )

        return result

    def _await_web_approval(
        self, tool_call: ToolCall, params: Dict[str, Any]
    ) -> tuple[Optional[str], Optional[ToolResult]]:
        """Gate a ``requires_confirmation`` tool call through the existing
        Approval queue/UI (``ApprovalStore`` + ``ApprovalBell``) instead of
        the CLI's synchronous ``confirm_callback``.

        Queues a ``PendingAction`` — the same record type and REST surface
        (``/v1/approvals/pending``, ``.../approve``, ``.../deny``) already
        used by the proactive-agent approval flow, so no new endpoint or UI
        is introduced. The payload is captured once, from the exact
        ``params`` about to be executed, and is never modified afterwards:
        approving action X can only resume the one tool call that queued X.

        This call blocks (via ``time.sleep`` polling) for up to
        ``_WEB_APPROVAL_TIMEOUT_SECONDS``. That is safe here because
        ``ToolExecutor.execute()`` already always runs inside the
        background thread backing the whole agent turn
        (``asyncio.to_thread`` in ``server/routes.py``), which already
        tolerates multi-minute waits for model inference — this adds no new
        class of blocking behaviour to the request lifecycle.

        Returns ``(action_id, None)`` when approved — the caller proceeds
        to execute the tool normally and tags the eventual result with
        ``action_id`` for traceability. Returns ``(action_id, ToolResult)``
        for denied/timeout, where the ``ToolResult`` is terminal and the
        caller must use it as-is without ever calling ``tool.execute()``.
        """
        from openjarvis.tools.approval_store import (
            ApprovalStore,
            STATUS_APPROVED,
            STATUS_EXPIRED,
            STATUS_PENDING,
            TIER_HIGH,
        )

        store = ApprovalStore()
        action = store.queue_action(
            action_type=f"tool:{tool_call.name}",
            description=f"Approve execution of '{tool_call.name}'?",
            payload={"tool": tool_call.name, "arguments": params},
            permission_key=f"tool:{tool_call.name}",
            tier=TIER_HIGH,
            ttl_hours=_WEB_APPROVAL_TIMEOUT_SECONDS / 3600.0,
        )
        logger.info(
            "Tool '%s' requires confirmation; queued web approval %s "
            "(timeout %.0fs)",
            tool_call.name,
            action.id,
            _WEB_APPROVAL_TIMEOUT_SECONDS,
        )

        deadline = time.time() + _WEB_APPROVAL_TIMEOUT_SECONDS
        decision = "timeout"
        while time.time() < deadline:
            time.sleep(_WEB_APPROVAL_POLL_INTERVAL_SECONDS)
            current = store.get_action(action.id)
            if current is None or current.status != STATUS_PENDING:
                decision = current.status if current is not None else "timeout"
                break

        if decision == STATUS_APPROVED:
            logger.info(
                "Web approval %s for '%s': APPROVED", action.id, tool_call.name
            )
            return action.id, None

        if decision == "timeout":
            store.update_status(action.id, STATUS_EXPIRED)
            content = (
                f"Tool '{tool_call.name}' was not approved within "
                f"{_WEB_APPROVAL_TIMEOUT_SECONDS:.0f}s and was not executed."
            )
        else:
            content = f"Tool '{tool_call.name}' execution was denied by the user."

        logger.info(
            "Web approval %s for '%s': %s",
            action.id,
            tool_call.name,
            decision.upper(),
        )
        result = ToolResult(tool_name=tool_call.name, content=content, success=False)
        result.metadata["approval"] = {"action_id": action.id, "decision": decision}
        return action.id, result

    @staticmethod
    def _json_safe_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a copy of *metadata* containing only JSON-serializable values.

        ``ToolExecutor`` annotates ``ToolResult.metadata`` with internal
        objects (currently ``_taint: TaintSet``).  Those are useful for
        in-process security checks but cannot be serialized when the
        ``TraceCollector`` writes ``TraceStep.metadata`` to JSON in the
        SQLite trace store.  This helper drops any keys whose value is
        not JSON-safe — silently, since the missing data is not
        load-bearing for downstream consumers.
        """
        if not metadata:
            return {}

        import json

        safe: Dict[str, Any] = {}
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                # Skip non-serializable values (e.g. TaintSet)
                continue
            safe[key] = value
        return safe

    def available_tools(self) -> List[ToolSpec]:
        """Return specs for all available tools."""
        return [t.spec for t in self._tools.values()]

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return tools in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]


def build_tool_descriptions(
    tools: List[BaseTool],
    *,
    include_category: bool = True,
    include_cost: bool = False,
) -> str:
    """Build rich text descriptions from a list of tools.

    This is the single source of truth for all text-based agents that need
    to describe available tools in their system prompts.

    Parameters
    ----------
    tools:
        List of tool instances.
    include_category:
        Whether to include the ``Category:`` line.
    include_cost:
        Whether to include ``Cost estimate:`` and ``Latency estimate:`` lines.

    Returns
    -------
    str
        Formatted multi-tool description, or ``"No tools available."`` if
        *tools* is empty.
    """
    if not tools:
        return "No tools available."

    from openjarvis.tools.description_loader import (
        get_tool_description_override,
    )

    sections: list[str] = []
    for t in tools:
        s = t.spec
        desc = get_tool_description_override(s.name) or s.description
        lines = [f"### {s.name}", desc]

        if include_category and s.category:
            lines.append(f"Category: {s.category}")

        if include_cost:
            if s.cost_estimate:
                lines.append(f"Cost estimate: ${s.cost_estimate:.4f}")
            if s.latency_estimate:
                lines.append(f"Latency estimate: {s.latency_estimate:.1f}s")

        # Parameter descriptions
        props = s.parameters.get("properties", {})
        required = set(s.parameters.get("required", []))
        if props:
            lines.append("Parameters:")
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                req_mark = ", required" if pname in required else ""
                desc = pinfo.get("description", "")
                if desc:
                    lines.append(f"  - {pname} ({ptype}{req_mark}): {desc}")
                else:
                    lines.append(f"  - {pname} ({ptype}{req_mark})")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


__all__ = ["BaseTool", "ToolExecutor", "ToolSpec", "build_tool_descriptions"]
