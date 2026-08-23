"""MAIA Proactive Monitoring V1 (FASE 4P.2).

Periodic, governed, deterministic re-checking of explicitly configured
evidence sources -- reusing the frozen FASE 4O.6 evidence model and the
frozen FASE 4P.1/4P.1A/4P.1B ProactiveReasoningService, deduplicated
against prior state so a notification is created only for something that
actually changed (NEW/materially changed/RESOLVED/REOPENED), never for
every unchanged cycle.

Reuses the existing scheduler (openjarvis.scheduler) for cadence/polling
persistence -- this package does not run its own background thread or
poll loop.
"""

from openjarvis.monitoring.types import (
    MonitorDefinition,
    MonitorRun,
    Notification,
)

__all__ = ["MonitorDefinition", "MonitorRun", "Notification"]
