"""Per-request tool selection for the function-calling loop (FASE 4L.2C).

Decides which of the agent's *already-enabled* tools to actually show the
model this turn. Purely additive to the existing enabled-tools resolution
(config.toml + the OPS Bridge auto-enable governance in
``ops_bridge_generic.py``, both unchanged) -- this module never enables,
disables, or registers a tool; it only trims what gets serialized into the
``tools=`` request parameter for one turn.

V1 is deliberately dumb (no embeddings, per instruction): keyword/substring
overlap between the query and each tool's own ``name``+``description`` --
exactly the two fields every OPS Bridge capability already exposes through
the Capability Registry's metadata, and nothing more. No Maffei/business-
domain vocabulary is hardcoded here. The Registry stays the sole source of
truth; the only structural assumption this module makes is the Generic
Adapter's own ``ops_dynamic_`` tool-id prefix, which is an OpenJarvis-side
naming convention (see ``ops_bridge_generic.py::_capability_to_tool_id``),
not a business concept -- it is what makes a tool a *routing candidate*
versus always-on, nothing more.
"""

from __future__ import annotations

import re
from typing import List

from openjarvis.tools._stubs import BaseTool

_OPS_DYNAMIC_PREFIX = "ops_dynamic_"
_REGISTRY_FALLBACK_TOOL_ID = "ops_dynamic_registry_list_capabilities"
_DEFAULT_TOP_N = 5
_WORD_RE = re.compile(r"[a-zA-Z0-9àèéìòù]+")
_MIN_TOKEN_LEN = 3
# Minimum shared *prefix* length for two tokens in different words/languages
# to count as a weak match (e.g. "produzione"/"production",
# "logistica"/"logistics" both share a 5-char prefix). This is a generic
# string-similarity heuristic, not a translation table -- it makes no
# assumption about which languages or domains are involved, and requires no
# per-word mapping to be maintained as new capabilities are added.
_FUZZY_PREFIX_LEN = 5


def _tokenize(text: str) -> set:
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= _MIN_TOKEN_LEN}


def _score(tool: BaseTool, query_tokens: set) -> int:
    haystack_tokens = _tokenize(f"{tool.spec.name} {tool.spec.description}")
    score = 0
    for qt in query_tokens:
        if qt in haystack_tokens:
            score += 2  # exact token match -- strong signal
            continue
        if len(qt) >= _FUZZY_PREFIX_LEN and any(
            len(ht) >= _FUZZY_PREFIX_LEN and qt[:_FUZZY_PREFIX_LEN] == ht[:_FUZZY_PREFIX_LEN]
            for ht in haystack_tokens
        ):
            score += 1  # shared-prefix cognate-style match -- weak signal
    return score


def _registry_fallback_tool() -> "BaseTool | None":
    """Instantiate the registry-introspection tool directly from
    ``ToolRegistry``, bypassing the auto-enabled gate.

    FASE 4I deliberately excludes ``ops.registry.list_capabilities`` from
    ``get_auto_enabled_ops_tool_ids()`` (it is infrastructural, not meant
    to sit in the always-on chat tool list) -- so it is never present in
    ``self._tools`` to route *to*. This is the one, explicit, narrow
    exception: when routing finds nothing relevant, offering this single
    already-TRUSTED, already-governance-passing discovery tool for that
    turn only is exactly the "senza caricare automaticamente tutto il
    catalogo" fallback this phase asks for -- not a new capability, not a
    change to what auto-enables permanently.
    """
    try:
        from openjarvis.core.registry import ToolRegistry

        if not ToolRegistry.contains(_REGISTRY_FALLBACK_TOOL_ID):
            return None
        tool_cls = ToolRegistry.get(_REGISTRY_FALLBACK_TOOL_ID)
        return tool_cls() if isinstance(tool_cls, type) else tool_cls
    except Exception:
        return None


def select_relevant_tools(
    tools: List[BaseTool],
    query: str,
    *,
    top_n: int = _DEFAULT_TOP_N,
) -> List[BaseTool]:
    """Return the subset of *tools* to send to the model this turn.

    Tools without the ``ops_dynamic_`` prefix (static, config.toml-enabled
    tools -- calculator, memory, web search, etc.) are always kept: this
    router only scales the part of the tool set that grows with the OPS
    Bridge Capability Registry, which is what FASE 4L.2 identified as the
    actual scaling risk. If there is nothing to route (no ops_dynamic_*
    tools present at all), the input list is returned unchanged.
    """
    always_on = [t for t in tools if not t.spec.name.startswith(_OPS_DYNAMIC_PREFIX)]
    routable = [t for t in tools if t.spec.name.startswith(_OPS_DYNAMIC_PREFIX)]

    if not routable:
        return tools

    query_tokens = _tokenize(query)
    scored = [(t, _score(t, query_tokens)) for t in routable]
    scored = [(t, s) for t, s in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[1], routable.index(pair[0])))

    selected = [t for t, _ in scored[:top_n]]

    if not selected:
        fallback = _registry_fallback_tool()
        if fallback is not None:
            selected = [fallback]

    return always_on + selected


__all__ = ["select_relevant_tools"]
