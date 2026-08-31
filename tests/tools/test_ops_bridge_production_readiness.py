"""M3.0 — OPS Bridge Production Readiness (OpenJarvis side).

Two production gaps, both observable only against a *remote* Bridge and
therefore invisible to the localhost-shaped tests that came before:

1. Timeouts were tuned for ``http://127.0.0.1:3000``. A 2.0s discovery
   budget is close to a warm remote HTTPS round trip and nowhere near a
   cold one; a 30.0s call budget is below a measured Render Free cold
   start (~33s). Both are now env-tunable, with a parser that refuses to
   let a malformed value break startup.

2. Every discovery failure collapsed into ``[]`` with no signal at all.
   The failure mode that matters most in production is the quietest one:
   the OPS Bridge answers ``HTTP 200`` with ``{"status": "forbidden"}``
   when the caller has no service credential, so ``raise_for_status()``
   passes and the envelope check silently yields nothing. That is
   indistinguishable from "no Bridge configured" unless it is reported.

Fail-closed is unchanged throughout: every case below still registers
zero capabilities. What changes is that the *reason* is now recoverable
from the logs.

Nothing here touches the network -- all HTTP is mocked with respx.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools import ops_bridge_generic
from openjarvis.tools.ops_bridge_generic import (
    _DEFAULT_CALL_TIMEOUT_SECONDS,
    _DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    _call_timeout_seconds,
    _discovery_timeout_seconds,
    _env_float,
    discover_and_register_ops_bridge_tools,
)

_BRIDGE_URL = "http://127.0.0.1:3000/api/ops-bridge"
_LOGGER_NAME = "openjarvis.tools.ops_bridge_generic"

# A sentinel that must never reach a log record or an error string.
_SECRET = "SECRET_TOKEN_MUST_NOT_APPEAR_IN_LOGS_9f3a1c"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # OPS_BRIDGE_BASE_URL is cleared too: every mock here is bound to the
    # default http://127.0.0.1:3000, so on a machine with a real Bridge
    # configured the client would resolve that host and miss all of them.
    for name in (
        "OPS_BRIDGE_SERVICE_TOKEN",
        "OPS_BRIDGE_SERVICE_ID",
        "OPS_BRIDGE_BASE_URL",
        "OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS",
        "OPS_BRIDGE_CALL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def warnings(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    return caplog


def _ok_discovery_payload() -> dict:
    return {
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
    }


class TestTimeoutDefaults:
    """TEST 1: defaults are the production-shaped ones, not the
    localhost-shaped ones they replaced (2.0 / 30.0)."""

    def test_discovery_default_is_bounded_but_remote_capable(self) -> None:
        assert _discovery_timeout_seconds() == _DEFAULT_DISCOVERY_TIMEOUT_SECONDS
        # Comfortably above a warm remote HTTPS round trip (~1.1-1.7s
        # measured), while still bounding import-time startup.
        assert _DEFAULT_DISCOVERY_TIMEOUT_SECONDS >= 5.0

    def test_call_default_exceeds_a_render_free_cold_start(self) -> None:
        assert _call_timeout_seconds() == _DEFAULT_CALL_TIMEOUT_SECONDS
        # A Render Free cold start was measured at ~33s; the previous
        # 30.0s default sat below it.
        assert _DEFAULT_CALL_TIMEOUT_SECONDS > 33.0


class TestTimeoutOverride:
    """TEST 2: both budgets are tunable per deployment."""

    def test_discovery_timeout_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS", "45")
        assert _discovery_timeout_seconds() == 45.0

    def test_call_timeout_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_BRIDGE_CALL_TIMEOUT_SECONDS", "90.5")
        assert _call_timeout_seconds() == 90.5

    def test_configured_call_timeout_reaches_the_http_layer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not just the helper in isolation -- the value a real call carries."""
        monkeypatch.setenv("OPS_BRIDGE_CALL_TIMEOUT_SECONDS", "77")
        seen: dict = {}

        def _fake_post(url, **kwargs):
            seen.update(kwargs)
            return httpx.Response(
                200, json=_ok_discovery_payload(), request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(ops_bridge_generic.httpx, "post", _fake_post)
        ops_bridge_generic._call_bridge("ops.production.get_kpi", {})
        assert seen["timeout"] == 77.0


class TestTimeoutParsingIsFailSafe:
    """TEST 3/4: a malformed or nonsensical value must never break
    startup -- it falls back, it does not raise."""

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "abc", "10s", "None", "1,5", "nan", "inf", "-inf"],
    )
    def test_non_numeric_falls_back(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS", raw)
        assert _discovery_timeout_seconds() == _DEFAULT_DISCOVERY_TIMEOUT_SECONDS

    @pytest.mark.parametrize("raw", ["0", "0.0", "-1", "-30.5"])
    def test_non_positive_falls_back(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("OPS_BRIDGE_CALL_TIMEOUT_SECONDS", raw)
        assert _call_timeout_seconds() == _DEFAULT_CALL_TIMEOUT_SECONDS

    def test_env_float_helper_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_BRIDGE_TEST_VALUE", "definitely not a float")
        assert _env_float("OPS_BRIDGE_TEST_VALUE", 3.0) == 3.0
        monkeypatch.delenv("OPS_BRIDGE_TEST_VALUE")
        assert _env_float("OPS_BRIDGE_TEST_VALUE", 3.0) == 3.0


class TestDiscoveryFailureIsObservable:
    """TEST 5-10: every distinct failure still registers nothing, but is
    now reported with a stable reason code instead of vanishing."""

    def _assert_failed_with(self, caplog: pytest.LogCaptureFixture, code: str) -> None:
        assert f"reason={code}" in caplog.text
        assert ops_bridge_generic.get_auto_enabled_ops_tool_ids() == []

    def test_timeout(self, warnings: pytest.LogCaptureFixture) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(side_effect=httpx.ConnectTimeout("too slow"))
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "timeout")

    def test_connection_error(self, warnings: pytest.LogCaptureFixture) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "network_error")

    def test_http_5xx(self, warnings: pytest.LogCaptureFixture) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(return_value=httpx.Response(503, text="down"))
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "http_error")
        assert "status_code=503" in warnings.text

    def test_non_json_body(self, warnings: pytest.LogCaptureFixture) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(200, text="<html>not json</html>")
            )
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "invalid_json")

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "ok", "data": {"capabilities": "not-a-list"}},
            {"status": "ok", "data": {}},
            {"status": "ok", "data": None},
            ["not", "an", "object"],
        ],
    )
    def test_malformed_envelope(
        self, warnings: pytest.LogCaptureFixture, payload: object
    ) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(return_value=httpx.Response(200, json=payload))
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "malformed_envelope")

    def test_forbidden_arrives_as_http_200_and_is_reported(
        self, warnings: pytest.LogCaptureFixture
    ) -> None:
        """The production failure this phase exists for.

        OPS ONE answers an unauthenticated Bridge call with HTTP 200 and
        status "forbidden" -- raise_for_status() passes, so before M3.0
        this produced an empty tool list and total silence.
        """
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "forbidden",
                        "data": None,
                        "source": None,
                        "period": None,
                        "reason": (
                            "This endpoint requires an authenticated caller "
                            "(a verified user session or a configured "
                            "trusted-service credential)."
                        ),
                        "confidence_status": "not_evaluated",
                    },
                )
            )
            assert discover_and_register_ops_bridge_tools() == []
        self._assert_failed_with(warnings, "envelope_not_ok")
        assert "status=forbidden" in warnings.text
        # The Bridge's own explanation is relayed, so an operator can tell
        # "no credential" apart from "Bridge not deployed".
        assert "trusted-service credential" in warnings.text


class TestSuccessPathUnchanged:
    """TEST 11: governance, registration and auto-enable behave exactly as
    before -- M3.0 must not widen what reaches the model."""

    def test_successful_discovery_still_registers_and_auto_enables(
        self, warnings: pytest.LogCaptureFixture
    ) -> None:
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(200, json=_ok_discovery_payload())
            )
            registered = discover_and_register_ops_bridge_tools()

        assert "ops_dynamic_production_get_kpi" in registered
        assert "ops_dynamic_production_get_kpi" in (
            ops_bridge_generic.get_auto_enabled_ops_tool_ids()
        )
        tool_cls = ToolRegistry.get("ops_dynamic_production_get_kpi")
        assert tool_cls().spec.required_capabilities == ["network:fetch"]
        # A healthy discovery is not noisy.
        assert warnings.text == ""

    def test_governance_still_fails_closed_on_untrusted_capability(
        self, warnings: pytest.LogCaptureFixture
    ) -> None:
        payload = _ok_discovery_payload()
        payload["data"]["capabilities"][0]["trust_status"] = "PARTIAL"
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(return_value=httpx.Response(200, json=payload))
            registered = discover_and_register_ops_bridge_tools()
        assert registered == []
        assert ops_bridge_generic.get_auto_enabled_ops_tool_ids() == []


class TestNoSecretLeak:
    """TEST 12: the new logging must not become a credential disclosure
    channel -- not on any failure branch."""

    @pytest.mark.parametrize(
        "mock_kwargs",
        [
            {"side_effect": httpx.ConnectError("refused")},
            {"side_effect": httpx.ConnectTimeout("too slow")},
            {"return_value": httpx.Response(503, text="down")},
            {"return_value": httpx.Response(200, text="<html>")},
            {"return_value": httpx.Response(200, json={"status": "forbidden", "reason": "no"})},
        ],
    )
    def test_token_never_reaches_the_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        warnings: pytest.LogCaptureFixture,
        mock_kwargs: dict,
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", _SECRET)
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(**mock_kwargs)
            assert discover_and_register_ops_bridge_tools() == []
        assert _SECRET not in warnings.text
        assert "x-ops-service-token" not in warnings.text.lower()
        assert "authorization" not in warnings.text.lower()

class TestTimeoutSubclassIsPreserved:
    """M3.3A.2 — a timeout must say *which* timeout it was.

    httpx raises ConnectTimeout, ReadTimeout, WriteTimeout and PoolTimeout
    from one base, and the tool caught the base and reported a single
    undifferentiated message. The distinction is the whole diagnosis: a
    stalled TCP/TLS handshake and a Bridge that accepted the connection but
    answered too slowly are different faults with opposite fixes, and in
    M3.4B.2C a real transient timeout could not be classified after the
    fact because the type had already been discarded here.
    """

    def _tool(self):
        """Register the capability, then hand back a live tool instance."""
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(200, json=_ok_discovery_payload())
            )
            discover_and_register_ops_bridge_tools()
        return ToolRegistry.get("ops_dynamic_production_get_kpi")()

    @pytest.mark.parametrize(
        "raised, expected, other",
        [
            (httpx.ConnectTimeout("handshake stalled"), "ConnectTimeout", "ReadTimeout"),
            (httpx.ReadTimeout("bridge answered too slowly"), "ReadTimeout", "ConnectTimeout"),
        ],
    )
    def test_the_subclass_name_reaches_the_tool_result(
        self, raised: httpx.TimeoutException, expected: str, other: str
    ) -> None:
        tool = self._tool()
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(side_effect=raised)
            result = tool.execute()

        assert result.success is False
        assert expected in result.content, result.content
        # Not merely "contains a name": it must name the right one.
        assert other not in result.content

    def test_write_and_pool_timeouts_are_distinguishable_too(self) -> None:
        """The two rarer subclasses share the branch and must not collapse."""
        seen = set()
        for raised in (
            httpx.WriteTimeout("send stalled"),
            httpx.PoolTimeout("no connection available"),
        ):
            tool = self._tool()
            with respx.mock:
                respx.post(_BRIDGE_URL).mock(side_effect=raised)
                result = tool.execute()
            assert result.success is False
            seen.add(type(raised).__name__ in result.content)
        assert seen == {True}

    def test_no_credential_reaches_the_timeout_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The added detail must not become a disclosure channel."""
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", _SECRET)
        tool = self._tool()
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                side_effect=httpx.ConnectTimeout("handshake stalled")
            )
            result = tool.execute()

        assert "ConnectTimeout" in result.content
        assert _SECRET not in result.content
        assert "Authorization" not in result.content
        assert "x-ops-service-token" not in result.content


class TestOtherFailureBranchesUnchanged:
    """M3.3A.2 touched one branch. The neighbouring ones must be untouched."""

    def _tool(self):
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(200, json=_ok_discovery_payload())
            )
            discover_and_register_ops_bridge_tools()
        return ToolRegistry.get("ops_dynamic_production_get_kpi")()

    def test_http_error_branch_is_unchanged(self) -> None:
        tool = self._tool()
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(return_value=httpx.Response(503, text="down"))
            result = tool.execute()
        assert result.success is False
        assert result.content == "OPS Bridge returned HTTP 503."

    def test_transport_error_branch_is_unchanged(self) -> None:
        tool = self._tool()
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            result = tool.execute()
        assert result.success is False
        assert result.content.startswith("Could not reach OPS Bridge:")
        # A ConnectError is not a timeout and must not be labelled as one.
        assert "timed out" not in result.content

    def test_the_success_path_still_returns_the_envelope(self) -> None:
        tool = self._tool()
        envelope = {
            "status": "ok",
            "data": {"oee": 88.61},
            "source": "test",
            "period": None,
            "reason": None,
            "confidence_status": "not_evaluated",
        }
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(return_value=httpx.Response(200, json=envelope))
            result = tool.execute()
        assert result.success is True
        assert "timed out" not in result.content
