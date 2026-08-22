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

from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.document_knowledge.service import DocumentKnowledgeService
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _service() -> DocumentKnowledgeService:
    return DocumentKnowledgeService()


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
                "says."
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
            lines.append(f"[{citation}]\n{r.content}")
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
                "document workspace (filename, type, chunk count). Use this to "
                "check what sources are available before claiming a topic isn't "
                "covered by any document."
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
        lines = [f"{d.filename} ({d.file_type}, {d.chunk_count} chunks)" for d in docs]
        return ToolResult(
            tool_name="document_list_sources",
            success=True,
            content="\n".join(lines),
            metadata={"num_documents": len(docs), "documents": [d.relative_path for d in docs]},
        )


__all__ = ["DocumentSearchTool", "DocumentListSourcesTool"]
