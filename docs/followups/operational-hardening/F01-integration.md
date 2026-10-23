# F01 — operational code and Research Lab UI integration

## Scope and baseline

Software integration only. The final user scope is **F01 only**. An intermediate
authorization to continue through F03 was withdrawn; the uncommitted F02 prototype
was removed from this branch and retained only in ignored local scratch storage.
F02/F03 are not implemented or delivered by this F01 branch. No operational Dashboard requests/restart,
Worker SSH/API, model operation, inference, RPC, hardware workflow or formal approval
was performed. Existing P00–P04 are retained and regression-tested, not reimplemented.
No applicable AGENTS.md was found; CONTRIBUTING.md and the requested operational,
execution-stability, acceptance, UI deployment/theme/typography documents were reviewed.
Historical deployment claims and test counts are not current verification.

Fresh `git fetch origin` found:

| Reference | Actual commit |
| --- | --- |
| origin/main | `49bda2e866d8fa2f25280f6cf761fd09bfcd952f` |
| origin/codex/current-source-pilot-v6 | `ba5bbce2c60d2d0af80aaa77c77424be704f8a08` |
| origin/codex/ui-llm-cluster-research-lab | `537fc84d0d998fd1bdf0d279bd4ae4c1657cee7a` |
| Operating codex/pi4-model-benchmark-20261014 | `e81e2b5ce84d9f9bc338e0da131f115d9ad8166e` |

Operating branch had no upstream and 11 dirty/untracked source files. Those files
were fingerprinted before work. The operating checkout was never switched, reset,
cleaned, staged or committed. UI integration status was **NOT_INTEGRATED**: main/UI
had 1/12 unique commits; P01/P02 and the existing stabilization history are already
ancestors of main. Main's hardware CAD/documentation commit is retained unchanged.

The app worktree helper could not resolve origin/main in the parent workspace, so
Git created the explicitly authorized isolated sibling `llm-cluster-benchmark-f01`
on `codex/operational-observability-hardening`, based on fetched main. The isolated
merge includes the UI ancestry and operating source e81e2b5 without conflicts.
Integration merge commit: `e4ffb2cee254fab14f424914eb73f350cafbfdec`
(parents: fetched main `49bda2e` and operating source `e81e2b5`).
That exact merge commit was successfully pushed non-force to
`origin/codex/operational-observability-hardening`; upstream was verified equal.
A subsequent documentation-only commit records this immutable integration SHA.

## Preserved integration

- UI: Matrix, controlled Compare with explicit baseline and metric units, model
  installation/execution/formal distinctions, themes, local fonts and responsive
  nine-page navigation. Existing synthetic capture evidence is retained.
- Backend: P01 server-owned Sweep/Campaign loops and read-only Sweep projections;
  P02 direct source-tree comparison, fresh preflight and closed formal gate;
  JobService children, fencing, resource overlap prevention, targeted cancel,
  cleanup/quarantine, default 1 job and explicit disjoint maximum 2.
- Seven already-existing operational fixes after UI: endpoint encoding, load-profile
  capability, streaming ownership, live child cleanup during recovery, GGUF template
  identity, single producer thread for inference locks/cancelling lease heartbeat,
  and preserving existing available-memory/swap telemetry. These were reviewed
  and merged, not rewritten.
- The 11 previous local changes were copied as reviewed source changes: read-only
  study Sweep discovery, study energy N/A policy in views/exports, completed-card
  model/context/Worker labels, read-only action controls, and independent model-card
  expansion. No study manifests, measurements, inventories or credentials were copied.
- requests.csv remains 19 columns; metric formulas, strategies, research locks and
  formal_execution_allowed are unchanged. Main's CAD tree is unchanged.

Integration fixes: existing frontend fixture cache-version expectations now match
the imported local assets. Vendored license text and two historical synthetic log
lines have only line-ending/trailing-space normalization; font binaries are intact.
Typography tests accept TYPOGRAPHY_OUTPUT_DIR so new regression captures can go to
ignored artifacts rather than overwrite historical capture evidence.

## Actual validation

All checks ran in the isolated merged working tree before this F01 merge commit.
Python used a local venv with read-only access to the existing dependency packages;
Node 22.17.1/npm 11.18.0/Playwright 1.62.1 dependencies were copied locally. No model
or Worker dependency was installed. Runtime/inventory/results are isolated under
`.artifacts/f01/`; inventory is empty. Browser fixture uses loopback port 4273 and
its own `.artifacts/playwright` paths, never the operational port 8080.

| Command / gate | Result |
| --- | --- |
| `.venv/bin/python -m unittest` with modules listed below, `-q` | exit 0, 218 tests, 18.414 s |
| `DASHBOARD_TEST_PORT=4273 npm test` (second run) | exit 0, syntax/fixtures/publication PNG, 39 E2E, 58.3 s |
| `TYPOGRAPHY_OUTPUT_DIR=$PWD/.artifacts/f01/typography DASHBOARD_TEST_PORT=4273 npm run test:e2e -- cluster/tests/e2e/typography.spec.js` | exit 0, 8 tests, 16.6 s, after output-path change |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | exit 0 |
| `git diff --check` and staged diff check | exit 0 after imported whitespace cleanup |

Python modules (all prefixed `cluster.tests.`): test_sweep_api, test_sweep_runner,
test_sweep_results, test_research_campaign, test_dashboard_research,
test_deployment_identity, test_research_locks, test_s10_integration,
test_durable_jobs, test_worker_runtime, test_context_runtime,
test_measurement_instrumentation. These exercise browser-free progression,
read-only GET, completed-trial restart deduplication, closed formal gate,
source mismatch, disjoint ownership/cancel and cleanup boundaries.

Initial npm exit 1 was an actual fixture integration mismatch against old cache
versions, fixed and rerun. Initial staged whitespace check flagged imported CRLF
license text/trailing spaces, fixed without changing wording. No failure is counted
as a pass. Ignored `.artifacts/f01/` retains the original and successful logs.
Full Python/packaging/shell and any future F03 validation are outside this
final F01-only delivery; F01 does not claim them. No hardware pass or timeout root-cause fix is claimed.

## Operating version and next deployment

Local process inspection only: PID 49634, uvicorn `cluster.dashboard.app:app`,
loopback port 8080, operating checkout above, started `2026-10-19T03:18:51.924017Z`.
Disk services.py and sweep_service.py mtimes precede that start; styles.css and
index.html mtimes (`2026-10-19T03:44:48Z`) follow it. Therefore the historical
2026-10-07 PID 73798 deployment record does not describe the current process.
Current in-memory Python content, served frontend content, active jobs and Worker
health are **UNKNOWN** here: no operational API/status polling was performed.
The PID/cwd and disk hashes establish only process/disk evidence, not runtime identity.
Mixed in-memory backend/disk frontend versions remain possible.

This integrated branch includes Python backend/Worker changes, so deployment is
not a static-only refresh: authorized Controller restart and selected Worker code
rollout/restart will be needed after checking active job/lease/cleanup state. Git
push is delivery only. Preserve runtime, results, inventory, models and locks;
rollback uses the prior code checkout/environment without deleting those data.
No production restart/deployment, main push, PR merge or hardware acceptance occurred.

## Delivery status

Software integration: PASS. Automated F01 validation: PASS as scoped above.
Git delivery: integration commit e4ffb2cee254fab14f424914eb73f350cafbfdec
pushed successfully to the feature branch; no main push or PR merge.
CI: no pass claimed before exact-commit workflow verification.
Deployment: NOT_PERFORMED. Hardware acceptance: NOT_PERFORMED.
Formal eligibility: UNCHANGED / gate closed; no lock regeneration.
