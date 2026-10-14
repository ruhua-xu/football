# CI / Development Speed Optimization V1

These tools are development-only and are not packaged in the application wheel.
The existing `.github/workflows/ci.yml` stays byte-identical to accepted PF-B.
The separate feature-only benchmark has different check names and cannot replace
the required release gate. Deployment of a new default CI policy needs review.

## Install tooling

Use an isolated development environment, then install `devtools/requirements.txt`.
`devtools/benchmark-constraints.txt` records the accepted Linux CI dependency
versions for a controlled parallel comparison; it does not change runtime requirements.

## Three feedback modes

Pass an existing evidence directory and an existing temporary directory outside
every Git checkout. The tools create new short-named test sandboxes underneath it.

```text
python devtools/gates.py FAST --area portfolio --evidence-root <evidence> --temp-root <private-temp>
python devtools/gates.py INTEGRATION --area observed --evidence-root <evidence> --temp-root <private-temp> --workers 2
python devtools/gates.py FULL_RELEASE --evidence-root <evidence> --temp-root <private-temp> --workers 2
```

Use `--plan` to inspect exact test selectors and operations before execution.
Areas: `devtools`, `portfolio`, `scoped`, `observed`, `correction`, `catalog`.
FAST runs Ruff, directly related unit/contract/small integration tests and frozen
contract checks. INTEGRATION runs complete related modules and key E2E paths.
FULL_RELEASE cannot accept an area: it runs all original tests plus tool tests,
Ruff/compile, real fresh migration+Alembic check, legacy/frozen tests, build,
isolated installed-wheel E2E, whitespace and safety checks. It is never a subset.

`--boundary <protected-file-manifest>` adds an actual read-only production-file
hash/identity check on the appropriate local host. Portable CI performs source and
sandbox safety only; it does not claim to inspect an absent production runtime.
Existing release approval/backup/maintenance rules still apply.

## Exact evidence reuse

For ordinary development, opt in with `--reuse`. Reuse requires the same clean
commit/tree, actual source bytes, Python executable, platform/SQLite, installed
package versions and verified RECORD bytes, date/environment, exact mode/selection,
worker/scheduler/seed configuration, and intact passing logs/JUnit. Changed or
dirty code, changed environment/configuration, missing/corrupt artifacts and failed
runs cannot be reused. Reuse reports the original receipt/date; it creates no new
test-pass claim. FULL_RELEASE rejects `--reuse` and executes all final gates.

The wheel cache is separately keyed by every packaging input and the verified
build environment/backend. Existing wheel contents and digest are checked before
reuse. No timestamp-only rebuild is needed. A FULL_RELEASE run still executes
isolated E2E against that exact wheel; FAST/INTEGRATION do not request wheel work.

## Measurement and isolation

`timings.py` extracts existing JUnit/CI evidence. It distinguishes cumulative
worker time from wall time and flags approximate CI progress envelopes.
`measure.py` records hardware/affinity/memory, package versions, wall time, peak
process-tree RSS, complete collection hashes, JUnit, scheduling and per-phase timings.
`--profile-init` additionally times selected real calls without changing results.
Use `--seed` for deterministic collection permutation; it never seeds application RNGs.

The original 3149-test collection is the parallel benchmark set. The default
serial CI still validates the same candidate, including its wheel once; parallel
experiment jobs run pytest only. Hardware-based worker selection caps at four and
reserves 3 GiB per worker until measurements justify another limit. It does not
assume the local six-worker choice is optimal on GitHub runners.

Only two shared functional fixtures use `tests/support/empty_database.py`.
Their template is a process-local immutable `bytes` object containing empty
schema and static initialization markers. Every test gets a new file/inode and
its own real sessions, inputs, clocks and approvals. No mutable connection, source,
model or populated business ledger is shared. Actual migration/legacy tests still
run migrations; existing files are never overwritten. Tool tests compare cold
schema/rows and exercise mutation and cross-process isolation.

There is no replay-result, model-state, authority, source-rights or runtime cache.
The longest tests retain all original assertions and real validation calls.
