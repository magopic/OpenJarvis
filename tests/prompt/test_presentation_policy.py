"""M3.6A -- the section that tells the assistant how to present an answer.

Without presentation guidance a capable model defaults to exhaustiveness: it
reports every field a tool handed it, because nothing told it a conversation
is not an audit log. This section supplies that guidance.

What these tests protect is the boundary the section must never cross. It may
decide the *shape* of a reply; it may not decide what the reply is allowed to
claim, and it may not let brevity swallow a caveat. Those two properties are
the whole reason the section is safe to add, so they are asserted directly
rather than left to review.
"""

from __future__ import annotations

import pytest

from openjarvis.core.config import SystemPromptConfig
from openjarvis.prompt.builder import SystemPromptBuilder


def _sections(**config_kwargs):
    builder = SystemPromptBuilder(
        agent_template="",
        system_prompt_config=SystemPromptConfig(**config_kwargs),
    )
    return builder._get_frozen_sections()


def _policy_text(**config_kwargs) -> str:
    for section in _sections(**config_kwargs):
        if section.name == "presentation_policy":
            return section.content
    return ""


def _flat(text: str) -> str:
    """Lowercase with runs of whitespace collapsed.

    The rule is wrapped prose, so a sentence the test cares about may be
    split across lines. Matching the wrapping instead of the wording would
    make these tests fail on a reflow and pass on a rewrite -- exactly
    backwards.
    """
    return " ".join(text.split()).lower()


# -- 1/2: the gate ---------------------------------------------------------


def test_the_section_is_present_by_default() -> None:
    names = [s.name for s in _sections()]
    assert "presentation_policy" in names


def test_the_section_is_absent_when_disabled() -> None:
    names = [s.name for s in _sections(presentation_policy=False)]
    assert "presentation_policy" not in names
    # Disabling presentation must not disturb anything else.
    assert "tool_grounding_discipline" in names
    assert "current_time" in names


def test_the_section_is_frozen_and_static() -> None:
    """It must not become per-turn content: the prefix is cached."""
    section = next(s for s in _sections() if s.name == "presentation_policy")
    assert section.cache_segment == "frozen_prefix"
    assert section.source == "builtin"
    # Same text on every build -- nothing interpolated.
    assert _policy_text() == _policy_text()


def test_it_sits_after_the_grounding_rules() -> None:
    """Order carries meaning: grounding constrains what may be said, and this
    only how. A presentation rule read before them would invite the model to
    trade one against the other."""
    names = [s.name for s in _sections()]
    assert names.index("presentation_policy") > names.index("tool_grounding_discipline")


# -- 3/4/5: the three depths ----------------------------------------------


def test_normal_is_stated_as_the_default() -> None:
    text = _policy_text()
    assert "NORMAL" in text
    assert "default" in text.lower()
    # The default has to be asserted, not merely listed first.
    normal_at = text.index("### NORMAL")
    assert "default" in _flat(text[normal_at : normal_at + 200])


def test_detailed_is_selected_by_user_intent_not_by_a_caveat() -> None:
    text = _policy_text()
    detailed = _flat(text[text.index("### DETAILED") : text.index("### BRIEF")])
    # It is chosen from what the user asked for ...
    assert "user" in detailed
    for signal in ("analysis", "details", "provenance", "audit"):
        assert signal in detailed, signal
    # ... and explicitly not because an answer carries a warning. This is the
    # correction the product made to the recon: uncertainty is orthogonal to
    # depth, so a caveat must not silently promote a reply to DETAILED.
    assert "never chosen because" in detailed


def test_brief_has_semantics_but_nothing_selects_it() -> None:
    text = _policy_text()
    brief = _flat(text[text.index("### BRIEF") : text.index("### These override")])
    assert "one or two" in brief
    assert "no table" in brief
    # Declared for a future spoken channel, deliberately not wired to one.
    assert "nothing selects brief today" in _flat(text)


# -- 6/7/8/9: the overrides ------------------------------------------------


def test_missing_data_can_never_be_omitted() -> None:
    text = _flat(_policy_text())
    assert "unavailable or materially incomplete" in text
    assert "say so" in text


def test_a_direct_request_that_failed_may_not_be_passed_off_as_observed() -> None:
    assert "as though it had been observed directly" in _policy_text().lower()


def test_indirect_provenance_must_be_distinguished() -> None:
    text = _flat(_policy_text())
    assert "only indirectly" in text
    assert "say which it is" in text


def test_uncertainty_and_conflict_must_be_stated() -> None:
    text = _flat(_policy_text())
    assert "state material uncertainty" in text
    assert "material conflict" in text


def test_nothing_may_be_invented_or_estimated() -> None:
    text = _flat(_policy_text())
    assert "do not invent or estimate" in text


def test_the_overrides_are_declared_incompressible_and_depth_independent() -> None:
    text = _policy_text()
    overrides = text[text.index("### These override") :]
    flat_overrides = _flat(overrides)
    assert "not compressible" in flat_overrides
    # They apply at every depth, and carrying one does not change the depth.
    assert "brief, normal and detailed" in flat_overrides
    assert "does not turn a short reply into a long one" in flat_overrides


def test_presentation_may_not_change_meaning() -> None:
    """The single sentence that keeps this a presentation policy."""
    text = _flat(_policy_text())
    assert "never a choice about meaning" in text
    assert "governs how you *present*" in text
    assert "never governs what you may claim" in text


def test_it_does_not_relax_the_grounding_rules_it_follows() -> None:
    text = _flat(_policy_text())
    for forbidden in ("you may omit", "skip the caveat", "no need to mention"):
        assert forbidden not in text, forbidden


# -- 10: the core stays universal -----------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "maffei",
        "oee",
        "kpi",
        "produzione",
        "production",
        "pastificio",
        "ops one",
        "ops bridge",
        "real_data",
        "confidence_status",
        "recordcount",
        "previous_value",
        "efficiency",
        "plant",
    ],
)
def test_no_business_vocabulary_reaches_the_universal_core(term: str) -> None:
    """This ships to every OpenJarvis install, not just the one that
    prompted it. The rule has to be about answering, not about anyone's
    business: it names no metric, no company, no system and no field."""
    assert term not in _flat(_policy_text()), f"business term in universal core: {term}"


def test_it_speaks_about_answers_rather_than_about_a_domain() -> None:
    text = _flat(_policy_text())
    for neutral in ("the user", "the question", "an answer", "a value"):
        assert neutral in text, neutral


# -- M3.6A.1 refinement: scope, silence, and what "uncertain" means --------
#
# The first behavioural gate passed on shape and failed on judgement. The
# answer led with the figure and used prose -- but it also recited record
# counts nobody asked for, volunteered a third year outside the question,
# and turned a field saying a check had not been run into managerial advice
# about relying on the data. Correct information, all of it, and none of it
# asked for. These pin the three rules that close that gap.


def test_the_answer_is_scoped_to_the_question() -> None:
    text = _flat(_policy_text())
    assert "answer the question that was asked, and stop there" in text
    # The trap is specifically that a result arrives carrying more than was
    # requested, and abundance reads as licence.
    assert "correct is not the same as relevant" in text
    assert "is not a reason to say it" in text


def test_availability_alone_does_not_justify_saying_something() -> None:
    text = _flat(_policy_text())
    assert "something being present in what you retrieved" in text
    # Extra findings are deferred to a follow-up rather than dropped.
    assert "keep it for the follow-up" in text


def test_metadata_is_silent_by_default_with_named_exceptions() -> None:
    text = _flat(_policy_text())
    assert "silent by default" in text
    # Silence must be the rule, and the ways out must be enumerated rather
    # than left to a judgement call like "when it matters" -- which is the
    # wording that let counts and completeness through.
    assert "break that silence in exactly three cases" in text
    for way_out in ("asked about quality", "departs from the ordinary case", "overrides below"):
        assert way_out in text, way_out
    assert "not to recite" in text


def test_unvolunteered_analysis_becomes_an_offer() -> None:
    text = _flat(_policy_text())
    assert "than to look further uninvited" in text


def test_an_unevaluated_check_is_not_uncertainty() -> None:
    """The distinction the first gate got wrong.

    A field saying an assessment was not performed records the absence of a
    judgement, not a negative one. Presenting it as a caveat invents a doubt
    the data never expressed -- which is its own kind of ungrounded claim.
    """
    text = _flat(_policy_text())
    assert "not performed, not evaluated, or is unavailable" in text
    assert "the absence of an assessment, not an adverse one" in text
    assert "do not translate such a field into a caution" in text


def test_material_uncertainty_is_defined_by_consequence_and_evidence() -> None:
    text = _flat(_policy_text())
    # Material is defined, not left open.
    assert "would change how a reasonable reader acts" in text
    # And it requires something affirmative, not a gap.
    assert "when something affirmatively indicates it" in text
    assert "not when a dimension simply went unmeasured" in text


def test_the_uncertainty_override_still_binds() -> None:
    """Narrowing what counts as material must not disarm the rule."""
    text = _flat(_policy_text())
    assert "state material uncertainty" in text
    # The other overrides are untouched by the refinement.
    assert "unavailable or materially incomplete" in text
    assert "do not invent or estimate" in text


# -- A.3: confirming metadata vs. a departure worth mentioning -------------
#
# Scope and confidence semantics both landed; full completeness still came
# out in the answer. The gap was in the exception itself: "the value changes
# how the answer should be read" reads as satisfied by confirmation too, so
# a 100% coverage figure looked like grounds for speaking. It is not -- it
# tells the reader what they already assumed. Only a departure does.


def test_confirming_metadata_stays_internal() -> None:
    text = _flat(_policy_text())
    assert "an indicator confirming the unremarkable" in text
    assert "tells the reader what they already assumed" in text
    # Naming the cost keeps it from reading as an arbitrary style rule.
    assert "sound as though it needed defending" in text


def test_a_departure_is_what_earns_words() -> None:
    text = _flat(_policy_text())
    assert "it is the departure that earns words" in text
    for departure in ("a gap", "a shortfall", "a partial or unusual state"):
        assert departure in text, departure


def test_the_rule_is_stated_in_one_line_either_way() -> None:
    assert "confirmation stays internal; deviation gets said" in _flat(_policy_text())


def test_the_middle_exception_no_longer_reads_as_satisfied_by_confirmation() -> None:
    """The precise wording that let full coverage through."""
    text = _flat(_policy_text())
    assert "departs from the ordinary case" in text
    # The old, over-broad phrasing must be gone.
    assert "the value genuinely changes how the answer should be read" not in text


def test_incomplete_data_is_still_disclosed() -> None:
    """Silence covers confirmation only. A real shortfall is still an
    override, and overrides are not compressible."""
    text = _flat(_policy_text())
    assert "unavailable or materially incomplete" in text
    assert "say so" in text
    assert "not compressible" in text


def test_asking_about_completeness_still_opens_it() -> None:
    assert "asked about quality, reliability or provenance" in _flat(_policy_text())


# -- Gate B: an evidence class has to survive being reformatted ------------
#
# The override already said to declare an indirect value as indirect, and the
# model did -- in its first draft, in prose. Then it rewrote that draft as a
# table, and the qualifier had nowhere to go: rows are built to look alike.
# By the final rewrite the indirect figure sat in a row indistinguishable
# from directly retrieved ones, under a caption spreading one result's record
# count across all three.
#
# Nothing was fabricated. The number was right. What was lost was the reader's
# ability to tell two kinds of evidence apart -- which is the part that makes
# an indirect figure safe to use at all.


class TestEvidenceClassSurvivesReformatting:
    def test_an_indirect_value_stays_usable(self) -> None:
        """The fix must not turn into "avoid indirect values"."""
        text = _flat(_policy_text())
        assert "may be perfectly sound" in text
        assert "use it freely" in text
        assert "just do not launder where it came from" in text

    def test_the_distinction_outlives_a_change_of_form(self) -> None:
        text = _flat(_policy_text())
        assert "this survives every change of form" in text
        assert "after you rewrite that sentence as a table" in text

    def test_the_table_row_is_named_as_where_it_disappears(self) -> None:
        """Naming the actual mechanism, not the abstraction: rows look alike
        by design, which is exactly why the qualifier vanishes there."""
        text = _flat(_policy_text())
        assert "rows look alike by design" in text
        assert "stops being distinguishable at all" in text

    def test_mixed_provenance_must_expose_the_difference_somehow(self) -> None:
        text = _flat(_policy_text())
        assert "when a representation cannot carry the qualifier" in text
        assert "that representation is the wrong one" in text

    def test_no_single_layout_is_mandated(self) -> None:
        """Four options offered, explicitly interchangeable -- a fixed
        template would be a worse rule and a worse answer."""
        text = _flat(_policy_text())
        for option in ("qualified prose", "mark the row", "add a column", "attach a note"):
            assert option in text, option
        assert "any of those is fine" in text

    def test_the_principle_is_stated_once_and_plainly(self) -> None:
        assert "presentation may change; evidence class may not" in _flat(_policy_text())

    def test_direct_evidence_is_untouched(self) -> None:
        """The rule speaks about carried values; a directly retrieved figure
        needs no qualifier and must not acquire one."""
        text = _flat(_policy_text())
        assert "carried inside a result about something else" in text
        # No instruction to caveat ordinary direct results.
        assert "qualify every value" not in text

    def test_no_defensive_extra_query_is_demanded(self) -> None:
        """Citing an indirect reference must not oblige a fresh lookup: the
        rule is about representation, not about retrieval."""
        text = _flat(_policy_text())
        for demand in ("query it directly", "retrieve it again", "call the tool again"):
            assert demand not in text, demand


class TestQualityMetadataScope:
    def test_attributes_belong_to_the_result_that_produced_them(self) -> None:
        text = _flat(_policy_text())
        assert "belong to the result that produced them and to nothing else" in text

    def test_a_carried_value_inherits_nothing_by_travelling_along(self) -> None:
        text = _flat(_policy_text())
        assert "does not inherit that result's counts, coverage, completeness or validation status" in text
        assert "merely by travelling with it" in text

    def test_nesting_and_adjacency_are_explicitly_not_scope(self) -> None:
        """The exact inference that produced "11 records per period"."""
        assert "nesting and adjacency are not scope" in _flat(_policy_text())

    def test_the_contract_is_the_only_thing_that_extends_scope(self) -> None:
        text = _flat(_policy_text())
        assert "only if the data contract says it covers that value too" in text

    def test_an_attribute_is_not_spread_over_a_whole_presentation(self) -> None:
        assert "do not spread it over everything you are presenting together" in _flat(_policy_text())


class TestTheEarlierOverridesSurvivedRenumbering:
    """Adding an override in the middle renumbers the rest. Nothing may have
    been dropped in the process."""

    @pytest.mark.parametrize(
        "clause",
        [
            "unavailable or materially incomplete",
            "as though it had been observed directly",
            "state material uncertainty",
            "material conflict",
            "do not invent or estimate",
            "not compressible",
            "never a choice about meaning",
        ],
    )
    def test_every_earlier_protection_is_still_there(self, clause: str) -> None:
        assert clause in _flat(_policy_text()), clause

    def test_the_overrides_are_numbered_one_through_seven(self) -> None:
        import re

        overrides = _policy_text()[_policy_text().index("### These override") :]
        numbers = [int(m) for m in re.findall(r"^(\d+)\. ", overrides, re.M)]
        assert numbers == list(range(1, 8)), numbers


class TestGateANormalStillCompatible:
    """The Gate A answer -- one sentence, two directly retrieved values -- must
    still be a correct answer under these rules."""

    def test_nothing_here_applies_to_a_plain_direct_answer(self) -> None:
        text = _flat(_policy_text())
        # The new rules are conditional on a carried value or mixed provenance.
        assert "when a value is available only indirectly" in text
        assert "a figure carried inside a result" in text
        # NORMAL's own defaults are intact.
        assert "answer the question that was asked, and stop there" in text
        assert "confirmation stays internal; deviation gets said" in text

    def test_an_override_still_does_not_change_the_depth(self) -> None:
        assert "does not turn a short reply into a long one" in _flat(_policy_text())


# -- Gate B retest: layout creates scope too -------------------------------
#
# The rewrite was not the only thing that went wrong. Before any nudge fired,
# the very first draft built a three-row table and then wrote a source
# caption underneath it reading "11 records, 100% completeness for each
# period" -- when only two of those three rows came from a result that
# reported anything of the kind.
#
# The model was not inferring that a carried value inherits its host's
# metadata; override 4 already forbade that and it was not doing it. It was
# writing a caption, and a caption under a table is about the table. The
# attribute was true of some rows and the container made it read as true of
# all of them.


class TestContainerScopeDoesNotWiden:
    def test_layout_is_named_as_a_source_of_scope(self) -> None:
        text = _flat(_policy_text())
        assert "the way you lay an answer out creates scope of its own" in text
        assert "this is the easier half to miss" in text

    @pytest.mark.parametrize(
        "container",
        [
            "a caption under a table",
            "a heading over a group",
            "a legend",
            "a footnote",
            "a closing sentence summarising a list",
        ],
    )
    def test_the_containers_are_named_generically(self, container: str) -> None:
        """Not just table captions: the same widening happens under any
        element that spans several items."""
        assert container in _flat(_policy_text()), container

    def test_anything_spanning_items_reads_as_covering_them_all(self) -> None:
        text = _flat(_policy_text())
        assert "sits across several items will be read as speaking about all of them" in text

    def test_a_partial_attribute_may_not_go_in_a_shared_position(self) -> None:
        text = _flat(_policy_text())
        assert "true of only some of the items must not be written into a shared position" in text

    def test_the_exact_trap_is_described(self) -> None:
        """Two results reporting the same figure is what made the
        generalisation feel safe. Naming it stops the rule reading as
        abstract."""
        text = _flat(_policy_text())
        assert "two results that each report the same figure" in text
        assert "do not license a caption saying it holds for every row" in text
        assert "when one of those rows came from somewhere else" in text

    def test_the_invariant_is_stated_plainly(self) -> None:
        assert "a container may not extend the reach of what it contains" in _flat(_policy_text())


class TestTheFixDoesNotSuppressMetadata:
    def test_mixed_provenance_does_not_mean_omit(self) -> None:
        """The failure mode on the other side: dropping the figures entirely
        would satisfy the rule and make DETAILED worse."""
        text = _flat(_policy_text())
        assert "keep the attribute" in text
        assert "do not drop useful information because the set is mixed" in text

    def test_mechanical_repetition_is_not_required(self) -> None:
        assert "do not repeat it mechanically against every item either" in _flat(_policy_text())

    def test_placement_options_are_offered_not_prescribed(self) -> None:
        text = _flat(_policy_text())
        for option in ("beside the items it covers", "named with them", "qualified in the same breath"):
            assert option in text, option

    def test_no_fixed_layout_is_imposed(self) -> None:
        text = _flat(_policy_text())
        for prescription in ("always use a column", "must use a table", "always add a footnote"):
            assert prescription not in text, prescription

    def test_a_homogeneous_direct_presentation_is_untouched(self) -> None:
        """The rule is conditional on an attribute being true of only some
        items. Where everything shares one provenance, nothing applies."""
        text = _flat(_policy_text())
        assert "true of only some of the items" in text
        assert "qualify every value" not in text


class TestGenericAcrossAttributeKinds:
    @pytest.mark.parametrize(
        "kind", ["counts", "coverage", "completeness", "validation status"]
    )
    def test_the_named_kinds_are_generic(self, kind: str) -> None:
        assert kind in _flat(_policy_text()), kind

    def test_it_is_written_about_attributes_not_field_names(self) -> None:
        """Sample size, confidence, freshness and verification status are all
        the same case; the rule must not be keyed to a field list."""
        text = _flat(_policy_text())
        assert "quality and validation attributes" in text
        assert "an attribute true of only some of the items" in text
        # No dependency on any concrete field identifier.
        for field_name in ("recordcount", "data_quality", "confidence_status", "period_status"):
            assert field_name not in text, field_name


class TestOverridesThreeAndFourCompose:
    def test_both_invariants_are_present_and_distinct(self) -> None:
        text = _flat(_policy_text())
        assert "presentation may change; evidence class may not" in text
        assert "a container may not extend the reach of what it contains" in text

    def test_they_do_not_contradict_each_other(self) -> None:
        """Three asks for the indirect value to be marked; four asks for an
        attribute not to be captioned over it. Both are satisfied by the same
        answer: keep the figure, mark the row, scope the caption."""
        text = _flat(_policy_text())
        assert "use it freely" in text
        assert "keep the attribute" in text
        # Neither tells the model to remove anything.
        assert "omit the value" not in text
        assert "remove the metadata" not in text

    def test_normal_depth_is_unaffected(self) -> None:
        text = _flat(_policy_text())
        # NORMAL's own rules are intact ...
        assert "answer the question that was asked, and stop there" in text
        assert "confirmation stays internal; deviation gets said" in text
        # ... and an override still does not change depth.
        assert "does not turn a short reply into a long one" in text

    def test_the_overrides_are_still_numbered_one_through_seven(self) -> None:
        import re

        overrides = _policy_text()[_policy_text().index("### These override") :]
        assert [int(m) for m in re.findall(r"^(\d+)\. ", overrides, re.M)] == list(range(1, 8))
