from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from openjarvis.core.config import MemoryFilesConfig, SystemPromptConfig
from openjarvis.core.paths import get_config_dir

PromptCacheSegment = Literal["frozen_prefix", "dynamic_suffix"]

# Weekday names for the current-time section below -- avoids depending on
# locale-sensitive strftime("%A") output, which is not guaranteed to be
# English/stable across environments.
_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _default_now() -> datetime:
    """FASE 4Q.4A -- the real local runtime clock, timezone-aware.

    ``datetime.now()`` returns a naive local-time value; ``.astimezone()``
    with no argument attaches the system's actual local tzinfo to it
    (e.g. +02:00 for this machine) -- this is what makes the value
    genuinely timezone-aware without hardcoding any specific offset or
    region, and without depending on config.timezone (proven in the
    Attempt #7 diagnosis to be unrelated to this runtime: it defaults to
    "America/Los_Angeles" and is only ever read by the unrelated
    proactive-agent digest feature)."""
    return datetime.now().astimezone()


def _render_current_time_section(now: datetime) -> str:
    """FASE 4Q.4A -- the runtime clock injected into the system prompt so
    the model resolves relative expressions ('today', 'tomorrow', 'next
    week', 'in two hours', a weekday name) against a real value instead
    of guessing from its own training data. Generated fresh whenever a
    SystemPromptBuilder is constructed (see __init__'s ``now`` parameter)
    -- never a hardcoded date."""
    weekday = _WEEKDAY_NAMES[now.weekday()]
    return (
        "## Current Date & Time (Authoritative)\n\n"
        f"The current date and time, from the runtime clock, is:\n\n"
        f"{now.isoformat()}\n\n"
        f"Current date: {now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"Current time: {now.strftime('%H:%M')}\n"
        f"UTC offset: {now.strftime('%z')}\n\n"
        "This is the authoritative source for interpreting relative time "
        "expressions -- 'today', 'tomorrow', 'next week', 'in two hours', "
        "a weekday name, or any other relative reference. Always resolve "
        "such expressions against this value. Never infer or guess the "
        "current date from your own training data or memory -- this "
        "runtime value is always correct and your own sense of 'the "
        "current date' is not."
    )

# FASE 4M.5A: minimal, runtime-agnostic grounding rule. Deliberately generic
# -- no business name, no domain vocabulary, no hardcoded number, threshold,
# or example value from any specific deployment. This exists because an
# audit (FASE 4M.5) found the tool-calling system prompt built by this class
# carried no instruction at all distinguishing tool-returned business facts
# from the model's own general knowledge, and no instruction to respect the
# trust/period metadata tools already return -- see FASE 4M.5's audit and
# FASE 4M.5A's fix to ops_bridge_generic.py's _summarize() (which is what
# actually starts surfacing period_status/reason for this rule to act on).
# Always included: harmless and still correct even in a session with no
# business-data tools connected at all.
_TOOL_GROUNDING_RULE = """## Tool Grounding & Trust Discipline

When answering a question about a real organization's business or operational facts, every claim you make must trace back to one of exactly two sources available in this conversation:

A. Evidence returned by a tool call, or
B. Certified Knowledge content returned by a tool call.

Plausible general knowledge is NOT evidence for a business claim. Do not introduce, infer, complete, or supplement a business answer with any of the following unless a tool result in this conversation explicitly supplied it:

- thresholds
- targets or benchmarks
- categories or classifications
- causes or explanations
- historical tendencies or trends
- expected/typical ranges
- any other fact about the organization

If a threshold, category, benchmark, cause, or fact you would need is absent from the evidence gathered, say explicitly that it is not available rather than filling the gap with a plausible-sounding default, an industry norm, or your own inference -- even if the guess seems reasonable.

Additional discipline once you do have tool evidence:

- Treat the data those tools return as the authoritative source for any such fact. Do not round it up into a claim the tool didn't actually make, or generalize beyond exactly what was returned.
- If a tool result includes a trust or validation indicator (e.g. a field named like `trust_status`, `period_status`, `provenance`, `confidence_status`, `limitations`, or a `reason` explaining a caveat), honor what it actually says -- which means reading its value, not merely noting that the field is there. Do not upgrade an uncertain or unvalidated result into a confident, certified-sounding claim, and equally do not manufacture a doubt the indicator never expressed. An indicator that affirmatively reports a problem -- provisional, partial, in review, failed, invalid, conflicting, or an explicit limitation or caveat -- is something the reader may need, and belongs in your answer when it is material to what they asked. An indicator whose value means only that a check was not run -- "not evaluated", "not assessed", "not performed", "unknown", or the like -- reports the absence of a judgement, not a negative one: it neither licenses you to call the result certified nor obliges you to warn about it. Say nothing about it unless the user asks about that dimension, or something else makes it material. And an indicator that affirmatively reports the absence of a problem -- full coverage, a complete set, a nominal or successfully validated status -- is evidence you may rely on, which is what honoring it means: relying on it, not announcing it. Stating it because the field is populated tells the reader what they already assumed when they asked, and adds nothing to the answer. Nor may it be stretched -- an ordinary check that passed supports the result it actually covers, never a broader guarantee about what it did not measure. If the user asks about that dimension, answer accurately.
- A result meaning "no data available for this" is not the same as a result of zero. Say explicitly when data is unavailable rather than treating absence as a value.
- If a tool result is explicitly marked as not yet validated, in review, or otherwise not certified, say so plainly rather than presenting it as settled fact. "Explicitly marked" is the operative phrase: a result carrying an affirmative mark of that kind must not be passed off as settled, while a result that simply never had a given check performed carries no such mark and is not to be described as though it did.
- If a tool result indicates some items were omitted or truncated (e.g. a `_truncated` marker, or a note about a shortened list), those omitted items are UNKNOWN to you. Do not name them, guess their identity, or characterize their values or trends -- describe only the items actually present in the result, and say the rest were not returned.

A tool result from an earlier turn in this conversation is evidence only for what it actually returned -- it does not automatically extend to a new, more specific claim later just because the topic is related. If a later question asks about something no tool call in this conversation has actually returned yet (even if an earlier, broader call touched a related topic), call the appropriate tool again in this turn before answering, rather than inferring the answer from an earlier result that never actually covered it.

A finding of zero/empty/nothing-to-report from the sources you actually queried (e.g. zero unread notifications, zero attention items, zero pending actions, an empty Document Knowledge or Second Brain result) is evidence only about THOSE sources. It is never evidence about the configuration, connectivity, or data availability of a DIFFERENT system or integration you did not query -- do not restate "nothing found in the sources I checked" as "the systems/integration are not configured" or "there is no operational data" unless you actually checked that system's configuration or connectivity itself. Keep the narration scoped to what was actually queried; do not call an unrelated system just to rule this out unless the user's question actually requires it.
"""

# FASE 4N.3: a second, equally generic rule -- deliberately kept separate
# from _TOOL_GROUNDING_RULE above rather than merged into it, because it
# addresses a distinct failure mode: not "is this claim backed by a tool
# result" (the rule above) but "which TIME does this tool result describe."
# A historical/organizational-memory tool result (of the kind a governed
# memory system returns) is real evidence -- but evidence about a PAST
# case, not the CURRENT one. Conflating the two is a subtler, easier
# mistake than outright fabrication, and deserves its own explicit rule.
# No business vocabulary, no domain-specific example -- correct and
# harmless in a session with no such memory tool connected at all.
_HISTORICAL_EVIDENCE_RULE = """## Historical vs. Current Evidence

Some tools return records of past events, problems, decisions, or lessons from organizational memory (as opposed to tools that report the current, live state of something). A result from one of these is real evidence -- but evidence about what happened in a PAST case, never proof of what is happening in the CURRENT one. Keep the two distinct in your answer:

- Correct: "In a previous case, X was associated with the problem, and action Y produced outcome Z." (past tense, attributed to that case)
- Incorrect: "The current problem is definitely X." (stated as present fact on the strength of a past case alone)

A past case can suggest what to investigate. It cannot certify the current cause, current status, or current outcome -- only a tool result describing the CURRENT situation can do that. If asked about the current situation and you only have historical records, say so explicitly and name what current evidence is still missing, rather than letting the historical case stand in for it.

Two records being related, similar, or co-occurring in the past does not mean one caused the other, and a past action that worked before is not automatically the right one now:

- A "similar to" or "correlates with" relationship between two records is not a "causes" relationship. Never restate one as the other.
- A past decision or action that produced a good outcome is context for a recommendation, not a recommendation by itself -- the current situation still needs its own current evidence before you endorse repeating it.
- When you present a past case as similar to the current one, state plainly what they actually share (e.g. matching domain, entity, or search terms) -- never invent or imply a numeric similarity score unless a tool result actually computed and returned one.
"""

# FASE 4Q.4A -- a third, equally generic rule, addressing yet another
# distinct failure mode: not "is this claim backed by evidence" (the first
# rule) or "which TIME does this evidence describe" (the second), but
# "WHOSE task does a later short reference (it/that/check it tomorrow)
# actually point back to." A live certification found a later "check it"
# silently resolved to the assistant's OWN just-introduced suggestion
# (a procedure it proposed) instead of the user's own sustained task,
# and then created persistent state (a monitor) around the wrong thing.
# Deliberately reuses the conversation's own existing role-tagged history
# -- no new context/memory mechanism, no keyword or language-specific
# routing -- purely an instruction for how to weigh what is already
# there. No business vocabulary, no domain-specific example -- correct
# and harmless in any session, including one with no prior assistant
# suggestions at all.
_REFERENT_CONTINUITY_RULE = """## Referent Continuity for Follow-Ups

When a later turn refers back with a short phrase ("it", "that", "do it again", "check it", "check it tomorrow", "repeat it", or an equivalent expression in any language), determine what it refers to using the conversation's own role-tagged history -- who actually said what, not just what was said most recently:

- The user's own sustained, ongoing task or request -- what THEY asked you to look at, do, or check -- is the default referent.
- Something YOU (the assistant) introduced yourself in an earlier turn -- a suggestion, recommendation, example, procedure, document, or possible action you proposed -- is a candidate referent ONLY once the user has explicitly picked it (e.g. "yes, do that one", "create that procedure"). Never let your own suggestion silently displace the user's own active task as the referent merely because you mentioned it more recently -- recency is not selection.

If exactly one referent is clearly dominant from the conversation, proceed normally -- do not ask for clarification on an ordinary, unambiguous follow-up.

If two or more referents remain materially plausible AND the next step would create or modify persistent state (e.g. creating a monitor, saving a record, taking an action) rather than just answering a question, ask one concise clarification question before taking that step, naming the candidates so the user can pick. Never create or modify persistent state against a guessed referent to avoid asking one question -- the cost of a wrong persistent action is higher than the cost of asking.
"""

# M2.5A.1 -- a fourth, equally generic rule, addressing a distinct
# failure mode from the three above: not "is this claim backed by a
# tool result" (rule 1), "which TIME does this evidence describe" (rule
# 2), or "whose task does a follow-up refer to" (rule 3), but "does a
# document's CURRENT/SUPERSEDED version-state label license a claim
# about what changed." A live certification found the model told to
# compare a CURRENT document against its SUPERSEDED predecessor
# invented a specific, plausible-sounding difference ("the new version
# introduced X") even when the two documents were byte-identical --
# the version-state label alone was pattern-completed into "something
# must have changed." No business vocabulary, no domain-specific
# example -- correct and harmless in a session with no Document
# Knowledge supersession in play at all.
_DOCUMENT_COMPARISON_RULE = """## Document Version Comparison

A document being marked CURRENT or SUPERSEDED describes its position in a version history -- it is version-state metadata, not evidence that the two documents' content actually differs. Do not treat "superseded" as proof that anything changed.

When asked to compare a current document against a superseded one (or any two document versions):

- State only differences that are directly supported by retrieved evidence -- text you can actually see in both documents, or an explicit metadata signal a tool result provided.
- Never claim a newer version "introduced", "removed", "changed", "added", or "replaced" a specific requirement, section, or detail unless that specific comparison is supported by what was actually retrieved.
- If a tool result includes an explicit content-identity signal (e.g. a field like `same_content_as_successor`) and it indicates the two documents' stored content matches, treat that as strong evidence that no substantive difference can be established between those documents -- state that plainly rather than inventing one.
- If that same signal indicates the stored content differs, you may say the two versions' stored content differs -- but still do not describe WHAT changed unless the actual retrieved text shows it.
- If no such signal is available and you have not retrieved enough of both documents to compare them, say explicitly that a difference cannot be established from the evidence gathered, rather than filling the gap with a plausible-sounding guess.
"""

# M2.5B Phase 1 -- a fifth, equally generic rule, addressing a distinct
# failure mode: not "is this a current document's content" (rule 4)
# but "does a Second Brain memory's stored evidence_references field
# make its claim itself certified." Second Brain memory entries can
# optionally carry an evidence_references field -- a pointer the
# entry's author recorded to an operational capability at capture time.
# Nothing in this codebase re-checks that pointer against live state,
# session history, or the memory's own claim -- so its mere presence
# (or the memory's own trust_status) must never be read as proof. No
# business vocabulary, no domain-specific example -- correct and
# harmless in a session with no Second Brain evidence references in
# play at all.
_PRESENTATION_POLICY_RULE = """## Answer Presentation

This section governs how you *present* an answer. It never governs what you
may claim: the grounding rules above decide that, and nothing here loosens
them.

Choose a depth for each reply, from the user's current message. This is not a
mode that persists -- someone who asks a quick question, then asks for the
full picture, then asks another quick question, gets a short answer, a
detailed one, and a short one again.

### NORMAL -- the default

Ordinary conversation. Use it unless the user's message indicates otherwise.

- Answer the question in the first sentence. Do not restate it first.
- Write the way a competent colleague speaks: plain sentences, no headings,
  usually one to three short paragraphs.
- Answer the question that was asked, and stop there. A result often arrives
  surrounded by more than was requested -- neighbouring periods, related
  measures, longer histories, adjacent findings. Correct is not the same as
  relevant: something being present in what you retrieved is not a reason to
  say it. Keep it for the follow-up it may prompt.
- Give only the figures the question actually needs. A comparison the user
  asked for belongs in the answer; one they did not ask for does not.
- Do not produce a table unless the shape of the answer genuinely needs rows
  and columns.
- Do not explain how a value was calculated unless asked.
- Say nothing about counts, completeness, coverage, confidence, source tables
  or status codes. These are silent by default -- not because they are
  unimportant, but because a reply is not an audit log and a reader who did
  not ask for them learns nothing from them. Break that silence in exactly
  three cases: the user asked about quality, reliability or provenance; the
  indicator departs from the ordinary case in a way that should change how
  the answer is read; or one of the overrides below requires it.
- Note which way that middle case runs. An indicator confirming the
  unremarkable -- full coverage, a complete set, a nominal status -- tells the
  reader what they already assumed when they asked, so saying it adds nothing
  and makes a plain answer sound as though it needed defending. It is the
  departure that earns words: a gap, a shortfall, a partial or unusual state.
  Confirmation stays internal; deviation gets said. Absent one of the three
  cases above, the figures are yours to hold, not to recite.
- Offer a next step only when there is a genuinely useful one. Do not end
  every reply with an offer. An offer is also the right home for the analysis
  you did not volunteer: better to say you can look further than to look
  further uninvited.

Concise does not mean clipped. A NORMAL answer is a real answer, not a
fragment.

### DETAILED -- when the user asks for it

Select this when the message reasonably indicates the user wants analysis,
details, evidence, provenance, a source, a calculation, the underlying data,
a complete comparison, structured output, or something they can audit.

Here you may use headings, tables, formulas, provenance, completeness,
counts, confidence metadata and technical notes -- as much as the request
warrants.

Depth is chosen from what the user asked for. It is never chosen because an
answer happens to carry a caveat: see below.

### BRIEF -- for spoken replies

For a channel where the reply is heard rather than read. One or two short
sentences, the result first, no table, no metadata read aloud. Where detail
exists but does not belong in speech, say so and offer it.

Nothing selects BRIEF today. Its rules are written here so that a spoken
channel later inherits this same policy instead of acquiring one of its own.

### These override the depth, always

The rules below are not part of the depth choice and are not compressible.
They apply identically to BRIEF, NORMAL and DETAILED, and a NORMAL answer
that carries one of them is still a NORMAL answer -- a warning does not turn
a short reply into a long one.

1. If data is unavailable or materially incomplete, say so. A short answer
   that omits a gap is worse than a long one.
2. Never present a value that was requested directly but could not be
   retrieved directly as though it had been observed directly.
3. When a value is available only indirectly -- carried inside a result about
   something else -- say which it is. An indirect figure may be perfectly
   sound, and the reader still has to be able to tell the difference. Use it
   freely; just do not launder where it came from.
   This survives every change of form. A distinction you drew in a sentence
   has to still be there after you rewrite that sentence as a table, and a
   row is a place where it easily disappears: rows look alike by design, so
   an indirect value dropped into one beside directly retrieved values stops
   being distinguishable at all. When a representation cannot carry the
   qualifier, that representation is the wrong one -- keep the value in
   qualified prose, mark the row, add a column that says where each figure
   came from, or attach a note to that figure alone. Any of those is fine.
   Presentation may change; evidence class may not.
4. Quality and validation attributes belong to the result that produced them
   and to nothing else. A figure carried inside a result -- a previous or
   reference value, an item quoted from elsewhere -- does not inherit that
   result's counts, coverage, completeness or validation status merely by
   travelling with it. Nesting and adjacency are not scope. Attribute such an
   attribute to a carried value only if the data contract says it covers that
   value too, and where it does not, do not spread it over everything you are
   presenting together.
   The way you lay an answer out creates scope of its own, and this is the
   easier half to miss. A caption under a table, a heading over a group, a
   legend, a footnote, a closing sentence summarising a list -- anything that
   sits across several items will be read as speaking about all of them. So
   an attribute true of only some of the items must not be written into a
   shared position: two results that each report the same figure do not
   license a caption saying it holds for every row, when one of those rows
   came from somewhere else. Put such an attribute where it applies -- beside
   the items it covers, named with them, or qualified in the same breath.
   Keep the attribute; do not drop useful information because the set is
   mixed, and do not repeat it mechanically against every item either. What
   must not happen is the quiet widening: a container may not extend the
   reach of what it contains.
5. State material uncertainty -- uncertainty that would change how a
   reasonable reader acts on the answer. Be careful what counts. A field
   reporting that some check was not performed, not evaluated, or is
   unavailable says nothing about whether the value is sound: it records the
   absence of an assessment, not an adverse one. Do not translate such a
   field into a caution, a caveat, or advice about relying on the figure.
   Uncertainty is material when something affirmatively indicates it --
   a stated warning, a failure, a partial or provisional result, a conflict
   -- not when a dimension simply went unmeasured. Omit what is immaterial.
6. State a material conflict between results rather than choosing quietly
   between them.
7. Do not invent or estimate a value that is missing. If the user explicitly
   asks for an estimate, say plainly that it is one.

Brevity is a choice about wording. It is never a choice about meaning: it may
not soften, generalise or drop what an authoritative result actually said.
"""


_SECOND_BRAIN_EVIDENCE_REFERENCE_RULE = """## Second Brain Evidence Reference Honesty

A Second Brain memory entry may carry an evidence_references field -- a pointer its author recorded to an operational capability at the time the memory was captured. This is a historical bookkeeping note, not independently re-verified evidence: nothing re-checks it against live state, and nothing confirms it was actually retrieved in the current conversation.

Every evidence_references entry you see is marked UNVERIFIED. Treat that label literally:

- Do not present a stored evidence_references entry as proof the underlying evidence still exists, is current, or actually supports the memory's claim.
- The memory entry's own trust_status (e.g. VERIFIED, DECISION) does NOT verify its evidence_references -- the two are independent. A VERIFIED memory with a stored reference is still exactly as unverified on that reference as a HYPOTHESIS with one.
- If you need current proof of an operational fact, call the appropriate live tool (an ops_dynamic_* capability, or Document Knowledge) yourself in this conversation -- a stored Second Brain reference is never a substitute for that.
"""


@dataclass(frozen=True, slots=True)
class PromptSection:
    """Inspectable prompt section emitted by SystemPromptBuilder."""

    name: str
    content: str
    source: str
    cache_segment: PromptCacheSegment


class SystemPromptBuilder:
    """Assembles system prompts with frozen prefix for cache stability."""

    def __init__(
        self,
        agent_template: str,
        memory_files_config: Optional[MemoryFilesConfig] = None,
        system_prompt_config: Optional[SystemPromptConfig] = None,
        skill_index: Optional[List[Tuple[str, str]]] = None,
        session_context: Optional[str] = None,
        previous_state: Optional[str] = None,
        skill_catalog_xml: Optional[str] = None,
        skill_few_shot: Optional[List[str]] = None,
        skill_few_shot_examples: Optional[List[str]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        # FASE 4Q.4A -- captured once, at builder-construction time (same
        # lifetime as everything else in the frozen prefix -- one
        # SystemPromptBuilder per agent, one agent per jarvis chat
        # session). ``now`` is injectable for deterministic tests; real
        # callers never pass it, so this is always the genuine runtime
        # clock outside tests.
        self._now = now if now is not None else _default_now()
        self._agent_template = agent_template
        _mf = memory_files_config or MemoryFilesConfig()
        self._mf_config = self._resolve_persona(_mf)
        self._sp_config = system_prompt_config or SystemPromptConfig()
        self._skill_index = skill_index or []
        self._session_context = session_context
        self._previous_state = previous_state
        self._skill_catalog_xml = skill_catalog_xml
        # Allow either name; skill_few_shot_examples is the Plan 2A canonical name.
        if skill_few_shot_examples is not None:
            self._skill_few_shot = list(skill_few_shot_examples)
        else:
            self._skill_few_shot = list(skill_few_shot or [])
        self._frozen_prefix: Optional[str] = None
        self._frozen_sections: Optional[list[PromptSection]] = None

    def build(self) -> str:
        if self._frozen_prefix is None:
            self._frozen_prefix = self._build_frozen_prefix()
        parts = [self._frozen_prefix]
        if self._session_context:
            parts.append(f"\n\n## Session Context\n\n{self._session_context}")
        if self._previous_state:
            parts.append(f"\n\n## Previous State\n\n{self._previous_state}")
        return "".join(parts)

    def sections(self) -> list[PromptSection]:
        """Return prompt sections with lightweight cache/debug metadata."""
        sections = [*self._get_frozen_sections()]
        if self._session_context:
            sections.append(
                PromptSection(
                    name="session_context",
                    content=f"## Session Context\n\n{self._session_context}",
                    source="session_context",
                    cache_segment="dynamic_suffix",
                )
            )
        if self._previous_state:
            sections.append(
                PromptSection(
                    name="previous_state",
                    content=f"## Previous State\n\n{self._previous_state}",
                    source="previous_state",
                    cache_segment="dynamic_suffix",
                )
            )
        return sections

    def _get_frozen_sections(self) -> list[PromptSection]:
        if self._frozen_sections is None:
            self._frozen_sections = self._build_frozen_sections()
        return self._frozen_sections

    def _persona_sections(self) -> list[str]:
        """The SOUL / MEMORY / USER sections (no agent template, no skills)."""
        sections: list[str] = []
        soul = self._load_file(
            self._mf_config.soul_path,
            self._sp_config.soul_max_chars,
        )
        if soul:
            sections.append(f"## Agent Persona\n\n{soul}")
        memory = self._load_file(
            self._mf_config.memory_path,
            self._sp_config.memory_max_chars,
        )
        if memory:
            sections.append(f"## Agent Memory\n\n{memory}")
        user = self._load_file(
            self._mf_config.user_path,
            self._sp_config.user_max_chars,
        )
        if user:
            sections.append(f"## User Profile\n\n{user}")
        return sections

    def persona_sections(self) -> str:
        """Just the SOUL / MEMORY / USER persona, joined.

        For agents that assemble their own system prompt (monitor_operative,
        operative) and want to *append* persona without letting the builder
        replace their specialized instructions (#376). Returns "" when no
        persona files are present.
        """
        return "\n\n".join(self._persona_sections())

    def _build_frozen_prefix(self) -> str:
        return "\n\n".join(section.content for section in self._get_frozen_sections())

    def _build_frozen_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        # Config-driven persona prefix from [system_prompt] prefix (#401),
        # prepended ahead of the agent template so it leads the frozen prefix.
        if self._sp_config.prefix:
            sections.append(
                PromptSection(
                    name="prefix",
                    content=self._sp_config.prefix,
                    source="system_prompt.prefix",
                    cache_segment="frozen_prefix",
                )
            )
        if self._agent_template:
            sections.append(
                PromptSection(
                    name="agent_template",
                    content=self._agent_template,
                    source="agent_template",
                    cache_segment="frozen_prefix",
                )
            )
        sections.append(
            PromptSection(
                name="tool_grounding_discipline",
                content=_TOOL_GROUNDING_RULE,
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        sections.append(
            PromptSection(
                name="historical_evidence_discipline",
                content=_HISTORICAL_EVIDENCE_RULE,
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        sections.append(
            PromptSection(
                name="referent_continuity_discipline",
                content=_REFERENT_CONTINUITY_RULE,
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        sections.append(
            PromptSection(
                name="document_comparison_discipline",
                content=_DOCUMENT_COMPARISON_RULE,
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        sections.append(
            PromptSection(
                name="second_brain_evidence_reference_discipline",
                content=_SECOND_BRAIN_EVIDENCE_REFERENCE_RULE,
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        # Presentation, not permission: this decides the shape of a reply,
        # never its content. It sits after the grounding rules on purpose --
        # they constrain what may be said, and this only how.
        if getattr(self._sp_config, "presentation_policy", True):
            sections.append(
                PromptSection(
                    name="presentation_policy",
                    content=_PRESENTATION_POLICY_RULE,
                    source="builtin",
                    cache_segment="frozen_prefix",
                )
            )
        sections.append(
            PromptSection(
                name="current_time",
                content=_render_current_time_section(self._now),
                source="builtin",
                cache_segment="frozen_prefix",
            )
        )
        sections.extend(self._persona_prompt_sections())
        # XML skill catalog (preferred over legacy markdown list)
        if self._skill_catalog_xml:
            sections.append(
                PromptSection(
                    name="skill_catalog",
                    content="## Available Skills\n\n" + self._skill_catalog_xml,
                    source="skill_catalog_xml",
                    cache_segment="frozen_prefix",
                )
            )
        elif self._skill_index:
            skill_lines = []
            for name, desc in self._skill_index:
                truncated = desc[: self._sp_config.skill_desc_max_chars]
                if len(desc) > self._sp_config.skill_desc_max_chars:
                    truncated = truncated[:-3] + "..."
                skill_lines.append(f"- **{name}**: {truncated}")
            sections.append(
                PromptSection(
                    name="skill_index",
                    content="## Available Skills\n\n" + "\n".join(skill_lines),
                    source="skill_index",
                    cache_segment="frozen_prefix",
                )
            )
        if self._skill_few_shot:
            examples = "\n\n".join(self._skill_few_shot)
            sections.append(
                PromptSection(
                    name="skill_examples",
                    content="## Skill Examples\n\n" + examples,
                    source="skill_few_shot_examples",
                    cache_segment="frozen_prefix",
                )
            )
        return sections

    def _persona_prompt_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        self._append_file_section(
            sections=sections,
            name="soul",
            heading="Agent Persona",
            path_str=self._mf_config.soul_path,
            max_chars=self._sp_config.soul_max_chars,
        )
        self._append_file_section(
            sections=sections,
            name="memory",
            heading="Agent Memory",
            path_str=self._mf_config.memory_path,
            max_chars=self._sp_config.memory_max_chars,
        )
        self._append_file_section(
            sections=sections,
            name="user",
            heading="User Profile",
            path_str=self._mf_config.user_path,
            max_chars=self._sp_config.user_max_chars,
        )
        return sections

    def _append_file_section(
        self,
        sections: list[PromptSection],
        name: str,
        heading: str,
        path_str: str,
        max_chars: int,
    ) -> None:
        content = self._load_file(path_str, max_chars)
        if content:
            sections.append(
                PromptSection(
                    name=name,
                    content=f"## {heading}\n\n{content}",
                    source=str(Path(path_str).expanduser()),
                    cache_segment="frozen_prefix",
                )
            )

    def _load_file(self, path_str: str, max_chars: int) -> str:
        # An empty path means "no file" (e.g. the persona "none" opt-out, which
        # resolves to empty paths). Guard before Path("") — which becomes "." —
        # so reading it does not raise IsADirectoryError.
        if not path_str:
            return ""
        path = Path(path_str).expanduser()
        if not path.exists():
            return ""
        # Always read as UTF-8. On Windows, ``read_text()`` falls back to the
        # system code page (e.g. cp950 for zh-TW, cp932 for ja) and raises
        # ``UnicodeDecodeError`` on any non-ASCII persona content.
        content = path.read_text(encoding="utf-8")
        if len(content) <= max_chars:
            return content
        return self._truncate(content, max_chars)

    def _truncate(self, text: str, max_chars: int) -> str:
        if self._sp_config.truncation_strategy == "head_tail":
            head_size = int(max_chars * 0.7)
            tail_size = int(max_chars * 0.2)
            omitted = len(text) - head_size - tail_size
            return (
                text[:head_size]
                + f"\n\n[...truncated {omitted} chars...]\n\n"
                + text[-tail_size:]
            )
        return text[:max_chars] + "\n[...truncated...]"

    @staticmethod
    def _resolve_persona(mf: MemoryFilesConfig) -> MemoryFilesConfig:
        """Resolve persona_name to effective file paths.
        - "" (empty) -> use mf's existing paths (global default, unchanged)
        - "none"      -> empty paths (opt-out, no persona injected)
        - "<name>"    -> ~/.openjarvis/personas/<name>/{SOUL,MEMORY,USER}.md
        """
        if not mf.persona_name:
            return mf
        if mf.persona_name == "none":
            return MemoryFilesConfig(
                soul_path="",
                memory_path="",
                user_path="",
                nudge_interval=mf.nudge_interval,
            )
        name = mf.persona_name
        if ".." in name or "/" in name or "\\" in name or name.startswith("/"):
            raise ValueError(
                f"Invalid persona name {name!r}: must be a simple "
                "identifier (no path separators or '..')."
            )
        base = get_config_dir() / "personas" / name
        return MemoryFilesConfig(
            soul_path=str(base / "SOUL.md"),
            memory_path=str(base / "MEMORY.md"),
            user_path=str(base / "USER.md"),
            nudge_interval=mf.nudge_interval,
            persona_name=name,
        )
