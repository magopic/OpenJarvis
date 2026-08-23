"""FASE 4P.2 STEP 12 -- the scheduler-invoked, LLM-free monitor-check agent.

Registered as agent "monitor_check" so the EXISTING TaskScheduler (see
scheduler/scheduler.py -- audited in STEP 1, reused unmodified) can
invoke a monitor cycle on cadence without any new scheduler, background
thread, or poll loop of this phase's own. TaskScheduler._execute_task()
always calls ``system.ask(task.prompt, agent=task.agent, ...)``, which
constructs the agent via ``agent_cls(engine, model, **kwargs)`` -- this
class accepts and ignores engine/model (no LLM call happens anywhere in
this file); detection stays 100% deterministic, exactly as
ProactiveReasoningService already is.
"""

from __future__ import annotations

from typing import Any, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.registry import AgentRegistry
from openjarvis.monitoring.service import MONITOR_PROMPT_PREFIX, MonitorService


@AgentRegistry.register("monitor_check")
class MonitorCheckAgent(BaseAgent):
    """Runs one deterministic monitor cycle. No engine, no prompt, no
    tool-calling loop -- the scheduler's cron/interval polling is the
    only thing that decides *when* this runs; this class decides nothing
    about *whether* to check, it just does."""

    agent_id = "monitor_check"
    accepts_tools = False

    def __init__(self, engine: Any = None, model: Any = None, **kwargs: Any) -> None:
        # Deliberately does NOT call BaseAgent.__init__ -- no engine/model
        # is ever used. Accepted positionally so the system orchestrator's
        # generic `agent_cls(s.engine, s.model, **agent_kwargs)` construction
        # attempt succeeds on the first try instead of falling through its
        # TypeError-catching fallbacks.
        self._bus = kwargs.get("bus")
        self._service = kwargs.get("service") or MonitorService()

    def run(self, input: str, context: Optional[AgentContext] = None, **kwargs: Any) -> AgentResult:
        if not input.startswith(MONITOR_PROMPT_PREFIX):
            return AgentResult(
                content=f"Not a monitor-check prompt: {input!r}",
                tool_results=[],
                turns=0,
                metadata={"error": True},
            )
        monitor_id = input[len(MONITOR_PROMPT_PREFIX):].strip()
        try:
            run, notifications = self._service.run_cycle(monitor_id)
        except KeyError as exc:
            return AgentResult(
                content=f"Monitor not found: {exc}",
                tool_results=[],
                turns=0,
                metadata={"error": True},
            )

        if notifications:
            lines = [f"Monitor {monitor_id}: {len(notifications)} notification(s) this cycle:"]
            for n in notifications:
                title = (n.insight_snapshot or {}).get("title", n.fingerprint)
                lines.append(f"  - [{n.transition}] {title}")
            content = "\n".join(lines)
        else:
            content = f"Monitor {monitor_id}: no change this cycle ({run.status})."

        return AgentResult(
            content=content,
            tool_results=[],
            turns=1,
            metadata={
                "run_status": run.status,
                "evidence_collected": run.evidence_collected,
                "insights_generated": run.insights_generated,
                "notification_count": len(notifications),
                "errors": run.errors,
            },
        )


__all__ = ["MonitorCheckAgent"]
