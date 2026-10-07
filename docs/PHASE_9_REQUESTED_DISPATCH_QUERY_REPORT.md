# Acceptance: scoped original-client local query

Added independently granted default-disabled QUERY_DISPATCH POST endpoint, transaction-private dispatch snapshot reuse, complete offline query verification and CLI. Request/account/original-client identity is bound to immutable local dispatch proof. Current owner/token/expiry is checked before and after evaluation. No database schema, dependencies, frontend, credentials or network changes.

Validation: 31 cases passed across query (3), ownership API (11) and model (17) suites; one additional existing dispatch acceptance case passed. Query cases exercise disabled/auth/action/account scope before storage; strict token types; wrong request/client/owner/token; takeover preserving original identity; expiry before and during evaluation; UNKNOWN hold preservation; resealed tampering; offline CLI and duplicate JSON keys; unchanged source/financial evidence. Existing httpx TestClient deprecation warning remains.

Alembic check reports no new upgrade operations, head0020 unchanged. Five services ready after API-only restart; /health/ready returns200. Ten requested-engine main tables remain empty; operator token and grants disabled. Actual default query returns503/no-store. Cloud startup draft saved, publication still required in environment settings.

Limits: local lease evidence and caller-declared local input only; no external query/absence proof, scheduler, authenticated venue result, shared capital or multiuser identity. Live stays disabled. Next: explicit local transport boundary and fault contracts before any venue integration.
