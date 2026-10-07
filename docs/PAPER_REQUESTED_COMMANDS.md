# Explicit local operator control commands

POST `/api/v1/paper-requested/control-commands` exposes only PAUSE, HALT, STOP and RESUME for already enrolled requested-quantity Paper accounts. It is disabled by default. Existing GET journal/controls routes keep their prior read-only behavior. No account creation, enrollment, order preparation/submission, source-event ingestion, local finalization, shared capital or Live endpoint is exposed.

Optional activation requires all three server settings together: PAPER_OPERATOR_TOKEN (a SecretStr containing 32..256 URL-safe characters), PAPER_OPERATOR_ACCOUNTS (JSON array of exact account IDs, at most100), and PAPER_OPERATOR_ACTIONS (JSON array of granted control actions). Empty/partial, duplicate or invalid grants fail configuration. Configuration exception text hides raw inputs to avoid exposing the token. Supply a real token securely through environment settings, never chat, a source file or a URL. No production token was generated or enabled in this slice. Account IDs are opaque exact strings, including Unicode. The single capability is a local operator grant, not multiuser identity, an authenticated per-person audit record or exchange permission. Use loopback access or a trusted TLS deployment boundary; plaintext bearer transport must not be exposed externally.

Requests require an Authorization Bearer header and this exact body:

```json
{
  "account_id": "configured-account",
  "command_id": "stable-client-command-id",
  "expected_control_revision": 2,
  "expected_financial_revision": 0,
  "action": "PAUSE",
  "created_at": "2026-01-01T00:00:00+00:00"
}
```

The timestamp is caller-supplied local evidence and must satisfy existing clock rules; it is not authenticated server time. No implicit wildcard grants, query-token authentication, cookie session or ignored extra body fields. Revisions require JSON integers, not booleans or strings. Authentication precedes storage access; account/action grants are checked before existence lookup. Missing/wrong authentication returns401; a valid token without the requested grant returns403. Disabled commands return503. A permitted absent account returns404. Stale versions, unenrolled accounts, invalid clocks, corrupted history, capacity and invalid transitions return a generic409. Storage/lock failure returns a generic503. Operation success and explicitly handled denial/error responses use Cache-Control no-store; standard request-validation errors use FastAPI's422 behavior.

Both revisions are checked inside the same PostgreSQL account transaction and lock as economic admission. A prepare-versus-control race can admit only one operation with the original checkpoint. No read-then-write fencing gap. New HTTP-fenced commands set a two-second database lock_timeout; this bounds lock waiting, not the whole request duration. Existing internal callers may omit the optional financial fence and retain previous behavior.

The response has version paper-requested-control-command-result-v1, the immutable accepted_command, the verified controls view from that same transaction, and external_submission_allowed=false. Repeating the exact identity/action/clock/revisions finds the original accepted record even after later account activity. Conflicting retries fail. The controls view reflects the retry transaction's current state; an old PAUSE acknowledgement does not imply the account is still paused after a later RESUME. Current token/grants are rechecked on every retry. Configuration changes require process restart; no token refresh endpoint or online principal registry is implemented. Existing immutable command facts already contain the accepted financial checkpoint, so schema0017 and export formats remain unchanged.

Fixtures use isolated databases and a clearly artificial test token. Main requested-engine tables stay empty, commands stay disabled and five existing services stay ready. Dependencies, frontend and startup steps are unchanged. Remaining scope includes durable actor identities, grant administration/revocation/audit, additional mutation authorization, scheduling, external dispatch/reconciliation and operational deployment. Next: extend the same permission/fencing boundary to local unsubmitted finalization, with separate explicit action grants, before any order preparation or dispatch interface.
