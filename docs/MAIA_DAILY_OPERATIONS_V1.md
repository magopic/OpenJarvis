# MAIA Daily Operations / Executive Attention V1 (FASE 4Q.4)

Status: deterministically certified (30 new tests + 21 in the extended
Operational Copilot suite). **Live Claude conversational certification
(STEP 18) and the independent claim review that depends on it (STEP 19)
were not run** -- `ANTHROPIC_API_KEY` is not visible in this shell, the
same documented gap as `docs/CHAT_ENGINE_SELECTION_PARITY.md` and
`docs/MAIA_NOTIFICATION_RUNTIME_V1.md`; not re-attempted, per this
engagement's "no blind retry" precedent. The strict engine-selection
guarantee itself was re-verified this phase: `jarvis chat --engine cloud
--model claude-sonnet-4-6` still produces the correct, explicit
`EngineConnectionError` refusal (no silent local fallback) rather than
hanging or substituting -- confirmed live, in this shell, this phase.

This is primarily an orchestration/reasoning phase, not an
infrastructure phase. Exactly one small primitive was added; everything
else reuses FASE 4O.x-4Q.3 unmodified.

## ARCHITECTURE AUDIT

Audited before writing any code (per instruction):

1. **Can the existing orchestrator already answer "Cosa devo guardare
   oggi?"** -- Structurally, yes: `OrchestratorAgent`'s function-calling
   loop, `operational_evidence.py`'s multi-source composition, and the
   claim-integrity ledgers are all already generic enough to reach
   `maia_notifications_list` for this question. What was missing was
   not capability but *shape*: a raw, unordered notification list gives
   the model nothing to reason about priority with except its own
   ad-hoc judgment, which is neither deterministic nor explainable.
2. **Does it know to check notifications first?** -- Only via tool
   description quality (already reasonably good after FASE 4Q.3); this
   phase strengthens it further with a purpose-built tool description
   naming the exact question shape it answers.
3. **Can it distinguish attention / normal status / acknowledged /
   historical / recommendation?** -- The raw data existed
   (severity/transition/status from FASE 4Q.3) but no code classified
   or grouped it this way before this phase.
4. **Is a priority model already present?** -- No. Audited
   `proactive_insight.py`, `monitoring/service.py`, every tool file --
   zero sort/priority/rank function existed anywhere in the codebase.
5. **Is severity/risk information sufficient?** -- Yes: `severity`
   (`INFO`/`ATTENTION`/`WARNING`/`CRITICAL`, `proactive_insight.py`,
   unchanged) and `transition`/`status`/`created_at` (FASE 4Q.3) are
   exactly the fields STEP 4 asks for -- no new field was needed.
6. **Can it drill notification -> monitor -> run -> evidence?** --
   Yes, already: `NotificationGetTool` returns the full
   `insight_snapshot` (id, title, summary, severity, confidence, status,
   `reasoning_basis`, `limitations`, `proposed_action_ids`) plus
   `monitor_id` -- everything a drill-down needs was already there.
7. **Can it use context in follow-ups?** -- Yes, unchanged: `jarvis
   chat`'s existing in-process history mechanism (FASE 4Q.1) already
   threads every prior turn, tool call, and tool result forward.
8. **Is a new tool genuinely required?** -- **Yes, exactly one**: no
   existing tool returns notifications already grouped and prioritized;
   every existing tool returns a flat list the model would have to
   reorder itself, inconsistently and unexplainably, turn after turn.

## ROOT GAP

One missing primitive: a deterministic, explainable classification +
priority ordering over already-persisted notifications. Confirmed by
audit: zero sort/rank/priority function existed anywhere before this
phase.

## IMPLEMENTATION DECISION

**Minimal, justified, single addition** -- `monitoring/attention.py`
(new module) + `maia_daily_attention_summary` (new tool). Explicitly
NOT a new subsystem:

- No new storage -- operates purely on `MonitorService.list_notifications()`'s
  existing return value.
- No new service method on `MonitorService` -- the tool calls the
  existing `list_notifications()` then post-processes in pure Python.
- No new memory, orchestrator, or scheduler -- verified structurally by
  test (`attention.py` contains no reference to `TaskScheduler`,
  `governed_actions`, Second Brain write tools, or any business-write
  capability).

## DAILY ATTENTION MODEL

STEP 3's four-way distinction, implemented as `classify_notification()`:

| Class | Rule |
|---|---|
| `ATTENTION_ITEM` | Not acknowledged, and transition is NEW/CHANGED/REOPENED -- genuinely needs review |
| `ACKNOWLEDGED` | `status == ACKNOWLEDGED` (wins regardless of transition -- already seen) |
| `INFORMATIONAL` | Not acknowledged, transition is RESOLVED -- the issue is gone, useful context, not a current problem |
| (Recommendation) | Never a `Notification` field at all -- MAIA's own reasoned text, kept structurally distinct by never being written back into any persisted evidence field (STEP 2: "do not persist fabricated narrative as evidence") |

## PRIORITY MODEL

Deterministic, three plain sort keys, no invented score:

1. **Severity** (`CRITICAL` > `WARNING` > `ATTENTION` > `INFO`) --
   proactive_insight.py's own existing ladder, not reinvented.
2. **Transition class** (NEW/REOPENED > CHANGED) -- FASE 4P.2's own
   existing vocabulary.
3. **Recency** (newest `created_at` first).

Every attention item carries an explicit `priority_reason: List[str]`
(e.g. `["severity=CRITICAL", "transition=NEW (newly requires review)",
"created_at=..."]`) -- never an opaque number. This is what lets MAIA
answer "Perché questa è la priorità?" with real, quotable factors.
Verified by test (letters C, D, E, F, G): deterministic ordering under
mixed severity, ties broken by transition then recency; a stale-but-
unread item is never silently dropped (STEP 14) but a genuinely fresher
CRITICAL item still outranks it.

## ATTENTION VS INFORMATION

Enforced structurally: `RESOLVED` notifications never enter
`attention_items` (verified letter H); `ACKNOWLEDGED` notifications
never do either, regardless of severity (verified letter E) -- an
acknowledged CRITICAL notification is not re-surfaced as new attention.

## SOURCE SELECTION

The new tool makes **exactly one** underlying call
(`list_notifications`) -- verified by test (letter P, source-inspection:
`self._service.` appears exactly once in `execute()`). It never touches
OPS, Second Brain, or Document Knowledge itself (verified letters K, N,
O) -- drilling into those remains entirely the model's own subsequent
tool-selection decision, per STEP 6's "do not call all three
automatically," reusing the exact discipline FASE 4O.6's Multi-Source
Reasoning already established.

## CONTEXT CONTINUITY

Not reinvented -- the exact mechanism FASE 4Q.1 already certified
generically is proven here specifically for the daily-briefing
conversational shape: a structural test
(`test_daily_briefing_followups_preserve_referent_across_turns`) walks
"Cosa devo guardare oggi?" (attention-tool call) into "Qual è il più
importante?" and confirms the second turn's messages still contain the
first turn's real tool result -- so "il più importante" can only ever
resolve to what the tool actually returned, never a model-invented item.
Every later follow-up in the phase's own example dialogue ("Perché?",
"Era già successo?", "C'è una procedura?", "Cosa mi consigli?",
"Controllalo domani") relies on this identical, single, unmodified
mechanism.

## DRILL-DOWN

Reused, not rebuilt: `NotificationGetTool`'s `insight_snapshot` already
carries `reasoning_basis`/`limitations`/`evidence` -- the model can
answer "Perché?"/"Da cosa dipende?" directly from this without any new
plumbing. "Era già successo?"/"C'è una procedura?" naturally route to
`second_brain_search`/`document_search` -- already-governed,
already-certified tools, unchanged.

## MISSING-EVIDENCE DISCIPLINE

Not reimplemented -- this is exactly `agents/operational_evidence.py`'s
existing, frozen `LIMITATIONS`/precedence mechanism (FASE 4O.6), which
this phase's new tool never touches or bypasses. A confirmed KPI decline
with no proven cause is already structurally forced to stay "confirmed
but unattributed" by that unchanged mechanism.

## RECOMMENDATION BOUNDARY

Structural, not narrative-only: `Notification` has no field for "cause"
or "recommendation" -- there is nowhere in the persisted schema a
recommendation *could* be mistaken for certified fact, even if the
model's own prose blurred the line. The attention tool's own description
explicitly states informational items are "not a current problem"
(verified letter M).

## NOTIFICATION INTERACTION

Unchanged, reused, re-verified working through the new attention-aware
flow (letters X, Y): `maia_notification_mark_read`/
`maia_notification_acknowledge` (FASE 4Q.3) are untouched by this phase.

## MONITORING HANDOFF

Unchanged: "Controllalo domani" still resolves to the exact same
`maia_monitor_create` tool FASE 4P.2/4Q.2 already built (verified letter
W). No task/reminder subsystem was added. The truthful constraints FASE
4Q.2 established remain exactly as they were: automatic execution
requires the separate `jarvis scheduler start` process,
`config.scheduler.enabled` is still `False` locally, no external push
exists.

## GOVERNED ACTION BOUNDARY

Untouched. `monitoring/attention.py` contains no reference to
`governed_actions`, `.approve(`, or any execution capability (verified
letters Z, AA, BB) -- a recommendation can at most lead to the model
independently choosing to call `maia_action_prepare` (unchanged,
FASE 4P.3), never directly to approval or execution.

## LIVE CLAUDE CERTIFICATION

**Not run** -- `ANTHROPIC_API_KEY` unavailable in this shell. What WAS
verified live this phase: the strict engine-selection guarantee itself
(`jarvis chat --engine cloud --model claude-sonnet-4-6` still correctly
refuses rather than silently falling back, reproduced this phase,
matching FASE 4Q.1A/4Q.3's prior confirmations). The actual
conversational dialogue (attention prioritization, drill-down reasoning,
recommendation boundary, monitoring handoff narration) requires genuine
language generation and could not be meaningfully substituted with a
scripted walkthrough the way FASE 4Q.3's mechanical read/ack lifecycle
could -- stated honestly rather than papered over with a partial
substitute presented as equivalent.

## CLAIM REVIEW

Not performed -- depends on STEP 18's live transcript, which does not
exist in this environment. Nothing is claimed here as reviewed that
wasn't.

## PERFORMANCE

`build_attention_summary()` is pure in-memory computation, no I/O:
0.30ms at 100 notifications, 1.60ms at 1,000. The daily-briefing tool
call itself is exactly one `list_notifications()` query (already
measured sub-millisecond at scale in FASE 4Q.3's own performance
section) -- no redundant or fan-out calls exist in the new tool.

## FUTURE OPS ONE INTEGRATION

No frontend built (per instruction). Conceptually, OPS ONE's future
Executive Attention view would call:

```
OPS ONE HOME / EXECUTIVE VIEW
        |
maia_daily_attention_summary  (or its future HTTP equivalent, a thin
        |                       wrapper -- no logic duplicated)
priority items (attention_items, each with priority_reason)
        |
notification detail (maia_notification_get -- unchanged, principal-gated)
        |
supporting operational evidence (insight_snapshot's reasoning_basis/
        |                        evidence -- already exists)
Ask MAIA (natural-language drill-down -- existing orchestrator)
        |
monitor / acknowledge / (future) governed action proposal
```

MAIA owns reasoning (the attention classification, priority ordering,
drill-down, recommendation). OPS ONE would own presentation and user
interaction. Both consume the exact same backend seam
(`MonitorService` + the new `attention.py` computation) -- no
competing experience was designed; consistent with FASE 4Q.2/4Q.3's own
established "backend-owned, CLI-agnostic" shape, kept ready for the
eventual OPS ONE + OpenJarvis + MAIA consolidation into one integrated
product.

## KNOWN LIMITATIONS

- Live Claude conversational certification and the claim review that
  depends on it were not performed this phase (credential
  unavailability in this shell) -- deterministic/structural
  certification (30 new tests) stands on its own, not presented as a
  substitute for the live check.
- The priority model is intentionally simple (three plain sort keys) --
  no confidence-weighted scoring, no cross-monitor correlation, no
  machine-learned ranking. This is a deliberate V1 choice (STEP 4:
  "avoid fake mathematical precision"), not an oversight.
- `build_attention_summary()` has no pagination/limit parameter of its
  own (unlike `list_notifications`, which does) -- for V1, the full
  principal-scoped notification set is always classified; at the
  measured performance (sub-2ms at 1,000 rows) this was judged
  unnecessary to add pre-emptively.
- The real OEE certification monitor (`170e4a4f6e2d4e50`) was not used
  in any test or certification this phase -- confirmed unchanged
  throughout.
