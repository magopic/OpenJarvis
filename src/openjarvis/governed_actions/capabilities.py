"""FASE 4P.3 STEP 6 -- the execution-capability allowlist.

No reflection, no arbitrary function names, no shell, no Python
execution, no generic execute_tool(name, args). A capability is
executable only if explicitly registered here with a schema, a risk
class, and a handler -- the SAME closed-allowlist discipline
`tools/maia_manage.py` already established for the gateway (FASE 4P.2A),
applied one layer deeper (to actual side-effecting execution, not just
tool routing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from openjarvis.governed_actions.types import RISK_HIGH, RISK_LOW, RISK_MEDIUM, RISK_PROHIBITED


@dataclass(frozen=True)
class CapabilityDefinition:
    """STEP 6: everything the governance layer needs to know about one
    executable capability, fixed at registration time -- never inferred
    or overridden at request time."""

    name: str
    description: str
    argument_schema: Dict[str, str]  # {field_name: "str"|"int"|"float"|"bool"}
    required_arguments: List[str]
    risk_class: str
    requires_confirmation: bool = True
    idempotent: bool = False
    timeout_seconds: float = 10.0
    rollback_capability: Optional[str] = None
    handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


_REGISTRY: Dict[str, CapabilityDefinition] = {}


def register_capability(definition: CapabilityDefinition) -> None:
    _REGISTRY[definition.name] = definition


def get_capability(name: str) -> Optional[CapabilityDefinition]:
    return _REGISTRY.get(name)


def list_capabilities() -> List[CapabilityDefinition]:
    return list(_REGISTRY.values())


def validate_arguments(cap: CapabilityDefinition, arguments: Dict[str, Any]) -> Optional[str]:
    """Returns an error string if arguments don't satisfy the
    capability's schema, else None. Simple type-name checking -- no
    arbitrary code, no eval, no reflection beyond `type(value).__name__`."""
    _TYPE_MAP = {"str": str, "int": int, "float": (int, float), "bool": bool}
    for req in cap.required_arguments:
        if req not in arguments:
            return f"Missing required argument: {req!r}"
    for key, value in arguments.items():
        if key not in cap.argument_schema:
            return f"Unknown argument for capability {cap.name!r}: {key!r}"
        expected = _TYPE_MAP.get(cap.argument_schema[key])
        if expected is not None and not isinstance(value, expected):
            return f"Argument {key!r} must be of type {cap.argument_schema[key]!r}"
    return None


# ---------------------------------------------------------------------------
# STEP 8: the ONE synthetic, harmless, local, reversible capability used to
# certify the engine. Never touches MEMORY.md, Second Brain, Document
# Knowledge, or any business file -- writes one timestamped line to a
# dedicated, clearly-synthetic marker file, deterministic and disposable
# (delete the file to fully reverse every effect of every test run).
# ---------------------------------------------------------------------------


def _default_test_notes_path() -> Path:
    from openjarvis.core.paths import get_config_dir

    return Path(get_config_dir()) / "governed_actions_test_notes.txt"


def make_test_write_note_handler(notes_path: Optional[Path] = None) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    path = notes_path or _default_test_notes_path()

    def _handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        from datetime import datetime, timezone

        note = arguments["note"]
        path.parent.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now(timezone.utc).isoformat()}] {note}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        return {"written": True, "note": note, "path": str(path)}

    return _handler


def register_default_capabilities(notes_path: Optional[Path] = None) -> None:
    """Registers the V1 capability set: one real, executable, LOW-risk
    test capability, plus MEDIUM/HIGH/PROHIBITED entries with NO handler
    (STEP 7: modeled/tested, never connected) so the risk-gating logic
    itself is exercisable without ever being able to actually run them."""
    register_capability(
        CapabilityDefinition(
            name="maia_test_write_note",
            description=(
                "STEP 8 synthetic test capability -- writes one timestamped "
                "line to an isolated local marker file. No network, no "
                "business data, fully reversible (delete the file)."
            ),
            argument_schema={"note": "str"},
            required_arguments=["note"],
            risk_class=RISK_LOW,
            requires_confirmation=True,
            idempotent=False,
            timeout_seconds=5.0,
            handler=make_test_write_note_handler(notes_path),
        )
    )
    register_capability(
        CapabilityDefinition(
            name="maia_test_medium_capability",
            description="STEP 7 risk-gating test fixture -- MEDIUM risk, never actually executes in V1.",
            argument_schema={"value": "str"},
            required_arguments=["value"],
            risk_class=RISK_MEDIUM,
            handler=None,
        )
    )
    register_capability(
        CapabilityDefinition(
            name="maia_test_high_capability",
            description="STEP 7 risk-gating test fixture -- HIGH risk, never actually executes in V1.",
            argument_schema={"value": "str"},
            required_arguments=["value"],
            risk_class=RISK_HIGH,
            handler=None,
        )
    )
    register_capability(
        CapabilityDefinition(
            name="maia_test_prohibited_capability",
            description="STEP 7 risk-gating test fixture -- PROHIBITED, never executes regardless of anything.",
            argument_schema={"value": "str"},
            required_arguments=["value"],
            risk_class=RISK_PROHIBITED,
            handler=None,
        )
    )


__all__ = [
    "CapabilityDefinition",
    "register_capability",
    "get_capability",
    "list_capabilities",
    "validate_arguments",
    "make_test_write_note_handler",
    "register_default_capabilities",
]
