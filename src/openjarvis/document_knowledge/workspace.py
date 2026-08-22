"""Authorized workspace root + safe path resolution (FASE 4O.5).

MAIA must never gain arbitrary filesystem access. This module is the
single choke point every other module in this package goes through to
turn a relative path into a real filesystem path -- nothing else in this
package touches ``Path.resolve()`` directly.

Deliberately a NEW, dedicated root -- not a reuse of the existing
``OPENJARVIS_WORKSPACE`` env var (``server/api_routes.py``'s
``/v1/memory/index``). That endpoint accepts an admin-supplied absolute
path with weak per-chunk provenance (a bare path string, no content hash,
no mtime, no dedup). Conflating the two would import that endpoint's
weaker guarantees into MAIA's own governed document workspace. This
package's authorized root is fixed, config-anchored, and every ingested
document gets full file-level provenance (see ``types.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from openjarvis.core.paths import get_config_dir

# Overridable only for tests -- production code always uses the default.
_ENV_OVERRIDE = "MAIA_DOCUMENT_WORKSPACE"
_DEFAULT_DIRNAME = "maia_documents"


class DocumentAccessError(Exception):
    """Raised whenever a path would resolve outside the authorized root,
    or otherwise fails the workspace's fail-closed security checks."""


def default_workspace_root() -> Path:
    """The authorized root when no explicit override is given.

    ``~/.openjarvis/maia_documents/`` (via ``get_config_dir()``, the same
    convention every other OpenJarvis config subdirectory uses) --
    auto-created on first use, never auto-populated. A user must
    explicitly place files here; MAIA never crawls the rest of the
    filesystem to find them.
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (get_config_dir() / _DEFAULT_DIRNAME).resolve()


def ensure_workspace_root(root: Optional[Path] = None) -> Path:
    """Return the authorized root, creating it if missing. Refuses to
    "create" a root that already exists as something other than a
    directory (e.g. a file or a broken symlink) -- fails closed rather
    than guessing."""
    resolved = (root or default_workspace_root()).resolve()
    if resolved.exists() and not resolved.is_dir():
        raise DocumentAccessError(f"Authorized workspace root is not a directory: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def safe_resolve(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` (absolute or relative to ``root``) and verify
    it stays inside ``root`` after resolution.

    ``Path.resolve()`` normalizes ``..`` segments AND follows symlinks on
    both POSIX and Windows, so comparing the *resolved* target against the
    *resolved* root via containment closes both the ``../`` traversal
    vector and the symlink-escape vector in one check -- the same pattern
    already established in ``tools/file_read.py::FileReadTool``.

    Raises ``DocumentAccessError`` (never returns a path outside root) for:
    - ``../`` traversal attempts
    - absolute paths outside the root
    - symlinks that resolve outside the root
    """
    root_resolved = root.resolve()
    target = candidate if candidate.is_absolute() else root_resolved / candidate
    try:
        resolved = target.resolve()
    except OSError as exc:  # e.g. a broken symlink loop
        raise DocumentAccessError(f"Could not resolve path: {candidate}") from exc

    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise DocumentAccessError(
            f"Path resolves outside the authorized workspace: {candidate} -> {resolved}"
        )
    return resolved


def relative_to_workspace(root: Path, resolved_path: Path) -> str:
    """POSIX-style relative path (stable across OSes) for use as a doc_id
    component -- callers must pass an already-``safe_resolve``d path."""
    return resolved_path.relative_to(root.resolve()).as_posix()


__all__ = [
    "DocumentAccessError",
    "default_workspace_root",
    "ensure_workspace_root",
    "safe_resolve",
    "relative_to_workspace",
]
