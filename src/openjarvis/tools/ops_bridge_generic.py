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


def _summarize(envelope: Dict[str, Any]) -> str:
    status = envelope.get("status")
    if status == "ok":
        return f"status=ok data={envelope.get('data')} period={envelope.get('period')}"
    return f"status={status} period={envelope.get('period')} reason={envelope.get('reason')}"


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
    """Discover TRUSTED, non-approval capabilities and register a tool for each.

    Returns the list of tool_ids registered. Never raises -- any failure to
    reach the Bridge (not running, network error, malformed response) results
    in an empty list, matching the try/except-and-skip convention every other
    optional tool in tools/__init__.py already follows.
    """
    try:
        envelope = _call_bridge_for_discovery()
    except Exception:
        return []

    if envelope.get("status") != "ok":
        return []
    capabilities = ((envelope.get("data") or {}).get("capabilities")) or []
    if not isinstance(capabilities, list):
        return []

    registered: List[str] = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        if capability.get("trust_status") != "TRUSTED":
            continue
        if capability.get("requires_approval", True) is not False:
            continue
        name = capability.get("name")
        if not isinstance(name, str) or not name:
            continue
        tool_id = _capability_to_tool_id(name)
        if ToolRegistry.contains(tool_id):
            continue
        try:
            tool_cls = _make_dynamic_tool_class(capability)
            ToolRegistry.register_value(tool_id, tool_cls)
            registered.append(tool_id)
        except Exception:
            continue
    return registered


def _call_bridge_for_discovery() -> Dict[str, Any]:
    url = f"{_bridge_base_url()}/api/ops-bridge"
    response = httpx.post(
        url,
        json={"capability": _LIST_CAPABILITY, "params": {}},
        timeout=_DISCOVERY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


__all__ = ["discover_and_register_ops_bridge_tools"]
