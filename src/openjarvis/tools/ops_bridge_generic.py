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
import logging
import math
import os
from typing import Any, Dict, List, Type

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:3000"
_LIST_CAPABILITY = "ops.registry.list_capabilities"
_TOOL_ID_PREFIX = "ops_dynamic_"

# M3.0 — OPS Bridge Production Readiness. Both budgets were tuned for a
# same-machine http://127.0.0.1:3000 Bridge and are wrong for a remote one:
#
#   - the call budget was 30.0s, *below* a measured Render Free cold start
#     (~33s), so the first call after an idle period timed out against a
#     Bridge that was in fact answering. 60.0 clears it with margin, and
#     costs nothing to installs without an OPS Bridge -- no capability is
#     registered for them, so no call is ever made.
#
#   - the discovery budget was 2.0s, uncomfortably close to a warm remote
#     HTTPS round trip (1.1-1.7s measured). It is raised, but deliberately
#     NOT past a cold start: discovery runs at import time (see
#     tools/__init__.py) and a larger default would stall startup for every
#     OpenJarvis install, the overwhelming majority of which have no OPS
#     Bridge at all. A deployment that must survive a cold start on the
#     first attempt raises OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS itself; a
#     discovery that does time out is now reported instead of silent.
_DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 10.0
_DEFAULT_CALL_TIMEOUT_SECONDS = 60.0
_DISCOVERY_TIMEOUT_ENV = "OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS"
_CALL_TIMEOUT_ENV = "OPS_BRIDGE_CALL_TIMEOUT_SECONDS"


def _env_float(name: str, default: float) -> float:
    """Read a positive, finite float from the environment, or fall back.

    Never raises: unset, blank, non-numeric, non-finite (nan/inf) or
    non-positive values all yield *default*. This sits on the import-time
    discovery path, so a typo in a deployment's environment must degrade to
    the default rather than break ``import openjarvis.tools`` outright.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError, AttributeError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


def _discovery_timeout_seconds() -> float:
    return _env_float(_DISCOVERY_TIMEOUT_ENV, _DEFAULT_DISCOVERY_TIMEOUT_SECONDS)


def _call_timeout_seconds() -> float:
    return _env_float(_CALL_TIMEOUT_ENV, _DEFAULT_CALL_TIMEOUT_SECONDS)

# M1.1 — OPS Bridge Authentication Boundary. Every OPS Bridge caller was
# previously anonymous (no headers sent at all); OPS ONE's own
# src/bridge/auth.ts already implements a dormant service-identity path
# (x-ops-service-token / x-ops-service-id -> trusted_service principal,
# gated by OPS_SERVICE_TOKEN being configured server-side) that this client
# now activates. Deliberately reuses that existing mechanism rather than
# inventing a new one. Read once per call (not cached) so a credential
# rotated via environment/process manager takes effect on the next call
# without a restart-triggered code path.
_SERVICE_TOKEN_HEADER = "x-ops-service-token"
_SERVICE_ID_HEADER = "x-ops-service-id"
_DEFAULT_SERVICE_ID = "openjarvis-maia"


def _auth_headers() -> Dict[str, str]:
    """Service-identity headers for an OPS Bridge call, or {} if unconfigured.

    Fails closed by omission, not by raising: if OPS_BRIDGE_SERVICE_TOKEN is
    unset, no auth headers are sent at all, and OPS ONE's own dispatcher
    (which rejects an unauthenticated/anonymous caller before resolving any
    capability) is what actually enforces the boundary -- this function's
    job is only to carry the credential when one is configured, never to
    duplicate that enforcement decision client-side.
    """
    token = os.environ.get("OPS_BRIDGE_SERVICE_TOKEN")
    if not token:
        return {}
    service_id = os.environ.get("OPS_BRIDGE_SERVICE_ID", _DEFAULT_SERVICE_ID)
    return {_SERVICE_TOKEN_HEADER: token, _SERVICE_ID_HEADER: service_id}

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

# M3.1A — authorization-aware chat auto-enable. The Bridge's own
# authorization policy (OPS ONE: `authorization?: 'none' | 'owner_only'`, an
# optional field, so it is simply absent on every capability that does not
# restrict itself) says which *principal* a capability needs. This runtime
# calls the Bridge as a trusted_service, and OPS ONE deliberately refuses
# owner_only to a service identity -- proving you are a legitimate caller is
# not proving you act for the owner. Offering the model a tool that provably
# cannot succeed only buys a wasted turn and a `forbidden` envelope.
#
# Only a recognized, explicitly-unrestricted policy opens the chat surface;
# anything unrecognized is withheld, so a policy OPS ONE adds later does not
# silently become chat-facing here before this side knows what it means.
_CHAT_UNRESTRICTED_AUTHORIZATION = "none"

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


def _is_chat_authorized(capability: Dict[str, Any]) -> bool:
    """Whether this capability is usable by the principal the runtime is.

    Deliberately a *separate* question from _passes_governance(), and applied
    at a different point:

      - _passes_governance() answers "may this capability exist here at all?"
        A failure there means the capability is not registered, full stop.

      - this answers "can the current principal actually use it?" A failure
        here withholds the tool from chat while leaving it registered, so the
        local registry keeps describing what the Bridge really exposes and a
        future runtime that forwards a real owner session needs no
        rediscovery or code change to pick it up.

    Fail-closed: absent or null means the capability declares no restriction
    (the shape 7 of the 8 production capabilities have today) and is
    unchanged from before M3.1A; an explicit 'none' means the same thing
    said out loud; anything else -- a policy this side does not recognize, a
    non-string, a case or whitespace variant -- is withheld rather than
    guessed at.
    """
    authorization = capability.get("authorization")
    if authorization is None:
        return True
    if not isinstance(authorization, str):
        return False
    return authorization == _CHAT_UNRESTRICTED_AUTHORIZATION


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
    # FASE 4M.5A: propagate two fields the Bridge already computes and
    # returns, which this function was previously dropping before they ever
    # reached the model. Neither is reinterpreted or reclassified here --
    # both are relayed exactly as the Bridge/capability set them:
    #   - period_status (REAL_DATA/REAL_ZERO/DATA_NOT_AVAILABLE/
    #     FUTURE_PERIOD/PARTIAL_PERIOD): a top-level envelope field, sibling
    #     to `data`, set by classifyPeriodStatus() on the OPS ONE side --
    #     this file must never duplicate that classification, only surface
    #     the value it's given.
    #   - reason: already included below for non-'ok' responses; also now
    #     included for 'ok' responses when present (e.g. a KNOWLEDGE
    #     capability response that is 'ok' but carries an explicit
    #     not-certified caveat, such as BUSINESS_LOGIC_IN_REVISION).
    # Neither addition touches `data` or interacts with
    # _compact_for_context's budget check below, which only measures `data`.
    status = envelope.get("status")
    period_status = envelope.get("period_status")
    period_status_part = f" period_status={period_status}" if period_status else ""

    if status != "ok":
        return f"status={status} period={envelope.get('period')}{period_status_part} reason={envelope.get('reason')}"

    reason = envelope.get("reason")
    reason_part = f" reason={reason}" if reason else ""
    prefix = (
        f"status=ok source={envelope.get('source')} period={envelope.get('period')}"
        f"{period_status_part} confidence_status={envelope.get('confidence_status')}{reason_part}"
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
        headers=_auth_headers(),
        timeout=_call_timeout_seconds(),
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
                # Name the subclass. httpx raises ConnectTimeout,
                # ReadTimeout, WriteTimeout and PoolTimeout from this one
                # base, and a bare `except httpx.TimeoutException` collapses
                # them into a single indistinguishable failure -- which left
                # a real transient timeout undiagnosable in M3.4B.2C, since
                # a stalled TCP/TLS handshake and a slow Bridge response need
                # opposite answers and looked identical here. The class name
                # is a fixed httpx identifier, so it carries no credential,
                # no URL and nothing from the request.
                return ToolResult(
                    tool_name=_tool_id,
                    content=(
                        f"OPS Bridge request timed out "
                        f"({type(exc).__name__}): {exc}"
                    ),
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


def _abandon_discovery(reason: str, detail: str) -> List[str]:
    """Fail closed on a discovery problem, and say why exactly once.

    Every caller of this helper registers nothing and returns [] -- the
    boundary is identical to the pre-M3.0 behavior. The only thing added is
    a single WARNING carrying a stable ``reason=<code>`` marker plus
    non-sensitive detail.

    Deliberately never receives (and so can never log) the request headers,
    the service token, or any Authorization value: callers pass an exception
    *type name*, an HTTP status code, or the Bridge's own server-authored
    `reason` string -- never the credential that was sent.
    """
    global _auto_enabled_tool_ids
    _auto_enabled_tool_ids = []
    logger.warning(
        "OPS Bridge capability discovery failed (reason=%s): %s. "
        "No OPS capabilities registered; MAIA will have no business tools "
        "this run. Bridge base URL: %s",
        reason,
        detail,
        _bridge_base_url(),
    )
    return []


def discover_and_register_ops_bridge_tools() -> List[str]:
    """Discover governance-passing capabilities and register a tool for each.

    A capability that fails the governance policy (_passes_governance) is not
    registered at all -- fail closed applies to ToolRegistry visibility, not
    just chat auto-enablement, so nothing dormant-but-unsafe sits around.

    Returns the list of tool_ids registered. Never raises -- any failure to
    reach the Bridge (not running, network error, malformed response) results
    in an empty list, matching the try/except-and-skip convention every other
    optional tool in tools/__init__.py already follows.

    M3.0: that empty list is unchanged, but it is no longer *silent*. Every
    distinct failure is reported once at WARNING with a stable
    ``reason=<code>`` marker, because in production the failures are not
    self-evident: OPS ONE answers an unauthenticated Bridge call with
    HTTP 200 and ``{"status": "forbidden"}``, so `raise_for_status()` passes
    and the result was previously indistinguishable from "no Bridge
    configured". Reporting the reason does not weaken the boundary -- fail
    closed is enforced identically on every branch below.

    Also refreshes the module's auto-enable list (see
    get_auto_enabled_ops_tool_ids) to exactly the tool_ids registered by this
    run that are meant to reach chat -- i.e. governance-passing capabilities
    minus the internal-only ones (see _INTERNAL_ONLY_CAPABILITIES).
    """
    global _auto_enabled_tool_ids

    try:
        envelope = _call_bridge_for_discovery()
    except httpx.TimeoutException as exc:
        return _abandon_discovery(
            "timeout",
            f"no response within {_discovery_timeout_seconds()}s ({type(exc).__name__})",
        )
    except httpx.HTTPStatusError as exc:
        return _abandon_discovery(
            "http_error", f"status_code={exc.response.status_code}"
        )
    except httpx.RequestError as exc:
        return _abandon_discovery("network_error", f"{type(exc).__name__}")
    except ValueError:
        # response.json() on a non-JSON body (HTML error page, proxy
        # interstitial, empty response).
        return _abandon_discovery("invalid_json", "response body was not valid JSON")
    except Exception as exc:  # never let discovery break `import openjarvis.tools`
        return _abandon_discovery("unexpected_error", f"{type(exc).__name__}")

    if not isinstance(envelope, dict):
        return _abandon_discovery(
            "malformed_envelope", f"expected a JSON object, got {type(envelope).__name__}"
        )

    status = envelope.get("status")
    if status != "ok":
        # Includes the production case this phase exists for: HTTP 200 with
        # status "forbidden" when no service credential is configured. The
        # Bridge's own `reason` is relayed verbatim -- it is server-authored
        # explanatory text, never a credential.
        return _abandon_discovery(
            "envelope_not_ok", f"status={status} bridge_reason={envelope.get('reason')}"
        )

    data = envelope.get("data")
    capabilities = data.get("capabilities") if isinstance(data, dict) else None
    if not isinstance(capabilities, list):
        return _abandon_discovery(
            "malformed_envelope",
            "envelope status was 'ok' but data.capabilities is missing or not a list",
        )

    registered: List[str] = []
    auto_enabled: List[str] = []
    withheld: List[tuple] = []
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
        # Registration above is unconditional for anything that passed
        # governance -- the local registry describes what the Bridge exposes.
        # Auto-enable below is narrower: it describes what MAIA can actually
        # use as the principal this runtime currently is (M3.1A).
        if name in _INTERNAL_ONLY_CAPABILITIES:
            continue
        if not _is_chat_authorized(capability):
            withheld.append((tool_id, capability.get("authorization")))
            continue
        auto_enabled.append(tool_id)

    if withheld:
        # Expected, not a failure -- but a tool silently missing from chat is
        # exactly the class of invisible behavior M3.0 set out to end, so say
        # it once at INFO. Only the tool id and the policy name are logged;
        # neither is a credential.
        logger.info(
            "OPS Bridge: %d capability(ies) registered but withheld from chat "
            "for this principal: %s. The Bridge enforces this server-side; "
            "they are kept in the local registry for future runtimes that can "
            "satisfy the policy.",
            len(withheld),
            ", ".join(f"{tool_id} (authorization={policy!r})" for tool_id, policy in withheld),
        )

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
        headers=_auth_headers(),
        timeout=_discovery_timeout_seconds(),
    )
    response.raise_for_status()
    return response.json()


__all__ = [
    "discover_and_register_ops_bridge_tools",
    "get_auto_enabled_ops_tool_ids",
]
