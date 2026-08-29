# MAIA Outlook Governed Actions V1 (FASE 4P.4)

Status: **PARTIAL.** The governed Outlook connector's architecture,
approval flow, account guard, recipient safety, idempotency, and claim
integrity are fully built and deterministically tested against a
synthetic (zero-network) transport. Real Microsoft Graph send
certification (a genuine, irreversible, real-world action) is
deliberately deferred: this environment has zero Microsoft/Azure AD
credentials configured (confirmed by this phase's own STEP 1 audit),
and actually sending a real email additionally requires the user's
separate, explicit, real-time chat authorization. Per the user's own
choice ("Build architecture now, credentials later"), that step is not
attempted here.

This is the FIRST real external business capability connected on top of
the frozen `governed_actions/` engine (`docs/MAIA_GOVERNED_ACTIONS_V1.md`,
FASE 4P.3/4P.3A). No governance mechanism from that phase was changed in
behavior -- only extended (see RISK MODEL below).

## AUTH MODEL

Delegated OAuth2 against Microsoft's multi-tenant + personal-account
endpoint (`https://login.microsoftonline.com/common/oauth2/v2.0/
{authorize,token}`), added as a `"microsoft"` entry in
`connectors/oauth.py`'s existing `OAUTH_PROVIDERS` registry -- reuses
the same generic browser + localhost-callback harness
(`run_connector_oauth()`) and the same `load_tokens`/`save_tokens`
0o600-permission JSON file pattern already used by every other
connector in this codebase. Not invoked in this phase (no real
credentials to authorize against).

## ACCOUNT BINDING

Fail-closed by design (`outlook_capability.verify_account_guard()`):

- `OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT` (env var, mirrors
  `second_brain/identity.py`'s `OPENJARVIS_PRINCIPAL_OVERRIDE`
  escape-hatch pattern) is the one account this connector may send as.
  Never settable by the model.
- **Unconfigured is a hard refusal, not "allow anything."** If the env
  var is unset, `verify_account_guard()` raises `OutlookGuardError`
  before any send is attempted -- verified by test
  (`TestAccountGuard`).
- A configured allowlist that doesn't match the transport's
  `get_authenticated_account()` also raises, refusing to send from any
  account other than the one explicitly allowed -- verified by test.
- Checked fresh inside the execution handler, immediately before the
  real send call, not only at prepare time.

## PERMISSIONS (least privilege)

Scopes: `openid`, `email`, `profile`, `offline_access`, `Mail.Send`.
Deliberately never `Mail.Read`, `Mail.ReadWrite`, or `User.Read`.
Account-identity verification (STEP 3) is satisfied entirely from the
ID token's own `preferred_username`/`email` claim
(`_decode_id_token_claims()` -- decoded, never signature-verified,
since the token was just received directly from Microsoft's own token
endpoint over TLS in the same exchange) rather than an extra Graph call,
so no `User.Read` permission is needed at all.

## CAPABILITY CONTRACT

STEP 4's two conceptual capabilities ("prepare a draft," "send an
approved email") map directly onto the already-frozen FASE 4P.3
lifecycle rather than requiring two separate registrations:

- `GovernedActionService.prepare_action(capability="outlook_send_email",
  arguments={"to": [...], "subject": ..., "body": ...})` **is** the
  draft -- PROPOSED then PENDING_APPROVAL, held entirely locally, zero
  Graph calls.
- `execute()` reaching this capability's registered handler **is** the
  send.

No new model-facing tool was added (STEP 18) -- the existing
`maia_action_prepare`/`maia_action_request_approval` tools
(`tools/governed_action_tools.py`, unchanged this phase) already accept
an arbitrary `capability` string, so `outlook_send_email` required zero
new tool surface. `maia_action_prepare`/`request_approval`/`reject`
still have no `approved_by`, `arguments_hash`, or status-setting
parameter (verified by the existing `TestModelCannotForgeApprovalFields`
suite, unaffected by this phase). Still no `approve`/`execute`
model-callable tool exists anywhere.

Registered in `governed_actions/capabilities.py`'s closed allowlist:
`argument_schema={"to": "list_str", "subject": "str", "body": "str"}`,
`required_arguments=["to", "subject", "body"]`, `risk_class=RISK_HIGH`,
`requires_confirmation=True`, `idempotent=False`,
`timeout_seconds=30.0`.

## RISK MODEL CHANGE

FASE 4P.3 scoped `_EXECUTABLE_IN_V1` to `{RISK_LOW}` only (its one real
capability, `maia_test_write_note`, was LOW risk by design). This phase
**deliberately, explicitly** extends it to `{RISK_LOW, RISK_HIGH}` in
`governed_actions/types.py`, since `outlook_send_email` -- a real,
irreversible communication to a third party -- is correctly classified
HIGH, not LOW. This is a conscious widening of *what can run*, not a
loosening of the actual safety boundary: the real guarantee was, and
remains, "nothing executes without a real, runtime-verified,
principal-bound, hash-immutable human approval" -- every invariant STEP
3-14 of FASE 4P.3 established applies identically regardless of risk
class. `RISK_MEDIUM` stays deliberately unconnected (no registered
capability uses it). `RISK_PROHIBITED` never executes, ever, regardless
of any future change to this set. The pre-existing risk-gating fixture
test (`maia_test_high_capability`, `handler=None`) still correctly ends
FAILED -- now via "no handler configured" instead of "risk class not
executable," same terminal, non-executing outcome.

## APPROVAL FLOW

Unchanged from FASE 4P.3, reused as-is: `orchestrator.py`'s
`detect_and_apply_runtime_approval()` (never a model-callable tool)
requires BOTH an exact affirmative-phrase match against the entire
trimmed user turn (never a substring) AND exactly one
`PENDING_APPROVAL` action for the real runtime principal, before
calling `approve()` then `execute()` in pure Python -- zero model
involvement in the actual approval decision. Two-or-more pending
actions yields an explicit ambiguity block, nothing approved. Covered
for the Outlook capability specifically by this phase's new claim
integrity tests (`TestClaimIntegrity`, `tests/governed_actions/
test_outlook_capability.py`).

## RECIPIENT RULES

`normalize_recipients()` (STEP 7): accepts a single string or a list of
strings, lowercases/trims, validates each against a simple
"looks like exactly one explicit email address" pattern. **No
distribution-list or group-alias expansion of any kind** -- this layer
has no directory-lookup permission to even attempt one, and anything
that doesn't look like a single explicit address is refused, never
guessed (verified by `test_group_alias_like_name_rejected`). An empty
list, a non-string entry, or a malformed address all raise
`OutlookGuardError`. This validation runs BOTH at `prepare_action()`
time (via the new `argument_validator` hook on `CapabilityDefinition`,
so a malformed recipient is rejected before the user is ever asked to
approve it) and again, defensively, inside the execution handler
immediately before the real send.

## ATTACHMENTS -- OUT OF SCOPE (STEP 8)

Deliberately not implemented in V1. `argument_schema` has no attachment
field; there is no code path that could accept, encode, or send a file
via this capability. A future phase would need its own explicit
schema/validation/size-limit design -- not silently bolted on here.

## IDEMPOTENCY

Enforced entirely by the unchanged `GovernedActionService.execute()`
EXECUTED-short-circuit (STEP 10) -- not by the Graph API, which has no
idempotency-key support for `sendMail`. A repeated `execute()` call
against an already-`EXECUTED` action id returns immediately with zero
additional side effects and zero additional transport calls (verified
by test: the synthetic transport's `sent_log` gains exactly one entry
across multiple repeated execute calls).

## FAILURE HANDLING

`SyntheticGraphTransport.fail_mode` simulates each STEP 11 failure
class independently, all raising `OutlookGuardError` (caught by the
capability handler, turned into a FAILED `GovernedAction`, never an
unhandled crash):

- `auth_expired` -- simulated expired/missing Microsoft auth token.
- `graph_unavailable` -- simulated network/timeout failure.
- `invalid_recipient` -- simulated provider-side recipient rejection.
- `rate_limit` -- simulated Graph 429.

`RealGraphTransport` implements the equivalent real-world cases: HTTP
transport errors, a one-time 401-triggered refresh-and-retry (mirrors
`google_auth.py`'s refresh-on-401 pattern), 429, and other >=400
responses -- all raising `OutlookGuardError` with the real HTTP status
folded into the message, never silently swallowed.

## CLAIM INTEGRITY

New tests (`TestClaimIntegrity` in `tests/governed_actions/
test_outlook_capability.py`) mirror FASE 4P.3's orchestrator-level
`test_x_.../test_y_...` pattern, applied to this capability specifically,
covering BOTH directions:

- **Pre-execution**: before any Outlook action resolves,
  `[ACTUALLY_EXECUTED_TOOLS]` correctly shows nothing executed.
- **Post-execution, success**: after a runtime-resolved approval against
  a synthetic transport with no `fail_mode`, the injected
  `[GOVERNED_ACTION_EVENT]` block reports `EXECUTED` and the real action
  id; the synthetic transport's `sent_log` independently confirms the
  send actually happened.
- **Post-execution, failure** (new for this phase): with
  `fail_mode="graph_unavailable"`, the block reports `FAILED`, never
  `EXECUTED` -- the model has no path to claim a send succeeded when
  the runtime observed a provider failure.

## SECRETS

Backend-only configuration throughout. Verified structurally
(`TestNoSecretLeakage`):

- `execution_result` (the dict returned to the `GovernedAction` and
  surfaced via `maia_action_get`) contains only `{"sent", "to",
  "message_id", "provider", "account"}` -- no access token, refresh
  token, or client secret field exists anywhere in that return path.
- `RealGraphTransport.get_authenticated_account()` fails closed
  (`OutlookGuardError`) rather than fabricating or exposing anything
  when no credentials file exists.
- Nothing in this module ever places a secret in a log line, an audit
  row, or a model-visible tool result. The audit table (below) only
  ever records the arguments hash, never raw argument values or tokens.
- No secrets were committed; none exist in this environment to commit.

## AUDIT

Inherits FASE 4P.3's unchanged `AuditEntry` mechanism exactly --
`action_id`, `timestamp`, `previous_status`, `new_status`, `principal`,
`reason`, `capability`, `arguments_hash`. A full Outlook send lifecycle
produces the same transition sequence as any other capability (prepared
-> approval requested -> approved -> executing -> executed/failed), with
`capability="outlook_send_email"` and the send's own arguments hash --
never the raw recipient list, subject, or body.

## LIVE CERTIFICATION -- DEFERRED (STEP 20/14, PARTIAL)

Per the user's explicit choice, real Microsoft Graph send certification
is **not attempted** in this phase. Two independent, genuine blockers,
neither of which is a code gap:

1. **No real Azure AD app credentials exist in this environment** (STEP
   1's own audit, re-confirmed here).
2. **Actually sending a real email is a real, irreversible external
   action** requiring the user's separate, explicit, real-time chat
   authorization at the moment of the send -- this is a standing
   operating constraint, not specific to this phase.

What full deterministic (non-live) testing against
`SyntheticGraphTransport` DOES certify, equivalently to what STEP 20's
scenarios ask for: draft/prepare, inspect pending content, explicit
approval via the exact same runtime-only hook used for every other
capability, a successful synthetic "send" with independently-verifiable
evidence (`sent_log`), a correctly-FAILED outcome under every STEP 11
failure mode, idempotent no-op on repeated execute, and ambiguity
handling with two pending actions -- all already exercised by FASE
4P.3's own service-level and orchestrator-level test suites, which this
capability is fully subject to (same `GovernedActionService`, same
runtime hook, no capability-specific bypass of any kind).

To complete live certification later: the user provides real Azure AD
app registration credentials (client id, and client secret if using a
confidential-client flow) and a target test mailbox, sets
`OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT`, runs `run_connector_oauth()` for
the `"microsoft"` provider once to obtain real tokens, sets
`OPENJARVIS_OUTLOOK_REAL_SEND=1` (the explicit opt-in gate added in
`tools/outlook_governed_registration.py` -- without it, `RealGraphTransport`
is never used even with real credentials present), and separately,
explicitly authorizes one controlled test send in chat at that time.

## DEFAULT REGISTRATION -- DEFENSE IN DEPTH

`tools/outlook_governed_registration.py` (new, imported at
`tools/__init__.py` load time, mirroring how every other tool module
registers itself) registers `outlook_send_email` by default using
`SyntheticGraphTransport` -- **not** `RealGraphTransport` -- unless the
environment variable `OPENJARVIS_OUTLOOK_REAL_SEND=1` is explicitly
set. This means: even though the capability is registered and reachable
by the model's existing `maia_action_prepare`/`request_approval` tools
in every normal session, no real network call to Microsoft Graph can
ever happen unless a human has deliberately flipped this switch --
independent of, and in addition to, the account guard and the approval
gate. Three independent gates stand between "model drafts an email" and
"a real email leaves this system": (1) the runtime-only approval hook,
(2) the account guard, (3) this explicit real-send opt-in. This
environment has none of the three engaged for real sending -- the
default synthetic registration is what every test and any live chat
session in this environment actually exercises today.

## KNOWN LIMITATIONS

- **Real send is honestly untested against a live Microsoft tenant.**
  `RealGraphTransport` is real, carefully-written code (not a stub) but
  has zero live verification in this environment -- stated here
  explicitly, not left implicit.
- No attachment support (STEP 8, by design -- see above).
- No distribution-list/group-alias expansion (STEP 7, by design) --
  every recipient must be an explicit address.
- `RISK_MEDIUM` capabilities remain unconnected; this phase only
  activates `RISK_HIGH` execution for this one specific capability.
- No calendar or ERP connector was added or touched (explicitly out of
  scope per this phase's own instructions).
- The existing IMAP-based `connectors/outlook.py` (`OutlookConnector`,
  app-password auth) is untouched and unrelated -- this phase's Graph
  capability uses a separate `connector_ids=("outlook_graph",)` OAuth
  provider entry to avoid any collision.
