"""Manifest-tracked rebuild/update engine for the Obsidian projection
(FASE 4O.2 STEP 11/12).

Two operations, both principal-scoped and both reading exclusively
through ``SecondBrainService`` (never ``SecondBrainStore``, never raw
SQL, never the audit log -- see the note on incremental scope below):

- ``rebuild()``: deterministic full rebuild. Always correct, always
  available, the ground truth this whole engine can fall back to.
- ``update()``: incremental -- compares each visible entry's own
  ``updated_at`` against what the manifest recorded last time and only
  re-renders what changed. Built as a thin filter on top of the exact
  same per-entry renderer ``rebuild()`` uses, so its correctness for
  entry-content changes is inherited, not independently reasoned about.

Incremental scope, stated plainly (the phase explicitly allows marking
this PARTIAL rather than inventing unsafe machinery to close the gap):
a *relationship* created or confirmed between two already-rendered
entries does not, by itself, change either entry's ``updated_at`` --
only entry mutations do (``create_entry``/``archive_entry``/
``supersede_entry`` all stamp a fresh ``updated_at``; relationship
mutations live in a separate table and don't touch it). So
``update()`` will not notice a relationship-only change and refresh
the two entries' "Relationships" sections on its own. This was a
deliberate simplicity/safety trade-off: the alternative (diffing the
audit log for relationship events) would mean reading audit records
that can contain a PRIVATE entry's title in their ``details`` even for
a principal who cannot read that entry -- a new privacy surface this
module has no reason to open. ``rebuild()`` always sees the true
current relationship state (it doesn't consult the manifest at all),
so the documented mitigation is the same hybrid strategy the FASE 4O.1
audit already recommended: run ``update()`` often, ``rebuild()``
periodically as a correctness safety net.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from openjarvis.second_brain.errors import SecondBrainAuthorizationError
from openjarvis.second_brain.projections.obsidian import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    VAULT_ROOT_FOLDER,
    _DASHBOARD_FILENAME,
    _DASHBOARD_FOLDER,
    _DOMAINS_FOLDER,
    _ENTITIES_FOLDER,
    domain_page_filename,
    entity_page_filename,
    note_relative_path,
    render_dashboard,
    render_domain_page,
    render_entity_page,
    render_entry_note,
)
from openjarvis.second_brain.service import SecondBrainService
from openjarvis.second_brain.types import SecondBrainEntry


@dataclass
class ProjectionStats:
    notes_written: int = 0
    notes_removed: int = 0
    entity_pages_written: int = 0
    domain_pages_written: int = 0
    skipped_unchanged: int = 0
    mode: str = "rebuild"  # or "update"


@dataclass
class _ManifestEntryRecord:
    relative_path: str
    updated_at: float


@dataclass
class _Manifest:
    version: int = MANIFEST_VERSION
    principal: str = ""
    last_sync_at: float = 0.0
    entries: Dict[str, _ManifestEntryRecord] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Optional["_Manifest"]:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("version") != MANIFEST_VERSION:
                return None  # unknown/future version -- safest to rebuild, not guess
            entries = {
                eid: _ManifestEntryRecord(**rec) for eid, rec in raw.get("entries", {}).items()
            }
            return cls(
                version=raw["version"],
                principal=raw.get("principal", ""),
                last_sync_at=raw.get("last_sync_at", 0.0),
                entries=entries,
            )
        except Exception:
            return None  # corrupt manifest -- caller falls back to rebuild

    def save(self, path: Path) -> None:
        payload = {
            "version": self.version,
            "principal": self.principal,
            "last_sync_at": self.last_sync_at,
            "entries": {
                eid: {"relative_path": rec.relative_path, "updated_at": rec.updated_at}
                for eid, rec in self.entries.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class ObsidianProjection:
    """Principal-scoped Second Brain -> Markdown projection engine.

    Every constructor caller must supply an explicit ``principal`` --
    there is no default. All reads go through ``service.list_entries``/
    ``service.get_entry``/``service.get_relationships``, the same
    authorization-enforcing calls already certified for the Second
    Brain tools (FASE 4N.2A/4N.4); PRIVATE entries owned by a different
    principal are filtered *before* any Markdown is generated, never
    written then hidden.
    """

    def __init__(self, service: SecondBrainService, vault_path: Path, principal: str) -> None:
        if not principal or not principal.strip():
            raise ValueError("ObsidianProjection requires a non-empty principal")
        self._service = service
        self._vault_path = Path(vault_path)
        self._principal = principal

    # -- shared helpers ---------------------------------------------------

    def _manifest_path(self) -> Path:
        return self._vault_path / MANIFEST_FILENAME

    def _fetch_all_entries(self) -> List[SecondBrainEntry]:
        """Every entry visible to this principal, active + superseded +
        archived -- the same fail-closed visibility clause certified in
        FASE 4N.1/4N.2A applies here exactly as it does to any other
        caller of ``list_entries``."""
        return self._service.list_entries(
            actor=self._principal, include_archived=True, limit=1_000_000
        )

    def _resolve_entry(self, entry_id: str) -> Optional[SecondBrainEntry]:
        """Principal-scoped lookup that never raises -- a relationship
        pointing at an entry this principal cannot read is silently
        omitted from rendering (see ``_relationship_line`` in
        ``obsidian.py``), never surfaced as an error or a placeholder
        that would itself leak the target's existence."""
        try:
            return self._service.get_entry(entry_id, actor=self._principal)
        except SecondBrainAuthorizationError:
            return None

    def _write_note(self, relative_path: Path, content: str) -> None:
        full_path = self._vault_path / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def _remove_note(self, relative_path: str) -> None:
        full_path = self._vault_path / relative_path
        if full_path.exists():
            full_path.unlink()

    def _render_and_write_entry(self, entry: SecondBrainEntry) -> Path:
        relationships = self._service.get_relationships(entry.id)
        outcome_backed: Optional[bool] = None
        if entry.type.value == "LESSON" or entry.trust_status.value == "LEARNED":
            outcome_backed = self._service.is_outcome_backed(entry.id)
        content = render_entry_note(
            entry, relationships, self._resolve_entry, outcome_backed=outcome_backed
        )
        rel_path = note_relative_path(entry)
        self._write_note(rel_path, content)
        return rel_path

    def _write_entity_domain_pages(
        self, entries: List[SecondBrainEntry]
    ) -> "tuple[int, int, Set[str], Set[str]]":
        by_entity: Dict[str, List[SecondBrainEntry]] = {}
        by_domain: Dict[str, List[SecondBrainEntry]] = {}
        for e in entries:
            for ent in e.entities:
                by_entity.setdefault(ent, []).append(e)
            for dom in e.domains:
                by_domain.setdefault(dom, []).append(e)

        for entity, ents in by_entity.items():
            self._write_note(
                Path(VAULT_ROOT_FOLDER) / _ENTITIES_FOLDER / entity_page_filename(entity),
                render_entity_page(entity, ents),
            )
        for domain, doms in by_domain.items():
            self._write_note(
                Path(VAULT_ROOT_FOLDER) / _DOMAINS_FOLDER / domain_page_filename(domain),
                render_domain_page(domain, doms),
            )
        return len(by_entity), len(by_domain), set(by_entity), set(by_domain)

    def _write_dashboard(
        self, entries: List[SecondBrainEntry], domains: Set[str], entities: Set[str]
    ) -> None:
        self._write_note(
            Path(VAULT_ROOT_FOLDER) / _DASHBOARD_FOLDER / _DASHBOARD_FILENAME,
            render_dashboard(entries, domains, entities),
        )

    # -- public operations --------------------------------------------------

    def rebuild(self) -> ProjectionStats:
        """Deterministic full rebuild. Idempotent: re-running against an
        unchanged Second Brain produces byte-identical files (no
        wall-clock timestamps are embedded in rendered content) and an
        unchanged manifest content-wise (only ``last_sync_at`` differs
        between two consecutive rebuilds)."""
        stats = ProjectionStats(mode="rebuild")

        vault_root = self._vault_path / VAULT_ROOT_FOLDER
        if vault_root.exists():
            shutil.rmtree(vault_root)

        entries = self._fetch_all_entries()
        manifest = _Manifest(principal=self._principal, last_sync_at=time.time())

        for entry in entries:
            rel_path = self._render_and_write_entry(entry)
            manifest.entries[entry.id] = _ManifestEntryRecord(
                relative_path=str(rel_path).replace("\\", "/"), updated_at=entry.updated_at
            )
            stats.notes_written += 1

        n_entities, n_domains, entities_set, domains_set = self._write_entity_domain_pages(entries)
        stats.entity_pages_written = n_entities
        stats.domain_pages_written = n_domains
        self._write_dashboard(entries, domains_set, entities_set)

        manifest.save(self._manifest_path())
        return stats

    def update(self) -> ProjectionStats:
        """Incremental update. Falls back to a full ``rebuild()`` when no
        usable manifest exists (first run, corrupt manifest, manifest
        from a different principal) -- never guesses at a partial state."""
        manifest = _Manifest.load(self._manifest_path())
        if manifest is None or manifest.principal != self._principal:
            return self.rebuild()

        stats = ProjectionStats(mode="update")
        entries = self._fetch_all_entries()
        current_ids = {e.id for e in entries}

        for entry in entries:
            prior = manifest.entries.get(entry.id)
            current_path = str(note_relative_path(entry)).replace("\\", "/")
            if prior is not None and prior.updated_at == entry.updated_at and prior.relative_path == current_path:
                stats.skipped_unchanged += 1
                continue
            if prior is not None and prior.relative_path != current_path:
                # Title (or type/archival/supersession state) changed --
                # the old filename would otherwise be orphaned (STEP 4).
                self._remove_note(prior.relative_path)
            self._render_and_write_entry(entry)
            manifest.entries[entry.id] = _ManifestEntryRecord(
                relative_path=current_path, updated_at=entry.updated_at
            )
            stats.notes_written += 1

        # Entries the manifest remembers but that no longer come back
        # from a principal-scoped fetch (e.g. visibility changed) must
        # not keep serving stale content.
        for stale_id in list(manifest.entries):
            if stale_id not in current_ids:
                self._remove_note(manifest.entries[stale_id].relative_path)
                del manifest.entries[stale_id]
                stats.notes_removed += 1

        n_entities, n_domains, entities_set, domains_set = self._write_entity_domain_pages(entries)
        stats.entity_pages_written = n_entities
        stats.domain_pages_written = n_domains
        self._write_dashboard(entries, domains_set, entities_set)

        manifest.last_sync_at = time.time()
        manifest.save(self._manifest_path())
        return stats


__all__ = ["ObsidianProjection", "ProjectionStats"]
