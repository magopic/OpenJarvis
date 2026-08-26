"""FASE 4Q.6 — MAIA Runtime Governance Contract, TEST C.

``jarvis serve`` already applied ``capability_policy`` to its primary agent
before FASE 4Q.6 (unlike ``jarvis chat``, which this phase fixed). This is a
no-regression proof for that existing wiring, plus coverage for the NEW
``app.state.capability_policy`` wiring this phase added so managed-agent
routes (which never shared the primary agent's construction code) can reach
the same policy -- see ``server/agent_manager_routes.py::_stream_managed_agent``
and ``agents/executor.py`` for the consumers (TEST D/E).

Modeled directly on ``tests/cli/test_serve_persona.py``'s
``_capture_create_app`` pattern -- boots ``serve`` just far enough to capture
what it hands to ``create_app`` without starting a real uvicorn server.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from openjarvis.cli import cli

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

serve_mod = importlib.import_module("openjarvis.cli.serve")


def _fake_engine() -> MagicMock:
    engine = MagicMock()
    engine.list_models.return_value = ["test-model"]
    engine.health.return_value = True
    engine.name = "mock"
    engine.engine_id = "mock"
    return engine


def test_serve_wires_capability_policy_into_primary_agent_and_app_state(
    monkeypatch,
):
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.config import JarvisConfig
    from openjarvis.core.registry import AgentRegistry

    if not AgentRegistry.contains("orchestrator"):
        AgentRegistry.register_value("orchestrator", OrchestratorAgent)

    config = JarvisConfig()
    config.server.host = "127.0.0.1"
    config.server.port = 8124
    config.intelligence.default_model = "test-model"
    config.telemetry.enabled = False
    config.agent_manager.enabled = False
    config.sessions.enabled = False
    config.channel.enabled = False
    config.skills.enabled = False
    config.agent.context_from_memory = False

    engine = _fake_engine()
    monkeypatch.setattr(serve_mod, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(serve_mod, "get_engine", lambda *a, **k: ("mock", engine))
    monkeypatch.setattr(serve_mod, "discover_engines", lambda *a, **k: {})
    monkeypatch.setattr(serve_mod, "discover_models", lambda *a, **k: {})

    _policy_sentinel = object()
    sec = MagicMock()
    sec.engine = engine
    sec.capability_policy = _policy_sentinel
    sec.audit_logger = None
    monkeypatch.setattr("openjarvis.security.setup_security", lambda *a, **k: sec)

    captured: dict = {}

    def _capture_create_app(*args, **kwargs):
        captured["agent"] = kwargs.get("agent")
        captured["capability_policy"] = kwargs.get("capability_policy")
        return MagicMock(name="app")

    with (
        patch("openjarvis.server.app.create_app", side_effect=_capture_create_app),
        patch("uvicorn.run", lambda *a, **k: None),
    ):
        result = CliRunner().invoke(
            cli, ["serve", "--agent", "orchestrator"], catch_exceptions=False
        )

    assert result.exit_code == 0, result.output

    agent = captured.get("agent")
    assert agent is not None, (
        "serve did not construct an agent or never reached create_app; "
        f"output:\n{result.output}"
    )
    # Regression: the primary agent's ToolExecutor must still hold the
    # policy exactly as before FASE 4Q.6.
    assert agent._executor._capability_policy is _policy_sentinel

    # New in FASE 4Q.6: the same policy must reach app.state so
    # managed-agent routes (a structurally separate construction path,
    # see agent_manager_routes.py::_stream_managed_agent) can apply it too.
    assert captured.get("capability_policy") is _policy_sentinel
