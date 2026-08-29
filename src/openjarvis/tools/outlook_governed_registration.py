"""FASE 4P.4 -- registers the outlook_send_email capability into the
frozen Governed Actions registry at import time (mirrors how
tools/__init__.py already registers monitoring_tools/proactive_insight_tools
etc. via import-time side effects).

Defense-in-depth default: registration uses SyntheticGraphTransport
(zero network calls, no real send possible) UNLESS
OPENJARVIS_OUTLOOK_REAL_SEND=1 is explicitly set -- which this
environment never has, since FASE 4P.4's own audit confirmed no real
Microsoft credentials exist here. Even with that env var set, a real
send additionally requires real, separately-configured Graph credentials
(RealGraphTransport fails closed with no credentials present) and a
real OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT match -- three independent gates,
not one.
"""

from __future__ import annotations

import os

from openjarvis.governed_actions.outlook_capability import (
    RealGraphTransport,
    SyntheticGraphTransport,
    get_allowed_account,
    register_outlook_capability,
)


def _default_transport():
    if os.environ.get("OPENJARVIS_OUTLOOK_REAL_SEND") == "1":
        return RealGraphTransport()
    # Safe default: no network calls, ever, unless explicitly opted in.
    return SyntheticGraphTransport(account=get_allowed_account() or "unconfigured@example.com")


register_outlook_capability(_default_transport(), allowed_account=get_allowed_account())

__all__: list = []
