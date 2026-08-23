"""FASE 4O.6 Multi-Source Reasoning / Evidence Composition V1 tests.

No prior test file existed for ``operational_evidence.py`` (confirmed by
audit) -- this covers both the pre-existing OPS-only behavior (kept
byte-for-byte unchanged) and the new HISTORICAL_EXPERIENCE/
DOCUMENT_EVIDENCE classification/composition this phase adds.
"""

from __future__ import annotations

from openjarvis.agents.operational_evidence import (
    SOURCE_CURRENT_OPERATIONAL_FACT,
    SOURCE_DOCUMENT_EVIDENCE,
    SOURCE_HISTORICAL_EXPERIENCE,
    SOURCE_KNOWLEDGE_DEFINITION,
    build_evidence,
)
from openjarvis.core.types import ToolResult


def ops_fact_result(domain: str = "production", value: float = 42.5, period: str = "2026-08") -> ToolResult:
    return ToolResult(
        tool_name=f"ops_dynamic_{domain}_get_kpi",
        content=str(value),
        metadata={
            "status": "ok",
            "data": {"value": value},
            "source": "live",
            "period": period,
            "period_status": "REAL_DATA",
        },
    )


def ops_knowledge_result(domain: str = "production", trust_status: str = "TRUSTED") -> ToolResult:
    return ToolResult(
        tool_name="ops_dynamic_knowledge_get_kpi_definition",
        content="definition",
        metadata={
            "status": "ok",
            "data": {
                "domain": domain,
                "metric_id": "resa",
                "definition": "Yield percentage.",
                "trust_status": trust_status,
                "provenance": "kpiDefinitionEngine v1",
            },
        },
    )


def second_brain_search_result(entries: list) -> ToolResult:
    return ToolResult(
        tool_name="second_brain_search", content="...", metadata={"num_results": len(entries), "entries": entries}
    )


def second_brain_get_result(entry: dict) -> ToolResult:
    return ToolResult(tool_name="second_brain_get", content="...", metadata=entry)


def second_brain_empty_result() -> ToolResult:
    return ToolResult(
        tool_name="second_brain_find_related_experiences", content="none", metadata={"num_candidates": 0}
    )


def document_search_result(results: list) -> ToolResult:
    return ToolResult(
        tool_name="document_search", content="...", metadata={"num_results": len(results), "results": results}
    )


def document_search_empty_result() -> ToolResult:
    return ToolResult(tool_name="document_search", content="none", metadata={"num_results": 0})


def make_entry(**overrides) -> dict:
    base = {
        "id": "e1", "type": "PROBLEM", "title": "Line stoppage", "summary": "Line 3 stopped unexpectedly.",
        "trust_status": "OBSERVED", "provenance": "operator log", "domains": ["production"], "entities": [],
        "relationships": [], "archived": False, "superseded_by": None,
    }
    base.update(overrides)
    return base


def make_document_hit(**overrides) -> dict:
    base = {
        "citation": "procedure.pdf, page 4", "filename": "procedure.pdf", "relative_path": "procedure.pdf",
        "page": 4, "section": None, "chunk_id": "c1", "doc_id": "d1", "score": 1.0,
        "content": "Inspect the QuantumRelay-9 every 30 days.",
    }
    base.update(overrides)
    return base


# -- pre-existing OPS-only behavior, unchanged ----------------------------------------------


def test_ops_fact_classified_as_current_operational_fact():
    evidence = build_evidence([ops_fact_result()])
    assert len(evidence.facts) == 1
    assert evidence.facts[0].source_class == SOURCE_CURRENT_OPERATIONAL_FACT
    assert evidence.facts[0].trust_status == "TRUSTED"


def test_ops_knowledge_classified_as_knowledge_definition():
    evidence = build_evidence([ops_knowledge_result()])
    assert len(evidence.knowledge) == 1
    assert evidence.knowledge[0].source_class == SOURCE_KNOWLEDGE_DEFINITION


def test_non_ok_ops_status_becomes_limitation_not_fact():
    tr = ToolResult(
        tool_name="ops_dynamic_production_get_kpi", content="n/a",
        metadata={"status": "data_not_available", "reason": "No data for this period."},
    )
    evidence = build_evidence([tr])
    assert evidence.facts == []
    assert any("data_not_available" in lim for lim in evidence.limitations)


def test_unrecognized_tool_result_silently_ignored_not_error():
    tr = ToolResult(tool_name="calculator", content="4", metadata={})
    evidence = build_evidence([tr])
    assert evidence.has_any_evidence() is False


# -- new: Second Brain -> HISTORICAL_EXPERIENCE ----------------------------------------------


def test_second_brain_search_classified_as_historical_experience():
    evidence = build_evidence([second_brain_search_result([make_entry()])])
    assert len(evidence.historical_experience) == 1
    item = evidence.historical_experience[0]
    assert item.source_class == SOURCE_HISTORICAL_EXPERIENCE
    assert "PROBLEM" in item.summary
    assert item.trust_status == "OBSERVED"


def test_second_brain_get_unwrapped_dict_classified():
    evidence = build_evidence([second_brain_get_result(make_entry(id="e2", type="DECISION"))])
    assert len(evidence.historical_experience) == 1
    assert "DECISION" in evidence.historical_experience[0].summary


def test_second_brain_preserves_experience_cycle_type_and_lifecycle():
    entry = make_entry(type="LESSON", archived=True, superseded_by="e99")
    evidence = build_evidence([second_brain_search_result([entry])])
    summary = evidence.historical_experience[0].summary
    assert "LESSON" in summary
    assert "[ARCHIVED]" in summary
    assert "superseded_by=e99" in summary


def test_second_brain_empty_result_becomes_explicit_limitation():
    evidence = build_evidence([second_brain_empty_result()])
    assert evidence.historical_experience == []
    assert any("Second Brain queried" in lim and "no historical precedent" in lim for lim in evidence.limitations)


def test_second_brain_never_contaminates_facts():
    evidence = build_evidence([second_brain_search_result([make_entry()])])
    assert evidence.facts == []
    assert evidence.knowledge == []


# -- new: Document Knowledge -> DOCUMENT_EVIDENCE --------------------------------------------


def test_document_search_classified_as_document_evidence():
    evidence = build_evidence([document_search_result([make_document_hit()])])
    assert len(evidence.document_evidence) == 1
    item = evidence.document_evidence[0]
    assert item.source_class == SOURCE_DOCUMENT_EVIDENCE
    assert item.provenance == "procedure.pdf, page 4"
    assert "QuantumRelay-9" in item.summary


def test_document_search_preserves_page_citation():
    evidence = build_evidence([document_search_result([make_document_hit(citation="manual.pdf, page 7", page=7)])])
    assert evidence.document_evidence[0].provenance == "manual.pdf, page 7"


def test_document_search_empty_result_becomes_explicit_limitation():
    evidence = build_evidence([document_search_empty_result()])
    assert evidence.document_evidence == []
    assert any("Document Knowledge queried" in lim and "no matching source" in lim for lim in evidence.limitations)


def test_document_evidence_never_contaminates_facts():
    evidence = build_evidence([document_search_result([make_document_hit()])])
    assert evidence.facts == []
    assert evidence.document_evidence[0].trust_status is None  # never inherits OPS TRUSTED


# -- multi-source composition ------------------------------------------------------------------


def test_three_source_composition_all_present_and_distinguishable():
    evidence = build_evidence(
        [ops_fact_result(), second_brain_search_result([make_entry()]), document_search_result([make_document_hit()])]
    )
    assert len(evidence.facts) == 1
    assert len(evidence.historical_experience) == 1
    assert len(evidence.document_evidence) == 1
    note = evidence.render_note()
    assert "FACTS -- CURRENT_OPERATIONAL_FACT" in note
    assert "HISTORICAL EXPERIENCE -- HISTORICAL_EXPERIENCE" in note
    assert "DOCUMENT EVIDENCE -- DOCUMENT_EVIDENCE" in note
    assert "PRECEDENCE" in note


def test_render_note_omits_precedence_when_only_facts_present():
    evidence = build_evidence([ops_fact_result()])
    note = evidence.render_note()
    assert "PRECEDENCE" not in note  # nothing to compose against yet


def test_domain_coverage_scoped_to_facts_only_not_historical_or_document():
    """trusted_domains_covered() must stay OPS-fact-scoped -- mixing in
    historical/document items would silently inflate cross-domain
    sufficiency with uncertified sources."""
    evidence = build_evidence(
        [
            ops_fact_result(domain="production"),
            second_brain_search_result([make_entry(domains=["logistics"])]),
            document_search_result([make_document_hit()]),
        ]
    )
    assert evidence.trusted_domains_covered() == {"production"}


def test_has_any_evidence_true_for_historical_or_document_alone():
    assert build_evidence([second_brain_search_result([make_entry()])]).has_any_evidence() is True
    assert build_evidence([document_search_result([make_document_hit()])]).has_any_evidence() is True


def test_insufficient_evidence_all_sources_report_gaps_explicitly():
    """STEP 11: if every source comes back empty, the model gets three
    explicit limitation lines, never silence that could be filled in."""
    tr_ops = ToolResult(
        tool_name="ops_dynamic_production_get_kpi", content="n/a",
        metadata={"status": "data_not_available", "reason": "No data for this period."},
    )
    evidence = build_evidence([tr_ops, second_brain_empty_result(), document_search_empty_result()])
    assert len(evidence.limitations) == 3
    assert evidence.has_any_evidence() is False
