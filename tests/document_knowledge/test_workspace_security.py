"""FASE 4O.5 SECURITY -- the 10 required scenarios, isolated per test."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openjarvis.document_knowledge.connector import LocalDocumentsConnector
from openjarvis.document_knowledge.service import DocumentKnowledgeConfig, DocumentKnowledgeService
from openjarvis.document_knowledge.workspace import DocumentAccessError, ensure_workspace_root, safe_resolve


# -- 1: allowed file inside workspace ---------------------------------------


def test_1_allowed_file_inside_workspace(workspace: Path):
    (workspace / "ok.txt").write_text("fine", encoding="utf-8")
    resolved = safe_resolve(workspace, Path("ok.txt"))
    assert resolved == (workspace / "ok.txt").resolve()


# -- 2: ../ traversal attempt ------------------------------------------------


def test_2_traversal_attempt_blocked(tmp_path: Path, workspace: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(DocumentAccessError):
        safe_resolve(workspace, Path("../outside/secret.txt"))


# -- 3: absolute external path -----------------------------------------------


def test_3_absolute_external_path_blocked(tmp_path: Path, workspace: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    with pytest.raises(DocumentAccessError):
        safe_resolve(workspace, secret)


# -- 4: symlink/junction escaping workspace ----------------------------------


def test_4_junction_escape_blocked(tmp_path: Path, workspace: Path):
    """Windows junction point (doesn't require elevated symlink privilege)
    as the escape vector -- same resolve()+containment defense that
    blocks traversal/absolute paths also follows and blocks this."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "escape_junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True
    )
    if result.returncode != 0 or not link.exists():
        pytest.skip("could not create a junction point on this system")
    with pytest.raises(DocumentAccessError):
        safe_resolve(workspace, Path("escape_junction/secret.txt"))


# -- 5: unsupported file ------------------------------------------------------


def test_5_unsupported_file_skipped(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "image.png").write_bytes(b"\x89PNG fake binary")
    outcome = service.ingest_now()
    assert "image.png" in outcome.skipped_unsupported
    assert outcome.added == []


# -- 6: malformed document ----------------------------------------------------


def test_6_malformed_pdf_reports_error_not_crash(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "broken.pdf").write_bytes(b"not a real pdf, just garbage bytes")
    outcome = service.ingest_now()  # must not raise
    assert "broken.pdf" in outcome.errors


# -- 7/8: changed source ------------------------------------------------------


def test_7_8_modified_document_replaces_not_accumulates(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("Original content version Alpha.", encoding="utf-8")
    outcome1 = service.ingest_now()
    assert outcome1.added == ["doc.txt"]

    (workspace / "doc.txt").write_text("Modified content version Beta entirely different.", encoding="utf-8")
    outcome2 = service.ingest_now()
    assert outcome2.updated == ["doc.txt"]
    assert outcome2.added == []

    found_new = service.search_documents("version Beta")
    found_old = service.search_documents("version Alpha")
    assert len(found_new) == 1
    assert found_old == []  # old content must not still be retrievable


# -- 9: deleted source ---------------------------------------------------------


def test_9_deleted_document_removed_from_index(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("UniqueDeletionMarkerContent here.", encoding="utf-8")
    service.ingest_now()
    assert service.search_documents("UniqueDeletionMarkerContent")

    (workspace / "doc.txt").unlink()
    outcome = service.ingest_now()
    assert outcome.removed == ["doc.txt"]
    assert service.search_documents("UniqueDeletionMarkerContent") == []
    assert service.list_documents() == []


# -- duplicate source (part of 7/8/9 family, explicit case) -------------------


def test_duplicate_ingestion_is_idempotent(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("Stable unchanged content.", encoding="utf-8")
    outcome1 = service.ingest_now()
    outcome2 = service.ingest_now()
    outcome3 = service.ingest_now()
    assert outcome1.added == ["doc.txt"]
    assert outcome2.unchanged == 1 and outcome2.added == []
    assert outcome3.unchanged == 1 and outcome3.added == []
    # No duplicate chunks accumulated across three sweeps.
    docs = service.list_documents()
    assert len(docs) == 1
    assert docs[0].chunk_count == 1


# -- 10: unauthorized workspace/root -------------------------------------------


def test_10_workspace_root_that_is_a_file_fails_closed(tmp_path: Path):
    not_a_dir = tmp_path / "not_a_directory"
    not_a_dir.write_text("I am a file, not a directory", encoding="utf-8")
    with pytest.raises(DocumentAccessError):
        ensure_workspace_root(not_a_dir)


def test_10_connector_yields_nothing_when_disconnected():
    connector = LocalDocumentsConnector(workspace_root=None)
    assert connector.is_connected() is False
    assert list(connector.sync()) == []


def test_sensitive_file_never_ingested(service: DocumentKnowledgeService, workspace: Path):
    (workspace / ".env").write_text("SECRET_API_KEY=do-not-ingest-me", encoding="utf-8")
    outcome = service.ingest_now()
    assert ".env" in outcome.skipped_sensitive
    assert service.search_documents("SECRET_API_KEY") == []
