# Cloud Engine Selection Integrity (FASE 4P.3A)

Status: **FROZEN.** A real, reproducible bug -- an explicit
`--engine cloud --model claude-sonnet-4-6` request could silently
generate with a completely different local model instead -- is fixed.
This is a cross-cutting fix to `jarvis ask` generally, not specific to
Governed Actions; it happened to be discovered while trying to complete
FASE 4P.3's live certification.

## ROOT CAUSE

Two independent, compounding factors:

1. **A missing dependency, silently swallowed.** The `anthropic` Python
   package was not installed in the project's `.venv`.
   `CloudEngine._init_clients()` does:
   ```python
   if os.environ.get("ANTHROPIC_API_KEY"):
       try:
           import anthropic
           self._anthropic_client = anthropic.Anthropic()
       except ImportError:
           pass
   ```
   `ModuleNotFoundError` is a subclass of `ImportError`, so the missing
   package was caught and silently ignored -- `self._anthropic_client`
   stayed `None`, `CloudEngine.health()` correctly (from the code's own
   logic) returned `False`. Fixed by installing a version of `anthropic`
   compatible with this codebase's calling convention: `anthropic>=0.30`
   in `pyproject.toml` has no upper bound, and an unpinned install pulls
   the latest `1.x` release, whose `messages.create()` no longer accepts
   the `temperature` keyword this codebase passes -- confirmed live
   (`TypeError: Messages.create() got an unexpected keyword argument
   'temperature'`) the first time the fix below made `--engine cloud`
   reachable again. Installed `anthropic<1.0` (resolved to `0.125.0`)
   instead, verified directly against the real API. Migrating `cloud.py`
   itself to the `1.x` SDK shape is a separate, larger undertaking, out
   of this phase's scope; `pyproject.toml`'s open-ended `>=0.30` pin is
   flagged below as worth tightening.

2. **`get_engine()`'s silent fallback, architecturally too broad.**
   `InferenceEngine.can_serve()` deliberately defaults to `True` for
   local engines (documented rationale: "can serve this model id" and
   "this model is actually installed" are different concerns -- correct
   for undirected/auto engine selection). Combined with factor 1 above,
   this meant: `cloud` engine's health check failed silently ->
   `get_engine()` fell through to `config.engine.default` (`llamacpp`)
   -> `LlamaCppEngine.can_serve("claude-sonnet-4-6")` returned `True` (by
   that same deliberate default) -> a real, different local model
   (`lmstudio-community/Qwen3.5-4B-GGUF`) generated under a request that
   explicitly named a specific cloud model. This is the same class of
   silent-substitution risk this engagement has fixed for tool-claim
   integrity (FASE 4P.1B) and evidence-source impersonation (FASE 4O.6)
   -- applied here to engine/model selection itself.

## ENGINE RESOLUTION PATH

`cli/ask.py::ask()` -> `get_engine(config, effective_engine_key,
model=selection_model)` (`engine/_discovery.py`) -> `_make_engine(key,
config)` constructs the engine -> `engine.health()` /
`engine.can_serve(model)` gate selection -> the chosen `(key, engine)`
drives `engine.generate(...)`. `system/builder.py` and
`cli/chat_cmd.py` call the same `get_engine()` but never pass `model=`,
so they were never exposed to this specific bug and are unaffected by
the fix (confirmed by reading both call sites).

## FALLBACK BEHAVIOR BEFORE

`get_engine()` built one `keys_to_try` list (`[engine_key, default_key]`
when both were set) and returned the first one whose `health()` +
`can_serve()` (if `model` given) passed -- silently, regardless of
whether `engine_key` came from an explicit `--engine` flag or was
entirely absent. An explicit engine+model pairing had no stronger
guarantee than an unopinionated auto-selection.

## FALLBACK BEHAVIOR AFTER

Split into two paths based on whether **both** `engine_key` and `model`
are given together:

- **Both given** (the `--engine cloud --model claude-sonnet-4-6` shape):
  strict. That exact engine is constructed; if unregistered, unhealthy,
  or unable to serve the model, `get_engine()` raises
  `EngineConnectionError` with the concrete reason. Never silently tries
  a different engine.
- **Only `engine_key`, or neither** (unchanged, deliberately preserved):
  the original `keys_to_try` + full-discovery fallback behavior,
  including issue #73's own fix ("engine_key alone, no model opinion,
  still falls back to any healthy engine rather than hard-failing") and
  issue #532's fix ("a model-incompatible engine is skipped for one that
  can serve it"). Neither regressed -- both have their own passing
  regression tests, re-verified against this change.

`cli/ask.py` catches the new `EngineConnectionError` around the
`get_engine()` call specifically and prints a clear, red, actionable
error (reusing the existing `hint_no_engine()` helper) instead of
generating with a silently different engine, or crashing with a raw
traceback.

## EXPLICIT CLOUD GUARANTEE

`--engine cloud --model claude-sonnet-4-6` now either genuinely reaches
the Anthropic API, or fails loudly with a specific, diagnosable reason
(unregistered engine / construction failure / health check failed /
cannot serve this model) -- never a silent model swap. Verified by 8 new
deterministic tests (`tests/engine/test_cloud_engine_selection_integrity.py`)
and 3 live Claude Sonnet 4.6 calls (trivial direct-engine call,
orchestrator call with real tool use, and a Governed Actions call) --
all confirmed via the JSON output's own `"model"` field to have used
`claude-sonnet-4-6`, none silently substituted.

## MODEL SELECTION GUARANTEE

Covered by the same strict path -- when `model` is explicitly given
alongside an explicit `engine_key`, the returned engine is guaranteed to
be able to serve exactly that model (verified via `can_serve()`) or the
call raises rather than returning a mismatched pairing.

## RESULT METADATA INTEGRITY

Audited (STEP 5): the direct-engine (`--agent ''`) JSON output already
includes a `"model"` field reporting the actually-resolved model --
confirmed accurate both before this fix (it honestly reported the
silently-substituted Qwen model, which is how the bug was diagnosed in
the first place) and after (correctly reports `claude-sonnet-4-6`). No
new metadata schema was added -- existing metadata already satisfies
this requirement, and the underlying selection guarantee above makes a
mismatch structurally impossible for the explicit-pair case going
forward, reducing how much after-the-fact verification even matters.

## KNOWN LIMITATIONS

- `pyproject.toml`'s `anthropic>=0.30` has no upper bound, so a fresh
  unpinned install can again pull a `1.x` release incompatible with
  `cloud.py`'s current calling convention (`temperature` as a top-level
  `messages.create()` kwarg). Worth tightening to `anthropic>=0.30,<1.0`
  or migrating `cloud.py` to the `1.x` shape -- both are out of this
  phase's scope, flagged for a future phase.
- The `except ImportError: pass` pattern in `CloudEngine._init_clients()`
  (repeated per-provider) still silently swallows a missing package for
  each provider client individually -- this phase did not add logging
  there, since the STEP 3 architectural fix (explicit pairs never
  silently substitute) already closes the actual safety gap regardless
  of *why* a client failed to construct. A future phase could still add
  a debug log per swallowed `ImportError` for easier diagnosis.
