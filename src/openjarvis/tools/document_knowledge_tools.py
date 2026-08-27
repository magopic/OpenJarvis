"""Model-callable tools over MAIA Document Knowledge V1 (FASE 4O.5).

Mirrors the ``tools/second_brain_tools.py`` pattern: thin wrappers around
``DocumentKnowledgeService`` that expose provenance-rich results to the
model, never bare retrieved text. A result here is DOCUMENT KNOWLEDGE
(traceable to a file the user placed in the authorized workspace) --
never a certified OPS ONE KPI (use the ops_dynamic_* tools for that) and
never a Second Brain entry (use the second_brain_* tools for governed
experiential memory). This tool cannot create a Second Brain entry, and
it cannot override or recompute an OPS Calculator result -- it has no
write path to either system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.document_knowledge.service import DocumentKnowledgeService
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _service() -> DocumentKnowledgeService:
    return DocumentKnowledgeService()


def _format_epoch(epoch: Optional[float]) -> str:
    """Render an ingestion epoch as a plain UTC date -- never labeled or
    presented as a document's business-effective date (M2.5A)."""
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()


@ToolRegistry.register("document_search")
class DocumentSearchTool(BaseTool):
    """Lexical search over documents in the authorized MAIA document
    workspace. Every result carries a citation (filename + page/section/
    chunk) -- always attribute an answer to its source, never restate
    retrieved text as if it were the model's own knowledge."""

    tool_id = "document_search"

    def __init__(self, service: Optional[DocumentKnowledgeService] = None) -> None:
        self._service = service or _service()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_search",
            description=(
                "Search documents (PDF/TXT/Markdown procedures, manuals, notes, "
                "reports) that the user has placed in their authorized MAIA "
                "document workspace. This is DOCUMENT KNOWLEDGE -- textual "
                "material with a traceable source file -- NOT a certified OPS "
                "ONE KPI (use the ops_dynamic_* tools for current operational "
                "facts) and NOT Second Brain organizational memory (use "
                "second_brain_search for that). A number found in a retrieved "
                "document is NOT a certified value; if the user needs a "
                "certified KPI, use the appropriate OPS tool instead, even if "
                "a document happens to mention a similar-looking number. "
                "Every result includes a citation (filename, and page number "
                "for PDFs) -- always cite it (e.g. 'According to procedure.pdf, "
                "page 4...') rather than presenting the text as your own "
                "knowledge. If nothing relevant is found, say so plainly; do "
                "not invent a citation or fill in what the document 'probably' "
                "says. A result marked [SUPERSEDED -- see X instead] is an "
                "OLDER document revision that a newer one has explicitly "
                "replaced -- prefer the newer document for current guidance, "
                "but the superseded text remains a legitimate historical "
                "answer if the user is specifically asking what an earlier "
                "revision said. SUPERSEDED is version-state metadata only -- "
                "it does NOT by itself mean the content changed. When a "
                "superseded result includes 'stored content is identical to "
                "the successor', the two documents' content matches exactly; "
                "say so and do not claim any requirement was added, removed, "
                "or changed. When it says the content hash differs, you may "
                "say the stored content differs, but never describe WHAT "
                "changed unless the actual retrieved text of both documents "
                "shows it. A result marked [SUPERSEDED -- recorded successor "
                "is missing from the workspace] means the document that was "
                "supposed to replace this one is no longer in the workspace "
                "(e.g. it was removed) -- this is a broken bookkeeping "
                "reference, not evidence that this older document is current "
                "again, and not evidence the supersession decision was wrong. "
                "Say the successor is missing if asked; do not claim this "
                "document is CURRENT and do not imply the supersession was "
                "reversed -- only a human explicitly running `jarvis document "
                "unsupersede` changes that."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search query."},
                    "filename": {
                        "type": "string",
                        "description": "Optional: restrict results to this exact filename.",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 10)."},
                },
                "required": ["query"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult(tool_name="document_search", success=False, content="query is required")

        limit = int(params.get("limit", 10))
        filename = params.get("filename")

        results = self._service.search_documents(query, top_k=limit, filename=filename)

        if not results:
            return ToolResult(
                tool_name="document_search",
                success=True,
                content="No matching documents found in the authorized workspace.",
                metadata={"num_results": 0},
            )

        lines = []
        summaries = []
        for r in results:
            citation = r.evidence.citation_label()
            if r.status == "SUPERSEDED" and r.successor_missing:
                # M2.5A orphaned-supersession repair: the recorded
                # successor no longer resolves (e.g. removed via
                # `jarvis document ingest`). Never invent a filename,
                # never say CURRENT, never imply the supersession
                # decision was reversed -- a human must run
                # `jarvis document unsupersede` to do that deliberately.
                warning = "[SUPERSEDED -- recorded successor is missing from the workspace] "
            elif r.status == "SUPERSEDED":
                successor = r.superseded_by_filename or r.superseded_by_doc_id
                if r.same_content_as_successor is True:
                    identity_note = " -- stored content is identical to the successor (same content hash)"
                elif r.same_content_as_successor is False:
                    identity_note = " -- stored content hash differs from the successor (nature of the difference not established here)"
                else:
                    identity_note = ""
                warning = f"[SUPERSEDED -- see {successor} instead{identity_note}] "
            else:
                warning = ""
            lines.append(f"{warning}[{citation}]\n{r.content}")
            summaries.append(
                {
                    "citation": citation,
                    "filename": r.evidence.filename,
                    "relative_path": r.evidence.relative_path,
                    "page": r.evidence.page,
                    "section": r.evidence.section,
                    "chunk_id": r.evidence.chunk_id,
                    "doc_id": r.evidence.doc_id,
                    "score": r.score,
                    "content": r.content,
                    "status": r.status,
                    "superseded_by_doc_id": r.superseded_by_doc_id,
                    "superseded_by_filename": r.superseded_by_filename,
                    "superseded_at": r.superseded_at,
                    "same_content_as_successor": r.same_content_as_successor,
                    "successor_missing": r.successor_missing,
                }
            )

        return ToolResult(
            tool_name="document_search",
            success=True,
            content="\n\n".join(lines),
            metadata={"num_results": len(results), "results": summaries},
        )


@ToolRegistry.register("document_list_sources")
class DocumentListSourcesTool(BaseTool):
    """Lists every document currently ingested in the authorized workspace
    -- no search, just an inventory of what MAIA can potentially cite."""

    tool_id = "document_list_sources"

    def __init__(self, service: Optional[DocumentKnowledgeService] = None) -> None:
        self._service = service or _service()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_list_sources",
            description=(
                "List every document currently ingested in the authorized MAIA "
                "document workspace (filename, type, chunk count, CURRENT/"
                "SUPERSEDED status, and when it was indexed). Use this to "
                "check what sources are available before claiming a topic isn't "
                "covered by any document. 'Indexed on <date>' is when MAIA "
                "ingested the file -- it is NOT the document's business-"
                "effective date; never claim a procedure has been in effect "
                "since its indexing date."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:  # noqa: ARG002 -- no params
        docs = self._service.list_documents()
        if not docs:
            return ToolResult(
                tool_name="document_list_sources",
                success=True,
                content="The document workspace is empty -- no documents ingested yet.",
                metadata={"num_documents": 0},
            )
        lines = []
        documents_meta = []
        for d in docs:
            indexed = _format_epoch(d.ingested_at)
            if d.status == "SUPERSEDED" and d.successor_missing:
                status_suffix = " [SUPERSEDED -- recorded successor is missing from the workspace]"
            elif d.status == "SUPERSEDED":
                if d.same_content_as_successor is True:
                    identity_note = ", same content hash as successor"
                elif d.same_content_as_successor is False:
                    identity_note = ", different content hash from successor"
                else:
                    identity_note = ""
                status_suffix = f" [SUPERSEDED by {d.superseded_by_filename or d.superseded_by_doc_id}{identity_note}]"
            else:
                status_suffix = ""
            lines.append(f"{d.filename} ({d.file_type}, {d.chunk_count} chunks, indexed {indexed}){status_suffix}")
            documents_meta.append(
                {
                    "relative_path": d.relative_path,
                    "doc_id": d.doc_id,
                    "status": d.status,
                    "ingested_at": d.ingested_at,
                    "superseded_by_doc_id": d.superseded_by_doc_id,
                    "superseded_by_filename": d.superseded_by_filename,
                    "superseded_at": d.superseded_at,
                    "same_content_as_successor": d.same_content_as_successor,
                    "successor_missing": d.successor_missing,
                }
            )
        return ToolResult(
            tool_name="document_list_sources",
            success=True,
            content="\n".join(lines),
            metadata={"num_documents": len(docs), "documents": documents_meta},
        )


__all__ = ["DocumentSearchTool", "DocumentListSourcesTool"]
