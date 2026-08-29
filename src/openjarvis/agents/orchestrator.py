"""OrchestratorAgent — multi-turn agent with tool-calling loop.

Supports two modes:

- **function_calling** (default): Uses OpenAI-format tool definitions and
  parses ``tool_calls`` from the engine response.
- **structured**: Uses a THOUGHT/TOOL/INPUT/FINAL_ANSWER text format
  (like ReAct) with a canonical system prompt from the orchestrator
  prompt registry.  This is the format used by the SFT/GRPO training
  pipelines, making the Orchestrator a distinctive trainable agent type.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.agents.operational_evidence import (
    _DOCUMENT_KNOWLEDGE_TOOL_NAMES,
    _SECOND_BRAIN_TOOL_NAMES,
    build_evidence,
)
from openjarvis.agents.proactive_insight import (
    ProactiveReasoningService,
    render_available_tools_manifest,
    render_claim_boundary_notice,
    render_governed_proactive_block,
    render_tool_execution_integrity,
    should_activate_proactive_analysis,
)
from openjarvis.governed_actions.runtime_hook import (
    detect_and_apply_runtime_approval,
    render_governed_action_event,
)
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tools._stubs import BaseTool

# FASE 4M.5B: bounded dynamic re-routing. tool_router.py's own selection
# (select_relevant_tools) is reused unchanged -- this only widens how many
# of its already-scored candidates are included, and only across turns
# where the conversation is genuinely continuing (the model made a tool
# call and needs another turn), never by loading the full catalog. Capped
# so a long tool-calling conversation can't creep toward offering every
# registered tool.
_ROUTING_BASE_TOP_N = 5
_ROUTING_EXPANSION_STEP = 2
_ROUTING_MAX_EXPANSIONS = 2

# M3.2C: how many recently-successful tools stay offerable across turns of
# the same conversation. The router scores the CURRENT utterance only, which
# is sound for an opening question and wrong for a follow-up: "E rispetto
# all'anno precedente?" tokenizes to {rispetto, anno, precedente}, matches no
# OPS tool, and so withdrew the very tool the conversation was about -- the
# model then called it from memory, without a schema, and guessed the
# arguments (M3.2B). Remembering what actually worked restores continuity
# without loosening the narrowing the router exists to provide.
#
# Kept deliberately small: a conversation realistically touches one to three
# business domains, and this only ever re-offers tools already in the
# authorized set, so it cannot grow toward the full catalog.
_STICKY_TOOL_LIMIT = 8

# FASE 4O.6A: bounded evidence-coverage check. Second Brain and Document
# Knowledge tools are always-on (tool_router.py never scores/caps them the
# way it does ops_dynamic_*), so -- unlike OPS -- nothing previously
# checked whether the model actually attempted them at all before
# finalizing an answer. This is a structural, tool-name-based grouping
# (reusing operational_evidence.py's own frozensets, the same source of
# truth its evidence classification already uses), never a keyword/phrase/
# language/business-domain match on the question text. `document_list_sources`
# is included here (but not in operational_evidence.py's own
# _DOCUMENT_KNOWLEDGE_TOOL_NAMES, which is evidence-bearing-results only)
# because attempting it still counts as the model having checked the
# Document Knowledge family.
_COVERAGE_FAMILIES = {
    "historical experience (Second Brain)": _SECOND_BRAIN_TOOL_NAMES,
    "document evidence (Document Knowledge)": _DOCUMENT_KNOWLEDGE_TOOL_NAMES
    | frozenset({"document_list_sources"}),
}

# M1.4 -- Multi-Source Degradation Hardening. Documented live failure
# (docs/MAIA_MULTI_SOURCE_REASONING_V1.md, KNOWN LIMITATIONS): after a
# repeated-retry sequence LoopGuard eventually stopped, the model's next
# turn had no tool_calls but its `content` was "a malformed, un-parsed
# tool-call fragment" rather than a coherent answer -- surfaced verbatim
# to the user. Root cause is a known, already-handled-elsewhere class of
# engine/tool-parser mismatch: some backends emit a tool call as
# ``<tool_call>{...}</tool_call>`` text in `content` instead of a
# structured `tool_calls` entry (see the identical pattern already
# recovered in hybrid/toolorchestra.py's `_TOOL_CALL_TAG_RE` and
# native_openhands.py's `_strip_tool_call_text`/`_parse_action` -- this is
# the same recovery, applied where OrchestratorAgent's function_calling
# loop previously had none).
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _recover_leaked_tool_call(content: str, available_tool_names: set) -> Optional[dict]:
    """If the engine leaked a genuine tool call as ``<tool_call>{...}</tool_call>``
    text instead of a structured ``tool_calls`` entry, recover it as a real
    call so it flows through the EXACT SAME downstream path (including the
    LoopGuard check) as any normally-parsed tool call -- no bypass, no new
    execution path. Returns None (never invents a tool call) unless the tag
    is present, its JSON payload parses, and its ``name`` matches a tool
    genuinely available this turn."""
    if not content or "<tool_call>" not in content:
        return None
    m = _TOOL_CALL_TAG_RE.search(content)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    args = obj.get("arguments", {})
    if not isinstance(name, str) or name not in available_tool_names or not isinstance(args, dict):
        return None
    return {"id": "recovered_tag_call", "name": name, "arguments": json.dumps(args)}


def _looks_like_malformed_final_answer(content: str) -> bool:
    """True when `content` -- about to be accepted as the FINAL user-facing
    answer because there are no (real or recovered) tool_calls this turn --
    is not actually natural language: empty, a leftover unparsed
    ``<tool_call>`` tag (recovery above already tried and failed on it), or
    a bare tool-call-shaped JSON object dumped as prose. Deliberately
    narrow -- must never misclassify a genuine natural-language answer."""
    stripped = (content or "").strip()
    if not stripped:
        return True
    if "<tool_call>" in stripped:
        return True
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        if isinstance(obj, dict) and "name" in obj and ("arguments" in obj or "input" in obj):
            return True
    return False


def _safe_evidence_fallback_answer(evidence) -> str:
    """Deterministic, non-fabricated final answer for when the model could
    not produce a coherent one (malformed content, recovery exhausted).
    States only counts/limitations already present on `evidence` -- never
    a conclusion, number, or fact not already certified there."""
    if not evidence.has_any_evidence():
        return (
            "I was unable to gather any evidence for this question this turn "
            "(no operational facts, historical precedent, or document "
            "references were retrieved), so I can't give a substantiated "
            "answer. Please rephrase or narrow the question."
        )
    parts = []
    if evidence.facts:
        parts.append(f"{len(evidence.facts)} operational fact(s)")
    if evidence.knowledge:
        parts.append(f"{len(evidence.knowledge)} knowledge definition(s)")
    if evidence.historical_experience:
        parts.append(f"{len(evidence.historical_experience)} historical precedent(s)")
    if evidence.document_evidence:
        parts.append(f"{len(evidence.document_evidence)} document reference(s)")
    summary = "I gathered " + ", ".join(parts) + " for this question, "
    summary += (
        "but ran into a formatting issue producing a complete answer this "
        "turn. Please ask again, ideally narrowing the question, so I can "
        "give you a direct answer from what was found."
    )
    if evidence.limitations:
        summary += " Known gaps: " + "; ".join(evidence.limitations)
    return summary


@AgentRegistry.register("orchestrator")
class OrchestratorAgent(ToolUsingAgent):
    """Multi-turn agent that routes between tools and the LLM.

    Implements a tool-calling loop:
    1. Send messages with tool definitions to the engine.
    2. If the response contains tool_calls, execute them and loop.
    3. If no tool_calls, return the final answer.
    4. Stop after ``max_turns`` iterations.

    In **structured** mode the agent instead uses a
    ``THOUGHT: / TOOL: / INPUT: / FINAL_ANSWER:`` text protocol
    identical to the format used by the orchestrator SFT/GRPO
    training pipelines.
    """

    agent_id = "orchestrator"
    _default_temperature = 0.7
    _default_max_tokens = 1024
    _default_max_turns = 10

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: Optional[List[BaseTool]] = None,
        bus: Optional[EventBus] = None,
        max_turns: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        mode: str = "function_calling",
        system_prompt: Optional[str] = None,
        prompt_builder: Optional[Any] = None,
        parallel_tools: bool = True,
        interactive: bool = False,
        confirm_callback=None,
    ) -> None:
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns,
            temperature=temperature,
            max_tokens=max_tokens,
            interactive=interactive,
            confirm_callback=confirm_callback,
            prompt_builder=prompt_builder,
        )
        self._mode = mode
        self._system_prompt = system_prompt
        self._parallel_tools = parallel_tools
        # M3.2C: per-conversation memory of tools that actually worked,
        # recent-first. Instance state, not module state: the chat CLI builds
        # one agent per session, so this is scoped to a conversation and is
        # never shared across sessions or users. Reset in run() when a new
        # conversation starts (see _reset_conversation_state_if_new).
        self._recent_successful_tools: list[str] = []

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._reset_loop_guard_for_new_turn()
        self._reset_conversation_state_if_new(context)
        if self._mode == "structured":
            result = self._run_structured(input, context, **kwargs)
        else:
            result = self._run_function_calling(input, context, **kwargs)
        # M3.2C: recorded here, from the mode-independent AgentResult, so both
        # modes behave identically and the two tool-execution branches inside
        # _run_function_calling need no duplicated bookkeeping.
        self._remember_successful_tools(getattr(result, "tool_results", None) or [])
        return result

    # ------------------------------------------------------------------
    # M3.2C — conversational tool availability
    # ------------------------------------------------------------------

    def _reset_conversation_state_if_new(self, context: Optional[AgentContext]) -> None:
        """Drop remembered tools when a fresh conversation begins.

        "Fresh" means the caller handed us no prior exchange: either no
        context at all, or a conversation carrying no user/assistant turns.
        That is exactly what the chat CLI produces on its first turn and
        after ``/clear``, so isolation between conversations needs no
        cooperation from the caller.
        """
        prior: list = []
        if context is not None:
            conversation = getattr(context, "conversation", None)
            prior = [
                m
                for m in (getattr(conversation, "messages", None) or [])
                if getattr(m, "role", None) != Role.SYSTEM
            ]
        if not prior:
            self._recent_successful_tools = []

    def _remember_successful_tools(self, tool_results: list) -> None:
        """Record tools that ran successfully, recent-first and bounded.

        Three conditions must all hold, and together they are what keeps this
        from ever widening what the model may reach:

          1. the tool was actually invoked -- it is in this turn's results;
          2. its execution succeeded -- ToolResult.success is True. This is
             the generic tool-execution signal, deliberately not any
             OPS-specific notion: the OPS Bridge adapter already maps a
             non-ok envelope to success=False, so a `forbidden` or
             `invalid_request` answer is not remembered as a working tool;
          3. it is still a member of self._tools, the set authorized for
             this session.

        Condition 3 is the whole security argument, and it is why no special
        case is needed for the capabilities this phase forbids: an
        owner_only capability and the internal-only registry tool are both
        absent from self._tools by construction (M3.1A governance, and the
        router's fallback resolving from ToolRegistry rather than from the
        authorized list), so neither can be recorded, and a tool whose
        authorization is later withdrawn stops being offered immediately.
        """
        authorized = {t.spec.name for t in self._tools}
        for result in tool_results:
            name = getattr(result, "tool_name", None)
            if not name or name not in authorized:
                continue
            if getattr(result, "success", False) is not True:
                continue
            if name in self._recent_successful_tools:
                self._recent_successful_tools.remove(name)
            self._recent_successful_tools.insert(0, name)
        del self._recent_successful_tools[_STICKY_TOOL_LIMIT:]

    def _with_sticky_tools(self, routed: list) -> list:
        """Union the routed set with remembered tools, preserving order.

        The router keeps doing semantic narrowing untouched; this only adds
        back tools this conversation has already used successfully and that
        are still authorized. Deduplicated, so a tool the router selected on
        its own is not offered twice.
        """
        if not self._recent_successful_tools:
            return routed
        selected = list(routed)
        present = {t.spec.name for t in selected}
        by_name = {t.spec.name: t for t in self._tools}
        for name in self._recent_successful_tools:
            if name in present:
                continue
            tool = by_name.get(name)
            if tool is not None:
                selected.append(tool)
                present.add(name)
        return selected

    # ------------------------------------------------------------------
    # Structured mode (THOUGHT/TOOL/INPUT/FINAL_ANSWER)
    # ------------------------------------------------------------------

    def _run_structured(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build system prompt
        if self._system_prompt:
            sys_prompt = self._system_prompt
        else:
            from openjarvis.learning.intelligence.orchestrator.prompt_registry import (
                build_system_prompt,
            )

            sys_prompt = build_system_prompt(tools=self._tools)

        messages = self._build_messages(input, context, system_prompt=sys_prompt)

        all_tool_results: list[ToolResult] = []
        turns = 0

        for _turn in range(self._max_turns):
            turns += 1

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            result = self._generate(messages)
            content = result.get("content", "")

            parsed = self._parse_structured_response(content)

            # FINAL_ANSWER -> done
            if parsed["final_answer"]:
                self._emit_turn_end(turns=turns)
                return AgentResult(
                    content=parsed["final_answer"],
                    tool_results=all_tool_results,
                    turns=turns,
                )

            # TOOL -> execute
            if parsed["tool"]:
                messages.append(Message(role=Role.ASSISTANT, content=content))

                tool_call = ToolCall(
                    id=f"orch_{turns}",
                    name=parsed["tool"],
                    arguments=self._normalize_structured_tool_input(
                        parsed["tool"],
                        parsed["input"],
                    ),
                )
                tool_result = self._executor.execute(tool_call)
                all_tool_results.append(tool_result)

                if tool_result.success:
                    observation = f"Observation: {tool_result.content}"
                else:
                    observation = (
                        f"Observation: Tool '{tool_result.tool_name}' failed: "
                        f"{tool_result.content}"
                    )
                messages.append(Message(role=Role.USER, content=observation))
                continue

            # Neither -> treat content as final answer
            self._emit_turn_end(turns=turns)
            return AgentResult(
                content=content,
                tool_results=all_tool_results,
                turns=turns,
            )

        # Max turns exceeded
        return self._max_turns_result(all_tool_results, turns)

    def _normalize_structured_tool_input(
        self,
        tool_name: str,
        raw_input: str,
    ) -> str:
        """Map unambiguous structured text input to a string parameter."""
        if not raw_input:
            return "{}"

        try:
            parsed_input = json.loads(raw_input)
        except json.JSONDecodeError:
            invalid_json = True
            string_value = raw_input
        else:
            invalid_json = False
            if isinstance(parsed_input, dict):
                return raw_input
            # INPUT is a text protocol. A non-object JSON value such as 42,
            # true, null, or [1, 2] may still be the intended text for a tool's
            # string parameter. Quoted JSON strings are decoded to remove only
            # their surrounding quotes; other values retain their source text.
            string_value = parsed_input if isinstance(parsed_input, str) else raw_input

        tool_spec = None
        for candidate in reversed(self._tools):
            candidate_spec = candidate.spec
            if candidate_spec.name == tool_name:
                tool_spec = candidate_spec
                break
        if tool_spec is None:
            return raw_input

        parameters = tool_spec.parameters
        parameter_container_type = parameters.get("type")
        if parameter_container_type not in (None, "object"):
            return raw_input

        properties = parameters.get("properties", {})
        required = parameters.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return raw_input

        if len(required) == 1 and required[0] in properties:
            parameter_name = required[0]
        elif not required and len(properties) == 1:
            parameter_name = next(iter(properties))
        else:
            return raw_input

        parameter_schema = properties[parameter_name]
        if not isinstance(parameter_schema, dict):
            return raw_input
        parameter_type = parameter_schema.get("type")
        accepts_string = parameter_type == "string" or (
            isinstance(parameter_type, list) and "string" in parameter_type
        )
        if not accepts_string:
            return raw_input

        allow_object_text = (
            tool_spec.metadata.get("structured_allow_object_text") is True
        )
        starts_like_object = raw_input.lstrip("\ufeff \t\r\n").startswith("{")
        if invalid_json and starts_like_object and not allow_object_text:
            return raw_input

        return json.dumps({parameter_name: string_value})

    @staticmethod
    def _parse_structured_response(text: str) -> dict:
        """Parse THOUGHT/TOOL/INPUT/FINAL_ANSWER from model output."""
        result = {
            "thought": "",
            "tool": "",
            "input": "",
            "final_answer": "",
        }

        thought_match = re.search(
            r"THOUGHT:\s*(.+?)(?=\nTOOL:|\nFINAL[_ ]?ANSWER:|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            result["thought"] = thought_match.group(1).strip()

        final_match = re.search(
            r"FINAL[_ ]?ANSWER:\s*(.+)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if final_match:
            result["final_answer"] = final_match.group(1).strip()
            return result

        tool_match = re.search(r"TOOL:\s*(.+)", text, re.IGNORECASE)
        if tool_match:
            result["tool"] = tool_match.group(1).strip()

        input_match = re.search(
            r"INPUT:\s*(.+?)(?=\nTHOUGHT:|\nTOOL:|\nFINAL|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if input_match:
            result["input"] = input_match.group(1).strip()

        return result

    # ------------------------------------------------------------------
    # Function-calling mode (original behaviour)
    # ------------------------------------------------------------------

    def _run_function_calling(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build initial messages
        messages = self._build_messages(
            input,
            context,
            system_prompt=self._system_prompt,
        )

        # FASE 4P.1A STEP 5 / FASE 4P.1B: tool-claim integrity, present
        # from turn 1 -- before any tool has run. Live testing in FASE
        # 4P.1/4P.1A found the model can fabricate a fake tool-call
        # transcript (including a fully invented dataset and a false
        # trust_status claim) in its own prose on the very first turn, and
        # that the integrity block alone did not reliably prevent this
        # when asked about a capability that is not actually registered.
        # FASE 4P.1B adds two more turn-0 blocks: a closed-world list of
        # what tools genuinely exist this session (so the model can look
        # up availability instead of guessing), and an explicit narrative/
        # governed-claims boundary (STEP 2/4) -- still a context-based
        # deterrent, not a hard guarantee; see docs/
        # MAIA_PROACTIVE_INSIGHT_V1.md's KNOWN LIMITATIONS for the honest
        # statement of what this does and does not close.
        messages.append(Message(role=Role.USER, content=render_claim_boundary_notice()))
        messages.append(
            Message(
                role=Role.USER,
                content=render_available_tools_manifest([t.spec.name for t in self._tools]),
            )
        )
        messages.append(Message(role=Role.USER, content=render_tool_execution_integrity([])))

        # FASE 4P.3 STEP 10/13/19/20: runtime-only approval detection, on
        # the ORIGINAL user input, before any generation this turn. The
        # model is never in this call chain -- if the input is an exact
        # affirmative phrase AND exactly one governed action is pending
        # this principal's approval, the runtime itself approves and
        # executes it here, in pure Python, before the model ever
        # responds. Zero or multiple pending actions: nothing is
        # approved (ambiguity is surfaced structurally instead). See
        # governed_actions/runtime_hook.py.
        governed_event = detect_and_apply_runtime_approval(input)
        governed_event_block = render_governed_action_event(governed_event)
        if governed_event_block:
            messages.append(Message(role=Role.USER, content=governed_event_block))

        # Get OpenAI-format tool definitions. FASE 4L.2C: route the OPS
        # Bridge dynamic tool subset per-turn instead of serializing every
        # enabled tool's schema unconditionally -- see tool_router.py. Only
        # trims what is *shown to the model this turn*; the underlying
        # enabled-tools set (config.toml + auto-enable governance) is
        # untouched.
        routing_expansions_used = 0

        def _route_tools(top_n: int) -> list[dict]:
            if not self._tools:
                return []
            from openjarvis.agents.tool_router import select_relevant_tools

            routed_tools = select_relevant_tools(self._tools, input, top_n=top_n)
            # M3.2C: the router narrows on this utterance alone; add back what
            # this conversation has already used successfully, so an anaphoric
            # follow-up does not lose the tool it is talking about.
            routed_tools = self._with_sticky_tools(routed_tools)
            return [t.to_openai_function() for t in routed_tools]

        openai_tools = _route_tools(_ROUTING_BASE_TOP_N)

        all_tool_results: list[ToolResult] = []
        turns = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        made_tool_call_last_turn = False
        coverage_nudge_used = False
        malformed_answer_nudge_used = False
        # M2.4 Stage 2C: per-family, not conversation-global -- a single
        # shared bool let one family's zero-result recovery (e.g. Second
        # Brain) permanently consume the "fires once" budget, silently
        # locking out a completely unrelated family's OWN independent
        # zero-result situation arising later in the same conversation
        # (live-reproduced: Document Knowledge came back empty after
        # Second Brain had already been recovered, and never got its own
        # nudge). Each family label may appear in this set at most once;
        # still hard-bounded overall since there are only ever as many
        # families as _COVERAGE_FAMILIES has entries.
        zero_result_retried_families: set[str] = set()

        for _turn in range(self._max_turns):
            turns += 1

            # FASE 4M.5B: bounded dynamic re-routing. Only widen the offered
            # tool set on a turn where the conversation is genuinely
            # continuing (the previous turn made a tool call), and only up
            # to _ROUTING_MAX_EXPANSIONS times -- reuses select_relevant_tools
            # unchanged, never loads the full catalog.
            if (
                turns > 1
                and made_tool_call_last_turn
                and routing_expansions_used < _ROUTING_MAX_EXPANSIONS
                and self._tools
            ):
                routing_expansions_used += 1
                openai_tools = _route_tools(
                    _ROUTING_BASE_TOP_N + routing_expansions_used * _ROUTING_EXPANSION_STEP
                )

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            # Build generate kwargs
            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)

            # Accumulate token usage
            usage = result.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])

            # M1.4: recover a tool call the engine leaked as
            # ``<tool_call>{...}</tool_call>`` text in `content` instead of
            # a structured `tool_calls` entry (see _recover_leaked_tool_call
            # above). The recovered call flows through the identical
            # tool-execution path below -- including the LoopGuard check --
            # exactly like any normally-parsed call; never a bypass.
            if not raw_tool_calls and content:
                recovered = _recover_leaked_tool_call(
                    content, {t.spec.name for t in self._tools}
                )
                if recovered:
                    raw_tool_calls = [recovered]
                    content = ""

            # No tool calls -> check continuation, then final answer
            if not raw_tool_calls:
                # FASE 4O.6A: one bounded evidence-coverage nudge. If an
                # always-on evidence family (Second Brain / Document
                # Knowledge) is available this session but was never
                # attempted in ANY prior turn, give the model exactly one
                # chance to consider it before accepting a final answer --
                # never forced, never repeated (coverage_nudge_used latches
                # after this fires once, mirroring the existing bounded
                # _ROUTING_MAX_EXPANSIONS pattern above). A missing source
                # remains an acceptable outcome: the nudge text explicitly
                # permits finalizing without it and forbids guessing.
                #
                # M2.4 -- Cross-Source Claim Coverage: the family-level check
                # above is unchanged (still "was this family touched at all
                # this session"), but the nudge text below no longer offers
                # an unconditional escape hatch. A live-reproduced failure
                # showed a compound question (e.g. "who cleans X, and who
                # moves Y") get a document_search-only answer that silently
                # dropped the Second Brain half, because the old wording
                # ("if not, it is fine to finalize without it") let the
                # model decline an unattempted family without ever checking
                # whether the ORIGINAL question still had an unaddressed
                # part that family could cover. No new decomposition/
                # planner logic is added -- the model itself already has
                # the original question and its own draft answer in
                # context, and is asked to self-certify completeness
                # against that, rather than being handed a blanket
                # permission slip. Single-source questions are unaffected:
                # `unattempted_families` is computed exactly as before, so
                # a question that only ever needed one family still never
                # triggers this at all.
                if not coverage_nudge_used:
                    available_names = {t.spec.name for t in self._tools}
                    attempted_names = {tr.tool_name for tr in all_tool_results}
                    unattempted_families = [
                        label
                        for label, names in _COVERAGE_FAMILIES.items()
                        if (names & available_names) and not (names & attempted_names)
                    ]
                    if unattempted_families:
                        coverage_nudge_used = True
                        nudge = (
                            "[EVIDENCE COVERAGE CHECK] Before finalizing, note "
                            "that the following evidence sources are available "
                            "this session but have not been checked yet: "
                            + "; ".join(unattempted_families)
                            + f". Re-read the user's ORIGINAL question: {input!r}. "
                            "Does your answer so far genuinely address every "
                            "distinct part of it? If any distinct part remains "
                            "unanswered and one of the unattempted sources above "
                            "could plausibly cover it, check that source now "
                            "before finalizing. If your answer already covers "
                            "every part of the original question, it is fine to "
                            "finalize without checking further -- do not call a "
                            "source that is not relevant to any part of the "
                            "question, and do not guess at what it would say."
                        )
                        messages.append(Message(role=Role.USER, content=nudge))
                        continue

                # M2.4 Stage 2 -- Zero-Result Retrieval Recovery: Stage 1
                # above only tracks whether a family's tool was CALLED
                # (`attempted_names`), not whether it ever returned
                # evidence. Both second_brain_search and document_search
                # return success=True, metadata={"num_results": 0} on a
                # genuine zero-match -- indistinguishable from a real hit
                # at Stage 1's bookkeeping level. Live-reproduced: the
                # model tried Second Brain once with an over-specific
                # query, got zero results, and finalized declaring that
                # part of the question unavailable, even though the same
                # entry was independently retrievable with a broader
                # query in the same runtime state. Only evaluated once
                # Stage 1 finds nothing left unattempted (every family
                # has at least one call this turn). A tool failure, a
                # non-search tool (e.g. document_list_sources, which
                # reports `num_documents` not `num_results`), or any
                # result missing the `num_results` key is never counted
                # as "empty evidence" -- only a genuine, successful,
                # zero-count search does.
                #
                # M2.4 Stage 2C: bounded PER FAMILY
                # (`zero_result_retried_families`), not conversation-
                # global. A single shared bool let one family's recovery
                # (e.g. Second Brain) permanently consume the "fires
                # once" budget, silently locking out a completely
                # unrelated family's OWN independent zero-result
                # situation arising later (live-reproduced: Document
                # Knowledge came back empty only after Second Brain had
                # already been recovered, and never got its own nudge).
                # Each family can still receive at most one recovery
                # nudge ever, advisory, model judges actual relevance --
                # only the SCOPE of "at most once" changed, from global
                # to per-family; still hard-bounded overall (at most as
                # many extra turns as _COVERAGE_FAMILIES has entries).
                attempted_names_now = {tr.tool_name for tr in all_tool_results}
                empty_family_snippets = []
                families_to_mark_retried = []
                for label, names in _COVERAGE_FAMILIES.items():
                    if label in zero_result_retried_families:
                        continue
                    family_results = [tr for tr in all_tool_results if tr.tool_name in names]
                    counts = [
                        tr.metadata.get("num_results")
                        for tr in family_results
                        if tr.success
                        and isinstance(tr.metadata, dict)
                        and "num_results" in tr.metadata
                    ]
                    if not (counts and all(c == 0 for c in counts)):
                        continue
                    families_to_mark_retried.append(label)
                    # M2.4 Stage 2B: Second Brain has a second,
                    # purpose-built broadening tool
                    # (second_brain_find_related_experiences --
                    # deterministic EXACT/STRUCTURED/TERM(OR)/
                    # RELATIONSHIP widening, already certified in M2.2
                    # to retrieve the same entries under different
                    # wording) that second_brain_search's own strict,
                    # implicit-AND FTS match (frozen since FASE
                    # 4N.2A, never touched here) cannot. Live-
                    # reproduced: the model retried the empty family
                    # with the SAME strict tool (or didn't retry at
                    # all) rather than escalating to the tool
                    # actually meant for "find this under different
                    # wording." Name it explicitly only when it
                    # genuinely hasn't been tried yet -- if it HAS
                    # already run and still found nothing, there is
                    # no further tool to escalate to, so this falls
                    # back to the same generic wording used for
                    # Document Knowledge (which has no equivalent
                    # broadening tool at all).
                    if (
                        label == "historical experience (Second Brain)"
                        and "second_brain_find_related_experiences" not in attempted_names_now
                    ):
                        empty_family_snippets.append(
                            "Second Brain was searched via second_brain_search "
                            "but returned no results. You have not yet tried "
                            "second_brain_find_related_experiences, which is "
                            "specifically intended to find relevant historical "
                            "experiences under different wording (it broadens "
                            "automatically by domain/entity/term/relationship, "
                            "unlike second_brain_search's exact-word matching). "
                            "If a part of the user's original question is still "
                            "unresolved, try that tool now using the relevant "
                            "domain/entity/context from the question before "
                            "finalizing."
                        )
                    else:
                        empty_family_snippets.append(f"{label} returned no results.")
                if empty_family_snippets:
                    zero_result_retried_families.update(families_to_mark_retried)
                    nudge = (
                        "[EVIDENCE COVERAGE CHECK] "
                        + " ".join(empty_family_snippets)
                        + f" Re-read the user's ORIGINAL question: {input!r}. "
                        "If a distinct part of it is still unaddressed and a "
                        "broader or differently-worded search could plausibly "
                        "cover it, try that once now before finalizing. If you "
                        "have already tried a reasonably broad search, it is "
                        "fine to finalize now and state plainly that nothing "
                        "relevant was found -- do not guess."
                    )
                    messages.append(Message(role=Role.USER, content=nudge))
                    continue

                content = self._check_continuation(result, messages)
                content = self._strip_think_tags(content)

                # M1.4 -- Multi-Source Degradation Hardening: never surface
                # a malformed/empty/tool-call-shaped fragment as the final
                # answer (see docs/MAIA_MULTI_SOURCE_REASONING_V1.md's
                # documented live failure). One bounded recovery nudge
                # first (mirrors the coverage-nudge pattern above); if the
                # very next attempt is STILL malformed, finalize with a
                # deterministic, evidence-grounded limitation answer
                # instead -- never inventing content, never looping.
                if _looks_like_malformed_final_answer(content):
                    if not malformed_answer_nudge_used:
                        malformed_answer_nudge_used = True
                        messages.append(
                            Message(
                                role=Role.USER,
                                content=(
                                    "[RESPONSE FORMAT CHECK] Your previous "
                                    "response was not a valid tool call and "
                                    "was not a clear answer either. Please "
                                    "either make one proper tool call, or "
                                    "give a direct, plain-language final "
                                    "answer using the evidence already "
                                    "gathered this turn."
                                ),
                            )
                        )
                        continue
                    content = _safe_evidence_fallback_answer(
                        build_evidence(all_tool_results)
                    )

                self._emit_turn_end(turns=turns, content_length=len(content))
                return AgentResult(
                    content=content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata={
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                    },
                )

            # Build ToolCall objects from raw dicts
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]

            # Append assistant message with tool calls
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                )
            )

            # Execute each tool (with loop guard check) and append results
            if self._parallel_tools and len(tool_calls) > 1:
                # Parallel execution
                def _exec_tool(tc: ToolCall) -> tuple:
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            return tc, ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                    return tc, self._executor.execute(tc)

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(tool_calls),
                ) as pool:
                    futures = {pool.submit(_exec_tool, tc): tc for tc in tool_calls}
                    results_map: dict[int, tuple] = {}
                    for future in concurrent.futures.as_completed(futures):
                        tc_orig = futures[future]
                        results_map[id(tc_orig)] = future.result()

                # Append results in original order
                for tc in tool_calls:
                    _, tool_result = results_map[id(tc)]
                    all_tool_results.append(tool_result)
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )
            else:
                # Sequential execution
                for tc in tool_calls:
                    # Loop guard check before execution
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            tool_result = ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                            all_tool_results.append(tool_result)
                            messages.append(
                                Message(
                                    role=Role.TOOL,
                                    content=tool_result.content,
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                )
                            )
                            continue

                    tool_result = self._executor.execute(tc)
                    all_tool_results.append(tool_result)

                    # Append tool response message
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )

            made_tool_call_last_turn = True

            # FASE 4M.5B: OperationalEvidence -- a structural recap of every
            # FACT/KNOWLEDGE/LIMITATION gathered so far (from ToolResult.metadata,
            # i.e. the Bridge envelopes already returned this conversation),
            # with per-item trust_status/period_status and a domain-coverage
            # count. No new fact or judgment is computed here -- see
            # operational_evidence.py.
            #
            # FASE 4M.5E: sent as role="user", not "system". Several chat
            # templates (e.g. Qwen3.5's, observed live against llama-server)
            # hard-reject a system-role message anywhere but the first
            # position ("System message must be at the beginning"), which
            # would crash every orchestrated turn on those backends. The
            # note's own "[OPERATIONAL EVIDENCE COLLECTED THIS TURN]" header
            # already marks it as a structural/automated note rather than
            # something the human said, so no rewording was needed -- this
            # mirrors the identical fix already used for the same
            # constraint in engine/_openai_compat.py's finalization retry.
            evidence = build_evidence(all_tool_results)
            evidence_note = evidence.render_note()
            messages.append(Message(role=Role.USER, content=evidence_note))

            # FASE 4P.1A STEP 2/4: the deterministic proactive-insight
            # engine runs here, in the orchestrator, NOT because the model
            # chose to call maia_analyze_evidence_for_insights -- it runs
            # whenever activation is structurally warranted (see
            # should_activate_proactive_analysis: explicit proactive
            # intent in the ORIGINAL user request, an explicit call to the
            # analysis tool this turn, or a certified-alert source),
            # regardless of which evidence-gathering tools the model
            # actually used. This is the fix for FASE 4P.1's finding that
            # Claude reliably preferred its familiar ops_dynamic_*/
            # second_brain_*/document_* tools over ever calling the new
            # analysis tool itself -- the model no longer needs to choose
            # it for the governed result to reach the final answer.
            # Recomputed fresh every tool-executing turn (cheap,
            # deterministic, <1ms even at 1000 evidence items -- see
            # FASE 4P.1's STEP 17 measurement) so it always reflects the
            # full evidence gathered so far, never a stale snapshot.
            if should_activate_proactive_analysis(
                input,
                all_tool_results,
                evidence,
                tool_names_called_this_turn=[tc.name for tc in tool_calls],
            ):
                insights = ProactiveReasoningService().analyze(all_tool_results, evidence)
                logger.debug(
                    "FASE 4P.1A: proactive analysis activated, %d insight(s): %s",
                    len(insights),
                    [i.id for i in insights],
                )
                messages.append(
                    Message(role=Role.USER, content=render_governed_proactive_block(insights))
                )

            messages.append(
                Message(role=Role.USER, content=render_tool_execution_integrity(all_tool_results))
            )

        # Max turns exceeded
        final_content = self._strip_think_tags(content) if content else ""
        # M1.4: the same final-answer guard as the normal exit path -- a
        # malformed/leftover tool-call fragment must never reach the user
        # just because it happened to be the content of the last turn
        # before max_turns was hit either.
        if _looks_like_malformed_final_answer(final_content):
            final_content = _safe_evidence_fallback_answer(build_evidence(all_tool_results))
        self._emit_turn_end(turns=turns, max_turns_exceeded=True)
        return AgentResult(
            content=final_content,
            tool_results=all_tool_results,
            turns=turns,
            metadata={
                "max_turns_exceeded": True,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
        )


__all__ = ["OrchestratorAgent"]
