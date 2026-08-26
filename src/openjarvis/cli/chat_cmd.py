"""``jarvis chat`` — interactive multi-turn chat REPL."""

from __future__ import annotations

import concurrent.futures
import logging
import sys
from typing import List, Optional

import click
from rich.console import Console
from rich.markdown import Markdown

from openjarvis.cli._tool_names import resolve_tool_names
from openjarvis.core.config import load_config
from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role
from openjarvis.memory import publish_completed_exchange

logger = logging.getLogger(__name__)


def _read_input(prompt: str = "You> ") -> Optional[str]:
    """Read user input with graceful EOF handling."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


@click.command()
@click.option("-e", "--engine", "engine_key", default=None, help="Engine backend.")
@click.option("-m", "--model", "model_name", default=None, help="Model to use.")
@click.option("-a", "--agent", "agent_name", default=None, help="Agent type.")
@click.option("--tools", default=None, help="Comma-separated tool names.")
@click.option("--system", "system_prompt", default=None, help="Custom system prompt.")
@click.option(
    "--persona",
    "persona_name",
    default=None,
    help=(
        "Named persona dir under ~/.openjarvis/personas/<name>/ "
        "(overrides config). Pass 'none' to disable all persona files."
    ),
)
@click.option(
    "--turn-timeout",
    "turn_timeout",
    default=300.0,
    type=float,
    help=(
        "Wall-clock bound in seconds for ONE chat turn (default 300 = 5 "
        "minutes). If exceeded, the turn is abandoned with a clear error -- "
        "prior conversation history is unaffected and the session continues."
    ),
)
def chat(
    engine_key: str | None,
    model_name: str | None,
    agent_name: str | None,
    tools: str | None,
    system_prompt: str | None,
    persona_name: str | None,
    turn_timeout: float,
) -> None:
    """Start an interactive multi-turn chat session.

    Commands during chat:
      /quit, /exit  — end session
      /clear        — clear conversation history
      /model        — show current model
      /help         — show available commands
      /history      — show conversation history
    """
    console = Console(stderr=True)

    config = load_config()
    bus = EventBus(record_history=False)

    import dataclasses as _dc

    effective_mf = (
        _dc.replace(config.memory_files, persona_name=persona_name)
        if persona_name is not None
        else config.memory_files
    )

    # Resolve engine
    from openjarvis.engine import EngineConnectionError, get_engine
    from openjarvis.intelligence import register_builtin_models

    register_builtin_models()

    # FASE 4Q.1A: mirror ask.py's exact strict-pairing call (FASE 4P.3A) --
    # when engine_key AND model are BOTH given together, that pairing is
    # authoritative: it either succeeds or raises EngineConnectionError, and
    # never silently substitutes a different engine/model (e.g. a local
    # engine standing in for an explicitly requested cloud model). Before
    # this fix, chat never passed `model` into get_engine() at all, so this
    # guard could never activate here even though it was already frozen and
    # working for `jarvis ask`. Reuses the same resolver -- no new logic.
    effective_engine_key = engine_key or config.intelligence.preferred_engine or None
    selection_model = model_name or config.intelligence.default_model or None
    try:
        resolved = get_engine(config, effective_engine_key, model=selection_model)
    except EngineConnectionError as exc:
        console.print(f"[red bold]Engine error:[/red bold] {exc}")
        sys.exit(1)
    if resolved is None:
        console.print("[red]No inference engine available.[/red]")
        sys.exit(1)

    engine_name, engine = resolved

    # FASE 4Q.6: apply security guardrails (scanners + capability_policy),
    # mirroring jarvis ask (ask.py:895-898) and jarvis serve
    # (serve.py:236-238) exactly -- both reassign `engine` to the possibly
    # security-wrapped one, then reuse `sec.capability_policy` later when
    # building the agent's kwargs. jarvis chat never did either.
    from openjarvis.security import setup_security

    sec = setup_security(config, engine, bus)
    engine = sec.engine

    model = model_name or config.intelligence.default_model
    if not model:
        from openjarvis.engine import discover_engines, discover_models

        all_engines = discover_engines(config)
        all_models = discover_models(all_engines)
        engine_models = all_models.get(engine_name, [])
        if engine_models:
            model = engine_models[0]
        else:
            console.print("[red]No model available.[/red]")
            sys.exit(1)

    # Resolve agent (optional)
    agent = None
    agent_explicitly_set = agent_name is not None
    agent_key = agent_name or config.agent.default_agent
    if agent_key and agent_key != "none":
        try:
            import openjarvis.agents  # noqa: F401 — trigger registration
            from openjarvis.core.registry import AgentRegistry

            # MAIA-safe upgrade -- see ask.py for the identical rationale:
            # only overrides a non-tool-capable, non-explicit default when
            # this session actually has a governed tool available to call.
            # FASE 4Q.4A: this used to check OPS Bridge auto-enable only,
            # missing the Second Brain/Document Knowledge/Proactive
            # Insight/Monitoring/maia_manage/Governed Action families that
            # resolve_tool_names() already always unions in as "safe to
            # auto-enable" (see _tool_names.py) -- so a default session
            # could never actually reach maia_daily_attention_summary or
            # any other MAIA tool. Now matches resolve_tool_names()'s own
            # full list. No-op when nothing it resolves is actually
            # registered.
            if not agent_explicitly_set and AgentRegistry.contains(agent_key):
                _candidate_cls = AgentRegistry.get(agent_key)
                if (
                    not getattr(_candidate_cls, "accepts_tools", False)
                    and AgentRegistry.contains("orchestrator")
                ):
                    import openjarvis.tools  # noqa: F401 — trigger registration
                    from openjarvis.core.registry import ToolRegistry

                    _candidate_tool_names = resolve_tool_names(
                        tools,
                        getattr(config.tools, "enabled", None),
                        getattr(config.agent, "tools", None),
                    )
                    if any(
                        ToolRegistry.contains(tname)
                        for tname in _candidate_tool_names
                    ):
                        agent_key = "orchestrator"

            if AgentRegistry.contains(agent_key):
                agent_cls = AgentRegistry.get(agent_key)
                kwargs: dict = {"bus": bus}

                # FASE 4Q.6: jarvis chat never applied capability_policy,
                # unlike jarvis ask/serve -- an accidental gap. Passed as a
                # constructor kwarg only when the class accepts it (e.g.
                # OrchestratorAgent/NativeReActAgent don't declare it and
                # would raise TypeError); apply_capability_policy() below,
                # after construction, is the real guarantee regardless.
                from openjarvis.agents._stubs import constructor_accepts_kwarg

                if sec.capability_policy is not None and constructor_accepts_kwarg(
                    agent_cls, "capability_policy"
                ):
                    kwargs["capability_policy"] = sec.capability_policy

                if getattr(agent_cls, "accepts_tools", False):
                    tool_names_list = resolve_tool_names(
                        tools,
                        getattr(config.tools, "enabled", None),
                        getattr(config.agent, "tools", None),
                    )
                    if tool_names_list:
                        import openjarvis.tools  # noqa: F401 — trigger registration
                        from openjarvis.core.registry import ToolRegistry
                        from openjarvis.tools._stubs import BaseTool

                        tool_instances = []
                        for tname in tool_names_list:
                            if ToolRegistry.contains(tname):
                                tcls = ToolRegistry.get(tname)
                                if isinstance(tcls, type) and issubclass(
                                    tcls, BaseTool
                                ):
                                    tool_instances.append(tcls())
                                elif isinstance(tcls, BaseTool):
                                    tool_instances.append(tcls)
                        if tool_instances:
                            kwargs["tools"] = tool_instances
                    kwargs["max_turns"] = config.agent.max_turns

                    def _confirm(prompt: str) -> bool:
                        console.print(
                            f"[yellow]Confirm:[/yellow] {prompt} [y/N] ",
                            end="",
                        )
                        ans = input().strip().lower()
                        return ans in ("y", "yes")

                    kwargs["interactive"] = True
                    kwargs["confirm_callback"] = _confirm

                import inspect as _inspect

                if (
                    "prompt_builder"
                    in _inspect.signature(agent_cls.__init__).parameters
                ):
                    from openjarvis.prompt.builder import SystemPromptBuilder

                    kwargs["prompt_builder"] = SystemPromptBuilder(
                        agent_template=config.agent.default_system_prompt or "",
                        memory_files_config=effective_mf,
                        system_prompt_config=config.system_prompt,
                    )

                agent = agent_cls(engine, model, **kwargs)
                from openjarvis.agents._stubs import apply_capability_policy

                apply_capability_policy(agent, sec.capability_policy)
        except Exception as exc:
            console.print(f"[yellow]Agent '{agent_key}' failed: {exc}[/yellow]")

    # Print banner
    console.print(
        f"[green bold]OpenJarvis Chat[/green bold]\n"
        f"  Engine: [cyan]{engine_name}[/cyan]  Model: [cyan]{model}[/cyan]"
        f"  Agent: [cyan]{agent_key or 'direct'}[/cyan]\n"
        f"  Type /help for commands, /quit to exit.\n"
    )

    # Background-work status banner (disappears after first user message)
    from openjarvis.cli._bg_state import get_status
    from openjarvis.cli._chat_banner import render_startup_banner

    _banner = render_startup_banner(get_status())
    if _banner:
        console.print(f"[dim cyan]{_banner}[/dim cyan]")

    # Completion-notification dispatcher (fires once per task per session)
    from openjarvis.cli._chat_notifications import NotificationDispatcher

    _notifications = NotificationDispatcher(get_status())

    # Automatic long-term memory — extracts durable facts in the background.
    memory_service = None
    try:
        from openjarvis.memory import build_memory_service

        memory_service = build_memory_service(config, engine, model, event_bus=bus)
        if memory_service is not None:
            memory_service.start()
            console.print("[dim]  Memory: active[/dim]")
    except Exception as exc:
        console.print(f"[yellow]Memory service unavailable: {exc}[/yellow]")
        memory_service = None

    # The document backend and automatic fact store are separate persistence
    # mechanisms. Context injection combines both at read time so facts from
    # previous sessions are immediately available without a manual index step.
    memory_backend = None
    if config.agent.context_from_memory:
        from openjarvis.cli.ask import _get_memory_backend

        memory_backend = _get_memory_backend(config)

    # Conversation state
    if not system_prompt:
        from openjarvis.prompt.builder import SystemPromptBuilder

        builder = SystemPromptBuilder(
            agent_template=config.agent.default_system_prompt or "",
            memory_files_config=effective_mf,
            system_prompt_config=config.system_prompt,
        )
        system_prompt = builder.build()

    history: List[Message] = []
    if system_prompt:
        history.append(Message(role=Role.SYSTEM, content=system_prompt))

    # REPL loop
    while True:
        for note in _notifications.diff(get_status()):
            console.print(f"[dim cyan]{note}[/dim cyan]")

        user_input = _read_input()
        if user_input is None:
            console.print("\n[dim]Goodbye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        cmd = user_input.lower()
        if cmd in ("/quit", "/exit", "/q"):
            console.print("[dim]Goodbye![/dim]")
            break
        elif cmd == "/clear":
            history = []
            if system_prompt:
                history.append(Message(role=Role.SYSTEM, content=system_prompt))
            console.print("[dim]History cleared.[/dim]")
            continue
        elif cmd == "/model":
            console.print(
                f"Model: [cyan]{model}[/cyan]  Engine: [cyan]{engine_name}[/cyan]"
            )
            continue
        elif cmd == "/help":
            console.print(
                "[bold]Commands:[/bold]\n"
                "  /quit, /exit  — end session\n"
                "  /clear        — clear conversation\n"
                "  /model        — show model info\n"
                "  /history      — show conversation\n"
                "  /help         — this message"
            )
            continue
        elif cmd == "/history":
            if not history:
                console.print("[dim]No history yet.[/dim]")
            else:
                for msg in history:
                    role_str = msg.role if isinstance(msg.role, str) else msg.role.value
                    role = role_str.upper()
                    console.print(f"[bold]{role}:[/bold] {msg.content[:200]}")
            continue

        # Add user message
        history.append(Message(role=Role.USER, content=user_input))

        generation_history = history
        agent_context_message = None
        if config.agent.context_from_memory:
            try:
                from openjarvis.memory import load_configured_facts
                from openjarvis.tools.storage.context import (
                    ContextConfig,
                    inject_context,
                )

                if memory_service is not None and hasattr(memory_service, "list_facts"):
                    facts = memory_service.list_facts()
                else:
                    facts = load_configured_facts(config)
                ctx_cfg = ContextConfig(
                    top_k=config.memory.context_top_k,
                    min_score=config.memory.context_min_score,
                    max_context_tokens=config.memory.context_max_tokens,
                )
                context_messages = inject_context(
                    user_input,
                    [] if agent is not None else history,
                    memory_backend,
                    config=ctx_cfg,
                    facts=facts,
                )
                if agent is not None:
                    if context_messages:
                        agent_context_message = context_messages[0]
                else:
                    generation_history = context_messages
            except Exception:
                logger.debug("Failed to inject memory context", exc_info=True)

        # Generate response even when optional memory context is unavailable.
        def _run_turn() -> str:
            if agent is not None:
                from openjarvis.agents._stubs import AgentContext

                agent_context = AgentContext()
                if agent_context_message is not None:
                    agent_context.conversation.add(agent_context_message)
                for msg in history[:-1]:
                    if msg.role != Role.SYSTEM:
                        agent_context.conversation.add(msg)
                response = agent.run(user_input, context=agent_context)
                return response.content if hasattr(response, "content") else str(response)
            else:
                result = engine.generate(generation_history, model=model)
                return (
                    result.get("content", "")
                    if isinstance(result, dict)
                    else str(result)
                )

        # FASE 4Q.1A: bound ONE turn's wall-clock time. Neither the per-HTTP
        # timeout inside engine clients (up to 600s) nor the loop guard
        # (bounds repeated identical calls, not elapsed time) stop a slow or
        # misrouted engine from making one turn run for an unbounded amount
        # of wall-clock time (observed: ~1h40 against a silently-substituted
        # local engine). Reuses the same ThreadPoolExecutor primitive
        # orchestrator.py already uses for parallel tool execution, applied
        # here at the turn level instead -- not a new abstraction.
        #
        # A timed-out call is NOT forcibly killed (Python has no safe,
        # cross-platform way to do that to a thread blocked in a C-level
        # socket read -- especially not on Windows). The worker thread is
        # abandoned (`shutdown(wait=False)`) and left to finish on its own,
        # bounded by the engine client's own per-call timeout; the *chat
        # loop* is not blocked waiting for it, so the user regains control
        # immediately.
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_run_turn)
            try:
                content = future.result(timeout=turn_timeout)
            except concurrent.futures.TimeoutError:
                console.print(
                    f"\n[red]Turn timed out after {turn_timeout:.0f}s -- no "
                    "response was generated. Prior conversation history is "
                    "unaffected; you can try again or /quit.[/red]\n"
                )
                pool.shutdown(wait=False)
                continue
            pool.shutdown(wait=False)

            history.append(Message(role=Role.ASSISTANT, content=content))
            console.print()
            console.print(Markdown(content))
            console.print()

            publish_completed_exchange(
                bus,
                user_input,
                content,
                source="cli.chat",
            )
        except KeyboardInterrupt:
            console.print("\n[dim]Generation interrupted.[/dim]")
            pool.shutdown(wait=False)
        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]\n")
            pool.shutdown(wait=False)

    if memory_service is not None:
        memory_service.stop()


__all__ = ["chat"]
