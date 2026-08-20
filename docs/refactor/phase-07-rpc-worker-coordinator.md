# Phase 07 — RPC Worker Coordinator Migration

Date: 2026-08-20 (Asia/Seoul)

## Goal

Remove the legacy inference-head coordinator assumption and run every native
llama.cpp RPC coordinator on exactly one selected, prepared Worker. Keep the
Mac Controller outside inference compute while preserving RPC topology,
custom split ordering, experimental acknowledgement, pinned runtime, security
guard, cancellation, and cleanup guarantees.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `591ea01 docs(refactor): record phase 06 benchmark core checkpoint`
- Implementation commit: `1227f63 refactor(rpc): move coordinator role to selected worker`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added a pure Worker-only RPC coordinator selection policy:
  1. use a valid explicit `rpc_coordinator_node`;
  2. otherwise select the first Jetson in the selected order;
  3. otherwise select the first Raspberry Pi;
  4. reject a Controller, legacy `role=head`, unselected coordinator, or fewer
     than two Workers.
- Replaced the legacy head-coordinator validation in the RPC strategy and
  rebuilt RPC request plans from the runtime-resolved Worker coordinator.
- Changed the native RPC backend so all participants are Workers, remote RPC
  devices exclude the coordinator, and a Pi coordinator contributes through
  its loopback CPU RPC device.
- Changed model preflight and coordinator startup to use the selected Worker's
  remote project/model path. The Mac Controller filesystem is never used as
  the coordinator model source.
- Changed `llama-server` to bind on the selected Worker's LAN interface for the
  duration of the experiment, while retaining loopback health checks and the
  private-LAN-only warning/guard.
- Added an idempotent `RpcSession.close()` and context-manager boundary. A
  cleanup failure raises `RPC_CLEANUP_FAILED`, prevents a completed summary,
  and remains eligible for finalizer retry.
- Added structured RPC lifecycle classification for not-prepared, device,
  coordinator, model-load, connection, and cleanup failures in preparation for
  Phase 09 failure persistence.
- Added Dashboard/API support for explicit Worker coordinator selection. The
  resolved coordinator is persisted in the experiment definition and is the
  only Worker required to hold the GGUF for RPC admission.
- Added result topology recording for the actual coordinator Worker,
  coordinator platform, participants, remote RPC Workers, device order,
  endpoints, and unchanged tensor-split values.

## Changed behavior

- Model-parallel RPC no longer accepts `head + worker...`. It requires two to
  four selected Workers and assigns exactly one of them as coordinator.
- Empty coordinator selection is deterministic: Jetson-first, then Pi, while
  preserving the user's selected order inside each platform class.
- Pi-only selected Worker sets are architecturally valid; the first selected Pi
  becomes coordinator when the native runtime preflight succeeds.
- RPC inference requests now reach `http://<worker-host>:18080` instead of a
  Controller-local loopback server.
- RPC model existence is checked on the coordinator Worker. The Controller's
  local model catalog is no longer an RPC admission requirement.
- Failed RPC HTTP connections now carry the additive
  `RPC_CONNECTION_FAILED` request-record code.

## New abstractions

- `cluster.benchmark.rpc_selection`: pure Worker eligibility and deterministic
  coordinator selection.
- `WorkerRpcBackend`: Worker-hosted native RPC lifecycle implementation.
- `RpcSession.close()`: idempotent cleanup and structured cleanup failure
  boundary.

## Moved / extracted code

- Coordinator role selection moved out of legacy head lookup and into the pure
  selection policy shared by planning, Dashboard admission, and runtime.
- RPC scenario construction is deferred until runtime platform preflight has
  resolved and recorded the actual coordinator.
- Remote model containment now derives from the validated Worker project path
  and validated relative GGUF identifier instead of a local Controller path.

## Backward compatibility

- `cluster.benchmark.runner` keeps its existing public CLI and compatibility
  helpers.
- `LegacyRpcBackend`, `legacy_runtime_command`, and
  `default_legacy_rpc_backend` remain import aliases to the Worker
  implementation for callers that have not migrated names yet; they no longer
  restore legacy head compute behavior.
- Existing result topology fields remain present. `coordinator_platform` is
  additive, and failed summaries now retain the already-known topology.
- Existing schema version, request planning counts, metrics formulas, warmup
  exclusion, concurrency, cancellation, multi-model suite behavior, and fixed
  CSV columns are unchanged.
- The intentional compatibility break is the MASTER SPEC requirement that a
  legacy head/Controller cannot participate in RPC compute.

## Benchmark invariants

- Single-node, replicated round-robin, broadcast, and node-sweep planners and
  metric formulas are unchanged and remain golden tested.
- An RPC logical request is sent once to the resolved Worker coordinator.
- Custom tensor-split input remains keyed by selected Worker order and is
  reordered only to match the recorded native device order.
- Warmup stays outside measured request records.
- Cleanup executes after successful requests, failed requests, cancellation,
  and exceptions; cleanup failure cannot produce `status=completed`.

## Security considerations

- The existing explicit experimental acknowledgement is still mandatory.
- The existing Worker-auth/RPC incompatibility guard is unchanged: native RPC
  can run only when Worker API token mode is off and the operator accepts the
  trusted-LAN warning.
- Native RPC ports remain unauthenticated and are started only for an
  experiment, then stopped through session cleanup. No automatic firewall,
  sudoers, reboot, or persistent service was added.
- The coordinator bind host is a fixed implementation value, not user-supplied
  shell input. Runtime commands continue to use separate argv values.
- The pinned llama.cpp commit remains
  `f49e9178767d557a522618b16ce8694f9ddac628`.
- No credential, token, password, SSH key, model binary, runtime result, or
  generated benchmark output is included in the checkpoint.

## Tests executed

- `.venv/bin/python -m unittest cluster.tests.test_rpc_coordinator cluster.tests.test_benchmark_core cluster.tests.test_core -q`
  - 70 passed, 0 failed, 0 skipped.
- `.venv/bin/python -m unittest discover -s cluster/tests -v`
  - 131 passed, 0 failed, 0 skipped.
  - The first sandboxed run reached 129 tests but two localhost-bound launcher
    cases were denied socket creation by the sandbox. The required rerun with
    local loopback permission passed all 131 tests after the final changes.
- `python3 -m compileall -q cluster/benchmark cluster/dashboard cluster/tests/test_rpc_coordinator.py`
  - Passed.
- `node --check cluster/dashboard/static/app.js`
  - Passed.
- `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `bash -n` for every tracked/project `*.sh` file.
  - Passed.
- `python3 -m py_compile scripts/llm-cluster`
  - Passed.
- `.venv/bin/python -m cluster.benchmark.runner --help` and
  `.venv/bin/python -m cluster.clusterctl --help`
  - Passed; existing CLI surfaces remain loadable.
- `git diff --check`
  - Passed.

## Tests skipped

- None in the final local suite.

## Hardware tests not executed

- Real native multi-Worker RPC inference: **NOT RUN — only one prepared/known
  Jetson Worker is currently available, while the implementation requires at
  least two prepared Workers.** The Mac Controller cannot be used to fill the
  missing compute role.
- Raspberry Pi RPC/runtime verification: **NOT RUN — explicitly deferred by
  the user.** No SSH connection, package installation, power-warning handling,
  or source modification was attempted on the Pi in Phase 07.
- No remote Jetson process, package, model, power mode, or deployed source was
  changed during Phase 07.

## Remaining risks

- Native llama.cpp RPC remains proof-of-concept, unauthenticated, and sensitive
  to LAN latency. Actual throughput and stability require a later two-Worker
  hardware acceptance run.
- Worker token authentication and unauthenticated native RPC remain mutually
  exclusive until a separately specified secure tunnel transport exists.
- The compatibility inventory loader still understands legacy head rows, but
  RPC planning and runtime reject those rows as participants.
- Dashboard model discovery remains Controller-local until the later Worker
  model-catalog phase. RPC admission itself now trusts the selected
  coordinator's live inventory rather than requiring a duplicate Controller
  model.
- Full structured failure serialization is intentionally deferred to Phase 09;
  Phase 07 establishes stable codes and exception/record boundaries only.

## Deferred work

- Durable scheduler/process recovery belongs to Phase 08 and was not started.
- Full failure persistence/diagnosis belongs to Phase 09 and was not started.
- Raspberry Pi hardware acceptance remains deferred until the user resumes it.

## Next phase readiness

- **READY for Phase 08, but Phase 08 has not been started.**
- Phase 07 stops after its report checkpoint and GitHub push.
