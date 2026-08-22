"""``jarvis second-brain`` — Second Brain maintenance commands.

FASE 4O.2: ``export-obsidian`` runs the one-way Second Brain -> Markdown
projection (``second_brain/projections/obsidian_sync.py``). This is a
distinct command from the pre-existing ``obsidian`` *ingestion*
connector (``connectors/obsidian.py``, wired through
``deep-research-setup``) -- that one reads a vault into
``KnowledgeStore``; this one writes a vault from the Second Brain. The
two are never combined into one command, matching the phase's explicit
one-way-only architecture.

FASE 4O.3: ``graph *`` commands expose the read-only Knowledge Graph
projection (``second_brain/projections/graph.py``) as deterministic
JSON on stdout -- the safest V1 exposure (STEP 12): a stable Python
service contract first, with CLI JSON as the inspection/integration
surface for now. No HTTP server is introduced here; OPS ONE, the Tauri
companion, or a future graph UI can shell out to this exactly like any
other ``jarvis ... `` JSON-producing command, or call
``second_brain.projections.graph`` directly in-process.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional, Tuple

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


def _csv_tuple(value: Optional[str]) -> Optional[Tuple[str, ...]]:
    if not value:
        return None
    return tuple(v.strip() for v in value.split(",") if v.strip())


def _emit_graph(response) -> None:
    """Deterministic JSON: sorted keys, UTF-8 preserved (no
    ``ensure_ascii`` mangling of Unicode labels), no Python-specific
    objects -- ``GraphResponse.to_dict()`` already reduces everything
    to plain str/float/bool/list/dict/None (STEP 13)."""
    click.echo(json.dumps(response.to_dict(), sort_keys=True, ensure_ascii=False, indent=2))


@second_brain_group.group("graph")
def graph_group() -> None:
    """Read-only Knowledge Graph projection (FASE 4O.3). Every
    subcommand prints one deterministic JSON ``GraphResponse`` to
    stdout and performs zero Second Brain writes."""


@graph_group.command("overview")
@click.option("--principal", default=None, help="Default: the resolved runtime principal.")
@click.option("--domains", default=None, help="Comma-separated domain filter.")
@click.option("--entities", default=None, help="Comma-separated entity filter.")
@click.option("--entry-limit", default=50, show_default=True, type=int)
@click.option("--max-nodes", default=None, type=int)
@click.option("--max-edges", default=None, type=int)
def graph_overview(
    principal: Optional[str], domains: Optional[str], entities: Optional[str], entry_limit: int,
    max_nodes: Optional[int], max_edges: Optional[int],
) -> None:
    """Aggregate/navigation overview -- domains, entities, and the most
    recently active authorized entries. Not a dump of every memory."""
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.graph import GraphBounds, GraphFilters, get_overview
    from openjarvis.second_brain.service import SecondBrainService

    resolved_principal = principal or resolve_runtime_principal()
    filters = GraphFilters(domains=_csv_tuple(domains), entities=_csv_tuple(entities))
    bounds = GraphBounds(**{k: v for k, v in {"max_nodes": max_nodes, "max_edges": max_edges}.items() if v is not None})

    service = SecondBrainService()
    try:
        response = get_overview(service, actor=resolved_principal, filters=filters, bounds=bounds, entry_limit=entry_limit)
    finally:
        service.close()
    _emit_graph(response)


@graph_group.command("neighborhood")
@click.argument("root_id")
@click.option("--principal", default=None, help="Default: the resolved runtime principal.")
@click.option("--depth", default=1, show_default=True, type=int)
@click.option("--include-archived", is_flag=True, default=False)
@click.option("--include-superseded", is_flag=True, default=False)
@click.option("--max-nodes", default=None, type=int)
@click.option("--max-edges", default=None, type=int)
def graph_neighborhood(
    root_id: str, principal: Optional[str], depth: int, include_archived: bool, include_superseded: bool,
    max_nodes: Optional[int], max_edges: Optional[int],
) -> None:
    """Deterministic bounded expansion from ROOT_ID over stored
    relationships -- entries, then their entities/domains."""
    from openjarvis.second_brain.errors import SecondBrainValidationError
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.graph import GraphBounds, GraphFilters, get_neighborhood
    from openjarvis.second_brain.service import SecondBrainService

    resolved_principal = principal or resolve_runtime_principal()
    filters = GraphFilters(include_archived=include_archived, include_superseded=include_superseded)
    bounds = GraphBounds(**{k: v for k, v in {"max_nodes": max_nodes, "max_edges": max_edges}.items() if v is not None})

    service = SecondBrainService()
    try:
        response = get_neighborhood(service, root_id, actor=resolved_principal, depth=depth, filters=filters, bounds=bounds)
    except SecondBrainValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    finally:
        service.close()
    _emit_graph(response)


@graph_group.command("domain")
@click.argument("domain")
@click.option("--principal", default=None, help="Default: the resolved runtime principal.")
@click.option("--entry-limit", default=100, show_default=True, type=int)
def graph_domain(domain: str, principal: Optional[str], entry_limit: int) -> None:
    """Entries sharing DOMAIN, plus their stored relationships."""
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.graph import get_domain_graph
    from openjarvis.second_brain.service import SecondBrainService

    resolved_principal = principal or resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_domain_graph(service, domain, actor=resolved_principal, entry_limit=entry_limit)
    finally:
        service.close()
    _emit_graph(response)


@graph_group.command("entity")
@click.argument("entity")
@click.option("--principal", default=None, help="Default: the resolved runtime principal.")
@click.option("--entry-limit", default=100, show_default=True, type=int)
def graph_entity(entity: str, principal: Optional[str], entry_limit: int) -> None:
    """Entries sharing ENTITY, plus their stored relationships."""
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.graph import get_entity_graph
    from openjarvis.second_brain.service import SecondBrainService

    resolved_principal = principal or resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_entity_graph(service, entity, actor=resolved_principal, entry_limit=entry_limit)
    finally:
        service.close()
    _emit_graph(response)


@graph_group.command("experience")
@click.argument("anchor_entry_id")
@click.option("--principal", default=None, help="Default: the resolved runtime principal.")
@click.option("--max-hops", default=4, show_default=True, type=int)
@click.option("--max-entries", default=12, show_default=True, type=int)
def graph_experience(anchor_entry_id: str, principal: Optional[str], max_hops: int, max_entries: int) -> None:
    """Graph view of the Experience Cycle (PROBLEM..LESSON) anchored at
    ANCHOR_ENTRY_ID, built on the certified ``get_experience_bundle``."""
    from openjarvis.second_brain.errors import SecondBrainValidationError
    from openjarvis.second_brain.identity import resolve_runtime_principal
    from openjarvis.second_brain.projections.graph import get_experience_graph
    from openjarvis.second_brain.service import SecondBrainService

    resolved_principal = principal or resolve_runtime_principal()
    service = SecondBrainService()
    try:
        response = get_experience_graph(
            service, anchor_entry_id, actor=resolved_principal, max_hops=max_hops, max_entries=max_entries
        )
    except SecondBrainValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    finally:
        service.close()
    _emit_graph(response)


__all__ = ["second_brain_group"]
