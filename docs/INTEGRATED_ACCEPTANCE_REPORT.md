# Integrated backend acceptance — 2026-10-09

Full regression exposed three concrete issues. All three were corrected and
revalidated with their affected paths. This report preserves initial failures;
it does not claim a clean second full-suite run or completion of the five blocks.

| Run | Result | Scope |
| --- | --- | --- |
| Initial serial run | 175 passed, interrupted | Replaced by bounded parallel run; incomplete |
| Full run, three workers | 1098 passed,3 failed; no errors/skips | All1101 collected backend cases |
| Targeted post-fix regression | 39 passed; no failures/errors/skips | All3 original failures, affected foundation/assessment paths and6 new cleanup cases |
| Main local backup drill | Passed | Schema0026, isolated restore and table row-content SHA256; main database unmodified |
| Schema/service checks | Passed | Alembic no upgrade operations; API/web/research/market/Paper all ready |

JUnit files under ignored .runtime are full-acceptance-parallel-20261009.xml and
acceptance-fixes-20261009.xml. Parsed case identities verify that all3 originally
failed cases were actually rerun successfully. The reports cover1107 distinct
cases including6 new ones. That union is not a second clean full-suite run.

## Backup cleanup correction

TimescaleDB can start a background connection after DROP FORCE's initial scan.
The full populated financial/inbox backup test restored and verified the data,
but failed to remove its temporary target. scripts/backup_drill.py now fences
new connections, terminates only that target's sessions and retries only the
concrete PostgreSQL ObjectInUse SQLSTATE55006, at most10 attempts. Target names
must match generated fc666_restore_ plus32 hexadecimal characters; source,
main, test-database and malformed names are rejected before connection access.
The actual active-connection cleanup test verifies target removal while source
schema remains intact. The original failed target was positively matched to its
retained verified backup manifest and removed; archive and manifest were retained.

## Assessment budget test correction

Two older tests intercepted every octet_length query. Newer health evidence
checks therefore failed before the intended assessment checks. Fault injection
is now restricted to requested_paper_attempts. Original budget-rejection,
zero-persisted-attempt and financial/source-unchanged assertions are retained.
No production budget, health gate or financial guard was relaxed.

## Reproduction and limits

Tests used existing pinned runtime/development dependencies plus an optional
pytest-xdist3.8.0 runner installed in the environment. Dependency manifests and
lockfiles were unchanged. The observed environment had a four-CPU quota; three
workers left capacity for application services. Tests use unique PostgreSQL
databases per case. Commands:

```
.venv/bin/pytest -q -n 3 --dist=loadfile --junitxml=.runtime/full-acceptance-parallel-20261009.xml
.venv/bin/pytest -q -n 3 --dist=loadfile tests/test_backup_cleanup.py tests/test_requested_attempts_storage_binding.py tests/test_five_block_foundations.py --junitxml=.runtime/acceptance-fixes-20261009.xml
.venv/bin/python -m scripts.backup_drill
.venv/bin/alembic check
.venv/bin/python scripts/dev_services.py status
```

The optimized frontend build and actual walk-forward browser/export acceptance
were previously verified in the recovery batch; frontend files did not change
here and were not rebuilt. This regression is not sustained production load,
offsite retention, infrastructure-failure acceptance, remote CI, verified
private exchange identity or deployment to external production infrastructure.
Remaining functional gates are in FIVE_BLOCK_EXECUTION_PLAN.md.
