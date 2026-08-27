"""Tests for ``jarvis document supersede`` (M2.5A).

CRITICAL SAFETY NOTE: ``document_cmd.py``'s commands construct
``DocumentKnowledgeService()`` with no config, which resolves to the
REAL default workspace (``~/.openjarvis/maia_documents*``) -- the same
databases holding the real M2.3 PDF. Every test here patches
``openjarvis.document_knowledge.service.default_config`` to a
``tmp_path``-backed config BEFORE invoking the CLI, so no test in this
file can ever read or mutate real company data. This mirrors the
patching pattern already used by ``tests/cli/test_add_cmd.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from openjarvis.cli.document_cmd import document_group
from openjarvis.document_knowledge.service import DocumentKnowledgeConfig, DocumentKnowledgeService


def _patched_config(tmp_path: Path) -> DocumentKnowledgeConfig:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return DocumentKnowledgeConfig(
        workspace_id="ws",
        workspace_root=workspace,
        knowledge_db_path=tmp_path / "index.db",
        file_state_db_path=tmp_path / "files.db",
    )


def test_document_supersede_marks_relationship(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("NovemberOldMarker text.", encoding="utf-8")
    (config.workspace_root / "procedure_b.md").write_text("NovemberNewMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        setup_service.close()

        result = CliRunner().invoke(document_group, ["supersede", "procedure_a.md", "--by", "procedure_b.md"])
        assert result.exit_code == 0, result.output
        assert "procedure_a.md" in result.output
        assert "procedure_b.md" in result.output
        assert "Marked superseded" in result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "SUPERSEDED"
        assert docs["procedure_a.md"].superseded_by_filename == "procedure_b.md"
        assert docs["procedure_b.md"].status == "CURRENT"
        assert docs["procedure_a.md"].chunk_count > 0  # chunks not deleted


def test_document_supersede_rejects_missing_target(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("OscarOnlyMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        setup_service.close()

        result = CliRunner().invoke(document_group, ["supersede", "procedure_a.md", "--by", "does_not_exist.md"])
        assert result.exit_code != 0
        assert "Not found" in result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "CURRENT"  # no partial mutation


def test_document_supersede_rejects_self_supersession(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("PapaSelfMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        setup_service.close()

        result = CliRunner().invoke(document_group, ["supersede", "procedure_a.md", "--by", "procedure_a.md"])
        assert result.exit_code != 0
        assert "Rejected" in result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "CURRENT"


def test_document_supersede_accepts_doc_id_form(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("QuebecOldMarker text.", encoding="utf-8")
    (config.workspace_root / "procedure_b.md").write_text("QuebecNewMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        docs_by_name = {d.filename: d.doc_id for d in setup_service.list_documents()}
        setup_service.close()

        result = CliRunner().invoke(
            document_group,
            ["supersede", docs_by_name["procedure_a.md"], "--by", docs_by_name["procedure_b.md"]],
        )
        assert result.exit_code == 0, result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "SUPERSEDED"


def test_document_list_json_includes_status_fields(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("RomeoListMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        setup_service.close()

        result = CliRunner().invoke(document_group, ["list"])
        assert result.exit_code == 0, result.output
        assert '"status"' in result.output
        assert '"superseded_by_doc_id"' in result.output


# -- M2.5A: jarvis document unsupersede (orphaned-supersession repair) -------------------


def test_document_unsupersede_restores_current(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("SierraOldMarker text.", encoding="utf-8")
    (config.workspace_root / "procedure_b.md").write_text("SierraNewMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        doc_a = next(d.doc_id for d in setup_service.list_documents() if d.filename == "procedure_a.md")
        doc_b = next(d.doc_id for d in setup_service.list_documents() if d.filename == "procedure_b.md")
        setup_service.supersede_document(doc_a, doc_b)
        setup_service.close()

        result = CliRunner().invoke(document_group, ["unsupersede", "procedure_a.md"])
        assert result.exit_code == 0, result.output
        assert "procedure_a.md" in result.output
        assert "now CURRENT" in result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "CURRENT"
        assert docs["procedure_a.md"].superseded_by_doc_id is None
        assert docs["procedure_a.md"].chunk_count > 0  # chunks untouched


def test_document_unsupersede_repairs_orphaned_reference(tmp_path: Path) -> None:
    """The exact live scenario: successor removed via ingest, predecessor
    left pointing at a dead doc_id -- `unsupersede` is the supported
    repair, restoring CURRENT without touching predecessor chunks."""
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("TangoOldMarker text.", encoding="utf-8")
    (config.workspace_root / "procedure_b.md").write_text("TangoNewMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        doc_a = next(d.doc_id for d in setup_service.list_documents() if d.filename == "procedure_a.md")
        doc_b = next(d.doc_id for d in setup_service.list_documents() if d.filename == "procedure_b.md")
        setup_service.supersede_document(doc_a, doc_b)
        setup_service.close()

        (config.workspace_root / "procedure_b.md").unlink()
        ingest_service = DocumentKnowledgeService()
        outcome = ingest_service.ingest_now()
        ingest_service.close()
        assert outcome.removed == ["procedure_b.md"]

        result = CliRunner().invoke(document_group, ["unsupersede", "procedure_a.md"])
        assert result.exit_code == 0, result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "CURRENT"
        assert docs["procedure_a.md"].successor_missing is False
        assert docs["procedure_a.md"].chunk_count > 0


def test_document_unsupersede_rejects_unknown_document(tmp_path: Path) -> None:
    config = _patched_config(tmp_path)
    (config.workspace_root / "procedure_a.md").write_text("UniformOnlyMarker text.", encoding="utf-8")

    with mock.patch("openjarvis.document_knowledge.service.default_config", return_value=config):
        setup_service = DocumentKnowledgeService()
        setup_service.ingest_now()
        setup_service.close()

        result = CliRunner().invoke(document_group, ["unsupersede", "does_not_exist.md"])
        assert result.exit_code != 0
        assert "Not found" in result.output

        check_service = DocumentKnowledgeService()
        try:
            docs = {d.filename: d for d in check_service.list_documents()}
        finally:
            check_service.close()
        assert docs["procedure_a.md"].status == "CURRENT"  # untouched, no partial mutation
