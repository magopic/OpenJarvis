"""M3.1A — owner-only capabilities must not auto-enable for chat.

M3.1 certified the authenticated production path and, in doing so, exposed a
client-side gap: ``_passes_governance`` screens trust_status,
requires_approval and category, but never looked at ``authorization``. OPS
ONE's registry declares ``authorization: "owner_only"`` on
``ops.actions.list``, and OPS ONE correctly refuses it to a service
principal (``trusted_service`` is deliberately not an owner) -- yet the
adapter still handed MAIA the tool, so the model could only ever spend a
turn to receive ``status: "forbidden"``.

The boundary was never broken: this is about not offering a key that
provably does not open the door.

The fix keeps two questions separate, and these tests pin that separation:

  A. DISCOVERY / REGISTRATION -- "what does the Bridge expose?" The local
     ToolRegistry keeps describing reality, ``ops.actions.list`` included,
     so a future runtime that forwards a real owner session needs no
     rediscovery.

  B. CHAT AUTO-ENABLE -- "what can MAIA actually use as the principal it is
     right now?" Anything the current principal cannot satisfy is withheld,
     and anything whose policy is unrecognized is withheld too.

No network: all HTTP is mocked with respx.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools import ops_bridge_generic as ob
from openjarvis.tools.ops_bridge_generic import (
    _is_chat_authorized,
    discover_and_register_ops_bridge_tools,
)

_BRIDGE_URL = "http://127.0.0.1:3000/api/ops-bridge"
_LOGGER_NAME = "openjarvis.tools.ops_bridge_generic"
_SECRET = "SECRET_TOKEN_MUST_NOT_APPEAR_M31A_4b7e"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPS_BRIDGE_SERVICE_TOKEN",
        "OPS_BRIDGE_SERVICE_ID",
        "OPS_BRIDGE_BASE_URL",
        "OPS_BRIDGE_DISCOVERY_TIMEOUT_SECONDS",
        "OPS_BRIDGE_CALL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def _capability(name: str, **overrides) -> dict:
    """A capability shaped exactly like OPS ONE's registry emits one.

    `authorization` is intentionally absent by default: OPS ONE declares it
    as an optional field (`authorization?: 'none' | 'owner_only'`) and
    `listCapabilityMetadata()` spreads the definition, so the key simply
    does not appear for the capabilities that never set it -- which is 7 of
    the 8 in production today.
    """
    cap = {
        "name": name,
        "description": f"test capability {name}",
        "category": "READ",
        "trust_status": "TRUSTED",
        "requires_approval": False,
        "input_schema": {"type": "object", "properties": {}},
    }
    cap.update(overrides)
    return cap


def _discover(*capabilities: dict) -> list:
    with respx.mock:
        respx.post(_BRIDGE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "data": {"capabilities": list(capabilities)},
                    "source": "ops_bridge_registry",
                    "period": None,
                    "reason": None,
                    "confidence_status": "not_evaluated",
                },
            )
        )
        return discover_and_register_ops_bridge_tools()


class TestAuthorizationPolicyHelper:
    """The policy in isolation: recognized-and-unrestricted passes,
    everything else fails closed."""

    @pytest.mark.parametrize(
        "cap",
        [
            _capability("ops.x.absent"),  # key not present at all
            _capability("ops.x.null", authorization=None),  # explicit JSON null
            _capability("ops.x.none", authorization="none"),  # explicit 'none'
        ],
        ids=["absent", "null", "none"],
    )
    def test_unrestricted_policies_are_chat_authorized(self, cap: dict) -> None:
        assert _is_chat_authorized(cap) is True

    @pytest.mark.parametrize(
        "value",
        [
            "owner_only",
            "admin_only",  # a policy OPS ONE might add later
            "OWNER_ONLY",  # case variance is not silently accepted
            " none",  # nor is stray whitespace
            "",
            123,
            {},
            [],
            True,
        ],
        ids=[
            "owner_only",
            "unknown-policy",
            "wrong-case",
            "padded",
            "empty",
            "int",
            "dict",
            "list",
            "bool",
        ],
    )
    def test_restricted_or_unrecognized_policies_are_withheld(self, value: object) -> None:
        assert _is_chat_authorized(_capability("ops.x.y", authorization=value)) is False


class TestRegistrationVsAutoEnableAreSeparate:
    """TEST 4/5: the point of M3.1A -- withheld from chat, still in the
    local registry."""

    def test_owner_only_is_registered_but_not_auto_enabled(self) -> None:
        registered = _discover(_capability("ops.actions.list", authorization="owner_only"))
        tool_id = "ops_dynamic_actions_list"

        # A. registration: the registry still describes what exists.
        assert tool_id in registered
        assert ToolRegistry.contains(tool_id)

        # B. auto-enable: MAIA is not handed a key that cannot open the door.
        assert tool_id not in ob.get_auto_enabled_ops_tool_ids()

    def test_unknown_authorization_is_registered_but_not_auto_enabled(self) -> None:
        registered = _discover(
            _capability("ops.future.thing", authorization="some_future_policy")
        )
        tool_id = "ops_dynamic_future_thing"
        assert tool_id in registered
        assert tool_id not in ob.get_auto_enabled_ops_tool_ids()


class TestUnrestrictedCapabilitiesStillReachChat:
    """TEST 1/2/3/7/8: no regression for everything that was already
    working."""

    @pytest.mark.parametrize(
        "cap,tool_id",
        [
            (_capability("ops.production.get_kpi"), "ops_dynamic_production_get_kpi"),
            (
                _capability("ops.logistics.get_kpi", authorization=None),
                "ops_dynamic_logistics_get_kpi",
            ),
            (
                _capability("ops.balance.get_kpi", authorization="none"),
                "ops_dynamic_balance_get_kpi",
            ),
            (
                _capability("ops.knowledge.get_kpi_definition", category="KNOWLEDGE"),
                "ops_dynamic_knowledge_get_kpi_definition",
            ),
        ],
        ids=["read-absent", "read-null", "read-none", "knowledge"],
    )
    def test_capability_is_registered_and_auto_enabled(self, cap: dict, tool_id: str) -> None:
        registered = _discover(cap)
        assert tool_id in registered
        assert tool_id in ob.get_auto_enabled_ops_tool_ids()


class TestInternalOnlyUnchanged:
    """TEST 6: the pre-existing internal-only rule is orthogonal to
    authorization and must keep working exactly as it did."""

    def test_list_capabilities_stays_registered_but_internal(self) -> None:
        registered = _discover(_capability("ops.registry.list_capabilities"))
        tool_id = "ops_dynamic_registry_list_capabilities"
        assert tool_id in registered
        assert tool_id not in ob.get_auto_enabled_ops_tool_ids()


class TestBaseGovernanceUnchanged:
    """TEST 9/10: M3.1A must not weaken -- or accidentally widen -- the
    trust/approval/category gate that came before it."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {"trust_status": "PARTIAL"},
            {"trust_status": "UNTRUSTED"},
            {"trust_status": None},
            {"requires_approval": True},
            {"category": "ACTION"},
            {"category": None},
        ],
        ids=["partial", "untrusted", "no-trust", "approval", "action", "no-category"],
    )
    def test_capability_failing_base_governance_is_not_registered_at_all(
        self, overrides: dict
    ) -> None:
        """Base governance is stricter than authorization: it blocks
        registration itself, not merely chat auto-enable."""
        registered = _discover(_capability("ops.blocked.thing", **overrides))
        assert registered == []
        assert "ops_dynamic_blocked_thing" not in ob.get_auto_enabled_ops_tool_ids()

    def test_owner_only_still_blocked_when_it_also_fails_base_governance(self) -> None:
        registered = _discover(
            _capability(
                "ops.blocked.owner", trust_status="PARTIAL", authorization="owner_only"
            )
        )
        assert registered == []


class TestProductionShapedRegistry:
    """The real production registry, end to end: 8 in, 7 registered
    (governance passes all), 6 auto-enabled for chat."""

    def test_full_registry_split(self) -> None:
        registered = _discover(
            _capability("ops.production.get_kpi"),
            _capability("ops.logistics.get_kpi"),
            _capability("ops.warehouse.get_status"),
            _capability("ops.waste.get_metrics"),
            _capability("ops.balance.get_kpi"),
            _capability("ops.knowledge.get_kpi_definition", category="KNOWLEDGE"),
            _capability("ops.actions.list", authorization="owner_only"),
            _capability("ops.registry.list_capabilities"),
        )
        auto = set(ob.get_auto_enabled_ops_tool_ids())

        assert len(registered) == 8
        assert auto == {
            "ops_dynamic_production_get_kpi",
            "ops_dynamic_logistics_get_kpi",
            "ops_dynamic_warehouse_get_status",
            "ops_dynamic_waste_get_metrics",
            "ops_dynamic_balance_get_kpi",
            "ops_dynamic_knowledge_get_kpi_definition",
        }
        assert len(auto) == 6
        # Withheld for two different, independent reasons.
        assert "ops_dynamic_actions_list" not in auto
        assert "ops_dynamic_registry_list_capabilities" not in auto


class TestM30ObservabilityNotRegressed:
    """TEST 11: the M3.0 failure reporting must survive M3.1A untouched."""

    def test_forbidden_envelope_still_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(
                return_value=httpx.Response(
                    200, json={"status": "forbidden", "reason": "no credential"}
                )
            )
            assert discover_and_register_ops_bridge_tools() == []
        assert "reason=envelope_not_ok" in caplog.text
        assert "status=forbidden" in caplog.text

    def test_network_failure_still_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
        with respx.mock:
            respx.post(_BRIDGE_URL).mock(side_effect=httpx.ConnectError("refused"))
            assert discover_and_register_ops_bridge_tools() == []
        assert "reason=network_error" in caplog.text


class TestNoSecretLeak:
    """TEST 12: the withheld-capability path must not become a disclosure
    channel any more than the failure paths did."""

    def test_token_absent_from_logs_when_capability_is_withheld(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("OPS_BRIDGE_SERVICE_TOKEN", _SECRET)
        caplog.set_level(logging.DEBUG, logger=_LOGGER_NAME)
        _discover(
            _capability("ops.actions.list", authorization="owner_only"),
            _capability("ops.production.get_kpi"),
        )
        assert _SECRET not in caplog.text
        assert "x-ops-service-token" not in caplog.text.lower()
        assert "authorization:" not in caplog.text.lower()
