from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.document_knowledge.service import DocumentKnowledgeConfig, DocumentKnowledgeService


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def service(tmp_path: Path, workspace: Path) -> DocumentKnowledgeService:
    config = DocumentKnowledgeConfig(
        workspace_id="ws",
        workspace_root=workspace,
        knowledge_db_path=tmp_path / "index.db",
        file_state_db_path=tmp_path / "files.db",
    )
    svc = DocumentKnowledgeService(config)
    yield svc
    svc.close()
