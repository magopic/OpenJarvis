from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.core.config import MemoryFilesConfig, SystemPromptConfig


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    soul = tmp_path / "SOUL.md"
    soul.write_text("You are a helpful research assistant.")
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- User prefers concise answers\n- User is a data scientist")
    user = tmp_path / "USER.md"
    user.write_text("- Name: Alice\n- Role: ML Engineer")
    return tmp_path


def test_build_frozen_prefix(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    prompt = builder.build()
    assert "Jarvis" in prompt
    assert "helpful research assistant" in prompt
    assert "concise answers" in prompt
    assert "Alice" in prompt


def test_config_prefix_prepended(memory_dir: Path):
    """Regression for #401: a configured system_prompt.prefix leads the
    assembled prompt, ahead of the agent template, and is exposed as a
    'prefix' section."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(prefix="ALWAYS ANSWER AS JARVIS."),
    )
    prompt = builder.build()
    assert prompt.startswith("ALWAYS ANSWER AS JARVIS.")
    assert "You are Jarvis." in prompt
    # Prefix is visible in the inspection API too (#457), as a frozen section.
    section_names = [s.name for s in builder.sections()]
    assert section_names[0] == "prefix"
    assert builder.sections()[0].cache_segment == "frozen_prefix"


def test_empty_prefix_leaves_prompt_unchanged(memory_dir: Path):
    """Backward compatibility: the default empty prefix adds no section and
    leaves build() output identical to having no prefix configured."""
    from datetime import datetime, timezone

    from openjarvis.prompt.builder import SystemPromptBuilder

    # FASE 4Q.4A -- build() now includes the authoritative current-time
    # section, which is genuinely time-dependent by design (that's the
    # whole point of Fix A). Two independently-constructed builders are
    # only comparable when pinned to the SAME instant -- otherwise this
    # assertion would flake on real wall-clock drift between the two
    # calls, which is not what this test is actually checking.
    fixed_now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)

    def _make(prefix: str) -> SystemPromptBuilder:
        return SystemPromptBuilder(
            agent_template="You are Jarvis.",
            memory_files_config=MemoryFilesConfig(
                soul_path=str(memory_dir / "SOUL.md"),
                memory_path=str(memory_dir / "MEMORY.md"),
                user_path=str(memory_dir / "USER.md"),
            ),
            system_prompt_config=SystemPromptConfig(prefix=prefix),
            now=fixed_now,
        )

    assert _make("").build() == _make("").build()
    # No 'prefix' section is emitted when prefix is empty.
    assert "prefix" not in [s.name for s in _make("").sections()]
    # And the first section is the agent template, as before.
    assert _make("").sections()[0].name == "agent_template"


def test_frozen_prefix_stability(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    first = builder.build()
    (memory_dir / "MEMORY.md").write_text("- CHANGED CONTENT")
    second = builder.build()
    assert first == second


def test_char_limit_truncation(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    (memory_dir / "SOUL.md").write_text("x" * 10000)
    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(soul_max_chars=100),
    )
    prompt = builder.build()
    # Count "x" only within the SOUL section's own rendered content, not
    # the whole prompt -- other builtin sections (grounding/discipline
    # rules) are free to contain the letter "x" in ordinary prose
    # without that being a truncation-logic regression.
    soul_section = next(s for s in builder.sections() if s.name == "soul")
    assert soul_section.content.count("x") <= 100
    assert "truncated" in prompt.lower()


def test_skill_index_in_prompt(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    skills = [("api_health_check", "Check API health across all endpoints")]
    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
        skill_index=skills,
    )
    prompt = builder.build()
    assert "api_health_check" in prompt
    assert "Check API health" in prompt


def test_dynamic_section_appended(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
        session_context="Platform: CLI | Session: abc123",
    )
    prompt = builder.build()
    assert "Platform: CLI" in prompt


def test_sections_expose_prompt_metadata(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
        session_context="Platform: CLI | Session: abc123",
        previous_state="Last task: summarize telemetry.",
    )

    sections = builder.sections()

    assert [section.name for section in sections] == [
        "agent_template",
        "tool_grounding_discipline",
        "historical_evidence_discipline",
        "referent_continuity_discipline",
        "document_comparison_discipline",
        "second_brain_evidence_reference_discipline",
        "current_time",
        "soul",
        "memory",
        "user",
        "session_context",
        "previous_state",
    ]
    assert sections[7].source == str(memory_dir / "SOUL.md")
    assert sections[7].cache_segment == "frozen_prefix"
    assert sections[-1].cache_segment == "dynamic_suffix"
    assert builder.build() == "\n\n".join(section.content for section in sections)


def test_tool_grounding_discipline_covers_cross_turn_claim_freshness(memory_dir: Path):
    """FASE 4Q.4A -- live certification finding: a claim ("no pending
    approvals") was made in one turn without a fresh tool call actually
    backing it, relying instead on an earlier, broader turn's result that
    never covered that specific question. The grounding rule must say
    explicitly that an earlier turn's tool result is evidence only for
    what it actually returned, not for a new, more specific claim later."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    sections = builder.sections()
    grounding = next(s for s in sections if s.name == "tool_grounding_discipline")
    assert "earlier turn" in grounding.content
    assert "call the appropriate tool again in this turn" in grounding.content


def test_tool_grounding_discipline_covers_source_scope(memory_dir: Path):
    """FASE 4Q.4A -- live certification finding: MAIA said 'the systems
    are not configured for your reality' on the strength of empty
    notifications/attention items/Second Brain/Document Knowledge results
    alone -- none of which say anything about OPS Bridge configuration or
    connectivity. The grounding rule must explicitly prohibit turning
    'nothing found in the sources I queried' into a claim about a
    different, unqueried system's configuration/availability."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    sections = builder.sections()
    grounding = next(s for s in sections if s.name == "tool_grounding_discipline")
    assert "not configured" in grounding.content
    assert "DIFFERENT system or integration" in grounding.content
    assert "did not query" in grounding.content


def test_referent_continuity_discipline_present_and_distinguishes_roles(memory_dir: Path):
    """FASE 4Q.4A Attempt #6B/CASE 1 -- live certification finding: 'lo'
    resolved to the assistant's OWN just-introduced suggestion (a
    procedure it proposed) instead of the user's own sustained task, and
    persistent state was created around the wrong thing. The rule must
    explicitly say the user's own task is the default referent, that an
    assistant suggestion needs explicit selection, and that genuine
    ambiguity before a persistent write should be clarified rather than
    guessed."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    sections = builder.sections()
    referent = next(s for s in sections if s.name == "referent_continuity_discipline")
    assert "recency is not selection" in referent.content
    assert "explicitly picked it" in referent.content
    assert "ask one concise clarification question" in referent.content


def test_document_comparison_discipline_present_and_grounds_version_claims(memory_dir: Path):
    """M2.5A.1 -- live certification finding: comparing a CURRENT document
    against its SUPERSEDED predecessor, MAIA claimed the newer version
    "introduced" content that did not actually differ (the two documents
    were byte-identical). The rule must say plainly that supersession
    status is not evidence of a content change, that a same_content
    signal indicating identical content rules out any difference claim,
    and that a differing-hash signal alone never licenses describing
    WHAT changed."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    sections = builder.sections()
    comparison = next(s for s in sections if s.name == "document_comparison_discipline")
    assert "not evidence that the two documents' content actually differs" in comparison.content
    assert "same_content_as_successor" in comparison.content
    assert "no substantive difference can be established" in comparison.content
    assert "do not describe WHAT changed unless the actual retrieved text shows it" in comparison.content
    assert "cannot be established from the evidence gathered" in comparison.content


def test_second_brain_evidence_reference_discipline_present_and_grounds_verification_claims(memory_dir: Path):
    """M2.5B Phase 1 -- Test G: the new rule must be present in the
    generated prompt (not merely defined), and must say plainly that a
    stored evidence_references pointer is UNVERIFIED regardless of the
    memory entry's own trust_status -- the core authority rule
    (evidence_reference != evidence_verified)."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    sections = builder.sections()
    rule = next(s for s in sections if s.name == "second_brain_evidence_reference_discipline")
    assert "UNVERIFIED" in rule.content
    assert "does NOT verify its evidence_references" in rule.content
    assert "VERIFIED memory with a stored reference is still exactly as unverified" in rule.content
    assert rule.content in builder.build()  # actually emitted, not merely defined


def _builder_with_now(memory_dir: Path, now):
    from openjarvis.prompt.builder import SystemPromptBuilder

    return SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
        now=now,
    )


def test_a1_current_time_section_present(memory_dir: Path):
    """FASE 4Q.4A Attempt #7 Fix A -- the model must receive an
    authoritative runtime date/time; the live certification fabricated
    run_at='2025-07-16T09:00:00+02:00' for a 'domani' request made on the
    real date 2026-08-25, over a year off, because no such context ever
    reached it."""
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 8, 25, 13, 25, 0, tzinfo=timezone(timedelta(hours=2)))
    builder = _builder_with_now(memory_dir, now)
    sections = builder.sections()
    current_time = next(s for s in sections if s.name == "current_time")
    assert "Current Date & Time" in current_time.content
    assert "2026-08-25" in current_time.content


def test_a2_current_time_is_timezone_aware(memory_dir: Path):
    from datetime import datetime, timezone, timedelta

    now = datetime(2026, 8, 25, 13, 25, 0, tzinfo=timezone(timedelta(hours=2)))
    builder = _builder_with_now(memory_dir, now)
    current_time = next(s for s in builder.sections() if s.name == "current_time")
    assert "+02:00" in current_time.content


def test_a3_current_time_generated_dynamically_not_hardcoded(memory_dir: Path):
    """Two builders with two different injected clocks must produce two
    different current_time sections -- proving the value is computed,
    never a static string."""
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=2))
    builder1 = _builder_with_now(memory_dir, datetime(2026, 8, 25, 13, 25, 0, tzinfo=tz))
    builder2 = _builder_with_now(memory_dir, datetime(2030, 1, 1, 0, 0, 0, tzinfo=tz))
    content1 = next(s for s in builder1.sections() if s.name == "current_time").content
    content2 = next(s for s in builder2.sections() if s.name == "current_time").content
    assert content1 != content2
    assert "2026-08-25" in content1
    assert "2030-01-01" in content2


def test_a4_controlled_clock_resolves_today_and_tomorrow(memory_dir: Path):
    """A controlled test clock of 2026-08-25T13:25:00+02:00 must yield a
    current_time section unambiguously stating today=2026-08-25, from
    which tomorrow=2026-08-26 follows by simple date arithmetic. No live
    model call involved -- this only proves the injected value is
    correct and present, not that a model would compute correctly."""
    from datetime import date, datetime, timedelta, timezone

    now = datetime(2026, 8, 25, 13, 25, 0, tzinfo=timezone(timedelta(hours=2)))
    builder = _builder_with_now(memory_dir, now)
    current_time = next(s for s in builder.sections() if s.name == "current_time")
    assert "2026-08-25" in current_time.content
    tomorrow = date(2026, 8, 25) + timedelta(days=1)
    assert tomorrow.isoformat() == "2026-08-26"
    assert tomorrow.isoformat() not in current_time.content  # only today is asserted, never precomputed


def test_a5_existing_frozen_sections_present_and_ordered(memory_dir: Path):
    """Adding current_time must not disturb the existing frozen sections
    or their order."""
    builder = _builder_with_now(memory_dir, None)
    names = [s.name for s in builder.sections()]
    assert names.index("tool_grounding_discipline") < names.index("historical_evidence_discipline")
    assert names.index("historical_evidence_discipline") < names.index("referent_continuity_discipline")
    assert names.index("referent_continuity_discipline") < names.index("document_comparison_discipline")
    assert names.index("document_comparison_discipline") < names.index("second_brain_evidence_reference_discipline")
    assert names.index("second_brain_evidence_reference_discipline") < names.index("current_time")
    assert names.index("current_time") < names.index("soul")


def test_default_now_used_when_not_injected(memory_dir: Path):
    """Real callers never pass `now` -- the genuine runtime clock must be
    used, and it must be timezone-aware."""
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    assert builder._now.tzinfo is not None


def test_sections_keep_frozen_file_content_stable(memory_dir: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(memory_dir / "SOUL.md"),
            memory_path=str(memory_dir / "MEMORY.md"),
            user_path=str(memory_dir / "USER.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )

    first = builder.sections()
    (memory_dir / "MEMORY.md").write_text("- CHANGED CONTENT")
    second = builder.sections()

    assert first == second


def test_missing_files_handled(tmp_path: Path):
    from openjarvis.prompt.builder import SystemPromptBuilder

    builder = SystemPromptBuilder(
        agent_template="You are Jarvis.",
        memory_files_config=MemoryFilesConfig(
            soul_path=str(tmp_path / "missing_soul.md"),
            memory_path=str(tmp_path / "missing_memory.md"),
            user_path=str(tmp_path / "missing_user.md"),
        ),
        system_prompt_config=SystemPromptConfig(),
    )
    prompt = builder.build()
    assert "Jarvis" in prompt
