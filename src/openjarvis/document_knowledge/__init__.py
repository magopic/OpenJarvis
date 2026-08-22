"""MAIA Document Knowledge Ingestion V1 (FASE 4O.5).

Governs the path: AUTHORIZED LOCAL WORKSPACE -> document -> ingestion ->
source/evidence identity -> parsed knowledge -> governed retrieval -> MAIA.

Explicitly a sibling to, never part of, ``second_brain/``:

- DOCUMENT KNOWLEDGE (this package) is textual material a user placed in an
  authorized workspace (procedures, manuals, notes, reports) -- traceable to
  a file, never a certified number.
- SECOND BRAIN (``second_brain/``) is governed experiential/organizational
  memory (PROBLEM/DECISION/OUTCOME/... entries), created only through its
  own propose/confirm workflow. Nothing in this package writes a Second
  Brain entry, directly or implicitly.
- OPS ONE's Calculator/Semantic Layer remains the sole authority for
  certified operational KPIs. A document's retrieved text is context, never
  a substitute for or override of a certified value.

See ``docs/MAIA_DOCUMENT_KNOWLEDGE_V1.md`` for the full contract.
"""

from __future__ import annotations

__all__: list[str] = []
