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

# FASE 4P.1: Proactive Insight tools are safe to auto-enable for the same
# reason -- every governance rule they touch is enforced inside the
# services they call (OPS Bridge governance, Second Brain visibility,
# Document Knowledge workspace confinement); this module adds no new
# authorization surface of its own, and exposes read/detect-only tools,
# never an execution tool. Mirrors the Second Brain/Document Knowledge
# pattern exactly so a default MAIA session can actually reach them.
_PROACTIVE_INSIGHT_TOOL_IDS = (
    "maia_analyze_evidence_for_insights",
    "maia_insights_list",
    "maia_insight_get",
    "maia_action_proposals_list",
    "maia_action_proposal_get",
)

# FASE 4P.2: monitor/notification management tools are configuration, not
# business execution -- creating/enabling a monitor never touches an
# external system, and a monitor cycle itself only ever reaches the same
# already-governed sources maia_analyze_evidence_for_insights does (see
# monitoring/service.py::_collect_evidence). No execution tool exists in
# this set (mirrors _PROACTIVE_INSIGHT_TOOL_IDS's own boundary exactly).
_MONITORING_TOOL_IDS = (
    "maia_monitors_list",
    "maia_monitor_get",
    "maia_monitor_create",
    "maia_monitor_enable",
    "maia_monitor_disable",
    "maia_monitor_run_now",
    "maia_notifications_list",
    "maia_notification_get",
    "maia_notification_acknowledge",
)

# FASE 4P.2A: maia_manage is an ADDITIONAL, consolidated gateway over the
# same insight/monitor/notification operations above (STEP 6 -- the
# individual tools are NOT removed, this is offered alongside them). It
# is a thin router (see tools/maia_manage.py); no new authorization
# surface, no execution capability.
_MAIA_MANAGE_TOOL_IDS = ("maia_manage",)

# FASE 4P.3: the governed-action model-facing surface. Deliberately does
# NOT include any approve/execute tool -- those exist only as
# runtime-only GovernedActionService methods, never as
# @ToolRegistry-registered, model-callable tools (see STEP 11's audit in
# tools/governed_action_tools.py). prepare/request_approval only ever
# advance PROPOSED -> PENDING_APPROVAL; nothing here can move an action
# past that point.
_GOVERNED_ACTION_TOOL_IDS = (
    "maia_actions_list",
    "maia_action_get",
    "maia_action_prepare",
    "maia_action_request_approval",
    "maia_action_reject",
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

    for pi_id in _PROACTIVE_INSIGHT_TOOL_IDS:
        if pi_id not in names:
            names.append(pi_id)

    for mon_id in _MONITORING_TOOL_IDS:
        if mon_id not in names:
            names.append(mon_id)

    for mm_id in _MAIA_MANAGE_TOOL_IDS:
        if mm_id not in names:
            names.append(mm_id)

    for ga_id in _GOVERNED_ACTION_TOOL_IDS:
        if ga_id not in names:
            names.append(ga_id)

    return names
