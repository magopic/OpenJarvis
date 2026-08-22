"""``jarvis document`` — MAIA Document Knowledge Ingestion V1 commands.

FASE 4O.5: ingest PDF/TXT/Markdown files placed in the authorized
``maia_documents`` workspace, and search them with full provenance. This
is a distinct system from ``jarvis second-brain`` (governed experiential
memory) and from OPS ONE (certified operational data) -- see
``docs/MAIA_DOCUMENT_KNOWLEDGE_V1.md``.
"""

from __future__ import annotations

import json

import click
from rich.console import Console

console = Console(stderr=True)


@click.group("document")
def document_group() -> None:
    """MAIA document knowledge workspace commands."""


@document_group.command("ingest")
def document_ingest() -> None:
    """Sweep the authorized document workspace and (re)build the index."""
    from openjarvis.document_knowledge.service import DocumentKnowledgeService
    from openjarvis.document_knowledge.workspace import ensure_workspace_root

    service = DocumentKnowledgeService()
    try:
        root = ensure_workspace_root(service.config.workspace_root)
        outcome = service.ingest_now()
    finally:
        service.close()

    console.print(f"[green]Workspace:[/green] {root}")
    console.print(f"  added:     {len(outcome.added)}")
    console.print(f"  updated:   {len(outcome.updated)}")
    console.print(f"  removed:   {len(outcome.removed)}")
    console.print(f"  unchanged: {outcome.unchanged}")
    console.print(f"  chunks written: {outcome.chunks_written}")
    if outcome.skipped_unsupported:
        console.print(f"  [dim]skipped (unsupported type): {len(outcome.skipped_unsupported)}[/dim]")
    if outcome.skipped_sensitive:
        console.print(f"  [yellow]skipped (sensitive file): {len(outcome.skipped_sensitive)}[/yellow]")
    if outcome.errors:
        console.print(f"  [red]errors: {len(outcome.errors)}[/red]")
        for path, msg in outcome.errors.items():
            console.print(f"    [red]{path}: {msg}[/red]")


@document_group.command("search")
@click.argument("query")
@click.option("--limit", default=10, show_default=True, type=int)
@click.option("--filename", default=None, help="Restrict to this exact filename.")
def document_search(query: str, limit: int, filename: str) -> None:
    """Search the document index. Prints deterministic JSON to stdout."""
    from openjarvis.document_knowledge.service import DocumentKnowledgeService

    service = DocumentKnowledgeService()
    try:
        results = service.search_documents(query, top_k=limit, filename=filename)
    finally:
        service.close()

    payload = [
        {
            "citation": r.evidence.citation_label(),
            "filename": r.evidence.filename,
            "relative_path": r.evidence.relative_path,
            "page": r.evidence.page,
            "section": r.evidence.section,
            "chunk_id": r.evidence.chunk_id,
            "doc_id": r.evidence.doc_id,
            "score": r.score,
            "content": r.content,
        }
        for r in results
    ]
    click.echo(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2))


@document_group.command("list")
def document_list() -> None:
    """List every ingested document. Prints deterministic JSON to stdout."""
    from openjarvis.document_knowledge.service import DocumentKnowledgeService

    service = DocumentKnowledgeService()
    try:
        docs = service.list_documents()
    finally:
        service.close()

    payload = [
        {
            "doc_id": d.doc_id,
            "relative_path": d.relative_path,
            "filename": d.filename,
            "file_type": d.file_type,
            "content_hash": d.content_hash,
            "chunk_count": d.chunk_count,
            "ingested_at": d.ingested_at,
        }
        for d in docs
    ]
    click.echo(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2))


__all__ = ["document_group"]
