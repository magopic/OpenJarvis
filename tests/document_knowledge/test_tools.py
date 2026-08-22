"""Tool-layer tests + static boundary checks (FASE 4O.5)."""

from __future__ import annotations

from pathlib import Path

from openjarvis.document_knowledge.service import DocumentKnowledgeService
from openjarvis.tools.document_knowledge_tools import DocumentListSourcesTool, DocumentSearchTool


def test_document_search_tool_auto_discovered():
    """The project's own autouse fixture (``tests/conftest.py``) clears
    every registry before each test for isolation -- module import-time
    ``@ToolRegistry.register`` side effects only ran once, at first
    import, so re-importing an already-cached module here would find an
    empty registry. ``importlib.reload`` re-runs the decorators, matching
    the pattern already established in ``tests/server/test_middleware.py``."""
    import importlib

    from openjarvis.core.registry import ToolRegistry
    import openjarvis.tools.document_knowledge_tools as mod

    importlib.reload(mod)

    assert "document_search" in ToolRegistry._entries()
    assert "document_list_sources" in ToolRegistry._entries()


def test_document_search_tool_execute(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "procedure.md").write_text(
        "## Maintenance\n\nPapaToolMarker inspection every 45 days.", encoding="utf-8"
    )
    service.ingest_now()

    tool = DocumentSearchTool(service=service)
    result = tool.execute(query="PapaToolMarker")
    assert result.success
    assert "procedure.md" in result.content
    assert result.metadata["num_results"] == 1
    assert result.metadata["results"][0]["citation"] == 'procedure.md, section "Maintenance"'


def test_document_search_tool_no_results_is_honest_not_fabricated(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "doc.txt").write_text("Some unrelated content.", encoding="utf-8")
    service.ingest_now()
    tool = DocumentSearchTool(service=service)
    result = tool.execute(query="NothingMatchesThisQueryAtAll")
    assert result.success
    assert result.metadata["num_results"] == 0
    assert "No matching" in result.content


def test_document_list_sources_tool(service: DocumentKnowledgeService, workspace: Path):
    (workspace / "a.txt").write_text("content a", encoding="utf-8")
    (workspace / "b.md").write_text("content b", encoding="utf-8")
    service.ingest_now()
    tool = DocumentListSourcesTool(service=service)
    result = tool.execute()
    assert result.success
    assert result.metadata["num_documents"] == 2


def test_document_search_tool_requires_query(service: DocumentKnowledgeService):
    tool = DocumentSearchTool(service=service)
    result = tool.execute(query="")
    assert result.success is False


# -- static boundary checks (STEP M / DO NOT create Second Brain entries / OPS coupling) ------


def _package_files():
    import openjarvis.document_knowledge as pkg

    pkg_dir = Path(pkg.__file__).parent
    return list(pkg_dir.glob("*.py")) + [
        Path(__import__("openjarvis.tools.document_knowledge_tools", fromlist=["x"]).__file__)
    ]


def _import_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip().startswith(("import ", "from "))]


def test_no_second_brain_write_path_exists_in_source():
    """Static proof, not just runtime absence-of-calls: nothing in this
    package IMPORTS anything that could create/propose/confirm a
    SecondBrainEntry (checked as import-statement patterns, not bare
    substrings -- the docstrings deliberately *mention* Second Brain in
    prose to explain the boundary, which isn't itself a coupling)."""
    forbidden = ["second_brain.service", "second_brain.store", "SecondBrainEntry", "propose_entry", "confirm_entry"]
    for path in _package_files():
        for line in _import_lines(path):
            for term in forbidden:
                assert term not in line, f"{path} unexpectedly imports something matching {term}: {line}"


def test_no_ops_coupling_exists_in_source():
    """Static proof that document ingestion/retrieval has no IMPORT path
    to any OPS ONE / OPS Bridge module -- it cannot recompute or override
    a certified KPI because it has no code path to OPS at all."""
    forbidden = ["ops_dynamic", "ops_bridge", "OPSBridge", "capability_registry", "CapabilityRegistry"]
    for path in _package_files():
        for line in _import_lines(path):
            for term in forbidden:
                assert term not in line, f"{path} unexpectedly imports something matching {term}: {line}"
