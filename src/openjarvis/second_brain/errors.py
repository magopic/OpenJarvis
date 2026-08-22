"""Validation errors for the Second Brain governance layer."""

from __future__ import annotations


class SecondBrainValidationError(ValueError):
    """Raised when an entry/relationship fails a governance rule.

    Never raised by the storage layer (``store.py``) -- only by
    ``SecondBrainService``, which is the sole enforcement point so a
    caller can never bypass governance by going around the service.
    """


class SecondBrainAuthorizationError(SecondBrainValidationError):
    """Raised when a caller's ``actor`` cannot access a PRIVATE entry.

    A subclass of ``SecondBrainValidationError`` (not a sibling) so
    existing callers that catch the base class still catch this --
    but tools should catch it specifically when they want to report
    "access denied" distinctly from "bad input".
    """


__all__ = ["SecondBrainAuthorizationError", "SecondBrainValidationError"]
