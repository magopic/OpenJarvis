"""FASE 4O.6: Document Knowledge tools must be auto-enabled by default,
mirroring Second Brain -- a registration gap this phase's audit found and
fixed in ``cli/_tool_names.py::resolve_tool_names``."""

from __future__ import annotations

from openjarvis.cli._tool_names import resolve_tool_names


def test_document_knowledge_tools_auto_enabled_when_no_explicit_config():
    names = resolve_tool_names(None)
    assert "document_search" in names
    assert "document_list_sources" in names


def test_second_brain_tools_still_auto_enabled_unchanged():
    names = resolve_tool_names(None)
    assert "second_brain_search" in names
    assert "second_brain_find_related_experiences" in names


def test_explicit_cli_value_returned_verbatim_not_unioned():
    """An explicit --tools value (what evals rely on for tight
    sandboxing) must NOT get Document Knowledge/Second Brain silently
    added -- this behavior predates this phase and must stay unchanged."""
    names = resolve_tool_names("calculator,web_search")
    assert names == ["calculator", "web_search"]
    assert "document_search" not in names


def test_explicit_configured_value_gets_document_knowledge_unioned_in():
    names = resolve_tool_names(None, "calculator")
    assert "calculator" in names
    assert "document_search" in names
    assert "document_list_sources" in names


def test_no_duplicate_if_document_tool_already_configured():
    names = resolve_tool_names(None, "document_search")
    assert names.count("document_search") == 1
