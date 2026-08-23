"""FASE 4P.2 STEP 12 -- MonitorCheckAgent: registered, LLM-free, reachable
via the exact same agent_cls(engine, model, **kwargs) construction path
system/orchestrator.py's _run_agent uses for every agent."""

from __future__ import annotations

import tempfile

from openjarvis.agents.monitor_check_agent import MonitorCheckAgent
from openjarvis.core.registry import AgentRegistry
from openjarvis.monitoring.service import MONITOR_PROMPT_PREFIX, MonitorService
from openjarvis.monitoring.store import MonitorStore


def _svc() -> MonitorService:
    return MonitorService(store=MonitorStore(tempfile.mktemp(suffix=".db")))


class TestMonitorCheckAgentRegistration:
    def test_registered_under_monitor_check(self):
        import importlib

        import openjarvis.agents.monitor_check_agent as mod

        importlib.reload(mod)
        assert AgentRegistry.contains("monitor_check")

    def test_constructs_with_engine_and_model_positional(self):
        """Mirrors system/orchestrator.py's agent_cls(s.engine, s.model,
        **agent_kwargs) -- must not require a real engine."""
        agent = MonitorCheckAgent(None, "some-model", bus=None)
        assert agent.agent_id == "monitor_check"

    def test_constructs_with_zero_args_fallback(self):
        agent = MonitorCheckAgent()
        assert agent.agent_id == "monitor_check"


class TestMonitorCheckAgentRun:
    def test_runs_a_real_cycle_no_llm_involved(self, monkeypatch):
        import openjarvis.tools.proactive_insight_tools as pit
        from openjarvis.core.types import ToolResult

        def fake_call_ops(self, capability, params):
            return ToolResult(
                tool_name="x",
                success=True,
                content="ok",
                metadata={
                    "status": "ok",
                    "period": "2026-07",
                    "source": {},
                    "data": {"metric": "oee", "value": 60.0, "threshold": 70.0, "threshold_type": "min"},
                },
            )

        monkeypatch.setattr(pit.ProactiveAnalyzeTool, "_call_ops", fake_call_ops)
        svc = _svc()
        mon = svc.create_monitor("x", {"ops_capability": "ops.production.get_kpi"})

        agent = MonitorCheckAgent(service=svc)
        result = agent.run(f"{MONITOR_PROMPT_PREFIX}{mon.id}")
        assert result.metadata["notification_count"] == 1
        assert "NEW" in result.content

    def test_unknown_monitor_id_fails_honestly(self):
        agent = MonitorCheckAgent(service=_svc())
        result = agent.run(f"{MONITOR_PROMPT_PREFIX}nonexistent")
        assert result.metadata.get("error") is True

    def test_non_monitor_prompt_rejected(self):
        agent = MonitorCheckAgent(service=_svc())
        result = agent.run("some ordinary prompt")
        assert result.metadata.get("error") is True
