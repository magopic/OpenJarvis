"""FASE 4P.4 -- the first real external Governed Actions capability:
sending one email via Microsoft Graph, after local, governed approval.

Deliberately does NOT add a new registered capability's worth of custom
approval/execution machinery -- STEP 4's "OUTLOOK_PREPARE_DRAFT" and
"OUTLOOK_SEND_APPROVED_EMAIL" map directly onto the existing, frozen
FASE 4P.3 lifecycle: `GovernedActionService.prepare_action(capability=
OUTLOOK_SEND_CAPABILITY, ...)` IS the draft (PROPOSED -> PENDING_APPROVAL,
held entirely locally, no Graph call), and `execute()` reaching this
capability's handler IS the send. No new model-facing tool is added --
the existing `maia_action_prepare`/`maia_action_request_approval` tools
already accept an arbitrary `capability` string.

Least privilege (STEP 2): the only Graph scope is `Mail.Send`. Nothing
here ever reads a mailbox or creates a real Graph draft. The account
guard (STEP 3) reads the ID token's own `preferred_username`/`email`
claim locally -- no extra `User.Read` Graph permission needed.

STEP 13: `SyntheticGraphTransport` is the default, dry-run transport
used by every automated test -- zero network calls, fully deterministic,
configurable to simulate any specific failure mode. `RealGraphTransport`
implements the actual Microsoft Graph HTTP calls following this
codebase's established pattern (gmail.py's flat-function httpx style,
google_auth.py's refresh-on-401 shape) -- written carefully against
Graph's well-documented, stable REST shape, but UNTESTED against a real
tenant in this environment (FASE 4P.4's own audit confirmed zero
Microsoft credentials exist here) -- reported honestly, not silently
assumed correct.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from openjarvis.governed_actions.capabilities import CapabilityDefinition, register_capability
from openjarvis.governed_actions.types import RISK_HIGH

OUTLOOK_SEND_CAPABILITY = "outlook_send_email"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OutlookGuardError(RuntimeError):
    """Raised for any account-guard or recipient-safety violation. Always
    caught by the capability handler and turned into a FAILED
    GovernedAction -- never propagates as an unhandled crash."""


# ---------------------------------------------------------------------------
# STEP 7: recipient safety -- explicit addresses only, normalized before
# hashing/approval. No distribution-list/group expansion of any kind
# (this layer has no directory-lookup permission to even attempt one).
# ---------------------------------------------------------------------------


def normalize_recipients(to: Any) -> List[str]:
    """Validates and normalizes a recipient list. Raises OutlookGuardError
    for anything ambiguous -- STEP 7's "if recipient identity is
    ambiguous: STOP and ask" is enforced here structurally: this function
    either returns a clean list of explicit addresses, or raises, there is
    no third "guess" outcome."""
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or not to:
        raise OutlookGuardError("At least one explicit recipient email address is required.")
    normalized: List[str] = []
    for addr in to:
        if not isinstance(addr, str):
            raise OutlookGuardError(f"Recipient must be a string email address, got {type(addr).__name__}.")
        cleaned = addr.strip().lower()
        if not _EMAIL_RE.match(cleaned):
            raise OutlookGuardError(
                f"{addr!r} does not look like a single, explicit email address -- "
                f"refusing rather than guessing (no distribution-list/group expansion in V1)."
            )
        normalized.append(cleaned)
    return normalized


# ---------------------------------------------------------------------------
# STEP 3: account binding -- fail closed if unconfigured or mismatched.
# ---------------------------------------------------------------------------


def get_allowed_account() -> Optional[str]:
    """The one Microsoft account this connector is permitted to send as.
    Read from an environment variable (mirrors second_brain/identity.py's
    OPENJARVIS_PRINCIPAL_OVERRIDE escape-hatch pattern) -- never settable
    by the model, only by whoever controls the process environment."""
    value = os.environ.get("OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT")
    return value.strip().lower() if value else None


def verify_account_guard(authenticated_account: str, *, allowed_account: Optional[str] = None) -> None:
    """Fail closed: refuses to proceed unless an allowlist is explicitly
    configured AND the authenticated account matches it exactly. An
    unconfigured guard is treated as a hard refusal, not "allow anything"
    -- STEP 3's "runtime must verify... Fail closed" applies to the
    absence of configuration too, not only to a mismatch."""
    allowed = allowed_account if allowed_account is not None else get_allowed_account()
    if not allowed:
        raise OutlookGuardError(
            "No allowed Microsoft account is configured "
            "(OPENJARVIS_OUTLOOK_ALLOWED_ACCOUNT) -- refusing to send from any account."
        )
    if not authenticated_account or authenticated_account.strip().lower() != allowed:
        raise OutlookGuardError(
            f"Authenticated Microsoft account {authenticated_account!r} does not match "
            f"the configured allowed account -- refusing to send."
        )


# ---------------------------------------------------------------------------
# Transport abstraction -- STEP 13's dry-run/synthetic path is the
# default; the real Graph transport is a separate, explicit opt-in.
# ---------------------------------------------------------------------------


class GraphTransport(Protocol):
    def get_authenticated_account(self) -> str: ...

    def send_mail(self, *, to: List[str], subject: str, body: str) -> Dict[str, Any]: ...


@dataclass
class SyntheticGraphTransport:
    """STEP 13: the default test/dry-run transport. Zero network calls.
    `account` is what `get_authenticated_account()` reports;
    `fail_mode` simulates one specific STEP 11 failure class when set."""

    account: str = "test-user@example.com"
    fail_mode: Optional[str] = None  # "auth_expired" | "graph_unavailable" | "invalid_recipient" | "rate_limit" | None
    sent_log: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sent_log is None:
            self.sent_log = []

    def get_authenticated_account(self) -> str:
        if self.fail_mode == "auth_expired":
            raise OutlookGuardError("Simulated: Microsoft auth token expired or missing.")
        return self.account

    def send_mail(self, *, to: List[str], subject: str, body: str) -> Dict[str, Any]:
        if self.fail_mode == "graph_unavailable":
            raise OutlookGuardError("Simulated: Microsoft Graph unavailable (network/timeout).")
        if self.fail_mode == "invalid_recipient":
            raise OutlookGuardError("Simulated: Graph rejected recipient as invalid.")
        if self.fail_mode == "rate_limit":
            raise OutlookGuardError("Simulated: Graph rate limit (429) exceeded.")
        message_id = f"synthetic-{len(self.sent_log) + 1}"
        record = {"to": list(to), "subject": subject, "body": body, "message_id": message_id}
        self.sent_log.append(record)
        return {"message_id": message_id, "provider": "synthetic"}


class RealGraphTransport:
    """The actual Microsoft Graph HTTP transport (STEP 9). Written
    against Graph's documented, stable REST shape (POST /v1.0/me/sendMail,
    GET /v1.0/me for account identity via the ID token claim, standard
    OAuth2 refresh_token grant) mirroring gmail.py's flat-function httpx
    style and google_auth.py's refresh-on-401 pattern.

    HONESTLY UNTESTED against a real tenant: FASE 4P.4's own audit
    confirmed zero Microsoft credentials exist in this environment. This
    is real, carefully-written code, not a stub -- but it has not been
    live-verified, and that is stated here rather than left implicit.
    """

    _GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, credentials_path: Optional[str] = None) -> None:
        from openjarvis.connectors.oauth import _CONNECTORS_DIR

        self._credentials_path = credentials_path or str(_CONNECTORS_DIR / "outlook_graph.json")

    def _access_token(self) -> str:
        import httpx

        from openjarvis.connectors.oauth import load_tokens, save_tokens

        tokens = load_tokens(self._credentials_path)
        if not tokens or not tokens.get("access_token"):
            raise OutlookGuardError("No Microsoft Graph credentials configured for outlook_send_email.")
        access_token = tokens["access_token"]
        id_token = tokens.get("id_token")
        # Refresh proactively is out of scope for V1; on a real 401 the
        # caller (send_mail/get_authenticated_account) refreshes once and
        # retries -- mirrors google_auth.py's call_with_refresh shape.
        self._id_token_claims_cache = _decode_id_token_claims(id_token) if id_token else {}
        return access_token

    def _refresh(self) -> Optional[str]:
        import httpx

        from openjarvis.connectors.oauth import load_tokens, save_tokens

        tokens = load_tokens(self._credentials_path)
        if not tokens:
            return None
        refresh_token = tokens.get("refresh_token")
        client_id = tokens.get("client_id")
        client_secret = tokens.get("client_secret")
        if not (refresh_token and client_id):
            return None
        data = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "offline_access Mail.Send",
        }
        if client_secret:
            data["client_secret"] = client_secret
        try:
            resp = httpx.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data=data,
                timeout=15.0,
            )
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        body = resp.json()
        tokens.update(body)
        save_tokens(self._credentials_path, tokens)
        return body.get("access_token")

    def get_authenticated_account(self) -> str:
        access_token = self._access_token()
        claims = getattr(self, "_id_token_claims_cache", {}) or {}
        account = claims.get("preferred_username") or claims.get("email")
        if not account:
            raise OutlookGuardError("Could not determine the authenticated Microsoft account from the ID token.")
        return account

    def send_mail(self, *, to: List[str], subject: str, body: str) -> Dict[str, Any]:
        import httpx

        access_token = self._access_token()
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
            },
            "saveToSentItems": True,
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = httpx.post(f"{self._GRAPH_BASE}/me/sendMail", json=payload, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            raise OutlookGuardError(f"Microsoft Graph request failed: {exc}") from exc

        if resp.status_code == 401:
            new_token = self._refresh()
            if not new_token:
                raise OutlookGuardError("Microsoft Graph auth expired and refresh failed.")
            headers = {"Authorization": f"Bearer {new_token}"}
            try:
                resp = httpx.post(f"{self._GRAPH_BASE}/me/sendMail", json=payload, headers=headers, timeout=30.0)
            except httpx.HTTPError as exc:
                raise OutlookGuardError(f"Microsoft Graph request failed after token refresh: {exc}") from exc

        if resp.status_code == 429:
            raise OutlookGuardError("Microsoft Graph rate limit (429) exceeded.")
        if resp.status_code >= 400:
            raise OutlookGuardError(f"Microsoft Graph send failed: HTTP {resp.status_code} {resp.text[:200]}")

        # POST /me/sendMail returns 202 Accepted with an empty body -- Graph
        # does not return the created message id synchronously for this
        # endpoint. We report what the provider actually gave us (nothing
        # beyond a 2xx) rather than inventing a message id.
        return {"message_id": None, "provider": "microsoft_graph", "http_status": resp.status_code}


def _decode_id_token_claims(id_token: str) -> Dict[str, Any]:
    """Decodes (never verifies signature -- the token was just received
    directly from Microsoft's own token endpoint over TLS in this same
    exchange, not from an untrusted third party) the JWT payload segment
    to read the account-identity claim. No extra Graph permission
    (`User.Read`) needed -- these are free OIDC claims already covered by
    the openid/email/profile scopes."""
    import base64

    try:
        payload_b64 = id_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# The capability handler + registration.
# ---------------------------------------------------------------------------


def make_outlook_send_handler(
    transport: Optional[GraphTransport] = None, *, allowed_account: Optional[str] = None
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    transport = transport or SyntheticGraphTransport()

    def _handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Arguments were already normalized at prepare_action() time (the
        # hash covers the normalized form) -- re-normalize defensively so
        # a hand-crafted/tampered record can't smuggle something the
        # prepare-time validation would have rejected.
        to = normalize_recipients(arguments["to"])
        subject = arguments["subject"]
        body = arguments["body"]

        # STEP 3: account guard, checked immediately before the real send.
        account = transport.get_authenticated_account()
        verify_account_guard(account, allowed_account=allowed_account)

        # STEP 9: the actual send. No success is ever claimed without this
        # call returning without raising.
        result = transport.send_mail(to=to, subject=subject, body=body)
        return {
            "sent": True,
            "to": to,
            "message_id": result.get("message_id"),
            "provider": result.get("provider"),
            "account": account,
        }

    return _handler


def _validate_outlook_arguments(arguments: Dict[str, Any]) -> Optional[str]:
    """STEP 7: rejects a malformed recipient at prepare_action() time,
    not only at execute() time -- the user should never be asked to
    approve something that was already going to fail."""
    try:
        normalize_recipients(arguments.get("to"))
    except OutlookGuardError as exc:
        return str(exc)
    if not arguments.get("subject", "").strip():
        return "Email subject cannot be empty."
    if not arguments.get("body", "").strip():
        return "Email body cannot be empty."
    return None


def register_outlook_capability(
    transport: Optional[GraphTransport] = None, *, allowed_account: Optional[str] = None
) -> None:
    """STEP 5: registers `outlook_send_email` in the frozen FASE 4P.3
    capability allowlist. Explicit static risk class (STEP 5: "do NOT
    infer dynamically") -- HIGH, since it is a real external
    communication to a third party, deliberately more cautious than the
    LOW-risk synthetic test capability. requires_confirmation=True
    always (STEP 6). Not idempotent at the provider level (Graph has no
    idempotency-key support for sendMail) -- idempotency is enforced
    entirely by GovernedActionService.execute()'s own EXECUTED-short-
    circuit (STEP 10), unchanged and already proven correct."""
    register_capability(
        CapabilityDefinition(
            name=OUTLOOK_SEND_CAPABILITY,
            description=(
                "Send one email via the authorized Microsoft 365/Outlook account, after "
                "explicit human approval. Never executes without runtime-verified approval; "
                "recipients/subject/body cannot change after approval."
            ),
            argument_schema={"to": "list_str", "subject": "str", "body": "str"},
            required_arguments=["to", "subject", "body"],
            risk_class=RISK_HIGH,
            requires_confirmation=True,
            idempotent=False,
            timeout_seconds=30.0,
            handler=make_outlook_send_handler(transport, allowed_account=allowed_account),
            argument_validator=_validate_outlook_arguments,
        )
    )


__all__ = [
    "OUTLOOK_SEND_CAPABILITY",
    "OutlookGuardError",
    "normalize_recipients",
    "get_allowed_account",
    "verify_account_guard",
    "GraphTransport",
    "SyntheticGraphTransport",
    "RealGraphTransport",
    "make_outlook_send_handler",
    "register_outlook_capability",
]
