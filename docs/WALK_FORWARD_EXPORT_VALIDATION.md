# Walk-forward export validation

Run from the repository root:

```sh
.venv/bin/python -m scripts.verify_walk_forward /path/to/export.json
```

The file must be a regular file, not a symlink, and at most 32 MiB. Duplicate JSON fields and non-finite values are rejected. The verifier checks envelope/manifest bounds and reruns every training candidate and selected cold test window. Canonical JSON comparison also rejects type substitutions such as `false` replaced with `0`. Valid v1 exports retain their existing format and run IDs.

Selection uses training total return only, with declared grid order breaking ties. Each test window starts with fresh cash and strategy state; returns are not compounded across independently funded windows. Repeated tuning against test results can contaminate evaluation. A successful replay does not authenticate the input data or establish profitability or permission to trade. Live trading stays disabled.
