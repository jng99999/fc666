# Fenced simulated submission v1

core/paper/submission.py implements paper-fenced-submission-v1 on the dedicated SQLite fault laboratory. It has no exchange calls, product account/economic writes, API or UI. Production schema remains0013 and the existing closed-bar lifecycle is unchanged. Owner labels are local coordination labels, not authenticated user identities.

## Ownership and fencing

SubmissionAdapter.claim locks the laboratory database with BEGIN IMMEDIATE and validates existing request/transcript/evidence. A lease has an owner, strictly increasing integer token and server wall-clock expiry. Durations are1..60 seconds. An unexpired same-owner retry returns the existing lease without extending it; a different owner is denied. Expiry allows a new token and owner; tokens are never reset or deleted through supported operations. A trigger prevents token rewind, deletion, unchanged-token updates and nonincreasing expiry.

Submit, delivery and recovery validate owner/token/expiry in the same serialized transaction. Submit and delivery recheck before commit: expiry during calculation rolls back the whole write. A stale token cannot write after takeover even if the owner label is reused. There is no heartbeat/renewal, distributed clock protocol, authentication or remote fencing claim. Server clock correctness remains an operational prerequisite.

Ownership enrollment requires a request with no prior transcript. Existing unowned laboratory events are legacy facts and cannot gain retrospective submission ownership. Ordinary FaultAdapter.append refuses requests that have acquired ownership, preventing the public unfenced mutation path from bypassing token checks. Low-level storage functions and administrator access are trusted internals, not a security boundary.

## Independently durable submission boundary

The request already exists in a separately committed transaction. submit atomically saves one immutable dispatch and the SUBMIT/UNKNOWN_SUBMISSION prefix. Dispatch freezes version, stable request-scoped client identity, request hash, original token, recording clock and initial-event hash. This marks an unresolved local simulated submission; it does not prove a remote request was sent.

The order primary key allows one dispatch only. Same-token retries and later owners return the exact original dispatch, with the original client identity, rather than creating a new attempt. Delivery requires that dispatch to be present and verified and cannot create another SUBMIT. Missing or changed dispatch evidence fails closed; no automatic rebuilding occurs.

inspect checks dispatch against request and initial events in one consistent read. recover requires current ownership, verifies all saved evidence and returns NOT_STARTED, WAIT_UNKNOWN_RESULT or RECONCILE_EXISTING_SUBMISSION. It always reports resubmission_allowed=false, execution_enabled=false and external_query_supported=false. An empty local result is never evidence of remote nonexistence. No absence-based retry or exchange query has been implemented.

## Acceptance and next dependency

20 submission cases plus38 existing fault/reconciliation cases pass. Tests cover unique concurrent ownership and submission, lease retry/takeover, old-token rejection, unknown-result query, immutable/corrupt/missing dispatch, unfenced-path rejection, legacy enrollment refusal, expiry within writes and actual SIGKILL before/after atomic dispatch commit. Reopen/retry retains one local dispatch. Synthetic fixture data stays in temporary laboratory files.

Next: versioned integration contract between simulated submission ownership and actual account economic acceptance. Specify full requested quantity, authorization/capital reservation, late fills and cancellation before introducing a new production engine version. Preserve old snapshots and ledgers. Full OMS, remote ACK/query/reconciliation, shared capital, identity authorization and Live remain incomplete.
