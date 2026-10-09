# Read-only private Spot snapshots

The tool collects balances, open orders and a bounded trade page through the
fixed Binance Spot GET client, then validates and hashes complete evidence.
Sequential requests are not atomic. The declared scope is a local credential
label, not verified exchange account identity. Trade history completeness is
never inferred. No orders, cancellations, financial imports or automatic repair
are available.

Credentials are read only from an owner-only regular file, with no symlink
following, a2KiB limit, duplicate-key rejection and sanitized failure messages.
The file contains exactly api_key and api_secret strings; provision actual raw
signing credentials securely outside the checkout. Proxy placeholders cannot
supply local HMAC secrets. Never put values in chat, tracked files or command-line
arguments. No real private credentials have been configured or tested here.

Run from /workspace/fc666:

```
.venv/bin/python -m scripts.private_snapshot capture --credentials-file /secure/private-spot.json --scope account-label --symbol BTCUSDT --from-id 0 --output .runtime/private-before.json
.venv/bin/python -m scripts.private_snapshot compare --before .runtime/private-before.json --after .runtime/private-after.json --output .runtime/private-differences.json
```

Capture requires api.binance.com destination access and actual credentials.
These commands describe supported usage; real exchange capture is unverified.
Output is created exclusively with0600 permissions, fsynced, never overwritten.
Keep the parent directory private and outside tracked sources.

Comparison requires equal scope, symbol and cursor and nonoverlapping ordered
capture windows. It reports changed/missing/new observed balances, orders and
trades by stable identity. Missing open orders are not evidence of cancellation
or execution; bounded trade page differences are not complete history. Numeric
strings are compared as recorded, so equivalent representations can be reported
as changes. NO_DIFFERENCES_IN_OBSERVED_SCOPE is never full reconciliation.

Offline reads reject duplicate/nonfinite JSON and oversized artifacts. Snapshot
verification recomputes row normalization, strict flags and digest. CLI errors
omit raw remote errors, credential values, paths and signed URLs. Tests use
MockTransport only; no exchange account is accessed.
