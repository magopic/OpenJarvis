"""M1.1 — OPS Bridge Authentication Boundary (OpenJarvis side).

Every OPS Bridge call was previously anonymous (no auth headers sent at
all). This exercises the real, observable behavior: the exact headers a
real HTTP call carries, for both discovery and execution, across both
call sites (the generic dynamic-capability adapter and the legacy
hardcoded production-KPI tool) -- not just that a helper function returns
the right dict in isolation.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools import ops_bridge_generic
from openjarvis.tools.ops_bridge_generic import (
    _auth_headers,
    _call_bridge,
    _call_bridge_for_discovery,
    discover_and_register_ops_bridge_tools,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # OPS_BRIDGE_BASE_URL and the two timeout budgets are cleared alongside
    # the credentials so this file stays hermetic. Every mock below is bound
    # to the default http://127.0.0.1:3000; on a machine where an operator
    # has configured a real Bridge, the client would resolve that host
    # instead and every mock would miss. respx blocks the call rather than
    # letting it reach the network, but the tests would still fail for a
    # reason that has nothing to do with what they assert.
    for name in (
        "OPS_BRIDGE_SERVICE_TOKEN",
        "OPS_BRIDGE_SERVICE_ID",
        "OPS_BRIDGE_BASE_URL",
        "OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS",
        "OPS_BRIDGE_CALL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


class TestAuthHeadersHelper:
    """TEST 1/2: header presence when configured; fail-closed-by-omission
    when not."""

    def test_no_headers_when_token_unconfigured(self) -> None:
        assert _auth_headers() == {}

    def test_headers_present_when_token_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        headers = _auth_headers()
        assert headers["x-ops-service-token"] == "dummy-test-token-not-real"
        assert headers["x-ops-service-id"] == "openjarvis-maia"

    def test_service_id_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_ID", "openjarvis-maia-staging")
        assert _auth_headers()["x-ops-service-id"] == "openjarvis-maia-staging"


class TestExecutionCallCarriesAuth:
    """TEST 4: a real _call_bridge() HTTP call carries the configured
    credential."""

    def test_call_bridge_sends_configured_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        with respx.mock:
            route = respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {},
                        "source": "test",
                        "period": None,
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            _call_bridge("ops.production.get_kpi", {})
        sent = route.calls.last.request
        assert sent.headers["x-ops-service-token"] == "dummy-test-token-not-real"
        assert sent.headers["x-ops-service-id"] == "openjarvis-maia"

    def test_call_bridge_sends_no_credential_when_unconfigured(self) -> None:
        with respx.mock:
            route = respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {},
                        "source": "test",
                        "period": None,
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            _call_bridge("ops.production.get_kpi", {})
        sent = route.calls.last.request
        assert "x-ops-service-token" not in sent.headers


class TestDiscoveryCallCarriesAuth:
    """TEST 3: discovery goes through the exact same credential path as
    execution -- not a second, divergent implementation."""

    def test_discovery_call_sends_configured_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        with respx.mock:
            route = respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {"capabilities": []},
                        "source": "test",
                        "period": None,
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            _call_bridge_for_discovery()
        sent = route.calls.last.request
        assert sent.headers["x-ops-service-token"] == "dummy-test-token-not-real"

    def test_full_discovery_flow_carries_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TEST 6 (partial): the real discover_and_register_ops_bridge_tools()
        entry point -- not just the private HTTP helper -- carries auth."""
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        with respx.mock:
            route = respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {"capabilities": []},
                        "source": "test",
                        "period": None,
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            discover_and_register_ops_bridge_tools()
        sent = route.calls.last.request
        assert sent.headers["x-ops-service-token"] == "dummy-test-token-not-real"


class TestLegacyProductionKpiToolSharesBoundary:
    """TEST 6: the legacy hardcoded tool (kept for backward compatibility,
    per its own module docstring) must not duplicate its own header logic
    -- it must go through the same _auth_headers() helper as the generic
    adapter."""

    def test_production_kpi_tool_imports_shared_auth_helper(self) -> None:
        from openjarvis.tools import ops_bridge_production_kpi

        assert (
            ops_bridge_production_kpi._auth_headers
            is ops_bridge_generic._auth_headers
        )

    def test_production_kpi_tool_execute_sends_configured_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        from openjarvis.tools.ops_bridge_production_kpi import (
            OpsBridgeProductionKpiTool,
        )

        with respx.mock:
            route = respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {"metric": "oee", "value": 90.0, "unit": "%"},
                        "source": "test",
                        "period": "2026-08",
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            tool = OpsBridgeProductionKpiTool()
            result = tool.execute(metric="oee")
        assert result.success is True
        sent = route.calls.last.request
        assert sent.headers["x-ops-service-token"] == "dummy-test-token-not-real"


class TestCredentialNeverExposedInErrorsOrLogs:
    """TEST 5: a failed call's ToolResult.content must never contain the
    raw credential value."""

    def test_credential_absent_from_timeout_error_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OPS_BRIDGE_SERVICE_TOKEN", "SECRET_VALUE_MUST_NOT_LEAK_12345"
        )
        with respx.mock:
            respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                side_effect=httpx.TimeoutException("timed out")
            )
            with pytest.raises(httpx.TimeoutException) as exc_info:
                _call_bridge("ops.production.get_kpi", {})
        assert "SECRET_VALUE_MUST_NOT_LEAK_12345" not in str(exc_info.value)

    def test_credential_absent_from_tool_result_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OPS_BRIDGE_SERVICE_TOKEN", "SECRET_VALUE_MUST_NOT_LEAK_67890"
        )
        from openjarvis.tools.ops_bridge_production_kpi import (
            OpsBridgeProductionKpiTool,
        )

        with respx.mock:
            respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(403, json={"reason": "forbidden"})
            )
            tool = OpsBridgeProductionKpiTool()
            result = tool.execute(metric="oee")
        assert result.success is False
        assert "SECRET_VALUE_MUST_NOT_LEAK_67890" not in result.content


class TestFase4Q5And4Q6Unaffected:
    """TEST 7/8: tool-resolution parity (4Q.5) and capability_policy
    governance (4Q.6) must be unaffected by this change -- ops_dynamic_*
    tools still register and still carry required_capabilities=["network:fetch"],
    which is what 4Q.6's capability_policy gate keys on."""

    def test_dynamic_tool_still_registers_and_keeps_required_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", "dummy-test-token-not-real")
        with respx.mock:
            respx.post("http://127.0.0.1:3000/api/ops-bridge").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "data": {
                            "capabilities": [
                                {
                                    "name": "ops.production.get_kpi",
                                    "description": "test",
                                    "trust_status": "TRUSTED",
                                    "requires_approval": False,
                                    "category": "READ",
                                    "input_schema": {"type": "object", "properties": {}},
                                }
                            ]
                        },
                        "source": "test",
                        "period": None,
                        "reason": None,
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            registered = discover_and_register_ops_bridge_tools()
        assert "ops_dynamic_production_get_kpi" in registered
        tool_cls = ToolRegistry.get("ops_dynamic_production_get_kpi")
        instance = tool_cls()
        assert instance.spec.required_capabilities == ["network:fetch"]
