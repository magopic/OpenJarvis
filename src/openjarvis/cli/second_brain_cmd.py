"""``jarvis second-brain`` — Second Brain maintenance commands.

FASE 4O.2: ``export-obsidian`` runs the one-way Second Brain -> Markdown
projection (``second_brain/projections/obsidian_sync.py``). This is a
distinct command from the pre-existing ``obsidian`` *ingestion*
connector (``connectors/obsidian.py``, wired through
``deep-research-setup``) -- that one reads a vault into
``KnowledgeStore``; this one writes a vault from the Second Brain. The
two are never combined into one command, matching the phase's explicit
one-way-only architecture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from openjarvis.core.paths import get_config_dir

console = Console(stderr=True)

_DEFAULT_VAULT_DIRNAME = "obsidian_vault"


def _default_vault_path() -> Path:
    """Inside OpenJarvis' own config directory, never a user's personal
    vault -- picking a destination outside ``get_config_dir()`` risks
    silently writing into a real Obsidian vault the user already has."""
    return get_config_dir() / _DEFAULT_VAULT_DIRNAME


@click.group("second-brain")
def second_brain_group() -> None:
    """Second Brain maintenance commands."""


@second_brain_group.command("export-obsidian")
@click.option(
    "--vault",
    "vault_path_str",
    default=None,
    help=f"Vault destination directory. Default: ~/.openjarvis/{_DEFAULT_VAULT_DIRNAME}/",
)
@click.option(
    "--principal",
    default=None,
    help="Explicit principal to export as. Default: the resolved runtime principal (same identity as the Second Brain tools).",
)
@click.option(
    "--rebuild",
    is_flag=True,
    default=False,
    help="Force a full deterministic rebuild instead of an incremental update.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Required to write into a non-empty directory that isn't already a MAIA projection.",
)
def export_obsidian(
    vault_path_str: Optional[str], principal: Optional[str], rebuild: bool, force: bool
) -> None:
    """Project the Second Brain into a Markdown/Obsidian vault.

    One-way only: this reads the Second Brain (through the same
    authorization-enforcing service every Second Brain tool uses) and
    writes Markdown. Nothing written here is ever read back into the
    Second Brain -- editing the generated notes has no effect.
    """
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.obsidian import MANIFEST_FILENAME
    from openjarvis.second_brain.projections.obsidian_sync import ObsidianProjection
    from openjarvis.second_brain.service import SecondBrainService

    vault_path = Path(vault_path_str).expanduser() if vault_path_str else _default_vault_path()
    resolved_principal = principal or resolve_runtime_principal()

    manifest_present = (vault_path / MANIFEST_FILENAME).exists()
    if vault_path.exists() and not manifest_present and any(vault_path.iterdir()) and not force:
        console.print(
            f"[red]Refusing to write into a non-empty directory that isn't already a "
            f"MAIA projection:[/red] {vault_path}\n"
            "This guards against silently overwriting an unrelated existing Obsidian "
            "vault. Pass --force if this destination is intentional, or choose a "
            "different --vault path."
        )
        sys.exit(1)

    service = SecondBrainService()
    try:
        projection = ObsidianProjection(service, vault_path, resolved_principal)
        stats = projection.rebuild() if rebuild else projection.update()
    finally:
        service.close()

    console.print(f"[green]Projection complete[/green] ({stats.mode}) -> {vault_path}")
    console.print(f"  notes written:   {stats.notes_written}")
    console.print(f"  notes removed:   {stats.notes_removed}")
    console.print(f"  entity pages:    {stats.entity_pages_written}")
    console.print(f"  domain pages:    {stats.domain_pages_written}")
    if stats.mode == "update":
        console.print(f"  unchanged (skipped): {stats.skipped_unchanged}")
    console.print(f"  principal:       {resolved_principal}")


__all__ = ["second_brain_group"]
