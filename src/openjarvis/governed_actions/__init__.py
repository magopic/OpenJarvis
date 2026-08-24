"""MAIA Governed Actions V1 (FASE 4P.3).

PROPOSAL -> APPROVAL -> AUTHORIZED EXECUTION -> AUDITABLE RESULT.

The model may propose, explain, and prepare parameters. The model may
never approve on its own behalf, alter approved parameters, or claim
execution occurred without real runtime evidence. Approval is detected
and applied entirely by the runtime (orchestrator.py), never by a
model-callable tool.
"""

from openjarvis.governed_actions.types import GovernedAction

__all__ = ["GovernedAction"]
