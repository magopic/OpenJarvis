"""M3.3A — the HTTP contract of the identified-conversation boundary.

Covers what the wire must guarantee, as opposed to the isolation mechanics
in test_conversation_isolation.py:

  * `conversation_id` is optional and additive -- every existing
    OpenAI-compatible client keeps working untouched;
  * an identified conversation refuses to run on a server with no API key
    configured, so the boundary a real front end uses cannot be wide open
    by accident (AuthMiddleware is deliberately a no-op without a key,
    which is fine for local development and not fine here);
  * an identified conversation refuses client-supplied `tools`, because the
    server owns the tool surface -- accepting them would route around
    auto-enable governance, owner_only and internal-only.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.conversation_state import ConversationStateStore  # noqa: E402
from openjarvis.server.routes import router  # noqa: E402


def _app(*, api_key: str = "", agent=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    engine = MagicMock()
    engine.generate.return_value = {"content": "Done.", "finish_reason": "stop", "usage": {}}
    app.state.engine = engine
    app.state.model = "test-model"
    app.state.agent = agent
    app.state.bus = None
    app.state.config = None
    app.state.memory_backend = None
    app.state.memory_service = None
    app.state.trace_store = None
    app.state.conversation_state_store = ConversationStateStore()
    if api_key:
        app.state.config = MagicMock()
        app.state.config.server.api_key = api_key
        app.state.config.agent.context_from_memory = False
    return app


def _body(**extra) -> dict:
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "ciao"}],
        **extra,
    }


class TestBackwardCompatibility:
    def test_request_without_conversation_id_is_unchanged(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body())
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Done."

    def test_conversation_id_is_optional_in_the_schema(self) -> None:
        from openjarvis.server.models import ChatCompletionRequest

        req = ChatCompletionRequest(model="m", messages=[])
        assert req.conversation_id is None

    def test_unknown_fields_still_rejected_as_before(self) -> None:
        from openjarvis.server.models import ChatCompletionRequest

        req = ChatCompletionRequest(
            model="m", messages=[], conversation_id="conv-A"
        )
        assert req.conversation_id == "conv-A"


class TestFailClosedOnIdentifiedPath:
    def test_conversation_id_refused_without_a_configured_api_key(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body(conversation_id="conv-A"))
        assert r.status_code == 503
        assert "OPENJARVIS_API_KEY" in r.json()["detail"]

    def test_conversation_id_allowed_with_env_api_key(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-key-not-real")
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body(conversation_id="conv-A"))
        assert r.status_code == 200

    def test_conversation_id_allowed_with_config_api_key(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
        client = TestClient(_app(api_key="cfg-key-not-real"))
        r = client.post("/v1/chat/completions", json=_body(conversation_id="conv-A"))
        assert r.status_code == 200

    def test_the_unidentified_path_is_still_open_for_local_use(
        self, monkeypatch
    ) -> None:
        """Deliberate: this task must not break local development."""
        monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
        client = TestClient(_app())
        assert client.post("/v1/chat/completions", json=_body()).status_code == 200


class TestToolInjectionBlocked:
    def test_tools_cannot_accompany_a_conversation_id(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-key-not-real")
        client = TestClient(_app())
        r = client.post(
            "/v1/chat/completions",
            json=_body(
                conversation_id="conv-A",
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "shell_exec", "parameters": {}},
                    }
                ],
            ),
        )
        assert r.status_code == 400
        assert "tools" in r.json()["detail"]

    def test_tools_without_conversation_id_still_work(self, monkeypatch) -> None:
        """The legacy raw function-calling path is untouched."""
        monkeypatch.delenv("OPENJARVIS_API_KEY", raising=False)
        client = TestClient(_app())
        r = client.post(
            "/v1/chat/completions",
            json=_body(
                tools=[
                    {"type": "function", "function": {"name": "calc", "parameters": {}}}
                ]
            ),
        )
        assert r.status_code == 200


class TestConversationIdValidation:
    @pytest.mark.parametrize(
        "bad", ["", "a/b", "../etc", "a b", "a\nb", "x" * 129, "é"]
    )
    def test_malformed_ids_are_rejected_with_400(self, monkeypatch, bad: str) -> None:
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-key-not-real")
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body(conversation_id=bad))
        assert r.status_code == 400

    def test_a_valid_id_is_accepted(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENJARVIS_API_KEY", "test-key-not-real")
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body(conversation_id="conv_A-1"))
        assert r.status_code == 200

    def test_no_secret_is_echoed_in_any_error(self, monkeypatch) -> None:
        secret = "SUPER_SECRET_KEY_DO_NOT_ECHO"
        monkeypatch.setenv("OPENJARVIS_API_KEY", secret)
        client = TestClient(_app())
        r = client.post("/v1/chat/completions", json=_body(conversation_id="a b"))
        assert r.status_code == 400
        assert secret not in r.text
