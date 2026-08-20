# OpenJarvis Agent Runtime — Freeze V1

**Scope:** OpenJarvis Experimental only (`C:\Projects\OpenJarvis-Vanilla-Claude\OpenJarvis`, branch `fix/operators-persistence`).
Vanilla, Latest Official, and OPS ONE are out of scope and were not touched to produce this document.

This document freezes the current state of OpenJarvis Experimental as a working **Agent Runtime**,
and defines the neutral contract that will let it be connected to — and later replaced in — a
business system (OPS ONE) without either side depending on the other's internals.

---

## 1. Runtime State

- **Branch:** `fix/operators-persistence`
- **HEAD at freeze time:** `fa4404b2` — *fix(speech): configure Kokoro for Italian instead of forcing English G2P*
- **Working tree:** clean except two pre-existing, unrelated artifacts present since before this
  work began (`frontend/src-tauri/Cargo.toml` line-ending normalization, `frontend/tsconfig.tsbuildinfo`
  incremental build cache) — neither touched intentionally in any of the work below.

### Principal commits of phase 3C (voice stabilization + performance)

| Commit | Summary |
|---|---|
| `675095d0` | feat(voice): continuous VAD-driven voice conversation loop *(pre-existing, baseline for this phase)* |
| `22c2a3ca` | feat(speech): add `/v1/speech/synthesize` endpoint for Chat response → TTS *(pre-existing, baseline for this phase)* |
| `7728e1e1` | fix(voice): single source of truth for Continuous Voice conversation history — removed a second, disconnected `historyRef` that let stray VAD misfires contaminate context; voice and text chat now share one history |
| `4daccf53` | fix(desktop): add `media-src` to Tauri CSP for Continuous Voice TTS playback — CSP lacked `media-src`, so `blob:` audio URLs were silently blocked, TTS never played |
| `df077afb` | perf(speech): reuse TTS backend instance across `/v1/speech/synthesize` requests — Kokoro's pipeline was being rebuilt from scratch on every call (~19-26s per short sentence); now cached, ~2s warm |
| `7946cc05` | perf(voice): pass explicit `language=it` to STT for Continuous Voice — removes auto-detect overhead/misdetection risk on short Italian utterances |
| `fa4404b2` | fix(speech): configure Kokoro for Italian instead of forcing English G2P — `lang_code="i"`, both Italian voices (`if_sara`, `im_nicola`) generated and compared, no voice chosen definitively |

---

## 2. Feature Matrix

| Feature | Status | Note |
|---|---|---|
| Chat (text) | **PASS** | Extensively exercised throughout this engagement, text and voice share one `useAppStore` history |
| Continuous Voice (VAD → STT → Chat → TTS → back to listening) | **PASS** | Verified end-to-end with real microphone speech, including multi-turn context (`2+2=4` → `×3=12`) |
| STT (faster-whisper) | **PASS** | Explicit `language=it` reduces latency (~8-17s → ~2-5s) and raises confidence (~0.93-0.99 → 1.00) vs auto-detect |
| TTS (Kokoro) | **PASS** | Instance-reuse fix removes per-request reload; Italian G2P (`lang_code="i"`) replaces the previous English-forced phonemization |
| Agents | **PASS** *(per user-confirmed baseline in 3C.9; not independently re-exercised in this document's session)* | |
| Memory | **PASS** *(per user-confirmed baseline in 3C.9; not independently re-exercised in this document's session)* | |
| Scheduler | **PASS** *(per user-confirmed baseline in 3C.9; not independently re-exercised in this document's session)* | |
| Desktop (Tauri/WebView2) | **PASS** | Rebuilt and relaunched multiple times across this engagement; a persistent WebView2-profile cache issue (stale Service Worker serving a pre-fix bundle after app restart) was identified and is a known operational gotcha, not a code defect — documented below |
| Tools | **Not independently verified in this session** | No tool-invocation path was exercised directly; only inferred healthy via `Agents PASS` |
| Approval flow | **Not independently verified in this session** | `/v1/approvals/pending` responded 200 during routine polling; the actual approve/deny action path was never exercised |
| Data Sources | **Not independently verified in this session** | Out of scope for phases 3C.7–3C.9 |
| Wake Word | **ENGINE VALIDATED / PRETRAINED MODEL NOT RELIABLE** | See §3 |

---

## 3. Wake Word Status

**Classification: ENGINE VALIDATED / PRETRAINED MODEL NOT RELIABLE — not integrated.**

What was validated (spike only, isolated Python venv, zero OpenJarvis code touched):
- `openwakeword` installs and runs cleanly on Windows with a real microphone (`sounddevice`, MME host API, 44.1kHz native device successfully opened at the requested 16kHz).
- The pretrained `hey_jarvis` model loads and produces continuous per-frame scores with no crashes over long sessions (tens of thousands of frames).
- CPU/RAM footprint is light: ~15-27% of one core, ~180-186MB RSS — negligible next to the LLM's own budget.
- A minimal feedback harness (immediate console print + Windows beep on detection, 2s cooldown) was built and worked correctly for interactive testing.

What was **not** validated — accuracy:
- A controlled 10-attempt test ("Hey Jarvis" × 10, threshold 0.5) produced **0/10** detections inside strict per-attempt windows (max score 0.1476); one negative phrase scored higher (0.1146) than 9 of the 10 genuine attempts.
- A second controlled 5-attempt test also produced **0/5** inside strict windows, though a very high-confidence cluster (0.98, five consecutive frames) landed just outside the window boundary — most likely attributable to detection latency (openWakeWord's classifier has a ~1.3s rolling context window) rather than window mis-timing alone.
- A 2-minute+ free-running session (5 spoken attempts) produced 4 raw score-≥0.5 clusters total across the whole recording, only 2 of which fell inside a reasonable 2-minute window; one near-miss (~0.33) was also observed.

**Conclusion:** the *engine* (install, mic access, inference pipeline, feedback loop) is sound and low-cost.
The *pretrained `hey_jarvis` model*, on this hardware/microphone/voice, does not clear its own default
threshold reliably enough to be usable as-is. It was deliberately **not integrated** into OpenJarvis per
the explicit instruction that governed this spike. Any future attempt would need either threshold tuning
validated against a much larger sample, a custom-trained wake word, or a different engine — none of which
were pursued here.

---

## 4. Known Limitations

- **Hardware-bound LLM latency.** Qwen3.5-4B (Q4_K_M) on this machine (Intel i5-1235U, 10C/12T, Iris Xe iGPU,
  16GB RAM, no discrete GPU) decodes at roughly 1.8-5.3 tok/s depending on configuration and load; prompt
  eval ranges ~16-35 tok/s. A full voice turn (STT + generation + TTS) commonly takes 40-130+ seconds,
  dominated by generation. This is a hardware ceiling, not a software defect — explicitly **deferred to
  future, more capable hardware** rather than optimized further in software (per the scope set for phase 3C.9).
- **Default llama-server config was unstable on this machine.** Out of the box (`n_slots=4`,
  full-size context per slot) the KV-cache allocation alone left the system with under 1GB free RAM at
  idle, and both `llama-server` and the OpenJarvis backend were observed to die silently (no crash log)
  under that pressure. Stabilized via `-dev Vulkan0 -ngl 999 --ctx-size 8192`, which comfortably supports
  5+ turn conversations (observed prompt sizes stayed under 110 tokens for 5 turns; real app usage with
  full history has reached ~2200+ tokens, still well inside 8192).
- **WebView2 profile caching.** Because the Tauri app's WebView2 user-data folder persists across process
  restarts, a Service-Worker-driven precache can keep serving a pre-rebuild JS bundle (and its embedded
  CSP) after a code fix and rebuild, masking the fix until the profile's `Cache`/`Code Cache`/`Service
  Worker` directories are cleared. This is an operational gotcha for anyone iterating on the Desktop build,
  not a defect in the shipped app.
- **Wake Word**: see §3 — not usable as-is.
- **Tools / Approval / Data Sources**: not exercised in this session's work; carried forward from the
  prior validated baseline without independent re-verification here.

---

## 5. Agent Runtime Contract

A neutral, OpenJarvis-agnostic contract any agent runtime (OpenJarvis today, Hermes or another
runtime tomorrow) must implement to sit behind the OPS Bridge.

```
AgentRequest {
  conversation_id: string
  user_input: string
  modality: "text" | "voice"
  context?: object            // opaque to the bridge; runtime-defined
  allowed_tools?: string[]    // capability names the runtime may invoke this turn
}

AgentResponse {
  status: "ok" | "error" | "partial"
  answer: string
  tool_calls?: ToolCall[]
  citations?: Citation[]
  errors?: ErrorDetail[]
  runtime_metadata?: object   // opaque to the caller; runtime-defined (timing, model used, etc.)
}
```

Rules:
- No OpenJarvis- or MAIA-specific naming, types, or assumptions appear in this contract.
- `context` and `runtime_metadata` are intentionally opaque `object` — each runtime may shape them
  differently; neither the Bridge nor OPS ONE should parse their internals.
- A runtime is conformant if it can be driven purely through `AgentRequest` → `AgentResponse`, with no
  side-channel dependency on how it stores conversation state, which model it runs, or how it does STT/TTS.

---

## 6. OPS Bridge Contract

The Bridge is the **only** boundary between any Agent Runtime and OPS ONE. It exposes business
**capabilities**, never raw data access:

```
ops.production.get_kpi
ops.logistics.get_kpi
ops.knowledge.search
ops.actions.create_task
```

The Agent Runtime never sees:
- Supabase (or any other datastore) directly
- table/schema names
- internal KPI formulas
- credentials of any kind
- SQL or any other query language

Every Bridge capability returns the same envelope shape, regardless of which capability was called:

```
BridgeResponse {
  status
  data
  source
  period
  reason
  confidence_status
}
```

This document defines the contract only. **No Bridge implementation exists yet** — building it is
explicitly out of scope for this freeze.

---

## 7. Adapter Pattern

```
Today:
  OpenJarvisAdapter → OPS Bridge → OPS ONE

Tomorrow:
  HermesAdapter      → OPS Bridge → OPS ONE
```

An Adapter's only job is translating between one runtime's native shape and the neutral
`AgentRequest`/`AgentResponse` contract (§5), and — on the other side — between the Bridge's
capability calls (§6) and however the runtime chooses to expose "tools" internally.

An Adapter must **not** contain:
- business logic
- KPI calculation
- direct data access
- domain-specific validation

If an Adapter starts accumulating any of the above, that logic belongs in the Bridge, not the Adapter —
this is the tell that the boundary is being violated.

---

## 8. Portability Checklist

**Must stay OUTSIDE the Agent Runtime** (i.e., must live in the Bridge or in OPS ONE itself):
- business logic
- KPI calculation
- database access
- business-domain authentication/authorization
- validation of business data
- domain trust decisions
- audit / data provenance tracking

**May legitimately live INSIDE the Agent Runtime**:
- conversation state
- personal memory
- voice (STT/TTS/VAD/wake word)
- tool selection (which tool to call, not what the tool does)
- personal scheduling
- agentic UX (chat, approvals UI, telemetry display, etc.)

**Rule of thumb:** if removing a piece of logic from the Agent Runtime and swapping in a different
runtime (Hermes) would silently change a business outcome, that logic was in the wrong place.

### OpenJarvis → Hermes substitution procedure (for when that day comes)

1. Implement a `HermesAdapter` conforming to §5's `AgentRequest`/`AgentResponse` contract.
2. Point the Bridge's runtime-facing side at `HermesAdapter` instead of `OpenJarvisAdapter` — the
   Bridge's business-facing side (§6) does not change at all.
3. Verify Hermes never receives Supabase access, credentials, table names, or SQL — same portability
   checklist (§8) applies unchanged to any runtime.
4. Retire `OpenJarvisAdapter` only after `HermesAdapter` passes the same conformance checks OpenJarvis
   passed here (§2's feature matrix, re-run against Hermes).
5. OPS ONE requires zero changes throughout this process — that is the point of the Bridge boundary.

---

## 9. What This Freeze Does Not Do

- Does not implement the OPS Bridge (§6 is a contract only).
- Does not connect to OPS ONE in any way.
- Does not create any MCP server/tooling.
- Does not integrate Wake Word (§3).
- Does not modify Vanilla, Latest Official, or OPS ONE.
