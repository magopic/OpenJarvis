"""Helpers for resolving CLI tool selections."""

from __future__ import annotations

from typing import Any

# FASE 4N.2: Second Brain tools are always safe to auto-enable -- unlike
# OPS Bridge tools (which need a live governance check against the
# Registry's trust_status), every Second Brain rule is enforced inside
# SecondBrainService itself, so there is nothing external to verify here.
# Listed explicitly (not discovered from the ToolRegistry at large) so
# auto-enable can never accidentally pull in some future unrelated tool.
_SECOND_BRAIN_TOOL_IDS = (
    "second_brain_search",
    "second_brain_get",
    "second_brain_propose_entry",
    "second_brain_confirm_entry",
    "second_brain_link",
    "second_brain_archive",
    "second_brain_find_related_experiences",  # FASE 4N.4
)

# FASE 4O.6: Document Knowledge tools are safe to auto-enable for the same
# reason Second Brain tools are -- every authorization/confinement rule
# (workspace root, sensitive-file blocking) is enforced inside
# DocumentKnowledgeService/the workspace module itself, nothing external
# to verify here. This phase's audit found Document Knowledge was the one
# governed tool family NOT unioned in below (Second Brain always was),
# meaning a default MAIA session could never actually reach it -- a
# registration gap, not a routing/scoring gap. Fixed here, mirroring the
# Second Brain pattern exactly.
_DOCUMENT_KNOWLEDGE_TOOL_IDS = (
    "document_search",
    "document_list_sources",
)


def _normalize_tool_names(value: Any) -> list[str]:
    """Normalize configured tool names from string or list-like values."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        names = []
        for item in value:
            text = str(item).strip()
            if text:
                names.append(text)
        return names

    text = str(value).strip()
    return [text] if text else []


def resolve_tool_names(
    cli_value: str | None,
    *configured_values: Any,
) -> list[str]:
    """Resolve tool names, preferring explicit CLI values over config fallbacks.

    An explicit ``--tools`` value is returned exactly as given -- this is
    what evals/benchmarks rely on for tight sandboxing. When no explicit
    value is passed, OPS Bridge tools that have passed governance for
    auto-enable (see ``ops_bridge_generic.get_auto_enabled_ops_tool_ids``)
    are unioned into the config-derived list, mirroring
    ``SystemBuilder._resolve_tools``. On a non-MAIA install this function
    returns an empty list, so behavior is unchanged there.
    """
    cli_names = _normalize_tool_names(cli_value)
    if cli_names:
        return cli_names

    names: list[str] = []
    for configured in configured_values:
        parsed = _normalize_tool_names(configured)
        if parsed:
            names = parsed
            break

    try:
        from openjarvis.tools.ops_bridge_generic import get_auto_enabled_ops_tool_ids

        for auto_id in get_auto_enabled_ops_tool_ids():
            if auto_id not in names:
                names.append(auto_id)
    except Exception:
        pass

    for sb_id in _SECOND_BRAIN_TOOL_IDS:
        if sb_id not in names:
            names.append(sb_id)

    for dk_id in _DOCUMENT_KNOWLEDGE_TOOL_IDS:
        if dk_id not in names:
            names.append(dk_id)

    return names
