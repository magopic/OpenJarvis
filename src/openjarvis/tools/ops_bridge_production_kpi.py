"""OPS Bridge adapter — neutral HTTP translation layer for the Agent Runtime.

Calls a single OPS Bridge capability (ops.production.get_kpi) over HTTP.
Contains no business logic, no data access, no credentials, and no knowledge
of how the Bridge computes its answer — it only translates a tool call into
an HTTP request and returns the Bridge's own response envelope unmodified.
This file is meant to stay swappable: replacing the runtime that calls this
tool (e.g. a future Hermes-based adapter) should never require any change on
the OPS Bridge / OPS ONE side, and vice versa.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# M3.0: the base-URL resolver and the call-timeout budget are imported from
# the generic adapter rather than restated here, exactly as _auth_headers
# already was (M1.1). This file previously kept its own _DEFAULT_BASE_URL,
# its own _bridge_base_url() and a hardcoded 30.0s timeout -- three copies
# of environment logic that could, and did, drift out of step with the
# adapter both call sites share. There is now one definition of each.
from openjarvis.tools.ops_bridge_generic import (
    _auth_headers,
    _bridge_base_url,
    _call_timeout_seconds,
    _tool_watchdog_seconds,
)

_CAPABILITY = "ops.production.get_kpi"


def _summarize(envelope: Dict[str, Any]) -> str:
    """One-line human-readable summary for the LLM; structured fields stay in metadata."""
    status = envelope.get("status")
    if status == "ok":
        data = envelope.get("data") or {}
        return (
            f"status=ok metric={data.get('metric')} value={data.get('value')} "
            f"unit={data.get('unit')} previous_value={data.get('previous_value')} "
            f"period={envelope.get('period')}"
        )
    return f"status={status} period={envelope.get('period')} reason={envelope.get('reason')}"


@ToolRegistry.register("ops_bridge_production_kpi")
class OpsBridgeProductionKpiTool(BaseTool):
    """Look up a production KPI via the OPS Bridge (pure HTTP translation, no business logic)."""

    tool_id = "ops_bridge_production_kpi"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ops_bridge_production_kpi",
            description=(
                "Look up a production KPI (e.g. OEE) from the connected business"
                " system via the OPS Bridge. Returns the Bridge's own"
                " status/data/source/period/confidence_status envelope"
                " untouched — never invents or recalculates a value."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "Four-digit year, e.g. 2026.",
                    },
                    "month": {
                        "type": "integer",
                        "description": "Month number 1-12.",
                    },
                    "plant": {
                        "type": "string",
                        "description": "Optional plant filter.",
                    },
                    "department": {
                        "type": "string",
                        "description": "Optional department filter.",
                    },
                    "line": {
                        "type": "string",
                        "description": "Optional production line filter.",
                    },
                    "metric": {
                        "type": "string",
                        "description": "Optional metric id (defaults to the Bridge's own default).",
                    },
                },
                "required": [],
            },
            category="business",
            required_capabilities=["network:fetch"],
            # Same rule as the dynamic tools, from the same source: the
            # executor's watchdog must outlast the HTTP call it wraps.
            timeout_seconds=_tool_watchdog_seconds(),
        )

    def execute(self, **params: Any) -> ToolResult:
        # 1. Validate shape only -- no KPI logic, no data lookup here.
        year = params.get("year")
        month = params.get("month")
        for key, value in (("year", year), ("month", month)):
            if value is not None and not isinstance(value, int):
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Invalid '{key}': must be an integer.",
                    success=False,
                )
        if month is not None and not (1 <= month <= 12):
            return ToolResult(
                tool_name=self.tool_id,
                content="Invalid 'month': must be between 1 and 12.",
                success=False,
            )
        for key in ("plant", "department", "line", "metric"):
            value = params.get(key)
            if value is not None and not isinstance(value, str):
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Invalid '{key}': must be a string.",
                    success=False,
                )

        # 2. Translate to the Bridge's own request contract -- pure mapping.
        bridge_params: Dict[str, Any] = {}
        if year is not None or month is not None:
            period: Dict[str, Any] = {}
            if year is not None:
                period["year"] = year
            if month is not None:
                period["month"] = month
            bridge_params["period"] = period
        for key in ("plant", "department", "line", "metric"):
            if params.get(key) is not None:
                bridge_params[key] = params[key]

        url = f"{_bridge_base_url()}/api/ops-bridge"
        try:
            response = httpx.post(
                url,
                json={"capability": _CAPABILITY, "params": bridge_params},
                headers=_auth_headers(),
                timeout=_call_timeout_seconds(),
            )
            response.raise_for_status()
            envelope = response.json()
        except httpx.TimeoutException as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"OPS Bridge request timed out after {_call_timeout_seconds()}s: {exc}",
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"OPS Bridge returned HTTP {exc.response.status_code}.",
                success=False,
            )
        except httpx.RequestError as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not reach OPS Bridge at {url}: {exc}",
                success=False,
            )
        except ValueError as exc:  # response body was not valid JSON
            return ToolResult(
                tool_name=self.tool_id,
                content=f"OPS Bridge returned a non-JSON response: {exc}",
                success=False,
            )

        # 3. Return the Bridge's own envelope untouched -- no reinterpretation,
        # no recalculation, no invented values.
        status = envelope.get("status")
        return ToolResult(
            tool_name=self.tool_id,
            content=_summarize(envelope),
            success=status == "ok",
            metadata=envelope,
        )


__all__ = ["OpsBridgeProductionKpiTool"]
