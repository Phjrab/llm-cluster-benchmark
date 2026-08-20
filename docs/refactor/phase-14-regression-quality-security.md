# Phase 14 — Full Regression / Security / Quality / Packaging Readiness

Date: 2026-08-20 (Asia/Seoul)

## Goal

Validate the completed Mac Controller / Jetson-Raspberry Pi Worker architecture
against the full benchmark, persistence, API, lifecycle, and security contract;
remove unsafe runtime behavior found by that audit; and establish an additive,
offline-verifiable Python packaging baseline without adding product features or
changing benchmark semantics.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `4e6175a docs(refactor): record phase 13 dashboard checkpoint`
- Security/runtime commit: `7030943 fix(security): harden cluster runtime boundaries`
- Quality/packaging commit: `0764068 test(quality): lock phase 14 compatibility and packaging`
- Both implementation commits pushed: `SUCCESS` to `origin/codex/mac-control-plane`

## Baseline and reconciliation

- Phase 13 baseline: `171` Python tests passed.
- Phase 14 final: `231` Python tests passed, `0` failed, `0` skipped.
- The additional coverage is security, process identity, native RPC cleanup,
  packaging, Worker-only inventory, runtime override, and compatibility matrix
  coverage. Existing benchmark golden tests remained green.
- No model, result, runtime state, token, SSH key, environment report, or device
  configuration was added to Git.

## Implemented

- Added Phase 14 golden/contract gates for single-node routing, round-robin,
  broadcast logical/physical counts, node sweep ordering, warmup exclusion,
  p50/p95 interpolation, throughput, exact agreement, speedup/efficiency,
  cancellation, per-node serialization, multi-model suites, RPC cleanup,
  raw-response/result durability, Controller/Worker role separation, and API/CLI
  compatibility.
- Added strict inventory validation for private/loopback IPv4, node IDs, SSH
  users, ports, project roots, and user-owned private regular identity files.
  Existing `~` and environment-variable identity references remain supported
  only after safe absolute-path resolution.
- Made Worker-only inventories valid for the public Controller CLI, benchmark
  runner, and durable child process. Legacy head rows remain readable but are
  never required or promoted to inference participants.
- Preserved late `CLUSTER_RUNTIME_DIR`, `CLUSTER_INVENTORY`, and
  `CLUSTER_RESULTS_DIR` overrides for Controller subprocesses while retaining
  the existing patchable `DEFAULT_*` compatibility seams.
- Added `process_guard.py`, which records PID, executable, working directory,
  complete argv, process creation time, and user in private atomic metadata.
  Worker, standalone legacy server, and native RPC launchers now use bounded
  TERM/KILL handling with identity revalidation, PID-reuse protection, lifecycle
  locks, exact listener ownership checks, and failed-start rollback.
- Hardened native RPC worker/coordinator startup and cleanup with exact native
  argv records, project-root cwd, candidate-owned socket checks, signal/exit
  traps, private logs/metadata, safe legacy adoption, and idempotent cleanup of
  every attempted device even if SSH loses the start response.
- Rejected `model_parallel_rpc` at both Dashboard and direct runner boundaries
  while Worker API authentication is enabled, because llama.cpp RPC itself is
  unauthenticated.
- Removed Dashboard credentials from SSE/query URLs. The streaming client now
  uses Fetch with `X-Cluster-Token`; query-string tokens are rejected.
- Sanitized unexpected Dashboard 500 responses and logs to a correlation ID,
  route context, and exception type without exception text, prompt text,
  command output, paths, or credentials.
- Made corrupt security settings fail closed, repaired existing Worker token
  modes to `0600`, hid Worker tokens from curl argv, and tightened known runtime,
  experiment, suite, job, result, response, inventory, and token artifacts to
  `0700` directories and `0600` files.
- Bound real Worker telemetry startup/shutdown to ASGI lifespan and added a
  bounded Jetson sampler thread shutdown.
- Added early, side-effect-free rejection of broad or ambiguous Worker setup
  project paths. Fixed allowlisted packages and `sudo -n` remain the only
  automatic system-package path.
- Added `pyproject.toml` with explicit package discovery, role-specific pinned
  Controller/Worker extras, runtime static/template/config/shell data, and no
  Controller inference dependency. Added an offline wheel-build, clean-venv
  install, and isolated import test.
- Reconciled the cluster README and default experiment node with the Mac
  Controller / Worker-only architecture.

## Changed behavior

- Public IPs, hostnames, CGNAT/link-local addresses, broad project roots,
  relative or permissive SSH identity files, and malformed security documents
  now fail before SSH, rsync, package installation, or inference.
- A Dashboard token in `?token=` is no longer accepted. Header authentication
  remains the supported wire contract when Dashboard authentication is enabled.
- A launcher no longer treats an unrelated healthy process on the same port as
  its own service. It succeeds only when the exact recorded child owns the
  listening socket and the health response is valid.
- Unsafe or incomplete PID metadata is refused without signalling. A matching
  legacy PID-only process can be adopted, but ambiguous/custom legacy processes
  require manual cleanup rather than a wildcard kill.
- Existing known sensitive runtime artifacts are permission-migrated when the
  Dashboard initializes; new artifacts are private at creation.
- `clusterctl` and the benchmark runner no longer require a synthetic enabled
  legacy head in a Mac Controller inventory.
- The shipped example now selects `edge-worker-01` rather than `edge-head`.

## New abstractions / extracted responsibilities

- `cluster.infrastructure.process_guard.ServiceSpec`: exact Python/uvicorn
  service identity and compatibility adoption contract.
- `cluster.infrastructure.process_guard.NativeServiceSpec`: exact executable,
  cwd, user, and full-argv contract for native llama.cpp services.
- `ProcessGuard`: atomic metadata, locate/adopt, listener ownership,
  terminate-candidate, bounded stop, and PID-reuse-safe signalling.
- Phase-specific regression/security/packaging tests are isolated from product
  runtime modules and do not alter result computation.

## Public compatibility matrix

| Surface | Phase 14 result |
|---|---|
| `single_node` | All requests remain mapped to exactly one selected Worker; extra Workers are rejected. |
| `replicated_round_robin` | Existing balanced request mapping and physical/logical request semantics unchanged. |
| `broadcast_compare` | `R × N` physical calls, exact agreement, and logical grouping unchanged. |
| `node_sweep` | Explicit Worker ordering, speedup, and efficiency baselines unchanged. |
| Warmup / cancellation / per-node lock | Warmup remains excluded; bounded submission and Worker serialization contracts remain green. |
| Metrics | p50/p95 interpolation, failed-latency exclusion, requests/s, and effective token/s formulas unchanged. |
| Multi-model suite | Ordered independent runs, unload/cooldown, partial/cancelled recovery, and cleanup failure persistence unchanged. |
| RPC | Worker coordinator selection and Pi-only loopback behavior unchanged; cleanup and security guards are stronger. |
| Result files | Existing `config.json`, `events.jsonl`, `responses.jsonl`, `summary.json`, and 19-column `requests.csv` schema remain readable and unchanged; permissions are tighter. |
| Dashboard API | Existing method/path manifest remains registered; error bodies are sanitized and query-token authentication is intentionally removed. |
| Worker API | Existing route manifest and opt-in token header remain unchanged; every route is covered by auth tests. |
| CLI | Existing commands and representative flags remain; Worker-only inventory and runtime overrides now work as documented. |
| Legacy data | Legacy head/config/result/PID adapters remain where safe; no committed result was rewritten or deleted. |
| Packaging | Source-checkout execution remains supported; wheel build/install/import is additive. |

## Backward compatibility

- No benchmark planner, scheduler, metric formula, result schema, response
  journal, suite state machine, model preflight rule, or chart/publication format
  was intentionally changed.
- Existing Dashboard and Worker route paths and existing CLI command names remain
  available. New checks reject only values that could not be proven safe.
- Existing auth-off behavior remains the default by explicit project policy.
  Valid existing settings and tokens continue to work; only corrupt/ambiguous
  security state fails closed.
- Legacy head inventories remain readable through compatibility paths. New Mac
  Controller operation no longer depends on them.
- Legacy scripts retain their public start/stop messages and environment-based
  host/port configuration. Unsafe PID-only processes are never wildcard-killed.

## Benchmark invariants

- Controller never loads a model or participates in inference/RPC computation.
- Only selected Jetson/Pi Workers receive benchmark requests.
- Warmups are not included in measured request metrics.
- Logical and physical request counts retain strategy-specific meaning.
- Failed requests do not contaminate latency percentiles or successful-token
  throughput.
- Raw responses and structured failures remain durable before final summary
  completion.
- Cleanup failure cannot produce a completed RPC or suite result.

## Security considerations

- SSH uses `BatchMode`, fixed argv shell quoting, a private identity file, and
  RFC1918/loopback-only inventory addresses.
- Automatic package installation remains restricted to a fixed allowlist and
  non-interactive `sudo -n`; no user-supplied package or sudo command is run.
- Token comparisons remain constant time. Tokens are absent from URL query
  strings, curl argv, action records, generic error bodies, and structured logs.
- llama.cpp RPC is still an unauthenticated proof-of-concept protocol. It is
  permitted only on the explicitly acknowledged private-LAN path, is ephemeral,
  and is mutually exclusive with Worker API authentication.
- Dashboard/Worker authentication remains disabled by default for the owner's
  trusted LAN workflow. Enabling it does not add TLS; an untrusted network still
  requires a VPN/TLS boundary.
- SSH host keys remain `accept-new` TOFU rather than pre-pinned fingerprints.

## Quality / module responsibility review

- Large compatibility coordinators remain: `dashboard/static/app.js` 2,260
  lines, `dashboard/services.py` 1,877 lines, and `clusterctl.py` 1,732 lines.
- Phases 10–13 already extracted events, result/model browser modules, Dashboard
  routes, application jobs, infrastructure, and benchmark core. Phase 14 avoided
  a high-risk mechanical split and added tests around the remaining compatibility
  facades instead.
- `dict[str, Any]` remains mainly at external JSON/process/API boundaries. Core
  domain, structured failures, process identity, and RPC selection are typed.
- Legacy setup/read adapters were retained because tests demonstrate active
  compatibility use; no unproven branch was deleted merely to reduce line count.

## Packaging readiness

- Offline wheel build with no dependency download: passed.
- Wheel contents and METADATA role extras: passed.
- Clean virtual environment installation with `--no-index --no-deps`: passed.
- Isolated `python -I` imports outside the source tree plus packaged config/static
  resource read: passed.
- The supported operational launcher/setup path intentionally remains a source
  checkout because runtime layout, code synchronization, and Worker build scripts
  are repository-root workflows. No console entry point was invented in this
  phase.

## Tests passed

- Full regression gate, including real local Dashboard lifecycle, restart,
  health, listener ownership, and tampered PID non-signal tests:

  ```bash
  .venv/bin/python -m unittest discover -s cluster/tests -v
  ```

  - `231` passed, `0` failed, `0` skipped.

- Focused process guard, native RPC, and RPC coordinator matrix: `39` passed.
- Offline packaging suite: `3` passed.
- Python `compileall` for domain/application/benchmark/infrastructure/
  integrations/CLI/Dashboard/Worker/tests: passed.
- `bash -n` for every shell script under `cluster/`, `scripts/`, and the four
  root benchmark/server scripts: passed.
- Public help surfaces for `clusterctl`, benchmark runner, and durable job
  process: passed.
- `node --check` for `app.js` and every `static/js/*.js` module: passed.
- `node cluster/tests/test_dashboard_exports.js`: passed.
- All cluster config JSON files parsed successfully.
- `git diff --check`: passed.

## Tests not run / reason

- No live Jetson or Raspberry Pi was mutated. CUDA/OpenBLAS inference, jtop,
  actual `ss`/psutil listener ownership on target Linux, remote SSH/rsync/sudo,
  native llama.cpp RPC over LAN, thermals, power modes, and real GGUF load/generate
  remain hardware acceptance work.
- No real model was downloaded, deleted, or benchmarked; Phase 14 is a regression,
  security, and packaging checkpoint rather than an experiment run.
- No browser visual automation was run. The only frontend behavioral change is
  the authenticated SSE transport, covered by JavaScript and Dashboard API
  contract tests; no visual layout was redesigned in this phase.

## Remaining issues

- The only deployment acceptance gate is live Jetson/Pi hardware validation.
- Non-blocking technical debt: TOFU SSH host keys, unauthenticated experimental
  native RPC, HTTP-without-TLS when auth is enabled, large compatibility facades,
  subset-based portions of the API manifest tests, and string-based fallback
  classification for some native RPC coordinator errors.
- The current Starlette TestClient emits an upstream `httpx` deprecation warning.
  Dependency migration was not mixed into this security checkpoint because all
  supported tests pass and the pinned role requirements are reproducible.
- The wheel proves clean packaging/import/resource separation; operational
  source-checkout smoke remains the supported deployment contract rather than a
  wheel-installed daemon contract.

## Next phase readiness

- **READY for live hardware acceptance when explicitly requested.**
- Phase 14 is complete. No later phase was started by this checkpoint.
