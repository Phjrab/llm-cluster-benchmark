# Phase 15 — Deployment / Hardware Acceptance / Git Finalization / Legacy Cleanup Gate

Date: 2026-08-20  
Branch: `codex/mac-control-plane`  
Status: **BLOCKED_BY_HARDWARE_ACCEPTANCE**

Phase 15 completed every available Mac Controller and single-Jetson acceptance step. Raspberry Pi acceptance, two-or-more-Worker replicated strategies, node sweep, and model-parallel RPC could not be executed because only one Jetson Worker was available. The legacy Jetson workspace was therefore **not deleted**.

## 1. Before / After Architecture

### Before

```text
Jetson legacy Head
├── Dashboard / scheduler
├── local llama-cpp inference
├── worker lifecycle
├── models
└── benchmark results

Mac
└── no authoritative Controller runtime
```

The legacy checkout at `/home/jetson_orin_nano/project/llm/local_llm_bench` is still a dirty, hardware-bound workspace. It remains at `98d88c2`, contains local benchmark/output changes, and must not be overwritten by deployment.

### After

```text
Mac Controller (source of truth; no inference)
├── localhost Dashboard / API
├── worker-only inventory
├── durable job / event / result storage
├── benchmark planning and aggregation
└── SSH / HTTP orchestration
         │
         └── Jetson Worker: 192.168.0.26
             ├── isolated deployed project
             ├── project-local .venv
             ├── llama-cpp-python 0.3.20 + CUDA sm_87
             ├── jtop 7.2.1 + psutil telemetry
             ├── Worker API on :8000
             └── Worker-owned GGUF model inventory
```

The deployed Worker path is `/home/jetson_orin_nano/project/llm/llm-cluster-benchmark-worker`. The legacy workspace and its models/results were not modified.

The authoritative workflow is now:

```text
Mac repository
→ code change
→ full tests
→ focused commit
→ GitHub push
→ worker deploy
→ hardware acceptance
```

Source code is not edited directly on Workers.

## 2. Module Responsibility Table

| Module | Responsibility | Depends on |
|---|---|---|
| `cluster/domain` | Controller/Worker roles, experiment/model/strategy types, identifiers, structured failures | Python standard library only |
| `cluster/infrastructure` | runtime paths, filesystem persistence, SSH/process safety, SSE and Worker HTTP client | domain, psutil where process inspection is required |
| `cluster/application` | durable jobs, suite ordering, model orchestration | domain, infrastructure, benchmark facade |
| `cluster/benchmark` | strategy plans, request execution, metrics, raw response/result persistence, RPC lifecycle | domain, infrastructure Worker client |
| `cluster/worker` | standalone Worker API, GGUF inventory, llama backend, telemetry | worker dependencies and platform-native runtime |
| `cluster/dashboard` | Mac Controller API, durable service facade, HTML/static UI | application, infrastructure, domain |
| `cluster/cli/controller.py` | safe local Dashboard start/stop/restart/status/logs | infrastructure process identity and runtime layout |
| `cluster/clusterctl.py` | remote Worker discovery, setup, deploy, model and lifecycle commands | infrastructure remote boundary, Worker scripts |
| `cluster/worker_setup.sh` | Jetson/Pi project-local venv and native backend readiness | fixed system package allowlist, platform toolchain |
| `cluster/integrations` | legacy inventory compatibility and canonical runtime layout | domain |

## 3. Dependency Graph

```text
Dashboard UI
    ↓ HTTP / SSE
Dashboard routes → application services → durable repositories
                         ↓                  ↓
                   benchmark facade → result/config/event artifacts
                         ↓
                  Worker HTTP client
                         ↓ LAN
Jetson/Pi Worker routes → inference backend → GGUF + CUDA/OpenBLAS
                       ↘ telemetry provider → jtop/psutil

Mac lifecycle CLI → process guard → localhost Dashboard
clusterctl → safe SSH/rsync argv → worker setup/start/stop/RPC scripts

domain ← infrastructure ← application / benchmark / dashboard / worker
```

The Controller import/dependency boundary does not include `llama_cpp`, CUDA, OpenBLAS, or jtop.

## 4. Changed Files

### Phase 15 product fixes

- `cluster/worker/start.sh`
  - enters the deployed project root before importing the process guard.
- `cluster/worker/stop.sh`
  - applies the same arbitrary-working-directory guarantee.
- `cluster/worker_setup.sh`
  - resolves the installed system `jetson-stats` version, validates it as a numeric release, and installs the matching venv client.

### Phase 15 tests

- `cluster/tests/test_process_guard.py`
  - locks project-root entry before the first process-guard call.
- `cluster/tests/test_worker_setup_safety.py`
  - locks system-service/client jtop version alignment and removes the stale 4.3.2 pin.

### Phase 15 report

- `docs/refactor/phase-15-deployment-hardware-git-cleanup.md`

### Runtime artifacts outside Git

- Mac Worker inventory: `.run/cluster/nodes.local.csv`
- successful run: `.run/cluster/results/20260820_220359_977d21`
- failure UX run: `.run/cluster/results/20260820_221013_2c01df`
- pre-deployment archive: `../migration-archives/jetson-legacy-20260820-phase15-predeploy`

No files were moved or removed in Phase 15. Existing compatibility wrappers remain in place. No legacy source, result, model, token, key, or environment file was deleted.

## 5. Public Compatibility

### CLI

- `llm-cluster start|stop|restart|status|logs` remains unchanged.
- `cluster.clusterctl` command and representative flag surfaces remain additive and pass compatibility tests.
- `cluster.benchmark.runner --config --inventory --results-dir` remains unchanged.

### Dashboard API

- Existing route manifest remains registered.
- Controller role stays explicit and inference-disabled.
- durable runs, raw responses, structured failures, Worker inventory, and environment actions remain readable after restart.

### Worker API

- Existing health, model inventory/install/verify/delete, model load/unload, and streaming routes remain registered.
- Worker auth remains optional and default-off according to the configured local-LAN policy.

### Result schema

- schema version 2 and the existing 19-column request CSV contract remain unchanged.
- `responses.jsonl` remains additive and durable before final summary creation.
- failed latency samples remain excluded from percentile calculations.

### Filesystem layout

- Controller state remains under Mac `.run/cluster` with 0700 directories and 0600 sensitive artifacts.
- Worker state remains under the deployed Worker project `.run/cluster`.
- legacy result readers and runtime-path overrides remain compatible.

### Shell scripts

- public shell syntax and wrapper delegation remain valid.
- worker lifecycle now works from arbitrary SSH working directories without changing its CLI.
- systemd auto-start was not introduced.

## 6. Test Report

### Automated

- Python unittest discovery: **233 passed, 0 failed**.
- Dashboard export fixture: passed.
- JavaScript syntax: passed.
- Python compileall: passed.
- all public shell scripts: `bash -n` passed.
- JSON configuration parse: passed.
- Controller and benchmark CLI help surfaces: passed.
- wheel build/install/isolated import gate: included in the Python suite and passed.
- `git diff --check`: passed.

### Mac clean-clone Controller acceptance

- clean clone from GitHub branch into `/tmp/llm-phase15-clean.UAB8Wf/repo`: passed.
- isolated HOME setup-controller: passed.
- Controller-only dependencies and empty Worker inventory: passed.
- user symlink invocation from `/tmp`: passed.
- lifecycle: stopped → start PID 72636 → restart PID 72671 → stop: passed.
- `/dashboard/health`: Controller role, inference disabled: passed.
- stop released localhost port 8080: passed.

### Jetson hardware integration

Device: NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super, Ubuntu 22.04.5, L4T 36.4.7, CUDA 12.6, aarch64.

- SSH key authentication and private-LAN inventory: passed.
- isolated worker-only deployment path: passed.
- fixed system package readiness: passed without sudo installation.
- project-local Python 3.10.12 venv: passed.
- `llama-cpp-python 0.3.20` CUDA `sm_87`: verified.
- power mode observed: `MAXN_SUPER`.
- jtop system/client 7.2.1 alignment: verified after Phase 15 fix.
- telemetry: `jtop+psutil`, ready=true, degraded=false.
- idle sample: GPU 0%, power 4.86 W, GPU temperature 40.56°C.
- model inventory started empty: verified.
- 1B Q4_K_M install → SHA-256 verify → delete → empty inventory → reinstall: passed.
- temporary loopback model server: explicitly stopped; PID and port 18081 absence verified.
- Worker API and model inventory on port 8000: passed.
- single-node CUDA benchmark: completed, 2/2 successful physical calls.
  - 48 generated tokens
  - 39.63 cluster tokens/s
  - TTFT p50 0.0927 s, p95 0.1172 s
  - E2E p50 0.6023 s, p95 0.6433 s
  - deterministic response hash matched across both requests.
- raw result artifacts: all five files present and mode 0600.
- Dashboard restart: PID changed and the Worker plus completed run were recovered.
- interactive chart legend: pressed state `true → false → true`.
- response review: prompt, model answer, response hash, TTFT/E2E/TPS visible.
- structured failure UX: `MODEL_MISSING` and its actionable solution visible.

### Skipped / blockers

- Raspberry Pi Worker acceptance: postponed; hardware not available in this phase.
- replicated round-robin with two or more Workers: blocked by Worker count.
- broadcast comparison: blocked by Worker count.
- cumulative/individual node sweep: blocked by Worker count.
- model-parallel RPC: blocked because the strategy requires at least two Workers.
- heterogeneous Jetson + Pi comparison: blocked by Pi availability.

## 7. Migration / Deployment Result

The current GitHub source-of-truth commit after hardware fixes is `786febd` on `origin/codex/mac-control-plane` before this report commit.

Hardware acceptance found and resolved two real deployment defects using the required workflow:

1. Worker lifecycle could not import `cluster.infrastructure.process_guard` when invoked over SSH from a non-project working directory.
   - fixed on Mac, 232 tests passed, commit `06fee68`, pushed, then redeployed.
2. the project venv used jetson-stats 4.3.2 while the active system jtop service used 7.2.1.
   - fixed on Mac, 233 tests passed, commit `786febd`, pushed, then redeployed.

The Mac Controller now owns orchestration and durable results. The Jetson runs only the Worker API and inference runtime in the new deployment path.

### Backup and deletion gate

The previously verified Phase 01 archive remains the complete Git/source-history backup. Its full Git bundle passed `git bundle verify`, and its 159-entry checksum manifest passed `shasum -a 256 -c`.

A new private Phase 15 archive was created before deployment:

`/Users/hajoonpark/Documents/자율설계/migration-archives/jetson-legacy-20260820-phase15-predeploy`

It contains 83 files / 4.6 MB:

- 20 current benchmark output files
- 56 current durable result files
- 3 current experiment files
- model paths, sizes, and nine recomputed SHA-256 hashes
- archive documentation and checksum manifest

All archive files are private (0600; directories 0700), and its checksum manifest passed verification. Tokens, SSH keys, settings, node inventory, model binaries, venvs, caches, PID/log files, and llama.cpp source/build directories were excluded. A second all-history bundle was intentionally not created because historical secrets cannot be proven absent; the already verified Phase 01 bundle remains the history backup.

**Deletion decision: denied.** The legacy Jetson workspace remains untouched because multi-Worker, Pi, and RPC hardware acceptance are incomplete.

## 8. Remaining Technical Debt

- complete Pi runtime, OpenBLAS, model install, telemetry, and benchmark acceptance.
- complete two-Worker round-robin, broadcast, cumulative/individual sweep, and RPC acceptance.
- confirm jtop version-alignment behavior on a second Jetson image/version.
- investigate the in-app Browser console message `c.nodeName.toLowerCase is not a function`; no matching product source expression was found and visible Dashboard behavior was unaffected.
- de-duplicate the same terminal structured failure when both `failure` and `failures` carry the identical record.
- improve the intermediate node error mapping for model-load HTTP 404; the terminal summary is correctly `MODEL_MISSING`, while the intermediate event currently reports `WORKER_OFFLINE`.
- reconcile the earlier Jetson power-mode configuration requirement with the refactored public API. This acceptance observed `MAXN_SUPER`, but the current Worker route manifest exposes telemetry rather than a power-mode mutation endpoint.
- wheel installation validates imports/data, while operational setup and launchers still intentionally assume a source checkout.

## 9. Recommended Next Work

### Required manual hardware acceptance before deletion

1. Register and deploy at least one additional Worker, including the postponed Raspberry Pi where available.
2. Run `environment-install --confirmed` and verify `backend.verified=true` on every Worker.
3. Install the same checksum-verified model on replicated-strategy Workers.
4. Run two-or-more-Worker round-robin and broadcast smoke configs.
5. Run cumulative and individual node sweeps and verify scenario-specific speedup/efficiency.
6. Build the pinned RPC runtime on every participant, run the acknowledged RPC smoke, and verify cleanup leaves ports 50052/18080 closed.
7. Restart the Dashboard during a live durable job and verify exact recovery/cancellation behavior on real hardware.
8. Re-run raw response, chart, failure, and publication export checks with multi-node results.
9. Recompute and verify the legacy archive checksum after any new legacy result activity.
10. Only after all checks pass, obtain explicit deletion approval for the exact legacy path.

Separate follow-up PR candidates, as required by the phase scope:

- prefill/input-token metrics
- energy-per-token
- network benchmark metadata
- additional auth hardening
- heterogeneous methodology improvements
- CI expansion

## Definition of Done

- [x] automated regression complete
- [x] actual Controller clean-clone setup validated
- [x] available Jetson hardware acceptance completed
- [x] unavailable hardware blockers explicit
- [x] Git state reviewed and source-of-truth workflow exercised twice
- [x] legacy data archived before any deletion
- [ ] full Jetson/Pi/multi-Worker/RPC acceptance
- [ ] legacy workspace deletion — intentionally blocked

