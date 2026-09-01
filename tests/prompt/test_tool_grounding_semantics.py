"""M3.6A.1 -- what a trust indicator's *value* means, not that it exists.

The grounding rule has always told the model to honor trust and validation
indicators. It named `confidence_status` among them and said to say so
plainly when a result is "not yet validated, in review, or otherwise not
certified" -- and a field whose value is "not evaluated" got swept into that
sentence. So an absence of assessment came out as a caution, and a correct
result arrived wearing a warning it never earned.

That is its own kind of ungrounded claim: inventing a doubt the data did not
express is no better than inventing a certainty. These tests pin both halves
-- what must still be reported, and what must stop being reported as adverse.

This file exists because nothing previously asserted the text of the
grounding rule at all. Touching a safety rule with no coverage is how a
safety rule quietly stops working.
"""

from __future__ import annotations

import pytest

from openjarvis.core.config import SystemPromptConfig
from openjarvis.prompt.builder import SystemPromptBuilder


def _section(name: str) -> str:
    builder = SystemPromptBuilder(
        agent_template="", system_prompt_config=SystemPromptConfig()
    )
    for section in builder._get_frozen_sections():
        if section.name == name:
            return section.content
    return ""


def _flat(text: str) -> str:
    """Lowercase, whitespace collapsed -- the rule is wrapped prose."""
    return " ".join(text.split()).lower()


def _grounding() -> str:
    return _flat(_section("tool_grounding_discipline"))


def _presentation() -> str:
    return _flat(_section("presentation_policy"))


# -- A: an absence of assessment is not an adverse assessment --------------


def test_an_indicator_is_read_by_value_not_by_its_presence() -> None:
    text = _grounding()
    assert "honor what it actually says" in text
    # The distinction the old wording lacked: a field being present is not
    # itself a signal.
    assert "not merely noting that the field is there" in text


@pytest.mark.parametrize(
    "phrasing", ["not evaluated", "not assessed", "not performed", "unknown"]
)
def test_a_check_that_was_never_run_is_named_as_such(phrasing: str) -> None:
    """The rule has to recognise the shapes this actually takes, or it will
    only cover the one field that prompted it."""
    assert phrasing in _grounding(), phrasing


def test_an_unrun_check_reports_no_judgement_either_way() -> None:
    text = _grounding()
    assert "the absence of a judgement, not a negative one" in text
    # Both directions in one sentence: it is not a warrant for a warning ...
    assert "nor obliges you to warn about it" in text
    # ... and not a warrant for claiming the opposite.
    assert "neither licenses you to call the result certified" in text


def test_it_stays_silent_unless_something_makes_it_relevant() -> None:
    text = _grounding()
    assert "say nothing about it unless the user asks about that dimension" in text


def test_doubt_may_not_be_manufactured() -> None:
    """Symmetry with the overclaim rule beside it: inventing uncertainty is
    as ungrounded as inventing confidence."""
    assert "do not manufacture a doubt the indicator never expressed" in _grounding()


def test_explicitly_marked_is_read_as_a_real_condition() -> None:
    text = _grounding()
    assert '"explicitly marked" is the operative phrase' in text
    assert "never had a given check performed carries no such mark" in text


# -- B: real warnings still have to be reported ---------------------------


def test_the_overclaim_prohibition_survives_verbatim() -> None:
    """The half of the sentence that must not have moved."""
    assert (
        "do not upgrade an uncertain or unvalidated result into a confident, "
        "certified-sounding claim" in _grounding()
    )


@pytest.mark.parametrize(
    "state", ["provisional", "partial", "in review", "failed", "invalid", "conflicting"]
)
def test_affirmatively_adverse_states_remain_reportable(state: str) -> None:
    text = _grounding()
    assert state in text, state
    assert "belongs in your answer when it is material" in text


def test_an_unvalidated_or_uncertified_result_must_still_be_disclosed() -> None:
    text = _grounding()
    assert "not yet validated, in review, or otherwise not certified" in text
    assert "say so plainly rather than presenting it as settled fact" in text


def test_unavailable_data_is_still_never_a_value() -> None:
    text = _grounding()
    assert "no data available for this" in text
    assert "say explicitly when data is unavailable" in text
    # And the presentation side keeps its own non-compressible override.
    assert "unavailable or materially incomplete" in _presentation()


def test_truncated_results_are_still_unknown() -> None:
    text = _grounding()
    assert "those omitted items are unknown to you" in text


def test_indirect_provenance_is_still_non_compressible() -> None:
    text = _presentation()
    assert "only indirectly" in text
    assert "say which it is" in text
    assert "as though it had been observed directly" in text


def test_nothing_may_still_be_invented() -> None:
    assert "do not invent or estimate" in _presentation()


# -- C: how the two sections interact -------------------------------------


def test_the_two_sections_no_longer_pull_in_opposite_directions() -> None:
    """The precedence failure this fixes.

    Presentation said to stay quiet about an unevaluated check; grounding
    named the field and said to honor it. Grounding won -- correctly, since a
    safety rule should outrank a style rule. The fix was to correct the
    premise, not to shout louder from presentation.
    """
    grounding, presentation = _grounding(), _presentation()
    # Both now say the same thing about an unrun check.
    assert "nor obliges you to warn about it" in grounding
    assert "do not translate such a field into a caution" in presentation
    # And both defer to the user actually asking.
    assert "unless the user asks about that dimension" in grounding
    assert "asked about quality" in presentation


def test_asking_about_the_dimension_makes_it_answerable() -> None:
    """Silence by default is not concealment: the question re-opens it."""
    assert "unless the user asks about that dimension" in _grounding()
    assert "break that silence in exactly three cases" in _presentation()


def test_detailed_may_expose_metadata_without_calling_it_adverse() -> None:
    presentation = _presentation()
    # DETAILED is allowed the metadata ...
    assert "confidence metadata" in presentation
    # ... while the adverse-conversion ban is stated for every depth.
    assert "brief, normal and detailed" in presentation


def test_grounding_still_precedes_presentation() -> None:
    builder = SystemPromptBuilder(
        agent_template="", system_prompt_config=SystemPromptConfig()
    )
    names = [s.name for s in builder._get_frozen_sections()]
    assert names.index("tool_grounding_discipline") < names.index("presentation_policy")


# -- domain neutrality -----------------------------------------------------


@pytest.mark.parametrize("term", ["maffei", "oee", "produzione", "pastificio", "ops bridge"])
def test_the_grounding_rule_stays_domain_neutral(term: str) -> None:
    assert term not in _grounding(), term


# -- the third case: an indicator that affirms nothing is wrong ------------
#
# The rule classified two kinds of indicator -- one that reports a problem,
# one that reports a check was never run -- and said nothing about the third,
# which is the commonest of all: a status affirming that everything is in
# order. Told to honor what such a field says, and given no guidance on what
# honoring a confirmation means, the model announced it. Full coverage and a
# nominal status came out in answers to questions that were about neither.
#
# Honoring positive evidence means being allowed to lean on it. It was never
# an instruction to read it aloud.


class TestPositiveConfirmation:
    def test_a_nominal_status_is_something_to_rely_on(self) -> None:
        text = _grounding()
        assert "affirmatively reports the absence of a problem" in text
        assert "is evidence you may rely on" in text

    @pytest.mark.parametrize(
        "shape", ["full coverage", "a complete set", "a nominal or successfully validated status"]
    )
    def test_the_shapes_it_takes_are_named(self, shape: str) -> None:
        """Named generically, so the rule covers more than the one field that
        exposed the gap."""
        assert shape in _grounding(), shape

    def test_honoring_it_is_reliance_not_narration(self) -> None:
        text = _grounding()
        assert "relying on it, not announcing it" in text
        assert "tells the reader what they already assumed" in text

    def test_a_populated_field_is_not_itself_a_reason_to_speak(self) -> None:
        assert "stating it because the field is populated" in _grounding()

    def test_a_confirmation_may_not_be_stretched_beyond_what_it_covers(self) -> None:
        """Silence must not become the other failure: a passed check is not a
        blanket guarantee."""
        text = _grounding()
        assert "nor may it be stretched" in text
        assert "never a broader guarantee about what it did not measure" in text

    def test_asking_about_the_dimension_still_gets_an_accurate_answer(self) -> None:
        assert "if the user asks about that dimension, answer accurately" in _grounding()

    def test_presentation_agrees_with_it(self) -> None:
        """Both sections must now say the same thing about a confirmation, or
        the precedence problem simply reappears one notch narrower."""
        assert "confirmation stays internal; deviation gets said" in _presentation()
        assert "relying on it, not announcing it" in _grounding()


class TestAllThreeCasesCoexist:
    """The classification is only useful if it is complete and ordered."""

    def test_each_case_is_present_and_distinct(self) -> None:
        text = _grounding()
        problem = text.index("affirmatively reports a problem")
        unrun = text.index("means only that a check was not run")
        positive = text.index("affirmatively reports the absence of a problem")
        assert problem < unrun < positive, "the three cases must all be present"

    def test_silence_never_reaches_a_material_negative(self) -> None:
        """The one thing these refinements must never have done."""
        text = _grounding()
        assert "belongs in your answer when it is material" in text
        assert "say explicitly when data is unavailable" in text
        assert (
            "do not upgrade an uncertain or unvalidated result into a confident, "
            "certified-sounding claim" in text
        )

    @pytest.mark.parametrize(
        "adverse",
        ["provisional", "partial", "in review", "failed", "invalid", "conflicting", "limitation"],
    )
    def test_every_adverse_state_survived_three_refinements(self, adverse: str) -> None:
        assert adverse in _grounding(), adverse

    def test_unavailable_data_is_still_never_silent(self) -> None:
        grounding, presentation = _grounding(), _presentation()
        assert 'no data available for this' in grounding
        assert "is not the same as a result of zero" in grounding
        assert "unavailable or materially incomplete" in presentation
        assert "not compressible" in presentation

    def test_indirect_provenance_survived_too(self) -> None:
        presentation = _presentation()
        assert "as though it had been observed directly" in presentation
        assert "say which it is" in presentation

    def test_no_evidence_was_removed_from_the_model_input(self) -> None:
        """These changes are about narration. Nothing was taken away from what
        the model receives: the rule still points at the indicators by name."""
        text = _grounding()
        for field in ("trust_status", "period_status", "provenance", "confidence_status", "limitations"):
            assert field in text, field
