# P02 — Formal Source Identity Direct Comparison

## Status

**COMPLETE (software verification only).**

P02 was performed on branch `codex/current-source-pilot-v6` from baseline commit
`d26662b3c29548c71f26c43e4f2c6408ed077806`. The branch and its upstream were
identical and the working tree was clean before the phase began.

No research lock, inventory, model artifact, Worker, or stored research result was
modified. No Worker connection, model operation, inference, native RPC operation,
hardware workflow, or source/runtime relock was performed.

## Reproduced gap

`deployment_identity_issues()` validated each observed deployment fingerprint and
rejected cross-Worker drift. It compared the observed Git commit with the locked
commit, but it did not compare the observed source tree directly with the expected
source tree already stored in the runtime lock.

Consequently, a selected Worker—or every selected Worker—could report the same
valid but wrong `source_tree_sha256` and avoid `SOURCE_FINGERPRINT_MISMATCH` when
the Git commit still matched. `validate_runtime_lock()` also accepted a missing,
malformed, or unverified locked source-tree value.

## Identity contract

The expected value is:

- `runtime_lock.workers[].deployment.source_tree_sha256`
- accompanied by `runtime_lock.workers[].deployment.source_tree_verified=true`

The observed value is supplied by the fresh Worker health snapshot at:

- `live_preflight_snapshot[node].deployment.source_tree_sha256`
- accompanied by `live_preflight_snapshot[node].deployment.verified=true`

Both values use the existing deployment-manifest definition. The source collector
builds a sorted list of source records containing relative path, byte size, file
SHA-256, and executable bit. `canonical_source_tree_sha256()` serializes that list
as canonical JSON and hashes the resulting bytes with SHA-256. Runtime data,
models, caches, frontend dependencies, and other configured exclusions do not
enter this source identity.

`deployment_manifest_sha256` remains a separate integrity value. P02 does not use
it as a substitute for source content identity because the complete manifest also
contains deployment and runtime metadata.

## Formal call path

The production formal path remains:

1. `DashboardFacade` creates the existing `CampaignRunner`.
2. `_campaign_runner().preflight()` obtains a fresh status snapshot and model
   inventory for the cell's selected Workers.
3. `assess_campaign_cell()` applies the per-cell formal drift and quality gate.
4. `assess_formal_eligibility()` validates the lock set and calls
   `deployment_identity_issues()`.
5. `CampaignRunner` dispatches through the existing durable `JobService` path only
   when the preflight and the separate formal execution gate both allow it.

An empty Campaign snapshot is converted to fail-closed evidence and produces
`PREFLIGHT_SNAPSHOT_MISSING`; it cannot bypass deployment identity checks.

## Implementation

`validate_runtime_lock()` now requires every formal Worker deployment entry to
contain a valid 64-character lowercase source-tree SHA-256 and requires the locked
source tree to have been verified.

`deployment_identity_issues()` now reads the expected tree from the selected
Worker's locked deployment entry and directly compares it with the valid observed
tree. The existing issue vocabulary is reused:

| Condition | Formal result |
| --- | --- |
| Expected or observed tree missing | `SOURCE_FINGERPRINT_MISSING` |
| Expected or observed tree malformed | `SOURCE_FINGERPRINT_INVALID` |
| Expected or observed verification false | `SOURCE_FINGERPRINT_UNVERIFIED` |
| Valid observed tree differs from the locked expected tree | `SOURCE_FINGERPRINT_MISMATCH` |

The comparison is per Worker, so it blocks a single selected Worker and also
blocks every selected Worker when they all share the same wrong tree. The existing
cross-Worker comparison remains as an additional drift check. Existing runtime
fingerprint, runtime version, pinned RPC commit, and Git commit checks remain in
place.

No new issue code, API, export field, scheduler, or execution path was introduced.
Exploratory Sweep and ordinary experiment admission do not call this formal-only
helper and retain their existing policy.

## Regression coverage

Added or updated synthetic tests cover:

- exact expected/observed source identity acceptance;
- matching commit with a different source tree;
- all selected Workers sharing the same wrong tree;
- one selected Worker with the wrong tree;
- missing, malformed, and unverified locked tree identity;
- missing, malformed, and unverified observed deployment identity;
- preservation of runtime fingerprint and pinned RPC commit blockers;
- empty fresh Campaign snapshot failing closed;
- a valid Campaign fixture using the locked source-tree value.

All tests use synthetic manifests, temporary files, mocks, or localhost test
servers. Output labels that mention SSH or model download belong to mocked unit
fixtures and did not contact external hardware or retrieve model binaries.

## Validation results

Commands were run against the P02 changes in isolated `/private/tmp` source copies
unless noted otherwise.

| Check | Result |
| --- | --- |
| Targeted deployment/lock/Campaign regression suite | **PASS — 21 tests** |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | **PASS — 718 tests** |
| `npm test` | **PASS — syntax, fixtures, publication PNG, 8 Playwright tests** |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | **PASS** |
| `.venv/bin/python scripts/ci/validate_repository.py` | **PASS — 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts** |
| `bash -n` for repository shell scripts | **PASS — 7 scripts** |
| `python3 -m pip wheel . --no-deps --no-build-isolation` | **PASS** |
| `.venv/bin/python -m unittest -v cluster.tests.test_packaging` | **PASS — 3 tests** |
| `git diff --check` | **PASS** |

The initial in-place `compileall` attempt could not create bytecode beside the
symlinked repository because of the workspace sandbox. It was rerun successfully
in an isolated writable source copy. An earlier isolated-test setup command also
stopped before tests because a zsh-reserved variable name replaced `PATH`; the
corrected command produced the 718-test result above. Neither setup issue changed
product code or test outcomes.

## Compatibility and gate state

The existing model checksum gate, cohort rules, power handling, runtime version,
runtime fingerprint, RPC pin, Git commit, Campaign serialization, JobService,
resource ownership, fencing, cleanup, quarantine, and the 19-column
`requests.csv` contract are unchanged.

`config/research/formal_experiment_matrix.json` still records
`formal_execution_allowed=false`. P02 does not alter that gate and does not claim
hardware or formal research readiness. The existing runtime lock was validated as
input but was not regenerated or edited.

## Remaining work

- Actual deployed source identity remains **HARDWARE_NOT_VERIFIED** in P02.
- A future approved hardware phase must collect fresh Worker snapshots and compare
  them through this gate.
- Any future source/runtime relock requires its own evidence and explicit approval.
- P03 diagnosis remains a separate phase and was not started.
