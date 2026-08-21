"""Validation errors for the Second Brain governance layer."""

from __future__ import annotations


class SecondBrainValidationError(ValueError):
    """Raised when an entry/relationship fails a governance rule.

    Never raised by the storage layer (``store.py``) -- only by
    ``SecondBrainService``, which is the sole enforcement point so a
    caller can never bypass governance by going around the service.
    """


__all__ = ["SecondBrainValidationError"]
