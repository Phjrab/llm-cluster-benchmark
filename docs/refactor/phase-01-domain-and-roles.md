# Phase 01 — Pure Domain + Controller/Worker Role Separation

Date: 2026-08-20 (Asia/Seoul)

Implemented:

- Added a side-effect-free `cluster.domain` package for controller, worker,
  experiment, strategy, identifier, layout, and structured-error concepts.
- Made `ControllerConfig` a controller-only type that cannot be inserted into a
  `WorkerInventory`.
- Added `WorkerNode` and `WorkerInventory` without `head`, `role`, or local
  controller semantics.
- Added a pure legacy-inventory adapter. Legacy `head` rows are retained as
  migration evidence but are never converted into a controller or inference
  participant.
- Moved `ExperimentConfig`, model-ID validation, and model-suite normalization
  from the benchmark runner into the pure domain layer.
- Added typed execution, sweep, and RPC identifiers while retaining the existing
  JSON string values.
- Added pure `ProjectLayout`, shared identifier validation, stable error codes,
  structured failure records, and a `ValueError`-compatible domain validation
  exception.
- Added 33 Phase 01 unit and boundary tests.

Changed behavior:

- Invalid scalar/container types now fail deterministically at the domain
  boundary instead of leaking `TypeError` or accepting booleans/floats as integer
  configuration values.
- Model identifiers must be canonical, repository-relative `.gguf` paths and may
  not contain traversal, aliases, backslashes, or control characters.
- New worker-domain records accept only RFC1918 or loopback IPv4 addresses. A
  hostname or non-private legacy address is preserved as an unresolved migration
  record instead of being activated automatically.
- A legacy `~`/environment-variable SSH identity path is preserved as migration
  metadata but is not copied into a new `WorkerNode`; explicit re-keying is
  required.
- Dedicated controller/project layout paths reject filesystem anchors, broad
  roots, traversal, and control characters.
- `rpc_coordinator_node` is available as additive configuration and is validated
  only for model-parallel RPC. Runtime coordinator selection is intentionally not
  migrated in this phase.

Backward compatibility:

- Existing imports of `ExperimentConfig`, `EXECUTION_STRATEGIES`,
  `validate_model_id`, and `normalize_model_ids` from
  `cluster.benchmark.runner` remain valid through compatibility re-exports.
- Existing external strategy strings are unchanged:
  `single_node`, `replicated_round_robin`, `broadcast_compare`, `node_sweep`, and
  `model_parallel_rpc`.
- Existing configuration defaults, unknown-key tolerance, runner planning,
  scheduling, aggregation, CLI surfaces, API fields, and result schemas are
  unchanged.
- A stale RPC custom-split selector does not invalidate a non-RPC experiment,
  matching the pre-extraction behavior.
- Legacy inventory rows and current legacy `Node` objects are both accepted by
  the adapter. Legacy heads, unresolved hosts, and key references remain
  inspectable through conversion metadata and warnings.

Tests passed:

- Phase 01 pure-domain suite: 33/33 passed.
- Full Python suite in an isolated environment using the checked-in pinned
  runtime dependencies: 85 tests run, 82 passed, 3 platform tests skipped, 0
  failed.
- Dashboard JavaScript syntax and export fixtures: passed.
- Bash syntax: 12/12 repository scripts passed.
- Benchmark runner and `clusterctl` CLI help smoke tests: passed.
- Python compile checks for the changed modules and tests: passed.
- Jetson CPython 3.10.12 in-memory syntax check: 12/12 Python files passed; no
  remote file was written or service changed.
- `git diff --check`: passed.

Tests not run / reason:

- Three launcher tests require Linux `/proc` and pidfd behavior and were skipped
  on the macOS controller. They were not replaced with weaker mocks.
- Live Jetson/Pi inference, model loading, RPC, telemetry, power-mode mutation,
  and dashboard browser interaction were not run because Phase 01 is a pure
  domain extraction and explicitly does not deploy or change runtime behavior.

Remaining issues:

- The legacy runtime still contains `role == "head"` branches and preserves its
  current head-selection behavior. Migrating actual worker selection is outside
  Phase 01 and must occur in its designated later phase.
- `rpc_coordinator_node` is validated and serialized but is not yet used by the
  runtime planner.
- Unresolved legacy hostname/non-private-address rows require an explicit,
  private-IPv4 worker registration before activation.
- The macOS lifecycle, controller persistence, RPC coordinator runtime, and full
  hardware acceptance gates remain future-phase work.

Next phase readiness:

- **READY for Phase 02.** Controller and worker roles are separated at the type
  level, the legacy read path is explicit and non-participating, shared pure
  validation exists, and all Phase 01 test gates pass.
- Phase 02 has not been started by this change.

## Compatibility boundary

The only product-code edit outside the new packages is the import surface in
`cluster/benchmark/runner.py`. The former inline domain declarations were
removed and re-imported from `cluster.domain`; request planning, execution,
metrics, persistence, and report generation bodies were not edited.

The adapter deliberately does not infer a new controller from a legacy head.
Controller construction requires explicit controller configuration in a later
application/infrastructure phase. This prevents historical `head = control +
inference` coupling from entering the new domain.

## Phase 01 Definition of Done

- [x] Controller and Worker concepts are separated at the type level.
- [x] The new domain does not require `head = control + inference`.
- [x] A legacy read-compatibility path exists without automatic participation.
- [x] `ExperimentConfig` is separate from transport/Pydantic schemas.
- [x] Strategy identifiers are typed with unchanged wire values.
- [x] Pure unit and compatibility tests pass.
