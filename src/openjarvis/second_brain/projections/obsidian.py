"""Second Brain -> Markdown -> Obsidian vault projection (FASE 4O.2).

One-way only: SECOND BRAIN is the authoritative source of truth,
OBSIDIAN is a derived, read-only human projection. There is no code
path anywhere in this module that reads a vault file and writes it
back into the Second Brain -- that would be a different connector
(``connectors/obsidian.py``, which already exists and does the
opposite direction: vault -> KnowledgeStore, deliberately not reused
here since mixing the two directions in one component would blur the
exact boundary this phase exists to keep sharp).

Every read goes through ``SecondBrainService`` -- never
``SecondBrainStore`` directly -- so PRIVATE authorization is enforced
by the same code path already certified in FASE 4N.2A/4N.4, not
reimplemented here. A projection run is always scoped to one explicit
principal; nothing in this module ever serializes an entry that
principal could not already read through the governed API.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from openjarvis.second_brain.errors import SecondBrainAuthorizationError
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import Relationship, RelationshipStatus, SecondBrainEntry

MANIFEST_VERSION = 1
MANIFEST_FILENAME = ".maia_projection_manifest.json"
VAULT_ROOT_FOLDER = "MAIA"

_TYPE_FOLDERS: Dict[str, str] = {
    "EVENT": "Events",
    "PROBLEM": "Problems",
    "OBSERVATION": "Observations",
    "HYPOTHESIS": "Hypotheses",
    "DECISION": "Decisions",
    "ACTION": "Actions",
    "OUTCOME": "Outcomes",
    "LESSON": "Lessons",
    "PROCEDURE": "Procedures",
    "MEETING_NOTE": "Meeting Notes",
}
_SUPERSEDED_FOLDER = "_Superseded"
_ARCHIVED_FOLDER = "_Archived"
_ENTITIES_FOLDER = "_Entities"
_DOMAINS_FOLDER = "_Domains"
_DASHBOARD_FOLDER = "Dashboard"
_DASHBOARD_FILENAME = "Dashboard.md"

_READ_ONLY_WARNING = (
    "> [!warning] Generated projection -- read-only\n"
    "> This note was generated from MAIA's Second Brain. **The Second Brain "
    "is the authoritative source** -- this file is a derived, read-only "
    "view. Editing it here has no effect: changes are **not** synchronized "
    "back, and the next rebuild/update will overwrite them.\n"
)

# -- filesystem-safe naming --------------------------------------------------

_WINDOWS_FORBIDDEN = set('<>:"/\\|?*') | {chr(c) for c in range(32)}
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def slugify(text: str, *, max_len: int = 60) -> str:
    """Filesystem-safe (Windows-safe), Unicode-preserving slug.

    Only strips characters Windows actually forbids in filenames --
    Italian/accented characters and other non-ASCII text pass through
    unchanged, since NTFS handles Unicode filenames natively and
    transliterating would make notes harder to recognize, not safer.
    """
    cleaned = "".join(c if c not in _WINDOWS_FORBIDDEN else "-" for c in text)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-. ")
    if not cleaned:
        cleaned = "untitled"
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    cleaned = cleaned[:max_len].rstrip("-. ")
    return cleaned or "untitled"


def _entry_date(entry: SecondBrainEntry) -> str:
    ts = entry.timestamp if entry.timestamp is not None else entry.created_at
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def note_filename(entry: SecondBrainEntry) -> str:
    """``<TYPE>_<date>_<slug-title>_<id-prefix>.md`` -- deterministic given
    the entry's current state. The 8-char id prefix guarantees no two
    entries ever collide even with identical titles/dates/types; it also
    makes the bare filename (without extension) usable directly as an
    Obsidian wikilink target with no folder-path ambiguity.
    """
    return f"{entry.type.value}_{_entry_date(entry)}_{slugify(entry.title)}_{entry.id[:8]}.md"


def note_folder(entry: SecondBrainEntry) -> str:
    """Archived takes precedence over superseded (both can be true at
    once); a caller wanting to know "is this active" should check
    ``archived_at``/``superseded_by`` directly rather than infer it from
    the folder name."""
    if entry.archived_at is not None:
        return _ARCHIVED_FOLDER
    if entry.superseded_by is not None:
        return _SUPERSEDED_FOLDER
    return _TYPE_FOLDERS[entry.type.value]


def note_relative_path(entry: SecondBrainEntry) -> Path:
    return Path(VAULT_ROOT_FOLDER) / note_folder(entry) / note_filename(entry)


def _wikilink(entry: SecondBrainEntry) -> str:
    return f"[[{note_filename(entry)[:-3]}]]"


def entity_page_filename(entity: str) -> str:
    return f"Entity_{slugify(entity)}.md"


def domain_page_filename(domain: str) -> str:
    return f"Domain_{slugify(domain)}.md"


# -- frontmatter / body rendering --------------------------------------------


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('"', '\\"')
    return f'"{text}"'


def _yaml_list(values: List[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_yaml_scalar(v) for v in values) + "]"


def render_frontmatter(entry: SecondBrainEntry, *, outcome_backed: Optional[bool]) -> str:
    """1:1 with real ``SecondBrainEntry`` fields -- nothing invented, no
    business fact added, no KPI value ever appears here (``EvidenceReference``
    itself has no value field -- see EVIDENCE REFERENCES in
    docs/MAIA_SECOND_BRAIN_V1.md)."""
    lines = [
        "---",
        "generated: true",
        "source_of_truth: false",
        f"second_brain_id: {_yaml_scalar(entry.id)}",
        f"type: {entry.type.value}",
        f"trust_status: {entry.trust_status.value}",
    ]
    if outcome_backed is not None:
        lines.append(f"outcome_backed: {_yaml_scalar(outcome_backed)}")
    lines.append(f"visibility: {entry.visibility.value}")
    lines.append(f"domains: {_yaml_list(entry.domains)}")
    lines.append(f"entities: {_yaml_list(entry.entities)}")
    lines.append(f"created_by: {_yaml_scalar(entry.created_by)}")
    lines.append(f"provenance: {_yaml_scalar(entry.provenance)}")
    lines.append(f"source: {_yaml_scalar(entry.source)}")
    if entry.confidence is not None:
        lines.append(f"confidence: {entry.confidence}")
    if entry.timestamp is not None:
        lines.append(
            f"event_timestamp: {_yaml_scalar(datetime.fromtimestamp(entry.timestamp, tz=timezone.utc).isoformat())}"
        )
    lines.append(
        f"created_at: {_yaml_scalar(datetime.fromtimestamp(entry.created_at, tz=timezone.utc).isoformat())}"
    )
    lines.append(f"superseded_by: {_yaml_scalar(entry.superseded_by)}")
    lines.append(f"archived: {_yaml_scalar(entry.archived_at is not None)}")
    if entry.evidence_references:
        lines.append("evidence_references:")
        for ref in entry.evidence_references:
            lines.append(f"  - capability: {_yaml_scalar(ref.capability)}")
            lines.append(f"    domain: {_yaml_scalar(ref.domain)}")
            lines.append(f"    metric: {_yaml_scalar(ref.metric)}")
            lines.append(f"    period: {_yaml_scalar(ref.period)}")
            if ref.filters:
                lines.append(f"    filters: {json.dumps(ref.filters)}")
            lines.append(f"    trust_status_at_capture: {_yaml_scalar(ref.trust_status_at_capture)}")
            if ref.fetched_at:
                lines.append(f"    fetched_at: {ref.fetched_at}")
    lines.append("---")
    return "\n".join(lines)


def _relationship_line(
    rel: Relationship, *, this_id: str, resolved_other: Optional[SecondBrainEntry]
) -> Optional[str]:
    """Returns None when the other end isn't resolvable (not visible to
    the current principal, or doesn't exist) -- silently omitted, never
    rendered as a placeholder, so a relationship's mere existence never
    leaks information about an entry the current principal cannot read."""
    if resolved_other is None:
        return None
    direction = "→" if rel.source_entry_id == this_id else "←"
    status_marker = {
        RelationshipStatus.CONFIRMED: "✅ CONFIRMED",
        RelationshipStatus.PROPOSED: "🟡 PROPOSED (unconfirmed)",
        RelationshipStatus.REJECTED: "❌ REJECTED",
    }[rel.status]
    return f"- {direction} **{rel.relation_type.value}** ({status_marker}) {_wikilink(resolved_other)}"


def render_entry_note(
    entry: SecondBrainEntry,
    relationships: List[Relationship],
    resolve_entry,
    *,
    outcome_backed: Optional[bool] = None,
) -> str:
    """``resolve_entry(entry_id) -> Optional[SecondBrainEntry]`` must
    already be principal-scoped (typically
    ``lambda eid: service.get_entry(eid, actor=principal)`` wrapped to
    swallow ``SecondBrainAuthorizationError`` -- see ``ObsidianProjection``)."""
    lines = [render_frontmatter(entry, outcome_backed=outcome_backed), ""]
    lines.append(f"# {entry.title}")
    lines.append("")
    lines.append(_READ_ONLY_WARNING)

    if entry.archived_at is not None:
        lines.append("> [!note] This entry is **archived**.")
        lines.append("")
    if entry.superseded_by is not None:
        newer = resolve_entry(entry.superseded_by)
        if newer is not None:
            lines.append(f"> [!note] This entry has been **superseded** by {_wikilink(newer)}. Prefer the newer version.")
        else:
            lines.append("> [!note] This entry has been **superseded** by a newer version.")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(entry.summary or "*(no summary)*")
    lines.append("")

    lines.append("## Lifecycle / Trust")
    lines.append("")
    lines.append(f"- **Type:** {entry.type.value}")
    lines.append(f"- **Trust status:** {entry.trust_status.value}")
    if outcome_backed is not None:
        lines.append(
            f"- **Outcome-backed:** {'Yes -- linked to a CONFIRMED OUTCOME' if outcome_backed else 'No -- not yet linked to a confirmed outcome; treat as unverified'}"
        )
    lines.append(f"- **Provenance:** {entry.provenance}")
    lines.append(f"- **Created by:** {entry.created_by}")
    lines.append("")

    lines.append("## Relationships")
    lines.append("")
    rel_lines = []
    for rel in relationships:
        if rel.status is RelationshipStatus.REJECTED:
            continue  # excluded from the normal body by default -- STEP 6
        other_id = rel.target_entry_id if rel.source_entry_id == entry.id else rel.source_entry_id
        other = resolve_entry(other_id)
        line = _relationship_line(rel, this_id=entry.id, resolved_other=other)
        if line:
            rel_lines.append(line)
    lines.extend(rel_lines if rel_lines else ["*(none)*"])
    lines.append("")

    lines.append("## Evidence References")
    lines.append("")
    if entry.evidence_references:
        for ref in entry.evidence_references:
            lines.append(
                f"- `{ref.capability}` — domain={ref.domain}, metric={ref.metric}, "
                f"period={ref.period}, trust_status_at_capture={ref.trust_status_at_capture or 'n/a'}"
            )
        lines.append("")
        lines.append(
            "*(References point back to the certified capability -- no numeric "
            "value is ever copied into the Second Brain or this projection.)*"
        )
    else:
        lines.append("*(none)*")
    lines.append("")

    return "\n".join(lines)


def render_entity_page(entity: str, entries: List[SecondBrainEntry]) -> str:
    lines = [
        "---",
        "generated: true",
        "source_of_truth: false",
        "derived: true",
        f"entity: {_yaml_scalar(entity)}",
        "---",
        "",
        f"# Entity: {entity}",
        "",
        _READ_ONLY_WARNING,
        (
            "> [!info] This is a navigation page, not a Second Brain entry. "
            "Entries listed below merely mention this entity -- co-occurrence "
            "here is navigation only, never a causal or semantic relationship."
        ),
        "",
        "## Related entries",
        "",
    ]
    active = [e for e in entries if e.archived_at is None and e.superseded_by is None]
    if active:
        for e in sorted(active, key=lambda x: x.created_at, reverse=True):
            lines.append(f"- {_wikilink(e)} ({e.type.value}, {e.trust_status.value})")
    else:
        lines.append("*(none)*")
    lines.append("")
    return "\n".join(lines)


def render_domain_page(domain: str, entries: List[SecondBrainEntry]) -> str:
    lines = [
        "---",
        "generated: true",
        "source_of_truth: false",
        "derived: true",
        f"domain: {_yaml_scalar(domain)}",
        "---",
        "",
        f"# Domain: {domain}",
        "",
        _READ_ONLY_WARNING,
        (
            "> [!info] This is a navigation page, not a Second Brain entry. "
            "Entries listed below merely share this domain -- co-occurrence "
            "here is navigation only, never a causal or semantic relationship."
        ),
        "",
        "## Related entries",
        "",
    ]
    active = [e for e in entries if e.archived_at is None and e.superseded_by is None]
    if active:
        for e in sorted(active, key=lambda x: x.created_at, reverse=True):
            lines.append(f"- {_wikilink(e)} ({e.type.value}, {e.trust_status.value})")
    else:
        lines.append("*(none)*")
    lines.append("")
    return "\n".join(lines)


def render_dashboard(entries: List[SecondBrainEntry], domains: Set[str], entities: Set[str]) -> str:
    """Navigation only -- counts and links. No AI summary, no diagnosis,
    no computed business KPI (STEP 9)."""
    active = [e for e in entries if e.archived_at is None and e.superseded_by is None]
    by_type: Dict[str, List[SecondBrainEntry]] = {}
    for e in active:
        by_type.setdefault(e.type.value, []).append(e)

    lines = [
        "---",
        "generated: true",
        "source_of_truth: false",
        "derived: true",
        "---",
        "",
        "# MAIA Second Brain — Dashboard",
        "",
        _READ_ONLY_WARNING,
        "## Counts (active entries only)",
        "",
    ]
    for type_name in _TYPE_FOLDERS:
        count = len(by_type.get(type_name, []))
        lines.append(f"- **{type_name}:** {count}")
    lines.append(f"- **Domains:** {len(domains)}")
    lines.append(f"- **Entities:** {len(entities)}")
    lines.append("")

    lines.append("## Recent entries")
    lines.append("")
    recent = sorted(active, key=lambda x: x.created_at, reverse=True)[:20]
    if recent:
        for e in recent:
            lines.append(f"- {_wikilink(e)} ({e.type.value})")
    else:
        lines.append("*(none)*")
    lines.append("")

    lines.append("## Domains")
    lines.append("")
    if domains:
        for d in sorted(domains):
            lines.append(f"- [[{domain_page_filename(d)[:-3]}]]")
    else:
        lines.append("*(none)*")
    lines.append("")

    lines.append("## Entities")
    lines.append("")
    if entities:
        for ent in sorted(entities):
            lines.append(f"- [[{entity_page_filename(ent)[:-3]}]]")
    else:
        lines.append("*(none)*")
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "VAULT_ROOT_FOLDER",
    "domain_page_filename",
    "entity_page_filename",
    "note_filename",
    "note_folder",
    "note_relative_path",
    "render_dashboard",
    "render_domain_page",
    "render_entity_page",
    "render_entry_note",
    "render_frontmatter",
    "slugify",
]
