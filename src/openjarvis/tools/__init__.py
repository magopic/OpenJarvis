"""Tools primitive — tool system with ABC interface and built-in tools."""

from __future__ import annotations

from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# Import built-in tools to trigger @ToolRegistry.register() decorators.
# Each is wrapped in try/except so the package loads even before the
# individual tool modules are created.
try:
    import openjarvis.tools.calculator  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.think  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.retrieval  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.llm_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_read  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.web_search  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter_docker  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.repl  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.storage_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.mcp_adapter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.channel_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.http_request  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.docker_shell_exec  # noqa: F401
    import openjarvis.tools.shell_exec  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.memory_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.second_brain_tools  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.document_knowledge_tools  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.proactive_insight_tools  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.monitoring_tools  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.maia_manage  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.governed_action_tools  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.outlook_governed_registration  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.user_profile_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.skill_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_write  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.apply_patch  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.git_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.db_query  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.pdf_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.image_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.audio_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.knowledge_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.text_to_speech  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.digest_collect  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.scan_chunks  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.knowledge_sql  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.ops_bridge_production_kpi  # noqa: F401
except ImportError:
    pass

# Generic OPS Bridge adapter (FASE 4G, governance policy FASE 4I): discovers
# capabilities from the Bridge's own Registry, keeps only ones that are
# TRUSTED, non-approval, and category READ/KNOWLEDGE, and registers one tool
# per capability found -- those that pass also auto-enable for chat without
# needing a config.toml entry (see get_auto_enabled_ops_tool_ids, consumed
# by SystemBuilder._resolve_tools and serve.py's _resolve_allowed_tools).
# Best-effort -- if the Bridge is unreachable, this registers nothing and
# OpenJarvis starts up exactly as before.
try:
    from openjarvis.tools.ops_bridge_generic import (
        discover_and_register_ops_bridge_tools,
    )

    discover_and_register_ops_bridge_tools()
except Exception:
    pass

__all__ = ["BaseTool", "ToolExecutor", "ToolSpec"]
