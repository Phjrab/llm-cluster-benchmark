# Roadmap Phase 00 — Current Main Baseline

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: baseline/report only; no product code changes

## 1. Phase contract

This report executes Phase 00 of `llm_cluster_future_roadmap.md`. It does not
re-run the historical refactor Phase 00 and does not start Roadmap Phase 01.

The repository, checked-in research locks, public interfaces, regression suite,
and registered Worker inventory were inspected from the current Mac Controller.
The Worker status check was read-only. No Worker was provisioned, restarted,
updated, power-cycled, or used for inference.

## 2. Git baseline and source of truth

| Item | Observed value |
|---|---|
| Repository | `/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark` |
| Remote | `https://github.com/Phjrab/llm-cluster-benchmark.git` |
| Baseline branch | `main` |
| Baseline commit | `128c7445c87da7796d4187c05aee3f50e508f650` |
| `origin/main` after fetch | `128c7445c87da7796d4187c05aee3f50e508f650` |
| Phase branch | `codex/roadmap-phase-00` |
| Baseline working tree | clean |
| Tracked files | 150 |
| Python files | 86 |
| Test files | 26 |
| Product shell/launcher files | 10 (9 Bash, 1 Python) |

The current `main` is the authoritative product tree. It already contains the
Mac Controller refactor, Pi power-integrity follow-ups, six-Worker acceptance,
research identity lock, dashboard/Worker operational improvements, and the
history-preserving legacy retirement merge.

Historical provenance remains reachable from the current history while the
retired top-level legacy product tree is absent from the current checkout. No
force-push, history rewrite, or legacy source resurrection is required for this
roadmap.

## 3. Current architecture baseline

```text
Mac Controller (control plane only)
├── Dashboard / FastAPI
├── Worker inventory and status monitor
├── SSH / rsync provisioning and deployment
├── model catalog and per-Worker model inventory
├── durable experiment/suite/job state
├── benchmark orchestration and result aggregation
└── research identity locks
        │
        ├── Jetson Worker × 3 (CUDA)
        └── Raspberry Pi Worker × 3 (OpenBLAS)
```
The Controller is not an inference participant. Benchmark planning rejects a
Controller in a Worker plan, and RPC coordinator selection resolves to a
selected Worker.

Primary module boundaries now present:

| Boundary | Current responsibility |
|---|---|
| `cluster/domain` | Controller/Worker, experiment, strategy, model, power, event, and failure types |
| `cluster/infrastructure` | storage, process identity, SSH/remote execution, SSE, Worker HTTP client, platform/runtime boundaries |
| `cluster/application` | durable jobs, suite execution, model service |
| `cluster/benchmark` | strategy planning, executor, metrics, persistence facade, power observations, RPC lifecycle |
| `cluster/worker` | standalone Worker API, llama-cpp backend, telemetry, power control |
| `cluster/dashboard` | Controller application facade, HTTP adapters, Dashboard UI |
| `cluster/research` | formal lock validation, canonical fingerprint, and eligibility assessment |
| `cluster/integrations` | runtime layout and legacy inventory adapters |

Large compatibility facades remain and are intentionally not split in this
phase:

| File | Lines |
|---|---:|
| `cluster/dashboard/static/app.js` | 2,668 |
| `cluster/dashboard/services.py` | 2,441 |
| `cluster/clusterctl.py` | 2,022 |
| `cluster/benchmark/runner.py` | 296 |
| `cluster/worker/app.py` | 229 |
| `cluster/worker/routes.py` | 248 |

These are later Roadmap Phase 12 candidates. They are not a blocker for Worker
deployment identity work.

## 4. Controller runtime baseline

| Item | Current value |
|---|---|
| OS | macOS 26.3 (Build 25D125) |
| Kernel | Darwin 25.3.0, arm64 |
| Python | 3.13.2 |
| FastAPI | 0.136.0 |
| Uvicorn | 0.44.0 |
| Pydantic | 2.13.1 |
| psutil | 7.2.2 |
| httpx | 0.28.1 |
| Dashboard state during baseline | stopped |
| Dashboard URL | `http://127.0.0.1:8080/` |

The current package versions match the checked-in runtime lock's Controller
snapshot. The lock's source commit remains the earlier formal lock baseline,
not the current Git commit; this is expected to become explicit deployment
identity input in Phase 01 rather than being silently rewritten here.

## 5. Registered Worker inventory and current reachability

The local Controller inventory contains six enabled Worker-only rows.

| Worker | Platform | Address | Project directory | Current read-only status |
|---|---|---|---|---|
| `jetson-worker-01` | Jetson | `192.168.0.26` | `/home/jetson_orin_nano/project/llm/llm-cluster-benchmark-worker` | SSH/project ready; API stopped (`connection refused`) |
| `jetson-worker-02` | Jetson | `192.168.0.19` | `/home/jetson2/project/llm/llm-cluster-benchmark-worker` | SSH/project ready; API stopped (`connection refused`) |
| `jetson-worker-03` | Jetson | `192.168.0.6` | `/home/ho/project/llm/llm-cluster-benchmark-worker` | SSH/project ready; API stopped (`connection refused`) |
| `pi-worker-02` | Raspberry Pi | `192.168.0.14` | `/home/pi2/project/llm/local_llm_bench` | SSH/project ready; API stopped (`connection refused`) |
| `pi-worker-03` | Raspberry Pi | `192.168.0.9` | `/home/pi3/llm-cluster-benchmark` | SSH/project ready; API stopped (`connection refused`) |
| `pi-worker-04` | Raspberry Pi | `192.168.0.5` | `/home/pi4/llm-cluster-benchmark` | SSH/project ready; API stopped (`connection refused`) |

Command used:

```bash
.venv/bin/python -m cluster.clusterctl status
```

The initial probe timed out while the devices were disconnected. After the
operator reconnected them, the same read-only command verified SSH access and
the configured project directory on all six Workers. Worker API port 8000 was
not listening on any device, and Phase 00 did not start it. This report therefore
distinguishes:

- checked-in/previously accepted hardware facts; and
- live facts observed on 2026-08-23.

No previous acceptance result is presented as current API/inference readiness.
Hardware and model mutation tests were not attempted.

## 6. Formal research lock baseline

The four checked-in lock documents parse and validate:

```text
config/research/model_lock.json
config/research/experiment_conditions.json
config/research/prompt_set.json
config/research/runtime_lock.json
```

Canonical lock facts:

| Item | Value |
|---|---|
| Lock ID | `formal-study-v1` |
| Lock version | 1 |
| Prompt set version | 1 |
| Canonical lock-set SHA-256 | `4a03235dd7ae27a0a915e918c583df99aad2011e99f0baf1a9f61a3dcb5c3ab4` |
| Declared SHA-256 values | all four match the computed fingerprint |
| Lock source baseline | `e70f63e797b9952ad1073ce16f556f0ff874b43b` |
| Native llama.cpp RPC pin | `f49e9178767d557a522618b16ce8694f9ddac628` |

Model approval state:

| Model key | Current status |
|---|---|
| `qwen2.5-1.5b-instruct-q4-k-m-official` | `source_locked` |
| `qwen2.5-3b-instruct-q4-k-m-official` | `source_locked` |
| `granite-3.3-2b-instruct-q4-k-m-official` | `source_locked` |
| `granite-3.3-8b-instruct-q4-k-m-official` | `source_locked` |

Formal eligibility remains blocked by checked-in evidence:

1. approved model count is zero;
2. every Worker deployment commit and clean state is `unverified`;
3. official binary installation/checksum and Worker verification are incomplete;
4. chat-template and tokenizer metadata hashes are incomplete;
5. Qwen2.5 3B project license acceptance is pending;
6. Jetson power modes differ (`MAXN_SUPER`, `MAXN_SUPER`, `15W`);
7. Jetson L4T versions differ (`R36.4.7` vs `R36.5.0`).

Pi 02/03 history-only `0x50000` power warnings remain non-blocking by policy.
They must be preserved as measurement-quality context, not converted into a
false clean state or a general inference blocker.

## 7. Public compatibility baseline

### 7.1 Controller lifecycle

```text
llm-cluster start
llm-cluster stop
llm-cluster restart
llm-cluster status
llm-cluster logs
```

The checked-in launcher is `scripts/llm-cluster`. Controller setup installs the
user-local command link; source-checkout execution remains supported.

### 7.2 Worker-management CLI

`python -m cluster.clusterctl` currently exposes 19 commands:

```text
inventory, status, doctor, environment-check, environment-install,
discover, setup, sync-code, sync-models, delete-models,
install-model-url, prepare, prepare-rpc, power-status, power-set,
start, stop, restart, select-model
```

The benchmark runner retains:

```text
python -m cluster.benchmark.runner
  --config
  [--inventory]
  [--results-dir]
```

The durable child-process entry point retains explicit job, inventory, and
results paths.

### 7.3 Dashboard HTTP surface

The module-level Dashboard app registers 28 product routes (plus FastAPI's four
documentation/OpenAPI routes):

```text
GET    /
GET    /dashboard/health
GET    /api/controller/status
GET    /api/bootstrap
GET    /api/status
POST   /api/status/refresh
POST   /api/network/scan
POST   /api/onboarding/ssh-key
POST   /api/nodes/probe
POST   /api/nodes
PATCH  /api/nodes/{node_name}/name
DELETE /api/nodes/{node_name}
GET    /api/nodes/{node_name}/power
POST   /api/nodes/{node_name}/power
GET    /api/environment
POST   /api/actions
GET    /api/actions
GET    /api/models
GET    /api/settings
PUT    /api/settings
GET    /api/events
POST   /api/experiments
GET    /api/experiments
GET    /api/experiment-groups
POST   /api/experiments/cancel
GET    /api/runs/{run_id}
GET    /api/runs/{run_id}/responses
DELETE /api/runs/{run_id}
```

### 7.4 Worker HTTP surface

The standalone Worker app retains 11 product routes:

```text
GET  /health
GET  /api/models
POST /api/select-model
POST /api/unload-model
POST /api/chat/stream
GET  /cluster/health
GET  /cluster/models
POST /cluster/models/verify
POST /cluster/models/delete
POST /cluster/models/install
POST /cluster/chat/stream
```

### 7.5 Result format

The durable run layout remains:

```text
results/<run-id>/
├── config.json
├── events.jsonl
├── requests.csv
├── responses.jsonl
└── summary.json
```

Compatibility invariants rechecked by tests:

- `summary.json` schema version remains 2;
- `requests.csv` remains exactly 19 metric columns;
- raw prompt/response and structured failures remain additive in
  `responses.jsonl`;
- `participant_nodes` keeps Worker hardware/runtime context in the summary;
- request completion is journaled before final aggregation;
- run deletion moves terminal results to private trash rather than erasing
  active evidence in place;
- RPC/suite cleanup failure cannot be reported as completed.

## 8. Regression baseline

### 8.1 Full Python regression

```bash
.venv/bin/python -m unittest discover -s cluster/tests -v
```

Result:

```text
Ran 338 tests in 88.664s
OK
```

- Passed: 338
- Failed: 0
- Skipped: 0
- Known warning: Starlette reports that using `httpx` through
  `starlette.testclient` is deprecated and recommends `httpx2`.

The full suite includes real local Dashboard lifecycle/port checks, process
identity protection, packaging, strategy/metric golden tests, result durability,
RPC cleanup fixtures, API/auth contracts, and research lock gates.

### 8.2 Focused and static gates

| Gate | Result |
|---|---|
| `cluster.tests.test_research_locks` | 27/27 PASS |
| Research/config JSON parse | 6/6 PASS |
| `node --check` for legacy `app.js` and split JS modules | PASS |
| `node cluster/tests/test_dashboard_exports.js` | PASS |
| Python `compileall` for `cluster` using a temp pycache | PASS |
| Bash syntax | 9/9 PASS |
| Python launcher syntax (`scripts/llm-cluster`) | 1/1 PASS |
| CLI help surfaces | PASS |

An initial generic script loop attempted to parse the Python
`scripts/llm-cluster` launcher as Bash. The gate was corrected by shebang/type:
nine Bash files were checked with `bash -n`, and the Python launcher was checked
with `py_compile`. This was a test-harness correction, not a product failure.

## 9. Tests not run and why

| Test | Reason |
|---|---|
| Live Worker health/model inventory | all six Worker APIs were stopped; Phase 00 did not start processes |
| CUDA/OpenBLAS model load/generate | Worker APIs were stopped; Phase 00 does not mutate or run inference |
| Live native RPC | unauthenticated RPC was not started in this read-only phase |
| Power-mode apply | Phase 00 is read-only and does not change system power state |
| Model download/install/delete | outside Phase 00 and would mutate Worker/model state |
| Browser visual E2E | no product/UI behavior changed; JS contract fixture was run |

Historical hardware acceptance remains evidence of prior capability, not proof
of current availability.

## 10. Roadmap Phase 01 implementation map

Phase 01 should implement Worker Deployment Manifest / Source Identity without
copying `.git` to Workers.

Likely product files and responsibilities:

| File | Phase 01 responsibility |
|---|---|
| new `cluster/domain/deployment.py` | typed deployment identity and mismatch reasons |
| new `cluster/infrastructure/deployment.py` | deterministic source-tree/requirements manifest and canonical SHA-256 |
| `cluster/clusterctl.py` | generate manifest from the exact rsync include/exclude set; deploy it atomically after successful code sync |
| `cluster/worker/routes.py` | expose the verified deployment manifest additively in `/cluster/health` |
| `cluster/worker/app.py` | load manifest through an explicit, testable boundary rather than relying on `.git` |
| `cluster/dashboard/services.py` | compare live deployment identity with formal lock/preflight and report drift |
| `cluster/research/locks.py` | formal-only source/deployment fingerprint eligibility rule |
| new/extended tests | deterministic tree hash, exclusions, tamper/missing/mismatch, health compatibility, preflight block, permission and no-secret checks |
| `docs/roadmap/phase-01-worker-deployment-identity.md` | exact contract, migration, test, and live acceptance report |

Exact design constraints for Phase 01:

1. hash the same deterministic source set that is deployed;
2. exclude `.git`, `.venv`, `.run`, models, outputs/results, caches, and secrets;
3. distinguish source commit from source tree SHA-256;
4. include pinned requirement hashes, llama-cpp-python version, RPC commit, and
   deployment timestamp without making timestamps part of deterministic identity;
5. atomically write private manifest files;
6. expose fields additively so old Workers remain readable but formal eligibility
   fails closed on missing/mismatched identity;
7. keep normal smoke experiments compatible unless formal mode is explicitly
   requested;
8. never edit source directly on a Worker.

## 11. Risk and readiness

### Current non-blocking technical debt

- SSH host keys still use `accept-new` TOFU instead of explicit pinning.
- Native llama.cpp RPC remains unauthenticated and private-LAN/ephemeral only.
- Dashboard/Worker token authentication defaults off by owner policy.
- Large Dashboard and CLI compatibility facades remain.
- Starlette/httpx emits a deprecation warning.

### Blocking the formal campaign

- Worker source/deployment identity is unverified.
- No model is approved.
- Jetson power/runtime cohorts are not aligned.
- The formal matrix, instrumentation, campaign runner, pilot, and repeat policy
  are not yet complete.

### Phase 01 readiness

**READY for implementation, offline tests, and SSH deployment validation.**

All six registered Workers are reachable through SSH and their project roots
exist. Their APIs are currently stopped, so Phase 01 must start/restart a Worker
only as part of its explicit deployment acceptance before checking the additive
health manifest. Worker reachability is no longer a Phase 01 blocker.

## 12. Completion report

```text
Implemented:
- Roadmap Phase 00 current-main baseline report only

Changed behavior:
- none

Backward compatibility:
- unchanged

Tests passed:
- Python full regression: 338/338
- research lock gates: 27/27
- JavaScript syntax/export fixture: passed
- Python compileall: passed
- Bash/Python launcher syntax: 10/10
- config/research JSON parse and fingerprint validation: passed

Tests not run / reason:
- live Worker health/inference/RPC/power/model mutation tests: all six Worker APIs
  were stopped; Phase 00 did not start or mutate them
- browser visual E2E: no product/UI change

Remaining issues:
- Worker deployment identity unverified
- approved model count zero
- Jetson power mode/L4T mismatch
- Worker APIs currently stopped

Next phase readiness:
- READY for Phase 01 implementation and SSH deployment validation
- Worker API manifest acceptance requires an explicit Phase 01 start/restart
```
