"""FASE 4P.3A -- cloud engine selection integrity test matrix (STEP 6).

Root cause: `anthropic` package missing from the venv made CloudEngine's
health() return False; get_engine() then silently fell through to
whatever local engine was configured/discovered, which (by
InferenceEngine.can_serve()'s deliberate True-for-any-model default for
local engines) claimed it could serve "claude-sonnet-4-6" and generated
with a completely different, silently-substituted local model.

Fix scope (deliberately narrow, see _discovery.py's own docstring): only
when BOTH engine_key AND model are given together does get_engine()
become strict (succeed exactly as requested, or raise
EngineConnectionError) -- preserving pre-existing #73 behavior
(engine_key alone, no model opinion, still falls back to any healthy
engine) and #532 behavior (no engine_key, model given, skips engines
that can't serve it) unchanged.
"""

from __future__ import annotations

from unittest import mock

import pytest

from openjarvis.core.config import JarvisConfig
from openjarvis.core.registry import EngineRegistry
from openjarvis.engine._base import EngineConnectionError, InferenceEngine
from openjarvis.engine._discovery import get_engine
from openjarvis.engine.cloud import CloudEngine


class _FakeEngine(InferenceEngine):
    engine_id = "fake"

    def __init__(self, *, healthy: bool = True, serves: bool = True, **kwargs) -> None:
        self._healthy = healthy
        self._serves = serves

    def generate(self, messages, *, model, **kwargs):
        return {"content": "ok", "usage": {}, "model": model}

    async def stream(self, messages, *, model, **kwargs):
        yield "ok"

    def list_models(self):
        return []

    def health(self) -> bool:
        return self._healthy

    def can_serve(self, model: str) -> bool:
        return self._serves


def _reg(key: str) -> None:
    cls = type(key.title(), (_FakeEngine,), {"engine_id": key})
    EngineRegistry.register_value(key, cls)


class TestExplicitEngineModelPairing:
    def test_a_explicit_cloud_plus_valid_model_selects_cloud(self):
        """A: explicit cloud + a model it can serve -> CloudEngine selected,
        no exception, no substitution."""
        EngineRegistry.register_value("cloud", CloudEngine)
        _reg("llamacpp")
        cfg = JarvisConfig()
        cfg.engine.default = "llamacpp"

        def _make(k, c):
            if k == "cloud":
                eng = CloudEngine.__new__(CloudEngine)
                for name in (
                    "_openai_client", "_anthropic_client", "_google_client",
                    "_openrouter_client", "_minimax_client", "_deepseek_client", "_codex_client",
                ):
                    setattr(eng, name, object() if name == "_anthropic_client" else None)
                return eng
            return _FakeEngine(healthy=True, serves=True)

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            result = get_engine(cfg, "cloud", model="claude-sonnet-4-6")
        assert result is not None
        assert result[0] == "cloud"
        assert isinstance(result[1], CloudEngine)

    def test_b_explicit_cloud_init_failure_raises_no_local_fallback(self):
        """B: explicit cloud + model, but the cloud engine is unhealthy
        (e.g. anthropic package missing, exactly the live-reproduced bug)
        -> explicit EngineConnectionError, NEVER a silently substituted
        local engine."""
        EngineRegistry.register_value("cloud", CloudEngine)
        _reg("llamacpp")
        cfg = JarvisConfig()
        cfg.engine.default = "llamacpp"

        def _make(k, c):
            if k == "cloud":
                eng = CloudEngine.__new__(CloudEngine)
                for name in (
                    "_openai_client", "_anthropic_client", "_google_client",
                    "_openrouter_client", "_minimax_client", "_deepseek_client", "_codex_client",
                ):
                    setattr(eng, name, None)  # all clients missing -- health() False
                return eng
            # A local engine IS running and claims (per its deliberate
            # default) that it can serve any model, including this one.
            return _FakeEngine(healthy=True, serves=True)

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            with pytest.raises(EngineConnectionError, match="not usable"):
                get_engine(cfg, "cloud", model="claude-sonnet-4-6")

    def test_c_explicit_local_engine_selected(self):
        """C: explicit local engine + a model it serves -> selected normally."""
        _reg("ollama")
        cfg = JarvisConfig()

        with mock.patch(
            "openjarvis.engine._discovery._make_engine",
            side_effect=lambda k, c: _FakeEngine(healthy=True, serves=True),
        ):
            result = get_engine(cfg, "ollama", model="qwen3.5:4b")
        assert result is not None
        assert result[0] == "ollama"

    def test_d_no_explicit_engine_configured_fallback_preserved(self):
        """D: no explicit engine_key -- existing default+discovery fallback
        behavior is completely unchanged."""
        _reg("bad")
        _reg("good")
        cfg = JarvisConfig()
        cfg.engine.default = "bad"

        def _make(k, c):
            return _FakeEngine(healthy=(k == "good"), serves=True)

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            result = get_engine(cfg, model="whatever")
        assert result is not None
        assert result[0] == "good"

    def test_e_explicit_model_not_replaced_silently(self):
        """E: the (key, engine) pair returned is exactly the one requested
        -- nothing about the pairing is silently altered."""
        EngineRegistry.register_value("cloud", CloudEngine)
        cfg = JarvisConfig()

        def _make(k, c):
            eng = CloudEngine.__new__(CloudEngine)
            for name in (
                "_openai_client", "_anthropic_client", "_google_client",
                "_openrouter_client", "_minimax_client", "_deepseek_client", "_codex_client",
            ):
                setattr(eng, name, object() if name == "_anthropic_client" else None)
            return eng

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            key, engine = get_engine(cfg, "cloud", model="claude-sonnet-4-6")
        assert key == "cloud"
        assert engine.can_serve("claude-sonnet-4-6")

    def test_f_explicit_cloud_cannot_serve_model_raises(self):
        """F: cloud engine is healthy but genuinely cannot serve the
        requested model (e.g. a gpt-* model with no OpenAI client) ->
        explicit error, not a silent local substitution."""
        EngineRegistry.register_value("cloud", CloudEngine)
        _reg("llamacpp")
        cfg = JarvisConfig()
        cfg.engine.default = "llamacpp"

        def _make(k, c):
            if k == "cloud":
                eng = CloudEngine.__new__(CloudEngine)
                for name in (
                    "_openai_client", "_anthropic_client", "_google_client",
                    "_openrouter_client", "_minimax_client", "_deepseek_client", "_codex_client",
                ):
                    setattr(eng, name, object() if name == "_anthropic_client" else None)
                return eng
            return _FakeEngine(healthy=True, serves=True)

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            with pytest.raises(EngineConnectionError, match="cannot serve"):
                get_engine(cfg, "cloud", model="gpt-5")

    def test_unregistered_explicit_engine_raises(self):
        cfg = JarvisConfig()
        with pytest.raises(EngineConnectionError, match="not a registered engine"):
            get_engine(cfg, "totally_not_a_real_engine", model="claude-sonnet-4-6")

    def test_engine_only_no_model_still_falls_back_73(self):
        """Explicit engine_key WITHOUT a model -- #73's own scenario --
        must still fall back to any healthy engine, unchanged."""
        _reg("requested")
        _reg("running")
        cfg = JarvisConfig()
        cfg.engine.default = "requested"

        def _make(k, c):
            return _FakeEngine(healthy=(k == "running"), serves=True)

        with mock.patch("openjarvis.engine._discovery._make_engine", side_effect=_make):
            result = get_engine(cfg, engine_key="requested")
        assert result is not None
        assert result[0] == "running"
