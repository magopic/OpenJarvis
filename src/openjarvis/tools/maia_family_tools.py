"""Canonical MAIA conversational tool families.

Single source of truth for the tool ids that make up each MAIA family,
so every tool-resolution path (CLI ``jarvis chat``/``jarvis ask`` via
``cli/_tool_names.py::resolve_tool_names``, and ``jarvis serve`` via
``cli/serve.py::_resolve_allowed_tools``) unions in the exact same ids
instead of maintaining separate copies. See FASE 4Q.5.
"""

from __future__ import annotations

# FASE 4N.2: Second Brain tools are always safe to auto-enable -- unlike
# OPS Bridge tools (which need a live governance check against the
# Registry's trust_status), every Second Brain rule is enforced inside
# SecondBrainService itself, so there is nothing external to verify here.
# Listed explicitly (not discovered from the ToolRegistry at large) so
# auto-enable can never accidentally pull in some future unrelated tool.
SECOND_BRAIN_TOOL_IDS = (
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
# to verify here.
DOCUMENT_KNOWLEDGE_TOOL_IDS = (
    "document_search",
    "document_list_sources",
)

# FASE 4P.1: Proactive Insight tools are safe to auto-enable for the same
# reason -- every governance rule they touch is enforced inside the
# services they call (OPS Bridge governance, Second Brain visibility,
# Document Knowledge workspace confinement); this module adds no new
# authorization surface of its own, and exposes read/detect-only tools,
# never an execution tool.
PROACTIVE_INSIGHT_TOOL_IDS = (
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
# this set (mirrors PROACTIVE_INSIGHT_TOOL_IDS's own boundary exactly).
MONITORING_TOOL_IDS = (
    "maia_monitors_list",
    "maia_monitor_get",
    "maia_monitor_create",
    "maia_monitor_enable",
    "maia_monitor_disable",
    "maia_monitor_run_now",
    "maia_daily_attention_summary",
    "maia_notifications_list",
    "maia_notifications_unread_count",
    "maia_notification_get",
    "maia_notification_mark_read",
    "maia_notification_acknowledge",
)

# FASE 4P.2A: maia_manage is an ADDITIONAL, consolidated gateway over the
# same insight/monitor/notification operations above -- the individual
# tools are NOT removed, this is offered alongside them. It is a thin
# router (see tools/maia_manage.py); no new authorization surface, no
# execution capability.
MAIA_MANAGE_TOOL_IDS = ("maia_manage",)

# FASE 4P.3: the governed-action model-facing surface. Deliberately does
# NOT include any approve/execute tool -- those exist only as
# runtime-only GovernedActionService methods, never as
# @ToolRegistry-registered, model-callable tools (see
# tools/governed_action_tools.py). prepare/request_approval only ever
# advance PROPOSED -> PENDING_APPROVAL; nothing here can move an action
# past that point.
GOVERNED_ACTION_TOOL_IDS = (
    "maia_actions_list",
    "maia_action_get",
    "maia_action_prepare",
    "maia_action_request_approval",
    "maia_action_reject",
)

# Ordered so every consumer unions the families in the same sequence
# (matters only for output ordering in list-based consumers; set-based
# consumers are order-independent).
MAIA_FAMILY_TOOL_ID_GROUPS: tuple[tuple[str, ...], ...] = (
    SECOND_BRAIN_TOOL_IDS,
    DOCUMENT_KNOWLEDGE_TOOL_IDS,
    PROACTIVE_INSIGHT_TOOL_IDS,
    MONITORING_TOOL_IDS,
    MAIA_MANAGE_TOOL_IDS,
    GOVERNED_ACTION_TOOL_IDS,
)
