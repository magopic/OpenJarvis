# Chat Engine Selection Parity + Turn Timeout (FASE 4Q.1A)

Status: **FROZEN.** Deterministic tests, a live refusal check (proving
the guard fires instead of silently substituting), and a subsequent
genuine live multi-turn certification against real Claude Sonnet 4.6 all
confirm the fix is correct -- see LIVE CERTIFICATION RESULT below.

Discovered while attempting FASE 4Q.1's live multi-turn certification:
`jarvis chat --engine cloud --model claude-sonnet-4-6` silently ran
against a local `llamacpp` engine for ~1h40 with no error, because
`chat_cmd.py` never had the same strict engine+model pairing guard
`ask.py` gained in FASE 4P.3A ([`docs/CLOUD_ENGINE_SELECTION_INTEGRITY.md`](CLOUD_ENGINE_SELECTION_INTEGRITY.md)).

## CHAT ENGINE SELECTION PARITY

`ask.py` (since 4P.3A) computes both an effective engine key and an
effective model, then calls the strict resolver:

```python
effective_engine_key = engine_key or config.intelligence.preferred_engine or None
selection_model = model_name or config.intelligence.default_model or None
resolved = get_engine(config, effective_engine_key, model=selection_model)
```

`chat_cmd.py` called `get_engine(config, engine_key)` -- **`model` was
never passed at all.** `engine/_discovery.py::get_engine()`'s strict
pairing branch (`if engine_key and model:`) only activates when BOTH are
truthy; omitting `model` meant chat could never reach it, regardless of
what `--model` the user gave, and silently fell into the old
auto/fallback branch instead -- exactly the pre-4P.3A behavior `ask.py`
no longer has.

**Fix**: `chat_cmd.py` now computes `effective_engine_key`/
`selection_model` identically to `ask.py` and wraps the call in the same
`except EngineConnectionError` handling (clear message, `sys.exit(1)`,
no fallback). No new resolver was written -- this reuses
`engine/_discovery.py::get_engine()` verbatim, the same function `ask.py`
already calls, unchanged.

## REQUESTED VS RESOLVED ENGINE/MODEL

The chat banner already sourced its displayed engine name from the
`(engine_name, engine) = resolved` tuple `get_engine()` actually
returned, not from the raw `--engine` CLI flag -- this was already
correct code, the underlying resolution feeding it was wrong. Once
resolution stopped silently substituting, the banner became truthful as
a direct consequence -- no separate banner-layer change was needed.
Verified by test (`test_e_banner_reports_actual_resolved_engine`): when
`get_engine()` is mocked to resolve to a DIFFERENT key than requested,
the banner reports that resolved key, proving it reads live resolution
state rather than echoing CLI input.

## STRICT EXPLICIT PAIRING

Unchanged behavior, reused exactly:

- `--engine cloud --model claude-sonnet-4-6` (both explicit) → either a
  genuine `CloudEngine` capable of serving that model, or an explicit
  `EngineConnectionError` with `sys.exit(1)` -- never a silent
  substitution.
- Only `--model` (no `--engine`), or only `--engine` (no `--model`), or
  neither → the original undirected/auto-fallback behavior (FASE #73)
  stays completely intact. Verified (`test_d_default_auto_behavior...`)
  that `get_engine()` is called with `engine_key=None` in this case --
  the strict branch is never spuriously triggered just because a model
  string happens to be set.

## TURN-LEVEL TIMEOUT

**Design** (STEP 4's audit): the per-HTTP-call timeout inside engine
clients (`_openai_compat.py`, 600s default) is not the problem --
lowering it would not have prevented the observed ~1h40 hang, since
nothing in `OrchestratorAgent`'s function-calling loop (up to
`max_turns=10` internal round-trips per user message: tool-calling,
evidence-coverage nudge, proactive-analysis check) or the loop guard
(bounds *repeated identical* tool calls, not elapsed time) bounds the
*wall-clock time of one whole user turn*. No existing timeout/
cancellation abstraction in the codebase does this at the turn level
either -- the closest (`agents/scheduler.py`'s stall detection) is a
polling mechanism for the persistent managed-agent runtime, not
applicable to a synchronous REPL call.

**Implementation**: a new `--turn-timeout` option (float seconds,
default `300.0` -- chosen to match this phase's own "~5 minutes per
turn" operational ceiling used throughout live certification). Each
turn's `agent.run(...)` / `engine.generate(...)` call is submitted to a
fresh single-worker `concurrent.futures.ThreadPoolExecutor` (the exact
same primitive `orchestrator.py` already uses for parallel tool
execution, applied one level up -- not a new abstraction), and the main
thread waits via `future.result(timeout=turn_timeout)`.

## TIMEOUT BEHAVIOR

- **Not a forced kill.** Python has no safe, cross-platform way to
  terminate a thread blocked in a C-level socket read -- doubly true on
  Windows, where this whole engagement runs. On timeout, the worker
  thread is *abandoned* (`pool.shutdown(wait=False)`), not joined --
  the chat loop does not block waiting for it. The thread itself is
  still bounded by the engine client's own per-call timeout (up to
  600s), so it is not a true unbounded zombie; CPython's own
  `concurrent.futures.thread` `atexit` hook will still join any
  surviving worker threads before the process can fully exit, so
  `jarvis chat` may take up to that long to exit after a very recent
  timeout -- an honest, bounded trade-off, not a silent leak. Documented
  under KNOWN LIMITATIONS below rather than left implicit.
- **No fabricated output.** On timeout, no `Message(role=ASSISTANT, ...)`
  is ever appended to `history`, and `publish_completed_exchange()` is
  never called for that turn. A clear `[red]Turn timed out...[/red]`
  message is shown instead.
- **Prior history survives intact.** The user's own message for the
  timed-out turn IS kept in `history` (asked, honestly left unanswered)
  -- nothing is erased, nothing invented. Verified
  (`test_h_prior_history_survives_a_timed_out_turn`): a later turn's
  `AgentContext` correctly contains the earlier successful exchange plus
  the timed-out turn's bare user message, with no fabricated reply for
  it.
- **Session continues normally.** The REPL loop simply proceeds to the
  next prompt; `/quit` still exits cleanly afterward. Verified
  (`test_j_user_can_continue_after_a_timeout`).

## LIVE CERTIFICATION RESULT

STEP 7's short live check (`jarvis chat --engine cloud --model
claude-sonnet-4-6`, message "Reply only with OK.") was first run in a
shell with no `ANTHROPIC_API_KEY` set. Result:

```
Engine error: Requested engine 'cloud' is not usable (health check failed) for
the explicitly requested model 'claude-sonnet-4-6'. Refusing to silently
substitute a different engine/model.
```

This was the **correct, expected** behavior of the fix -- reproduced
instantly (well under the 5-minute operational limit) rather than
hanging, naming the real, specific cause instead of masking it. Before
this fix, this exact situation would have silently fallen back to
`llamacpp` -- the bug this phase exists to close.

**With valid credentials present, STEP 8's real multi-turn certification
subsequently completed successfully** -- a genuine, persistent six-turn
Italian conversation ran against `Engine: cloud  Model: claude-sonnet-4-6
Agent: orchestrator`, independently cross-checked against the real OPS
Bridge, Second Brain, and Document Knowledge stores (see
`docs/MAIA_OPERATIONAL_COPILOT_V1.md`'s LIVE CERTIFICATION section for
the full transcript and verification). This is the conclusive proof the
engine-selection fix holds under real use, not just under a mocked or
refusal-path test.

## KNOWN LIMITATIONS

- A timed-out turn's worker thread is abandoned, not killed -- bounded
  by the engine client's own per-call timeout (up to 600s), and
  `jarvis chat`'s own process exit may be delayed by up to that long if
  a timeout just occurred (CPython's `concurrent.futures.thread` atexit
  hook joins surviving pool threads). No safe cross-platform forced-kill
  alternative exists, especially on Windows.
- The turn timeout applies uniformly to both the agentic path
  (`agent.run(...)`) and the direct-engine path (`engine.generate(...)`)
  -- intentionally not special-cased, for consistency and simplicity.
- `--turn-timeout` is chat-only; `jarvis ask` remains a single bounded
  CLI invocation (already implicitly bounded by process lifetime) and
  was not touched.
