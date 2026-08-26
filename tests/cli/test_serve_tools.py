"""Regression tests for tool selection during ``jarvis serve`` startup."""

from __future__ import annotations

import pytest

from openjarvis.cli.serve import _resolve_allowed_tools
from openjarvis.core.config import JarvisConfig
from openjarvis.tools.maia_family_tools import MAIA_FAMILY_TOOL_ID_GROUPS

_ALL_MAIA_FAMILY_IDS = {tid for group in MAIA_FAMILY_TOOL_ID_GROUPS for tid in group}


@pytest.mark.parametrize(
    "configured",
    [
        "code_interpreter,file_read",
        ["code_interpreter", "file_read"],
    ],
)
def test_tools_enabled_is_used_by_serve(configured):
    config = JarvisConfig()
    config.tools.enabled = configured

    allowed, explicit = _resolve_allowed_tools(config)

    assert {"code_interpreter", "file_read"} <= allowed
    assert explicit is True


def test_tools_enabled_takes_precedence_over_legacy_agent_tools():
    config = JarvisConfig()
    config.tools.enabled = "file_read"
    config.agent.tools = "calculator"

    allowed, explicit = _resolve_allowed_tools(config)

    assert "file_read" in allowed
    assert "calculator" not in allowed
    assert explicit is True


def test_agent_tools_remains_a_backward_compatible_fallback():
    config = JarvisConfig()
    config.agent.tools = "file_read"

    allowed, explicit = _resolve_allowed_tools(config)

    assert "file_read" in allowed
    assert explicit is True


def test_serve_defaults_tools_when_no_selection_is_configured():
    allowed, explicit = _resolve_allowed_tools(JarvisConfig())

    assert {"think", "calculator", "web_search"} <= allowed
    assert explicit is False


# FASE 4Q.5: server-mode MAIA tool parity with `jarvis chat`. Server-mode
# conversations must resolve the same MAIA conversational tool families
# CLI already resolves automatically -- previously `_resolve_allowed_tools`
# unioned in only OPS Bridge tools, leaving Second Brain, Document
# Knowledge, Proactive Insight, Monitoring/Daily Attention, maia_manage,
# and Governed Actions unreachable over `/v1/chat/completions`.


def test_server_default_resolves_all_maia_families():
    """A default server-mode session (no explicit tools config) must reach
    every MAIA family CLI reaches automatically -- the FASE 4Q.5 gap."""
    allowed, _ = _resolve_allowed_tools(JarvisConfig())

    assert _ALL_MAIA_FAMILY_IDS <= allowed


def test_server_with_explicit_config_still_gets_maia_families_unioned():
    """Mirrors CLI's own `test_explicit_configured_value_gets_document_knowledge_unioned_in`:
    an explicit config.tools.enabled value still gets MAIA families unioned
    in on top -- only a raw CLI `--tools` flag (which serve has no
    equivalent of) would skip the union."""
    config = JarvisConfig()
    config.tools.enabled = "calculator"

    allowed, _ = _resolve_allowed_tools(config)

    assert "calculator" in allowed
    assert _ALL_MAIA_FAMILY_IDS <= allowed


def test_second_brain_reachable_in_server_mode():
    allowed, _ = _resolve_allowed_tools(JarvisConfig())
    assert "second_brain_search" in allowed
    assert "second_brain_find_related_experiences" in allowed


def test_document_knowledge_reachable_in_server_mode():
    allowed, _ = _resolve_allowed_tools(JarvisConfig())
    assert "document_search" in allowed
    assert "document_list_sources" in allowed


def test_daily_attention_and_monitoring_reachable_in_server_mode():
    allowed, _ = _resolve_allowed_tools(JarvisConfig())
    assert "maia_daily_attention_summary" in allowed
    assert "maia_monitors_list" in allowed
    assert "maia_monitor_create" in allowed
    assert "maia_notifications_list" in allowed


def test_maia_manage_and_governed_actions_reachable_in_server_mode():
    allowed, _ = _resolve_allowed_tools(JarvisConfig())
    assert "maia_manage" in allowed
    assert "maia_actions_list" in allowed
    assert "maia_action_prepare" in allowed


def test_unrelated_generic_tool_not_exposed_merely_by_maia_parity():
    """MAIA family parity must not accidentally widen exposure of unrelated,
    non-default, non-configured generic tools (e.g. shell_exec) -- parity
    means the MAIA contract matches CLI, not "expose everything"."""
    allowed, _ = _resolve_allowed_tools(JarvisConfig())

    assert "shell_exec" not in allowed
    assert "docker_shell_exec" not in allowed
    assert "apply_patch" not in allowed
    assert "git_tool" not in allowed
