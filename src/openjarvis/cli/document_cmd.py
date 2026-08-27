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
            "status": d.status,
            "superseded_by_doc_id": d.superseded_by_doc_id,
            "superseded_by_filename": d.superseded_by_filename,
            "superseded_at": d.superseded_at,
            "same_content_as_successor": d.same_content_as_successor,
            "successor_missing": d.successor_missing,
        }
        for d in docs
    ]
    click.echo(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2))


@document_group.command("supersede")
@click.argument("old")
@click.option("--by", "new", required=True, help="The document that replaces OLD (relative_path or doc_id).")
def document_supersede(old: str, new: str) -> None:
    """Mark OLD as superseded by the document given via --by.

    OLD and --by each accept either a relative_path (as ingested) or a
    doc_id. This is a human-controlled admin operation: it does not
    ingest, does not delete any chunk, and has no model-callable
    equivalent (M2.5A -- Document Authority & Supersession V1).
    Validation runs entirely before any write; on any failure below, the
    database is left completely unchanged.
    """
    from openjarvis.document_knowledge.service import DocumentKnowledgeService, DocumentSupersessionError

    service = DocumentKnowledgeService()
    try:
        old_doc_id = service.resolve_doc_id(old)
        if old_doc_id is None:
            console.print(f"[red]Not found:[/red] {old!r} does not match any ingested document (path or doc_id).")
            raise SystemExit(1)

        new_doc_id = service.resolve_doc_id(new)
        if new_doc_id is None:
            console.print(f"[red]Not found:[/red] {new!r} does not match any ingested document (path or doc_id).")
            raise SystemExit(1)

        try:
            service.supersede_document(old_doc_id, new_doc_id)
        except DocumentSupersessionError as exc:
            console.print(f"[red]Rejected:[/red] {exc}")
            raise SystemExit(1)

        old_doc = service.get_document(old_doc_id)
        new_doc = service.get_document(new_doc_id)
    finally:
        service.close()

    old_label = old_doc.filename if old_doc else old_doc_id
    new_label = new_doc.filename if new_doc else new_doc_id
    console.print(f"[green]Marked superseded:[/green] {old_label} -> now SUPERSEDED, replaced by {new_label}")
    console.print(f"  old doc_id: {old_doc_id}")
    console.print(f"  new doc_id: {new_doc_id}")
    console.print(
        "  Old chunks were NOT deleted -- the old document remains fully "
        "searchable and retrievable, now annotated as SUPERSEDED."
    )


@document_group.command("unsupersede")
@click.argument("document")
def document_unsupersede(document: str) -> None:
    """Clear DOCUMENT's supersession link, restoring it to CURRENT.

    DOCUMENT accepts either a relative_path (as ingested) or a doc_id.
    This is a human-controlled admin operation -- it is the repair path
    for an orphaned supersession (e.g. the recorded successor was
    removed from the workspace via `jarvis document ingest`), but it is
    NOT automatic: a missing successor never restores CURRENT status by
    itself, only this deliberate command does. It does not touch any
    chunk and does not modify the successor document, if one still
    exists. Has no model-callable equivalent. Validation runs entirely
    before any write; on failure, the database is left completely
    unchanged.
    """
    from openjarvis.document_knowledge.service import DocumentKnowledgeService, DocumentSupersessionError

    service = DocumentKnowledgeService()
    try:
        doc_id = service.resolve_doc_id(document)
        if doc_id is None:
            console.print(
                f"[red]Not found:[/red] {document!r} does not match any ingested document (path or doc_id)."
            )
            raise SystemExit(1)

        try:
            service.clear_supersession(doc_id)
        except DocumentSupersessionError as exc:
            console.print(f"[red]Rejected:[/red] {exc}")
            raise SystemExit(1)

        doc = service.get_document(doc_id)
    finally:
        service.close()

    label = doc.filename if doc else doc_id
    console.print(f"[green]Supersession cleared:[/green] {label} -> now CURRENT")
    console.print(f"  doc_id: {doc_id}")
    console.print("  No chunks were touched; the previously-recorded successor (if any) was not modified.")


__all__ = ["document_group"]
