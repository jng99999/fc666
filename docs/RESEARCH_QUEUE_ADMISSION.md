# Research queue admission

The internal queue accepts ordinary backtests (no `research_type`), `holdout`, and `walk_forward`. Unknown or malformed type values are rejected rather than silently treated as ordinary backtests.

Before a database admission transaction, every batch member is checked against the fixed strategy registry and validated configuration. Source data must contain 2..1000 finalized, matching-instrument, contiguous bars for a Spot instrument. Holdout and walk-forward window/candidate constraints are also checked before admission. Invalid members reject the whole batch. The existing advisory admission lock still enforces the active-job limit atomically.

Workers independently check the retained request's type against its pinned engine version before computation. Existing ordinary v2 jobs retain their legacy replay path; current ordinary, holdout and walk-forward tasks use their own versions. Digest validation remains a consistency check, not authentication of database contents or market-data provenance.

There is no arbitrary Python strategy execution or implicit trading permission. Successful admission and computation do not establish statistical validity or profitability.
