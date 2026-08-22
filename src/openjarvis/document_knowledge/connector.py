"""Local authorized-workspace document connector (FASE 4O.5).

Modeled directly on ``connectors/obsidian.py::ObsidianConnector`` (same
``BaseConnector`` shape, same walk-and-yield-``Document`` pattern) but
extended for what this feature needs and Obsidian's connector doesn't:

- PDF support (page-aware, via ``parsers.py``) alongside TXT/Markdown.
- Hard workspace-root confinement (``workspace.safe_resolve``) -- Obsidian
  trusts whatever ``vault_path`` it's given (a user-supplied CLI flag);
  this connector's root is fixed and every path is defensively re-resolved
  against it.
- Sensitive-file blocking (``security.file_policy.is_sensitive_file``).
- Real per-file identity via SHA-256 + mtime (not just an mtime filter).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.document_knowledge.parsers import (
    SUPPORTED_EXTENSIONS,
    DocumentParseError,
    UnsupportedDocumentError,
    parse_document,
)
from openjarvis.document_knowledge.workspace import safe_resolve
from openjarvis.security.file_policy import is_sensitive_file

_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}


@ConnectorRegistry.register("maia_documents")
class LocalDocumentsConnector(BaseConnector):
    """Reads PDF/TXT/Markdown files from an authorized local workspace.

    Parameters
    ----------
    workspace_root:
        The already-authorized root directory (see ``workspace.py`` --
        callers are expected to have called ``ensure_workspace_root()``
        first; this connector does not create directories itself).
    """

    connector_id = "maia_documents"
    display_name = "MAIA Document Workspace"
    auth_type = "filesystem"

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self._root = workspace_root
        self._items_synced = 0
        self._items_total = 0
        self._skipped_unsupported: List[str] = []
        self._skipped_sensitive: List[str] = []
        self._errors: Dict[str, str] = {}

    def is_connected(self) -> bool:
        return self._root is not None and self._root.is_dir()

    def disconnect(self) -> None:
        self._root = None

    @property
    def skipped_unsupported(self) -> List[str]:
        return list(self._skipped_unsupported)

    @property
    def skipped_sensitive(self) -> List[str]:
        return list(self._skipped_sensitive)

    @property
    def errors(self) -> Dict[str, str]:
        return dict(self._errors)

    def sync(
        self,
        *,
        since: Optional[datetime] = None,  # noqa: ARG002 -- ingest.py does its own hash-based diffing
        cursor: Optional[str] = None,  # noqa: ARG002 -- unused, part of the ABC
    ) -> Iterator[Document]:
        """Walk the authorized workspace and yield one ``Document`` per
        supported file. Every path is re-verified via ``safe_resolve``
        against the workspace root before being read -- even though
        ``os.walk`` starts from an already-authorized root, this defends
        against a directory becoming a symlink to somewhere else between
        the walk starting and a given file being opened (TOCTOU)."""
        if self._root is None:
            return

        root = self._root
        self._skipped_unsupported = []
        self._skipped_sensitive = []
        self._errors = {}

        collected: List[Path] = []
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for filename in files:
                collected.append(Path(dirpath) / filename)

        self._items_total = len(collected)
        synced = 0

        for fpath in collected:
            try:
                resolved = safe_resolve(root, fpath)
            except Exception:
                # A file that fails safe_resolve (e.g. a symlink escaping
                # the workspace after the walk started) is silently
                # excluded -- fail closed, never surfaced as ingestible.
                continue

            rel_path = resolved.relative_to(root).as_posix()

            if is_sensitive_file(resolved):
                self._skipped_sensitive.append(rel_path)
                continue

            if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
                self._skipped_unsupported.append(rel_path)
                continue

            try:
                pages = parse_document(resolved)
            except (UnsupportedDocumentError, DocumentParseError) as exc:
                self._errors[rel_path] = str(exc)
                continue
            except OSError as exc:
                self._errors[rel_path] = f"Could not read file: {exc}"
                continue

            stat = resolved.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            full_text = "\n\n".join(p.text for p in pages)

            metadata: Dict[str, Any] = {
                "relative_path": rel_path,
                "pages": [{"page": p.page, "text": p.text} for p in pages] if pages[0].page is not None else None,
            }

            doc = Document(
                doc_id=f"maia_documents:{rel_path}",
                source="maia_documents",
                doc_type="document",
                content=full_text,
                title=resolved.stem,
                timestamp=mtime,
                url=None,
                metadata=metadata,
                source_id=rel_path,
            )
            synced += 1
            yield doc

        self._items_synced = synced

    def sync_status(self) -> SyncStatus:
        return SyncStatus(state="idle", items_synced=self._items_synced, items_total=self._items_total)


__all__ = ["LocalDocumentsConnector"]
