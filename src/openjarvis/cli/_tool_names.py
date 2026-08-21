"""Helpers for resolving CLI tool selections."""

from __future__ import annotations

from typing import Any


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

    return names
