"""Generic OPS Bridge adapter — discovers capabilities, registers tools dynamically.

Replaces the "one Python file per capability" pattern (see
``ops_bridge_production_kpi.py``, kept temporarily for backward
compatibility) with a single component that:

1. calls ``ops.registry.list_capabilities`` on the OPS Bridge at startup;
2. keeps only capabilities whose Registry metadata says
   ``trust_status == "TRUSTED"`` and ``requires_approval == False``;
3. builds and registers one native OpenJarvis tool per surviving
   capability, using only the name/description/input_schema the Registry
   returned.

This module depends on nothing but the OPS Bridge HTTP contract
({capability, params} -> envelope) and the Registry's introspection
response shape. It contains no Supabase/table/formula/OPS-service
knowledge and no MAIA-specific concept -- exactly like
``ops_bridge_production_kpi.py``, just generalized over whatever the
Registry reports instead of one hardcoded capability.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Type

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_BASE_URL = "http://127.0.0.1:3000"
_LIST_CAPABILITY = "ops.registry.list_capabilities"
_DISCOVERY_TIMEOUT_SECONDS = 2.0
_CALL_TIMEOUT_SECONDS = 30.0
_TOOL_ID_PREFIX = "ops_dynamic_"

# FASE 4L.2C: generic size control for what gets reinjected into the LLM's
# context on the tool-result turn. The Bridge itself always returns the full
# envelope untouched (see ToolResult.metadata below) -- only the *summary
# text* fed back to the model is shape-limited here, and only when it would
# actually be large. Not tuned to any specific capability's field names:
# any top-level list-valued field in `data`, from any current or future
# capability, is subject to the same generic truncation.
_SUMMARY_CHAR_BUDGET = 1500
_MAX_LIST_ITEMS_IN_SUMMARY = 5

# Governance policy (FASE 4I): a capability may be auto-enabled for chat only
# if trust_status == TRUSTED, requires_approval == False, and category is one
# of these. Anything else (ACTION, PARTIAL/UNTRUSTED/NOT_IMPLEMENTED trust,
# requires_approval=true, or missing/unknown metadata) fails closed.
_AUTO_ENABLE_CATEGORIES = {"READ", "KNOWLEDGE"}

# Infrastructural capabilities that the Generic Adapter itself calls for
# discovery and has no reason to expose as an LLM-facing chat tool, even
# though they pass governance and stay registered in ToolRegistry.
_INTERNAL_ONLY_CAPABILITIES = {_LIST_CAPABILITY}

# Populated by discover_and_register_ops_bridge_tools(); read by the two
# existing config.tools.enabled consumers (SystemBuilder._resolve_tools and
# serve.py's _resolve_allowed_tools) so a TRUSTED/READ|KNOWLEDGE capability
# is available to chat without being hand-added to config.toml.
_auto_enabled_tool_ids: List[str] = []


def _passes_governance(capability: Dict[str, Any]) -> bool:
    """Fail closed on anything missing, invalid, or outside the policy."""
    name = capability.get("name")
    if not isinstance(name, str) or not name:
        return False
    if capability.get("trust_status") != "TRUSTED":
        return False
    if capability.get("requires_approval") is not False:
        return False
    category = capability.get("category")
    if category not in _AUTO_ENABLE_CATEGORIES:
        return False
    return True


def _bridge_base_url() -> str:
    return os.environ.get("OPS_BRIDGE_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _capability_to_tool_id(capability_name: str) -> str:
    """'ops.production.get_kpi' -> 'ops_dynamic_production_get_kpi'."""
    stripped = capability_name[4:] if capability_name.startswith("ops.") else capability_name
    return _TOOL_ID_PREFIX + stripped.replace(".", "_")


def _schema_to_tool_parameters(input_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Pass the Registry's input_schema through as-is; it is already a plain
    JSON-schema-shaped object ({type, properties, required?}) -- no
    reinterpretation needed."""
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    return {
        "type": "object",
        "properties": input_schema.get("properties") or {},
        **({"required": input_schema["required"]} if input_schema.get("required") else {}),
    }


def _compact_for_context(
    data: Any, *, budget_chars: int = _SUMMARY_CHAR_BUDGET, max_items: int = _MAX_LIST_ITEMS_IN_SUMMARY
) -> "tuple[Any, bool]":
    """Generic (capability-agnostic) size control for the LLM-facing summary.

    Only triggers when the JSON-serialized `data` object exceeds
    `budget_chars`. When it does, any top-level field whose value is a list
    longer than `max_items` is truncated to the first `max_items` entries,
    with `<field>_returned_count`/`<field>_total_count`/`<field>_truncated`
    markers added alongside it. Scalar fields and short lists are left
    exactly as-is. Discovers what to truncate purely from shape (is this
    value a list, is it long) -- no field name from any specific capability
    is referenced, so this applies unchanged to any future capability that
    returns a large list under any key.
    """
    if not isinstance(data, dict):
        return data, False
    try:
        full_json = json.dumps(data, default=str)
    except Exception:
        return data, False
    if len(full_json) <= budget_chars:
        return data, False

    compacted: Dict[str, Any] = {}
    any_truncated = False
    for key, value in data.items():
        if isinstance(value, list) and len(value) > max_items:
            compacted[key] = value[:max_items]
            compacted[f"{key}_returned_count"] = max_items
            compacted[f"{key}_total_count"] = len(value)
            compacted[f"{key}_truncated"] = True
            any_truncated = True
        else:
            compacted[key] = value
    return compacted, any_truncated


def _summarize(envelope: Dict[str, Any]) -> str:
    status = envelope.get("status")
    if status != "ok":
        return f"status={status} period={envelope.get('period')} reason={envelope.get('reason')}"

    prefix = (
        f"status=ok source={envelope.get('source')} period={envelope.get('period')} "
        f"confidence_status={envelope.get('confidence_status')}"
    )
    compacted, truncated = _compact_for_context(envelope.get("data"))
    if truncated:
        return (
            f"{prefix} data={compacted} "
            "note='One or more list fields were truncated for context size; "
            "see the *_total_count/_returned_count/_truncated markers next to "
            "each. Call again with a narrower filter or a smaller limit "
            "parameter to see different items -- the full result is still "
            "available from the Bridge, only what is shown to you here was "
            "shortened.'"
        )
    return f"{prefix} data={envelope.get('data')}"


def _call_bridge(capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{_bridge_base_url()}/api/ops-bridge"
    response = httpx.post(
        url,
        json={"capability": capability, "params": params},
        timeout=_CALL_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _make_dynamic_tool_class(capability: Dict[str, Any]) -> Type[BaseTool]:
    """Build one BaseTool subclass for a single discovered capability.

    Nothing here is capability-specific business logic: every value comes
    from the ``capability`` dict the Registry itself returned.
    """
    capability_name = capability["name"]
    _tool_id = _capability_to_tool_id(capability_name)
    description = capability.get("description", f"OPS Bridge capability '{capability_name}'.")
    parameters = _schema_to_tool_parameters(capability.get("input_schema") or {})

    class _DynamicOpsBridgeTool(BaseTool):
        tool_id = _tool_id
        is_local = False

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=_tool_id,
                description=description,
                parameters=parameters,
                category="business",
                required_capabilities=["network:fetch"],
            )

        def execute(self, **params: Any) -> ToolResult:
            try:
                envelope = _call_bridge(capability_name, params)
            except httpx.TimeoutException as exc:
                return ToolResult(
                    tool_name=_tool_id,
                    content=f"OPS Bridge request timed out: {exc}",
                    success=False,
                )
            except httpx.HTTPStatusError as exc:
                return ToolResult(
                    tool_name=_tool_id,
                    content=f"OPS Bridge returned HTTP {exc.response.status_code}.",
                    success=False,
                )
            except httpx.RequestError as exc:
                return ToolResult(
                    tool_name=_tool_id,
                    content=f"Could not reach OPS Bridge: {exc}",
                    success=False,
                )
            except ValueError as exc:
                return ToolResult(
                    tool_name=_tool_id,
                    content=f"OPS Bridge returned a non-JSON response: {exc}",
                    success=False,
                )

            # Return the Bridge's own envelope untouched -- no reinterpretation,
            # no recalculation, no invented values.
            return ToolResult(
                tool_name=_tool_id,
                content=_summarize(envelope),
                success=envelope.get("status") == "ok",
                metadata=envelope,
            )

    _DynamicOpsBridgeTool.__name__ = f"DynamicOpsBridgeTool_{_tool_id}"
    return _DynamicOpsBridgeTool


def discover_and_register_ops_bridge_tools() -> List[str]:
    """Discover governance-passing capabilities and register a tool for each.

    A capability that fails the governance policy (_passes_governance) is not
    registered at all -- fail closed applies to ToolRegistry visibility, not
    just chat auto-enablement, so nothing dormant-but-unsafe sits around.

    Returns the list of tool_ids registered. Never raises -- any failure to
    reach the Bridge (not running, network error, malformed response) results
    in an empty list, matching the try/except-and-skip convention every other
    optional tool in tools/__init__.py already follows.

    Also refreshes the module's auto-enable list (see
    get_auto_enabled_ops_tool_ids) to exactly the tool_ids registered by this
    run that are meant to reach chat -- i.e. governance-passing capabilities
    minus the internal-only ones (see _INTERNAL_ONLY_CAPABILITIES).
    """
    global _auto_enabled_tool_ids

    try:
        envelope = _call_bridge_for_discovery()
    except Exception:
        _auto_enabled_tool_ids = []
        return []

    if envelope.get("status") != "ok":
        _auto_enabled_tool_ids = []
        return []
    capabilities = ((envelope.get("data") or {}).get("capabilities")) or []
    if not isinstance(capabilities, list):
        _auto_enabled_tool_ids = []
        return []

    registered: List[str] = []
    auto_enabled: List[str] = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        if not _passes_governance(capability):
            continue
        name = capability["name"]
        tool_id = _capability_to_tool_id(name)
        if not ToolRegistry.contains(tool_id):
            try:
                tool_cls = _make_dynamic_tool_class(capability)
                ToolRegistry.register_value(tool_id, tool_cls)
            except Exception:
                continue
        registered.append(tool_id)
        if name not in _INTERNAL_ONLY_CAPABILITIES:
            auto_enabled.append(tool_id)

    _auto_enabled_tool_ids = auto_enabled
    return registered


def get_auto_enabled_ops_tool_ids() -> List[str]:
    """Tool ids that passed governance and should reach chat automatically.

    Consumed by the two existing config.tools.enabled resolvers
    (SystemBuilder._resolve_tools, serve.py's _resolve_allowed_tools) so a
    TRUSTED READ/KNOWLEDGE capability is available without being hand-added
    to config.toml. Never raises; returns [] if discovery hasn't run or
    found nothing -- callers should treat that as "no OPS tools available",
    not as an error.
    """
    return list(_auto_enabled_tool_ids)


def _call_bridge_for_discovery() -> Dict[str, Any]:
    url = f"{_bridge_base_url()}/api/ops-bridge"
    response = httpx.post(
        url,
        json={"capability": _LIST_CAPABILITY, "params": {}},
        timeout=_DISCOVERY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


__all__ = [
    "discover_and_register_ops_bridge_tools",
    "get_auto_enabled_ops_tool_ids",
]
