"""M3.6A.1 -- what a coverage nudge must not cost the answer.

Both nudges ask the model to reconsider a draft it has already written, and
that reconsideration is valuable: it is what makes an unqueried evidence
family actually get queried. But a live trace showed the price. The first
draft correctly marked a figure as indirect -- carried inside another
period's result rather than fetched for itself -- and the rewrite that
followed the nudge dropped the qualifier, leaving that figure looking
exactly like the ones retrieved directly.

Nothing was fabricated and no number was wrong. What was lost was the
reader's ability to tell two kinds of evidence apart, which is the whole
reason an indirect figure is safe to use.

The static presentation policy could not prevent it: the model had obeyed
that policy moments earlier, in the very same trace. The nudge is a
Role.USER message arriving after the draft, so it carries recency and the
transformation context the system prompt cannot. These tests pin the
invariant to the nudges themselves, and pin equally hard that it did not
turn into "do not rewrite".
"""

from __future__ import annotations

import pytest

from openjarvis.agents.orchestrator import _PRESERVE_EVIDENCE_CLASS


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


INVARIANT = _flat(_PRESERVE_EVIDENCE_CLASS)


def _nudge_sources() -> str:
    """The orchestrator source, so both call sites can be asserted."""
    import inspect

    from openjarvis.agents import orchestrator

    return inspect.getsource(orchestrator)


class TestBothNudgePathsCarryIt:
    def test_the_invariant_is_written_once(self) -> None:
        """One wording, two uses -- the defect survived in whichever path
        was left out, so they must not drift apart."""
        source = _nudge_sources()
        assert source.count("_PRESERVE_EVIDENCE_CLASS = (") == 1
        assert source.count("+ _PRESERVE_EVIDENCE_CLASS") == 2

    def test_the_evidence_coverage_check_carries_it(self) -> None:
        source = _nudge_sources()
        start = source.index("[EVIDENCE COVERAGE CHECK] Before finalizing")
        window = source[start : start + 2500]
        assert "_PRESERVE_EVIDENCE_CLASS" in window

    def test_the_zero_result_recovery_carries_it(self) -> None:
        source = _nudge_sources()
        start = source.index("relevant was found -- do not guess.")
        window = source[start : start + 400]
        assert "_PRESERVE_EVIDENCE_CLASS" in window


class TestRewritingRemainsAllowed:
    """The nudge exists to make the model reconsider. An invariant that
    froze the draft would break the thing it is attached to."""

    def test_restructuring_is_explicitly_permitted(self) -> None:
        assert "however you restructure the answer" in INVARIANT
        for allowed in ("expanding", "reordering", "shortening", "rewriting"):
            assert allowed in INVARIANT, allowed
        assert "is fine" in INVARIANT

    def test_it_never_says_to_freeze_or_copy_the_previous_answer(self) -> None:
        for forbidden in (
            "do not rewrite",
            "do not change",
            "keep the answer as is",
            "copy the previous",
            "reproduce the previous",
            "verbatim",
            "unchanged",
        ):
            assert forbidden not in INVARIANT, forbidden

    def test_further_tool_use_is_not_discouraged(self) -> None:
        """The invariant must say nothing that competes with the nudge's own
        instruction to go and check another source."""
        for forbidden in ("do not call", "do not search", "no further", "stop looking"):
            assert forbidden not in INVARIANT, forbidden
        assert "add coverage" in INVARIANT


class TestWhatMustSurviveARewrite:
    @pytest.mark.parametrize(
        "qualification", ["indirect", "inferred", "provisional", "uncertain"]
    )
    def test_a_marked_value_stays_marked(self, qualification: str) -> None:
        assert qualification in INVARIANT, qualification
        assert "must still be marked that way afterwards" in INVARIANT

    def test_an_attribute_may_not_spread_across_results(self) -> None:
        """The scope half: an attribute scoped to one result must not end up
        describing everything presented beside it."""
        assert "an attribute you scoped to one result" in INVARIANT
        assert "must not end up spread across others" in INVARIANT

    def test_an_interpretation_may_not_become_a_fact(self) -> None:
        assert "an interpretation must not resurface as a fact" in INVARIANT

    def test_the_direction_of_the_constraint_is_one_way(self) -> None:
        """Only strengthening is forbidden. A rewrite may still soften or
        drop a claim the new evidence undermines."""
        assert "quietly upgrading a claim is not" in INVARIANT
        assert "without strengthening what you already said" in INVARIANT


class TestItStaysGeneric:
    @pytest.mark.parametrize(
        "term",
        [
            "oee",
            "maffei",
            "produzione",
            "kpi",
            "period",
            "record count",
            "recordcount",
            "completeness",
            "ops",
        ],
    )
    def test_no_domain_or_field_vocabulary(self, term: str) -> None:
        """This ships in the universal orchestrator and fires for every
        agent, every tool family and every domain.

        What is banned is field and business vocabulary, not ordinary
        English. The bare word "record" was on this list until the invariant
        needed it in its plain sense -- the conversation's record of what
        happened -- so the ban is on the metadata sense that could leak a
        schema into a universal rule, which is what it was always for.
        """
        assert term not in INVARIANT, term

    def test_it_speaks_about_claims_and_values(self) -> None:
        for neutral in ("value", "claim", "evidence", "answer"):
            assert neutral in INVARIANT, neutral


class TestExistingCoverageBehaviourIntact:
    """The nudges' own instructions must be untouched by the addition."""

    def test_the_coverage_check_still_asks_what_it_asked(self) -> None:
        source = _nudge_sources()
        for clause in (
            "[EVIDENCE COVERAGE CHECK] Before finalizing",
            "have not been checked yet",
            "Does your answer so far genuinely address every ",
            "do not guess at what it would say",
        ):
            assert clause in source, clause

    def test_the_zero_result_recovery_still_asks_what_it_asked(self) -> None:
        source = _nudge_sources()
        for clause in (
            "broader or differently-worded search could plausibly ",
            "try that once now before finalizing",
            "state plainly that nothing ",
            "relevant was found -- do not guess",
        ):
            assert clause in source, clause

    def test_both_are_still_delivered_as_user_messages(self) -> None:
        source = _nudge_sources()
        assert source.count("messages.append(Message(role=Role.USER, content=nudge))") == 2

    def test_the_families_and_their_bounds_are_unchanged(self) -> None:
        from openjarvis.agents.orchestrator import _COVERAGE_FAMILIES

        assert set(_COVERAGE_FAMILIES) == {
            "historical experience (Second Brain)",
            "document evidence (Document Knowledge)",
        }
        source = _nudge_sources()
        assert "coverage_nudge_used = True" in source
        assert "zero_result_retried_families" in source


# -- the record of what happened, not only what was claimed ---------------
#
# The first version of this invariant protected epistemic state, and in the
# next trace it held: an indirect figure stayed marked, an attribute stayed
# scoped. The same rewrite still went wrong twice, in the one area the
# invariant said nothing about.
#
# It described four blocked requests -- the conversation carried the literal
# reason, "exceeded poll budget" -- as the system hitting a limit on
# simultaneous calls, which never happened. And having been told by the
# nudge that two evidence families had not been checked yet, it wrote that
# nothing exists in them: an assertion about the contents of sources it had
# just been told were unread, in the same breath as admitting they were
# unread.
#
# Both states were present, accurate and persistent in the conversation. The
# rewrite changed them anyway, so the invariant now covers the record of how
# a result was obtained as well as what it claims.


class TestRetrievalStateSurvivesRewriting:
    def test_the_record_of_what_happened_is_named(self) -> None:
        assert "the same holds for the record of what actually happened" in INVARIANT
        # And it points at the conversation as the authority for it.
        assert "this conversation already shows which lookups ran" in INVARIANT

    def test_rewriting_the_record_is_the_thing_forbidden(self) -> None:
        assert "may say it better but may not say it differently" in INVARIANT

    def test_unsearched_is_not_searched_and_empty(self) -> None:
        assert "not looked at is not the same as looked at and empty" in INVARIANT

    def test_a_successful_empty_search_is_not_a_missing_one(self) -> None:
        assert "a search that succeeded and found nothing is not a search that never happened" in INVARIANT

    def test_blocked_before_running_is_not_executed_and_failed(self) -> None:
        assert "a request stopped before it ran is not a call that ran and failed" in INVARIANT

    def test_the_recorded_reason_is_the_only_reason(self) -> None:
        """The exact mutation: an accurate 'poll budget' became a
        likelier-sounding 'concurrency limit'."""
        assert "the reason it was stopped is whatever the record says it was" in INVARIANT
        assert "not a likelier-sounding one" in INVARIANT

    def test_it_is_stated_in_terms_of_states_not_mechanisms(self) -> None:
        """Domain- and implementation-neutral: no guard, budget or limit is
        named, so the rule holds for any runtime that records such states."""
        for implementation in ("loop guard", "poll_budget", "loopguard", "timeout=", "concurrency limit"):
            assert implementation not in INVARIANT, implementation


class TestItDoesNotDemandNarration:
    """The opposite failure: an invariant that made the model recite its own
    plumbing would fight the presentation policy and make answers worse."""

    def test_machinery_need_not_be_described(self) -> None:
        assert "none of this obliges you to describe machinery" in INVARIANT
        assert "does not matter to the answer is better left out" in INVARIANT

    def test_an_unconsulted_source_may_simply_go_unmentioned(self) -> None:
        assert "can simply go unmentioned" in INVARIANT

    def test_no_defensive_call_is_required(self) -> None:
        """A source may be left unread; the nudge already permits finalising
        without it, and this must not quietly withdraw that."""
        assert "never required to call one to be allowed to finish" in INVARIANT

    def test_the_constraint_binds_only_once_something_is_mentioned(self) -> None:
        assert "once you do mention any of it, say only what the record supports" in INVARIANT

    def test_naming_a_source_as_unconsulted_stays_allowed(self) -> None:
        """Silence and honesty are both acceptable; only the false negative
        is not."""
        assert "may be named as unconsulted" in INVARIANT
        assert "never reported as holding nothing" in INVARIANT


class TestTheEarlierProtectionsAreStillThere:
    @pytest.mark.parametrize(
        "clause",
        [
            "keep the evidence qualifications it already carries",
            "must still be marked that way afterwards",
            "an attribute you scoped to one result",
            "must not end up spread across others",
            "an interpretation must not resurface as a fact",
            "add coverage without strengthening what you already said",
        ],
    )
    def test_epistemic_state_protection_survived_the_extension(self, clause: str) -> None:
        assert clause in INVARIANT, clause

    @pytest.mark.parametrize(
        "qualification", ["indirect", "inferred", "provisional", "uncertain"]
    )
    def test_the_four_qualifications_survived(self, qualification: str) -> None:
        assert qualification in INVARIANT, qualification

    def test_rewriting_is_still_permitted_after_the_extension(self) -> None:
        assert "however you restructure the answer" in INVARIANT
        for allowed in ("expanding", "reordering", "shortening", "rewriting"):
            assert allowed in INVARIANT, allowed
        for forbidden in ("do not rewrite", "verbatim", "unchanged", "copy the previous"):
            assert forbidden not in INVARIANT, forbidden

    def test_one_invariant_still_serves_both_paths(self) -> None:
        source = _nudge_sources()
        assert source.count("_PRESERVE_EVIDENCE_CLASS = (") == 1
        assert source.count("+ _PRESERVE_EVIDENCE_CLASS") == 2
