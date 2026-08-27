"""M2.5A -- Document Authority & Supersession V1. Tests A-K (+ required
extras) per the approved implementation contract.

Live-motivated: Phase 0 recon found Document Knowledge had zero
recency/version signal reaching the model at all, and no way to mark
that one ingested document supersedes another -- unlike Second Brain,
which already has an equivalent `superseded_by` mechanism. These tests
drive the real `DocumentKnowledgeService`/tool layer, not mocks.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openjarvis.document_knowledge.service import DocumentKnowledgeService, DocumentSupersessionError
from openjarvis.document_knowledge.types import DocumentRecord
from openjarvis.tools.document_knowledge_tools import DocumentListSourcesTool, DocumentSearchTool


def _doc_id_for(service: DocumentKnowledgeService, filename: str) -> str:
    for d in service.list_documents():
        if d.filename == filename:
            return d.doc_id
    raise AssertionError(f"{filename} not found among ingested documents")


# -- A: legacy document, no supersession metadata -> CURRENT -----------------


def test_a_legacy_document_with_no_supersession_metadata_is_current(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure.md").write_text("AlphaLegacyMarker original procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_id = _doc_id_for(service, "procedure.md")

    record = service.get_document(doc_id)
    assert record.status == "CURRENT"
    assert record.superseded_by_doc_id is None
    assert record.superseded_at is None


# -- B: A superseded by B -----------------------------------------------------


def test_b_document_a_superseded_by_b(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("BravoOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("BravoNewMarker procedure version two.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")

    service.supersede_document(doc_a, doc_b)

    record_a = service.get_document(doc_a)
    assert record_a.status == "SUPERSEDED"
    assert record_a.superseded_by_doc_id == doc_b
    assert record_a.superseded_by_filename == "procedure_b.md"
    assert record_a.superseded_at is not None


# -- C: search result for A warns that B is newer ----------------------------


def test_c_search_result_for_a_warns_that_b_is_newer(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("CharlieOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("CharlieNewMarker procedure version two.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    results = service.search_documents("CharlieOldMarker")
    assert len(results) == 1
    r = results[0]
    assert r.status == "SUPERSEDED"
    assert r.superseded_by_doc_id == doc_b
    assert r.superseded_by_filename == "procedure_b.md"
    assert r.superseded_at is not None

    tool = DocumentSearchTool(service=service)
    result = tool.execute(query="CharlieOldMarker")
    assert result.success
    assert "SUPERSEDED" in result.content
    assert "procedure_b.md" in result.content
    assert result.metadata["results"][0]["status"] == "SUPERSEDED"
    assert result.metadata["results"][0]["superseded_by_filename"] == "procedure_b.md"


# -- D: B remains CURRENT ------------------------------------------------------


def test_d_b_remains_current(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("DeltaOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("DeltaNewMarker procedure version two.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    record_b = service.get_document(doc_b)
    assert record_b.status == "CURRENT"
    assert record_b.superseded_by_doc_id is None

    results = service.search_documents("DeltaNewMarker")
    assert len(results) == 1
    assert results[0].status == "CURRENT"
    assert results[0].superseded_by_doc_id is None


# -- E: list_sources exposes status --------------------------------------------


def test_e_list_sources_exposes_status(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("EchoOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("EchoNewMarker procedure version two.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    tool = DocumentListSourcesTool(service=service)
    result = tool.execute()
    assert result.success
    assert "SUPERSEDED" in result.content
    assert "procedure_b.md" in result.content  # named as the successor in A's line

    by_path = {d["relative_path"]: d for d in result.metadata["documents"]}
    assert by_path["procedure_a.md"]["status"] == "SUPERSEDED"
    assert by_path["procedure_a.md"]["superseded_by_filename"] == "procedure_b.md"
    assert by_path["procedure_b.md"]["status"] == "CURRENT"
    assert by_path["procedure_b.md"]["superseded_by_filename"] is None


# -- F: A remains explicitly retrievable ---------------------------------------


def test_f_a_remains_explicitly_retrievable(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("FoxtrotOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("FoxtrotNewMarker procedure version two.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    # Explicit historical retrieval (by filename filter) still finds A.
    results = service.search_documents("FoxtrotOldMarker", filename="procedure_a.md")
    assert len(results) == 1
    assert results[0].evidence.filename == "procedure_a.md"
    assert results[0].status == "SUPERSEDED"


# -- G: unrelated C unaffected --------------------------------------------------


def test_g_unrelated_c_unaffected(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("GolfOldMarker procedure version one.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("GolfNewMarker procedure version two.", encoding="utf-8")
    (workspace / "unrelated_c.md").write_text("GolfUnrelatedMarker completely separate document.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    doc_c = _doc_id_for(service, "unrelated_c.md")
    record_c = service.get_document(doc_c)
    assert record_c.status == "CURRENT"
    assert record_c.superseded_by_doc_id is None

    results = service.search_documents("GolfUnrelatedMarker")
    assert len(results) == 1
    assert results[0].status == "CURRENT"


# -- H: missing target rejected -------------------------------------------------


def test_h_missing_target_rejected(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("HotelOnlyMarker procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")

    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_a, "maia_documents:ws:does_not_exist.md")

    with pytest.raises(DocumentSupersessionError):
        service.supersede_document("maia_documents:ws:does_not_exist.md", doc_a)


# -- I: self-supersession rejected ----------------------------------------------


def test_i_self_supersession_rejected(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("IndiaSelfMarker procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")

    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_a, doc_a)


# -- J: direct AND indirect cycles rejected --------------------------------------


def test_j_direct_cycle_rejected(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("JulietDirectMarkerA procedure text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("JulietDirectMarkerB procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")

    service.supersede_document(doc_a, doc_b)  # A -> B
    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_b, doc_a)  # B -> A would close a direct 2-cycle


def test_j_indirect_cycle_rejected(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("JulietIndirectMarkerA procedure text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("JulietIndirectMarkerB procedure text.", encoding="utf-8")
    (workspace / "procedure_c.md").write_text("JulietIndirectMarkerC procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    doc_c = _doc_id_for(service, "procedure_c.md")

    service.supersede_document(doc_a, doc_b)  # A -> B
    service.supersede_document(doc_b, doc_c)  # B -> C
    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_c, doc_a)  # C -> A would close A -> B -> C -> A


# -- Additional required tests ---------------------------------------------------


def test_supersession_does_not_delete_chunks(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("KiloChunkMarker procedure text that must survive.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("KiloNewMarker replacement procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    chunk_count_before = service.get_document(doc_a).chunk_count

    service.supersede_document(doc_a, doc_b)

    record_a = service.get_document(doc_a)
    assert record_a.chunk_count == chunk_count_before
    assert record_a.chunk_count > 0
    # The chunk content is still physically retrievable, not tombstoned.
    assert len(service.search_documents("KiloChunkMarker")) == 1


def test_superseded_at_is_populated(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("LimaTimeMarkerA procedure text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("LimaTimeMarkerB procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")

    service.supersede_document(doc_a, doc_b)

    record_a = service.get_document(doc_a)
    assert isinstance(record_a.superseded_at, float)
    assert record_a.superseded_at > 0


def test_ingested_at_is_not_represented_as_effective_date():
    """Static contract check: DocumentRecord has no 'effective_date'
    field (deliberately not added per the approved contract), and the
    tool-facing description explicitly disclaims ingested_at as a
    business-effective date."""
    field_names = {f.name for f in dataclasses.fields(DocumentRecord)}
    assert "effective_date" not in field_names
    assert "source_modified_at" not in field_names

    import openjarvis.tools.document_knowledge_tools as mod

    list_tool = mod.DocumentListSourcesTool.__new__(mod.DocumentListSourcesTool)
    description = list_tool.spec.description
    assert "business-effective date" in description
    assert "Indexed on" in description or "indexed" in description.lower()


def test_no_model_callable_supersession_tool_was_registered():
    """Static + runtime proof: only document_search/document_list_sources
    are registered from this module -- no document_supersede (or any
    other write/authority-mutation) tool exists as a model-callable
    ToolRegistry entry."""
    import importlib

    from openjarvis.core.registry import ToolRegistry
    import openjarvis.tools.document_knowledge_tools as mod

    importlib.reload(mod)

    assert "document_search" in ToolRegistry._entries()
    assert "document_list_sources" in ToolRegistry._entries()
    assert "document_supersede" not in ToolRegistry._entries()
    assert "document_unsupersede" not in ToolRegistry._entries()

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "supersede_document" not in source
    assert "clear_supersession" not in source
    assert "@ToolRegistry.register" in source
    register_count = source.count("@ToolRegistry.register")
    assert register_count == 2  # document_search, document_list_sources -- no third tool


def test_failed_supersession_leaves_state_unchanged(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("MikeUnchangedMarkerA procedure text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("MikeUnchangedMarkerB procedure text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")

    before_a = service.get_document(doc_a)
    before_b = service.get_document(doc_b)
    assert before_a.status == "CURRENT" and before_b.status == "CURRENT"

    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_a, "maia_documents:ws:nonexistent.md")

    with pytest.raises(DocumentSupersessionError):
        service.supersede_document(doc_a, doc_a)

    after_a = service.get_document(doc_a)
    after_b = service.get_document(doc_b)
    assert after_a.status == "CURRENT"
    assert after_a.superseded_by_doc_id is None
    assert after_a.superseded_at is None
    assert after_b.status == "CURRENT"


# ---------------------------------------------------------------------------
# M2.5A -- Orphaned Supersession Repair. Live-reproduced: the successor
# document was deleted from the workspace and swept away by
# `jarvis document ingest`, leaving the predecessor's superseded_by_doc_id
# pointing at a doc_id no longer in FileStateStore. A missing successor
# must never silently restore CURRENT status -- only a deliberate human
# repair (clear_supersession / `jarvis document unsupersede`) does that.
# ---------------------------------------------------------------------------


def test_1_successor_removed_by_ingest_predecessor_stays_superseded_and_flagged(
    service: DocumentKnowledgeService, workspace: Path
):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    (workspace / "procedure_b.md").unlink()
    outcome = service.ingest_now()
    assert outcome.removed == ["procedure_b.md"]

    record_a = service.get_document(doc_a)
    assert record_a.status == "SUPERSEDED"  # never auto-reverts to CURRENT
    assert record_a.successor_missing is True


def test_2_successor_missing_clears_filename_and_content_identity(
    service: DocumentKnowledgeService, workspace: Path
):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA2 predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB2 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)

    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    record_a = service.get_document(doc_a)
    assert record_a.superseded_by_filename is None
    assert record_a.same_content_as_successor is None
    # The stale doc_id itself is preserved (not invented, not cleared) --
    # only a deliberate human repair clears it.
    assert record_a.superseded_by_doc_id == doc_b


def test_3_document_search_renders_missing_successor_warning(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA3 predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB3 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)
    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    tool = DocumentSearchTool(service=service)
    result = tool.execute(query="OrphanMarkerA3")
    assert result.success
    assert "recorded successor is missing from the workspace" in result.content
    assert "CURRENT" not in result.content  # never implies the document is current again
    meta = result.metadata["results"][0]
    assert meta["status"] == "SUPERSEDED"
    assert meta["successor_missing"] is True
    assert meta["superseded_by_filename"] is None
    assert meta["same_content_as_successor"] is None


def test_4_document_list_sources_renders_missing_successor_warning(
    service: DocumentKnowledgeService, workspace: Path
):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA4 predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB4 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)
    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    tool = DocumentListSourcesTool(service=service)
    result = tool.execute()
    assert result.success
    assert "recorded successor is missing from the workspace" in result.content
    by_path = {d["relative_path"]: d for d in result.metadata["documents"]}
    assert by_path["procedure_a.md"]["status"] == "SUPERSEDED"
    assert by_path["procedure_a.md"]["successor_missing"] is True


def test_6_clear_supersession_restores_current(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA6 predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB6 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)
    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    service.clear_supersession(doc_a)

    record_a = service.get_document(doc_a)
    assert record_a.status == "CURRENT"
    assert record_a.superseded_by_doc_id is None
    assert record_a.superseded_at is None
    assert record_a.successor_missing is False
    assert record_a.same_content_as_successor is None


def test_8_unsupersede_unknown_document_fails_state_unchanged(service: DocumentKnowledgeService, workspace: Path):
    from openjarvis.document_knowledge.service import DocumentSupersessionError

    (workspace / "procedure_a.md").write_text("OrphanMarkerA8 predecessor text.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB8 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    service.supersede_document(doc_a, doc_b)
    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    with pytest.raises(DocumentSupersessionError):
        service.clear_supersession("maia_documents:ws:does_not_exist.md")

    record_a = service.get_document(doc_a)
    assert record_a.status == "SUPERSEDED"  # unchanged by the rejected call
    assert record_a.successor_missing is True


def test_9_clear_supersession_deletes_zero_chunks(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure_a.md").write_text("OrphanMarkerA9 predecessor text that must survive.", encoding="utf-8")
    (workspace / "procedure_b.md").write_text("OrphanMarkerB9 successor text.", encoding="utf-8")
    service.ingest_now()
    doc_a = _doc_id_for(service, "procedure_a.md")
    doc_b = _doc_id_for(service, "procedure_b.md")
    chunk_count_before = service.get_document(doc_a).chunk_count
    service.supersede_document(doc_a, doc_b)
    (workspace / "procedure_b.md").unlink()
    service.ingest_now()

    service.clear_supersession(doc_a)

    record_a = service.get_document(doc_a)
    assert record_a.chunk_count == chunk_count_before
    assert record_a.chunk_count > 0
    assert len(service.search_documents("OrphanMarkerA9")) == 1
