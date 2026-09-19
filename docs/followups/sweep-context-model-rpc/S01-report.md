# S01 — typed sweep domain and pure compiler

2026-09-19 (Asia/Seoul). Workstream: WS-S01 sweep planning.

S01 — Software COMPLETE (domain/compiler scope only).
Hardware: NOT RUN — user-operated. CI: NOT CHECKED.
S02 and later development phases have not been started.

## Baseline and scope

- Baseline/tested parent: `e486cd7dfd7a14165c837a20e1fb763459f8d386`.
- Branch: `codex/current-source-pilot-v6`; initial working tree clean.
- Remote `https://github.com/Phjrab/llm-cluster-benchmark.git` confirmed that feature
  SHA and main `eebb8f134ac2fe251f5e9dd5a723bb653db9a840` before work.
- No applicable AGENTS.md was found; CONTRIBUTING and integrated Master/Data
  Contracts/Integration Notes/S01 plus all S00 reports informed this change.
- The user's continuation was treated as authorization for the next single phase,
  S01. Runtime execution, UI, R/H packages and hardware work remain outside scope.

## Implemented

`cluster/domain/sweep.py` provides frozen SweepSpec, AxisSpec, RunCondition,
ModelReference, PromptVariant, WorkerReference, RpcProfile, execution/budget/exclusion
policies, capability verdicts, BaseCell, Trial, workload/counts and ResolvedPlan.
Boundary parsing rejects unknown keys and malformed scalars, duplicate JSON keys,
template imports, invalid profiles and formal namespace reuse.

`cluster/application/sweep_planner.py` compiles grid/OAT/explicit conditions with
bounded expansion, deterministic hashes and ordering, visible duplicate/excluded/
blocked/unknown rows, independent Trial identities, and strict import recompilation.
Existing strategy validation/definitions/work_units are reused. There is no network,
filesystem, subprocess, tokenizer, model loader, Dashboard import or scheduler here.

All required context/model/runtime/sampling/RPC axes are represented. Future runtime
axes retain requested values and expose blockers instead of silently discarding them.
The output always denies execution in S01; resolution/readiness do not constitute
execution authorization. See [S01-contract.md](S01-contract.md) for exact schema,
canonicalization, conservative budgets, compatibility and deferred wiring.

## Actual tests and results

Checks used the existing Python/Node installations. Temporary runtime/results and
header-only inventory were placed under `/private/tmp/s01-sweep-audit/`:

```sh
export CLUSTER_RUNTIME_DIR=/private/tmp/s01-sweep-audit/runtime
export CLUSTER_RESULTS_DIR=/private/tmp/s01-sweep-audit/results
export CLUSTER_INVENTORY=/private/tmp/s01-sweep-audit/empty.csv
export PYTHONDONTWRITEBYTECODE=1
```

| Command/check | Actual result |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_sweep_planner -q` | 37 tests PASS, 0.229s |
| `.venv/bin/python -m unittest cluster.tests.test_sweep_planner cluster.tests.test_domain cluster.tests.test_benchmark_core cluster.tests.test_durable_jobs cluster.tests.test_rpc_coordinator cluster.tests.test_research_campaign cluster.tests.test_models cluster.tests.test_model_library_followup -q` | 164 tests PASS, 1.773s |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | 575 tests, 16.456s: initially 570 passed, 3 failures from fixture/env precedence, 2 errors from sandbox loopback binding; all five passed in targeted reruns below |
| Three auth/token tests with their own mocked temporary defaults and CLUSTER_RUNTIME_DIR unset only while running those tests | 3 PASS, 0.003s |
| `.venv/bin/python -m unittest cluster.tests.test_launcher.ControllerLifecycleTests -v` with loopback permission | 2 PASS, 1.232s; temporary fake local lifecycle only |
| `PYTHONPYCACHEPREFIX=/private/tmp/s01-sweep-audit/pycache .venv/bin/python -m compileall -q cluster scripts` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS: 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `npm run test:syntax` | PASS |
| `npm run test:fixtures` | PASS: dashboard export fixtures OK |
| Additional bounded roundtrip check | 500 trials, 237993-byte plan, verify_plan equality PASS |

The final evidence covers all 575 discovered tests (538 existing + 37 new) across
the full run and targeted reruns. This is not a claim that the first discovery
command exited successfully. Packaging/wheel isolated install/import tests are
included in that discovery. Browser E2E, PNG rendering and ShellCheck were not rerun
for this backend-only change; no shell or frontend source changed.

The three environment-sensitive tests are:

- `test_core.PlatformPlanTests.test_worker_api_auth_is_disabled_by_default_and_configurable`
- `test_phase14_security.TokenAndArtifactSecurityTests.test_corrupt_settings_cannot_silently_disable_worker_auth`
- `test_phase14_security.TokenAndArtifactSecurityTests.test_worker_token_is_repaired_to_private_mode_without_logging_secret`

Those tests patch DEFAULT_SETTINGS/DEFAULT_WORKER_TOKEN with temporary paths; the
global runtime override has higher precedence in clusterctl. Dashboard services
were first imported with the isolated runtime, then that override was removed only
while executing these three existing fixtures. No product fix or test weakening was
needed. The other two errors were `PermissionError` on loopback socket.bind and
were rerun with permission. Synthetic 4-byte model-download progress in test logs
comes from existing mocks, not a real model transfer.

The first new-test run also exposed two fixture mistakes: reversed arguments to
the existing build_strategy_scenarios in four subtests, and mixed prompt modes for
one shared ref. Tests were corrected to the actual function signature and a single
token-profile fixture, then all 37 passed. No failure has been concealed as a skip.

## Acceptance evidence and limits

- 2 models × 3 contexts × 3 concurrency × 2 outputs × 3 repeats produces 36 cells/
  108 unique Trial IDs. RPC 2 × 3 profiles × 2 contexts × 3 repeats produces 12/36.
- OAT baseline deduplicates without dropping candidate rows. Equivalent custom
  ratios deduplicate while preserving original weights. Explicit Worker order is
  semantic. Huge grids fail before _conditions/product materialization.
- Roundtrip/defensive-copy/immutable records, model/quantization/template identity
  changes, axis order, hash/ID/count/approval tampering and seeded ordering tested.
- Unknown config/axes, bool-as-int, empty/nonfinite values, duplicate JSON keys,
  invalid timeout/budgets, node/profile/key mismatch and exclusion revision tested.
- Broadcast and cumulative/individual node sweep workloads agree with the existing
  task planner; warmup and repeats are counted separately. Duration/storage unknown.
- Missing identities, missing installations, Pi offload, endpoint aliases, multipart,
  exact-token overflow, template differences and unsupported axes remain visible.
- Existing execution source, public API/CLI, runtime/model cache behavior, JobService single-job
  admission, Worker slot count, research locks and existing result schemas are unchanged.

Deferred exactly as planned: S02 parameter application/tokenizer accuracy, S03 live
catalog resolver, S04 RPC argv changes, S05 reservations, S06 execution, S07 API,
S08 UI, S09 comparison/export, S10 integrated handoff. A cached valid cell is not a
hardware PASS and not an executable sweep. Actual hardware acceptance stays NOT RUN.

## Git delivery

Stage only the two new modules, new test module and two S01 Markdown documents.
Review diff/staged diff/secret patterns, commit and non-force push on the confirmed
feature branch. The final response records the actual new commit/push result;
this report intentionally does not embed its own commit SHA. No main push, merge,
force push, lock/inventory mutation or hardware workflow dispatch is part of S01.

STOPPED. S02 has not been started.
