"""FASE 4O.5 RETRIEVAL TESTS A-O."""

from __future__ import annotations

from pathlib import Path

from openjarvis.document_knowledge.service import DocumentKnowledgeConfig, DocumentKnowledgeService
from tests.document_knowledge.pdf_fixture import make_synthetic_pdf


# -- A: exact phrase retrieval -------------------------------------------------


def test_a_exact_phrase_retrieval(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.md").write_text("The QuartzFalcon-7 sensor requires calibration.", encoding="utf-8")
    service.ingest_now()
    results = service.search_documents("QuartzFalcon-7 sensor")
    assert len(results) == 1
    assert "QuartzFalcon-7" in results[0].content


# -- B: multi-document retrieval -----------------------------------------------


def test_b_multi_document_retrieval(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc1.md").write_text("SharedTermBravo appears in document one.", encoding="utf-8")
    (workspace / "doc2.txt").write_text("SharedTermBravo also appears in document two.", encoding="utf-8")
    service.ingest_now()
    results = service.search_documents("SharedTermBravo")
    filenames = {r.evidence.filename for r in results}
    assert filenames == {"doc1.md", "doc2.txt"}


# -- C: bounded results ---------------------------------------------------------


def test_c_bounded_results(service: DocumentKnowledgeService, workspace: Path):
    for i in range(20):
        (workspace / f"doc{i}.txt").write_text(f"CommonTermCharlie entry number {i}.", encoding="utf-8")
    service.ingest_now()
    results = service.search_documents("CommonTermCharlie", top_k=5)
    assert len(results) == 5


# -- D: deterministic ordering ----------------------------------------------------


def test_d_deterministic_ordering(service: DocumentKnowledgeService, workspace: Path):
    for i in range(6):
        (workspace / f"doc{i}.txt").write_text(f"DeterministicDelta marker text sample {i}.", encoding="utf-8")
    service.ingest_now()
    order1 = [r.evidence.chunk_id for r in service.search_documents("DeterministicDelta", top_k=10)]
    order2 = [r.evidence.chunk_id for r in service.search_documents("DeterministicDelta", top_k=10)]
    assert order1 == order2


# -- E: provenance survives retrieval ----------------------------------------------


def test_e_provenance_survives_retrieval(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure.md").write_text(
        "## Inspection\n\nEchoProvenanceMarker inspection frequency is 30 days.", encoding="utf-8"
    )
    service.ingest_now()
    results = service.search_documents("EchoProvenanceMarker")
    assert len(results) == 1
    ev = results[0].evidence
    assert ev.filename == "procedure.md"
    assert ev.relative_path == "procedure.md"
    assert ev.content_hash
    assert ev.doc_id
    assert ev.chunk_id
    assert ev.section == "Inspection"


# -- F: PDF page traceability --------------------------------------------------------


def test_f_pdf_page_traceability(service: DocumentKnowledgeService, workspace: Path):
    make_synthetic_pdf(
        [
            "Page one contains FoxtrotPageOneMarker exclusively.",
            "Page two contains FoxtrotPageTwoMarker exclusively.",
        ],
        workspace / "manual.pdf",
    )
    outcome = service.ingest_now()
    assert outcome.added == ["manual.pdf"]

    r1 = service.search_documents("FoxtrotPageOneMarker")
    r2 = service.search_documents("FoxtrotPageTwoMarker")
    assert len(r1) == 1 and r1[0].evidence.page == 1
    assert len(r2) == 1 and r2[0].evidence.page == 2
    assert r1[0].evidence.citation_label() == "manual.pdf, page 1"
    assert r2[0].evidence.citation_label() == "manual.pdf, page 2"


# -- G: duplicate ingestion behavior ---------------------------------------------------


def test_g_duplicate_ingestion_behavior(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("GolfDuplicateMarker stable text.", encoding="utf-8")
    o1 = service.ingest_now()
    o2 = service.ingest_now()
    assert o1.added == ["doc.txt"]
    assert o2.added == [] and o2.unchanged == 1
    results = service.search_documents("GolfDuplicateMarker")
    assert len(results) == 1  # never duplicated


# -- H: modified-document behavior ------------------------------------------------------


def test_h_modified_document_behavior(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("HotelOriginalMarker version one.", encoding="utf-8")
    service.ingest_now()
    (workspace / "doc.txt").write_text("HotelUpdatedMarker version two.", encoding="utf-8")
    outcome = service.ingest_now()
    assert outcome.updated == ["doc.txt"]
    assert service.search_documents("HotelOriginalMarker") == []
    assert len(service.search_documents("HotelUpdatedMarker")) == 1


# -- I: deleted-document behavior --------------------------------------------------------


def test_i_deleted_document_behavior(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("IndiaDeletionMarker content.", encoding="utf-8")
    service.ingest_now()
    (workspace / "doc.txt").unlink()
    outcome = service.ingest_now()
    assert outcome.removed == ["doc.txt"]
    assert service.search_documents("IndiaDeletionMarker") == []


# -- J: authorization isolation -----------------------------------------------------------


def test_j_authorization_isolation_two_workspaces(tmp_path: Path):
    ws_a = tmp_path / "ws_a"
    ws_a.mkdir()
    ws_b = tmp_path / "ws_b"
    ws_b.mkdir()
    (ws_a / "secret_a.txt").write_text("JulietPrivateMarkerAlpha only in workspace A.", encoding="utf-8")
    (ws_b / "public_b.txt").write_text("Unrelated workspace B content.", encoding="utf-8")

    svc_a = DocumentKnowledgeService(
        DocumentKnowledgeConfig(
            workspace_id="ws_a", workspace_root=ws_a,
            knowledge_db_path=tmp_path / "a_index.db", file_state_db_path=tmp_path / "a_files.db",
        )
    )
    svc_b = DocumentKnowledgeService(
        DocumentKnowledgeConfig(
            workspace_id="ws_b", workspace_root=ws_b,
            knowledge_db_path=tmp_path / "b_index.db", file_state_db_path=tmp_path / "b_files.db",
        )
    )
    try:
        svc_a.ingest_now()
        svc_b.ingest_now()
        assert len(svc_a.search_documents("JulietPrivateMarkerAlpha")) == 1
        assert svc_b.search_documents("JulietPrivateMarkerAlpha") == []
    finally:
        svc_a.close()
        svc_b.close()


# -- K: malformed input --------------------------------------------------------------------


def test_k_malformed_input_does_not_crash_search(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("Normal content for Kilo test.", encoding="utf-8")
    service.ingest_now()
    # Hyphens/colons/parens are FTS5 syntax traps if not sanitized.
    for weird_query in ["Kilo-9:test", "((()))", 'weird"quote', "AND OR NOT"]:
        results = service.search_documents(weird_query)  # must not raise
        assert isinstance(results, list)


# -- L: no Second Brain write ----------------------------------------------------------------


def test_l_no_second_brain_write(service: DocumentKnowledgeService, workspace: Path, tmp_path: Path):
    from openjarvis.second_brain.service import SecondBrainService
    from openjarvis.second_brain.store import SecondBrainStore

    sb_store = SecondBrainStore(db_path=tmp_path / "sb_test.db")
    sb = SecondBrainService(store=sb_store)
    try:
        before = len(sb.list_entries(actor="test:isolated", include_archived=True, limit=1000))
        (workspace / "doc.txt").write_text("Lima document ingestion should not touch Second Brain.", encoding="utf-8")
        service.ingest_now()
        service.search_documents("Lima")
        after = len(sb.list_entries(actor="test:isolated", include_archived=True, limit=1000))
        assert before == after == 0
    finally:
        sb.close()


# -- M: no OPS mutation ----------------------------------------------------------------------
# Document ingestion has no import path to, or dependency on, any OPS ONE
# module or tool whatsoever -- verified structurally (no such import exists
# anywhere in document_knowledge/), so there is no runtime call to assert
# against. See test_no_ops_coupling in test_tools.py for the static check.


# -- N: no invented citation/source -----------------------------------------------------------


def test_n_no_invented_citation_for_empty_results(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("Completely unrelated content about NovemberTopic.", encoding="utf-8")
    service.ingest_now()
    results = service.search_documents("QuestionNotInAnyDocumentWhatsoever")
    assert results == []  # never fabricates a result to fill the gap


# -- O: retrieval after process restart -------------------------------------------------------


def test_o_retrieval_survives_service_restart(tmp_path: Path, workspace: Path):
    config = DocumentKnowledgeConfig(
        workspace_id="ws", workspace_root=workspace,
        knowledge_db_path=tmp_path / "index.db", file_state_db_path=tmp_path / "files.db",
    )
    svc1 = DocumentKnowledgeService(config)
    (workspace / "doc.txt").write_text("OscarPersistenceMarker survives restart.", encoding="utf-8")
    svc1.ingest_now()
    svc1.close()

    # Fresh service instance against the SAME db paths -- simulates a
    # process restart (new SQLite connections, no in-memory state carried over).
    svc2 = DocumentKnowledgeService(config)
    try:
        results = svc2.search_documents("OscarPersistenceMarker")
        assert len(results) == 1
        # Re-running ingest against the same unchanged files must also be a no-op.
        outcome = svc2.ingest_now()
        assert outcome.added == [] and outcome.unchanged == 1
    finally:
        svc2.close()
