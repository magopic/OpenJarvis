"""FASE 4Q.4 -- the one small, genuinely missing primitive the STEP 1
audit found: a deterministic, explainable attention classification and
priority ordering over Notification Runtime V1's already-persisted
Notification objects.

Deliberately NOT a new subsystem: no new storage, no new service, no new
scheduler, no new memory. Pure computation over what
MonitorService.list_notifications() already returns. This module has no
knowledge of any specific business domain, KPI, or threshold -- ordering
is derived purely from the SAME structural fields FASE 4Q.3 already
persists (severity, transition, status, created_at), never from content
inference (mirrors agents/operational_evidence.py's own "structural,
never content-inferred" discipline).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from openjarvis.monitoring.types import (
    STATUS_ACKNOWLEDGED,
    TRANSITION_NEW,
    TRANSITION_REOPENED,
    TRANSITION_RESOLVED,
    Notification,
)

CLASS_ATTENTION_ITEM = "ATTENTION_ITEM"
CLASS_ACKNOWLEDGED = "ACKNOWLEDGED"
CLASS_INFORMATIONAL = "INFORMATIONAL"

# STEP 4: ordinal ranks, not invented scores -- each is a real, existing
# field's own natural ordering (proactive_insight.py's own severity
# ladder; the dedup transition vocabulary FASE 4P.2 already defines).
_SEVERITY_RANK = {"CRITICAL": 4, "WARNING": 3, "ATTENTION": 2, "INFO": 1}
_TRANSITION_RANK = {TRANSITION_REOPENED: 3, TRANSITION_NEW: 3, "CHANGED": 2, TRANSITION_RESOLVED: 0}


def classify_notification(n: Notification) -> str:
    """STEP 3's three-way split. Acknowledged wins regardless of
    transition (an acknowledged RESOLVED is still just acknowledged);
    otherwise a RESOLVED transition is informational (the issue is
    gone, not a current problem -- STEP 16 letter H); everything else
    (NEW/CHANGED/REOPENED, not yet acknowledged) is an attention item."""
    if n.status == STATUS_ACKNOWLEDGED:
        return CLASS_ACKNOWLEDGED
    if n.transition == TRANSITION_RESOLVED:
        return CLASS_INFORMATIONAL
    return CLASS_ATTENTION_ITEM


def _priority_reason(n: Notification) -> List[str]:
    """Explicit, human-readable factors -- never an opaque score. This is
    exactly what lets MAIA answer 'Perché questa è la priorità?' with
    real reasons instead of a number nobody can explain."""
    reasons = [f"severity={n.severity or 'unknown'}"]
    if n.transition in (TRANSITION_NEW, TRANSITION_REOPENED):
        reasons.append(f"transition={n.transition} (newly requires review)")
    elif n.transition == "CHANGED":
        reasons.append("transition=CHANGED (situation shifted)")
    reasons.append(f"created_at={n.created_at}")
    return reasons


def _parse_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0.0


def build_attention_summary(notifications: List[Notification]) -> Dict[str, Any]:
    """Groups and orders an already-fetched notification list. Does not
    query anything itself -- the caller (a tool) is responsible for
    fetching a principal-scoped list via MonitorService.list_notifications()
    first; this function never sees or needs a principal, since it only
    ever operates on whatever list it was handed.

    STEP 4: deterministic V1 ordering within ATTENTION_ITEM --
    severity (desc), then transition class (NEW/REOPENED before CHANGED),
    then recency (newest first). No fake mathematical precision -- three
    plain sort keys, each individually explainable via priority_reason.
    """
    attention: List[Dict[str, Any]] = []
    acknowledged: List[Dict[str, Any]] = []
    informational: List[Dict[str, Any]] = []

    for n in notifications:
        klass = classify_notification(n)
        entry = {
            "id": n.id,
            "monitor_id": n.monitor_id,
            "transition": n.transition,
            "severity": n.severity,
            "title": n.title,
            "summary": n.summary,
            "status": n.status,
            "created_at": n.created_at,
            "class": klass,
        }
        if klass == CLASS_ATTENTION_ITEM:
            entry["priority_reason"] = _priority_reason(n)
            attention.append(entry)
        elif klass == CLASS_ACKNOWLEDGED:
            acknowledged.append(entry)
        else:
            informational.append(entry)

    attention.sort(
        key=lambda e: (
            -_SEVERITY_RANK.get(e["severity"] or "", 0),
            -_TRANSITION_RANK.get(e["transition"], 0),
            -_parse_ts(e["created_at"]),
        )
    )
    # Acknowledged/informational: recency only, most recent first -- no
    # priority_reason (they are not being prioritized for action).
    acknowledged.sort(key=lambda e: -_parse_ts(e["created_at"]))
    informational.sort(key=lambda e: -_parse_ts(e["created_at"]))

    return {
        "attention_items": attention,
        "acknowledged": acknowledged,
        "informational": informational,
        "has_attention_items": bool(attention),
    }


__all__ = [
    "CLASS_ATTENTION_ITEM",
    "CLASS_ACKNOWLEDGED",
    "CLASS_INFORMATIONAL",
    "classify_notification",
    "build_attention_summary",
]
