# Phase 06 — Benchmark Core Modularization

Date: 2026-08-20 (Asia/Seoul)

## Goal

Split the legacy benchmark God module into strategy, planner, executor,
metrics, persistence, transport, and RPC-session boundaries without changing
benchmark math, concurrency, cleanup, or result serialization. Apply the
intentional workers-only participant migration for non-RPC strategies while
leaving legacy RPC coordinator semantics for Phase 07.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `907f304 docs(refactor): record phase 05 hardware validation`
- Implementation commit: `5053d07 refactor(benchmark): modularize benchmark execution core`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added a strategy object registry for single-node, replicated round-robin,
  broadcast comparison, node sweep, and model-parallel RPC planning.
- Added a deterministic planner that retains selected Worker order, request
  identifiers, replica indices, logical-to-physical mapping, and scenario
  ordering.
- Added `ScenarioExecutor` for bounded `ThreadPoolExecutor` scheduling,
  logical-group broadcast concurrency, cancellation-aware submission, and
  cancellation-aware one-in-flight-per-Worker warmup scheduling.
- Moved percentile and all schema-v2 aggregate formulas into a pure metrics
  module, including per-Worker metrics, cluster throughput, logical/physical
  counts, exact answer agreement, cumulative speedup, and scaling efficiency.
- Added `RunPersistence` as the run/event/CSV/summary boundary over the existing
  filesystem repository.
- Moved Worker and native RPC streaming HTTP into a transport boundary.
- Added `RpcBackend`, `RpcSession`, and `LegacyRpcBackend`; native runtime
  commands and ephemeral process cleanup are isolated in the RPC module.
- Reduced `cluster.benchmark.runner` from the execution God module to a
  compatibility facade, dependency assembly point, and CLI.

## Changed behavior

- Non-RPC benchmark plans now accept inference Workers only. A legacy
  `role=head` participant or the Mac Controller is rejected instead of being
  assigned inference requests.
- Existing round-robin, broadcast, and sweep fixtures were intentionally
  migrated from `[head, worker...]` to ordered Worker-only fixtures.
- RPC planning deliberately retains the legacy `head` coordinator plus one or
  more Workers. Coordinator selection is not migrated in this phase.

## New abstractions

- `cluster.benchmark.strategies`: strategy objects and registry
- `cluster.benchmark.planner`: deterministic request/scenario planning
- `cluster.benchmark.models`: immutable task/scenario records
- `cluster.benchmark.executor`: measurement and warmup scheduling
- `cluster.benchmark.metrics`: pure aggregation formulas
- `cluster.benchmark.persistence`: run/event persistence facade
- `cluster.benchmark.transport`: Worker/RPC streaming HTTP
- `cluster.benchmark.rpc`: RPC backend/session and cleanup lifecycle
- `cluster.benchmark.core`: readable orchestration facade

## Moved / extracted code

- Strategy descriptions, validation, request mapping, and work-unit formulas
  moved out of `runner.py`.
- Thread-pool scheduling, failure records, cancellation submission bounds, and
  warmup scheduling moved out of `runner.py`.
- Metric formulas moved byte-for-behavior into a pure module.
- RPC runtime command construction, start/preflight/stop sequencing, topology
  construction, and cleanup retry boundary moved out of the general runner.
- Config/event/request/summary persistence calls moved behind `RunPersistence`.

## Backward compatibility

- `cluster.benchmark.runner` continues to export `ExperimentConfig`,
  `RequestTask`, `StrategyScenario`, `percentile`, strategy planning helpers,
  catalog helpers, aggregate compatibility name, RPC compatibility helpers,
  `run_experiment`, and the existing CLI arguments.
- Schema version remains `2`; the representative summary key set, 19-column
  `requests.csv` header, config fields, and observed event types are golden
  asserted.
- No result field was removed. No throughput, percentile, success, agreement,
  speedup, or efficiency formula changed.
- Broadcast concurrency remains logical-group concurrency; another logical
  request is submitted only after every replica in the prior group completes.
- Cancellation still stops new submissions and waits for already-running calls.
- RPC cleanup failure still fails the run and causes a final cleanup retry.
- The only intentional compatibility change is removal of Controller/legacy
  head participation from non-RPC inference plans.

## Benchmark invariants

- Five requests over two Workers map `w1,w2,w1,w2,w1`.
- Three broadcast logical requests over two Workers produce six physical calls.
- Cumulative sweep preserves the explicit selected Worker order.
- Warmup calls are never included in measured request records.
- Linear-interpolation p50/p95 remain `2.5` and `3.85` for `[1,2,3,4]`.
- Exact output SHA-256 agreement, logical/physical counts, cumulative speedup,
  and scaling efficiency retain their existing formulas.
- The Worker inference backend's `RLock` continues to serialize generation per
  Worker; the Controller executor does not add a different scheduling lock.

## Security considerations

- No token, password, SSH key, model binary, runtime result, or generated test
  artifact is included.
- Existing Worker token-header construction and model path containment are
  unchanged.
- RPC remains explicitly unauthenticated, ephemeral, private-LAN-only and is
  stopped in normal, failure, and finalization paths. Cleanup guarantees were
  not weakened.
- The Mac Controller remains outside all non-RPC participant plans and is not
  given an inference runtime.

## Tests executed

- `.venv/bin/python -m unittest cluster.tests.test_benchmark_core -v`
  - 11 passed, 0 failed, 0 skipped.
- `.venv/bin/python -m unittest cluster.tests.test_core.ExperimentTests -v`
  - 22 passed, 0 failed, 0 skipped.
- `.venv/bin/python -m unittest discover -s cluster/tests -v`
  - 117 passed, 0 failed, 0 skipped.
  - Includes localhost-bound controller launcher lifecycle tests executed with
    sandbox network permission.
- `python3 -m compileall -q cluster/benchmark cluster/tests/test_benchmark_core.py cluster/tests/test_core.py`
  - Passed.
- `node --check cluster/dashboard/static/app.js`
  - Passed.
- `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `bash -n` for 12 public/project shell scripts and `python3 -m py_compile scripts/llm-cluster`
  - Passed after correctly separating the Python launcher from shell scripts.
- `python3 -m cluster.benchmark.runner --help` and
  `python3 -m cluster.clusterctl --help`
  - Passed; legacy CLI surfaces remain loadable.
- `git diff --check`
  - Passed.

## Tests skipped

- None in the final full local suite.

## Hardware tests not executed

- Raspberry Pi Worker verification: **NOT RUN — explicitly deferred by the
  user**. No connection, package installation, or power-warning evaluation was
  attempted in Phase 06.
- Jetson benchmark execution and real native RPC topology: **NOT RUN — Phase 06
  is a hardware-independent core modularization**. No remote device state was
  changed.

## Remaining risks

- The compatibility inventory loader still understands the legacy mandatory
  head row even though non-RPC participant planning rejects that row. Full
  inventory/control-plane migration is outside Phase 06.
- RPC still uses the legacy head coordinator and legacy topology semantics by
  design. Moving coordinator selection to a prepared Worker is Phase 07.
- Raw response persistence and crash replay remain later-phase work; Phase 06
  intentionally preserves the current hash/metrics-only request schema.

## Deferred work

- Phase 07: select and validate a Worker RPC coordinator while keeping the Mac
  Controller out of compute and preserving cleanup safety.
- Raspberry Pi environment and hardware acceptance remain deferred until the
  user resumes them.

## Next phase readiness

- **READY for Phase 07, but Phase 07 has not been started.**
- Phase 06 stops after its report checkpoint and GitHub push.
