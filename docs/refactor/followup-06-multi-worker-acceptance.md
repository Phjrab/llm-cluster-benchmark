# FOLLOWUP 06 — Jetson + Raspberry Pi Multi-Worker Acceptance

Status: **COMPLETE**

This phase executed only heterogeneous Jetson + Raspberry Pi multi-Worker acceptance. It did not run native RPC, did not install or rebuild an already-ready Worker environment, did not edit Worker source directly, and did not modify or delete the legacy Jetson workspace. The purpose was functional correctness and durable evidence, not linear heterogeneous speedup.

## Worker inventory and revisions

Selected Worker order was fixed and preserved throughout all four strategies:

1. `jetson-worker-01` — `192.168.0.26`, user `jetson_orin_nano`, NVIDIA Jetson Orin Nano Super, Ubuntu 22.04.5 LTS, `aarch64`
2. `pi-worker-02` — `192.168.0.14`, user `pi2`, Raspberry Pi 5 Model B Rev 1.0, Ubuntu 24.04.4 LTS, `aarch64`

Both are enabled private-LAN Workers. The macOS Controller and legacy head were not inference participants. Other registered Workers were outside the selected topology.

The Mac source-of-truth branch was clean at `53db712dcafaa8c2c54078e8a5584a27af295590` before hardware execution. Both isolated Worker deployments received the same repository `sync-code` payload and were restarted before acceptance. Their deployment directories intentionally exclude `.git`, so source parity was proven by byte-identical SHA-256 fingerprints on both Workers and the Mac for the following execution-critical files:

- `cluster/worker/routes.py`: `196bd31917fb933c4157b74cfd35a42ef1630c1577baa785d979c06f69d23fba`
- `cluster/benchmark/runner.py`: `39403a5287c30e0ce30a63610e6858d076a79be2b7bfc41b52f1effd473a2704`
- `cluster/domain/experiment.py`: `f2ee23dca2b9c8233c0632e06cd7b2b98f75d5a283faec369892bc4f5ad25685`
- `cluster/domain/power.py`: `e029aea3ee05d351c76ded54c8f0b7a1c01ea9f73652a1a2bb09ee8db87edd9d`
- `cluster/clusterctl.py`: `c99d2904d187fe2071e19c3ebc6f5f17b6348f765250f58e6902e96c647c38b9`

`environment-check` returned `READY` for both Workers. No package install, virtual-environment recreation, or backend rebuild was performed:

- Jetson: Python 3.10.12, `llama-cpp-python` 0.3.20, CUDA 12.6 / SM 8.7 verified, six inference threads, jtop telemetry ready
- Pi: Python 3.12.3, `llama-cpp-python` 0.3.20, OpenBLAS verified, four inference threads, psutil telemetry ready

## Model/checksum parity

Both Workers already contained and independently verified the same model before experiment timing:

- ID/file: `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- Quantization: Q4_K_M
- Size: 807,694,464 bytes
- SHA-256: `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
- `checksum_valid=true` on both Worker verification endpoints

No model was downloaded or synchronized during any benchmark. The deterministic common workload used context 512, temperature 0, top-p 0.9, seed 42, one warmup request, 16 generated tokens, prompt persistence, and uniform-config enforcement. Because a Raspberry Pi participated, the common replicated configuration used `n_gpu_layers=0`; results still record the Jetson CUDA and Pi OpenBLAS runtime identities separately.

## Power baselines

Jetson baseline:

- power profile: `MAXN_SUPER`
- `jetson_clocks`: off
- idle total power: approximately 4.74 W
- CPU/GPU temperature: approximately 38.3/38.5 °C
- telemetry provider: jtop + psutil, ready and not degraded

Raspberry Pi baseline and every run boundary:

- source: fixed `vcgencmd get_throttled`
- raw/status: `0x0` / `ok`
- current flags: all false
- historical flags: all false
- blocking: false
- measurement quality: `clean`

Power integrity remained additive evidence and never changed run status or request metrics.

## Round-robin

- run ID: `20260821_141601_9acdd1`
- strategy: `replicated_round_robin`
- status: `completed`
- logical/physical/successful: 5 / 5 / 5
- selected-order assignment: Jetson, Pi, Jetson, Pi, Jetson
- per-Worker balance: Jetson 3, Pi 2
- generated tokens: 80
- cluster throughput: 22.167850 tok/s
- TTFT p50 / E2E p95: 0.116477 / 1.803702 seconds
- average generation rate: Jetson 19.867320 tok/s, Pi 9.597783 tok/s

Warmup was excluded, every measured response was persisted, and the Controller received no inference request.

## Broadcast

- run ID: `20260821_141629_443e09`
- strategy: `broadcast_compare`
- status: `completed`
- logical/physical/successful: 3 / 6 / 6
- mapping: each logical request went once to Jetson and once to Pi
- group concurrency: 1 logical group
- all-replica success: 100%
- exact response-hash agreement: 100%
- physical replica aggregate: 17.759787 tok/s
- TTFT p50 / E2E p95: 0.124140 / 1.805267 seconds
- average generation rate: Jetson 23.364590 tok/s, Pi 9.610340 tok/s

The 17.76 tok/s value is aggregate duplicate work, not user-answer throughput. The Dashboard labels it accordingly and displays all six per-replica responses and hashes.

## Cumulative sweep

- run ID: `20260821_141811_eb09a4`
- strategy/mode: `node_sweep` / `cumulative`
- status: `completed`
- total logical/physical/successful: 8 / 8 / 8
- scenario order: `[jetson-worker-01]`, then `[jetson-worker-01, pi-worker-02]`

Scenario results:

- `nodes-1`: 4 / 4 successful, 18.950325 tok/s, speedup 1.0, efficiency 1.0
- `nodes-2`: 4 / 4 successful, 17.333863 tok/s, speedup 0.914700, efficiency 0.457350

The speedup and efficiency are calculated from the first selected-Worker scenario. The heterogeneous two-Worker scenario being slower is a valid observation for this short workload; it is not a functional failure.

## Individual sweep

- run ID: `20260821_141849_3e63f2`
- strategy/mode: `node_sweep` / `individual`
- status: `completed`
- total logical/physical/successful: 8 / 8 / 8
- scenario order: Jetson, then Pi

Independent scenarios used the same model and four-request workload:

- `jetson-worker-01`: 4 / 4, 17.945339 tok/s, TTFT p50 0.061343 s, E2E p95 1.055789 s
- `pi-worker-02`: 4 / 4, 8.869808 tok/s, TTFT p50 0.139354 s, E2E p95 1.811513 s

These values form the heterogeneous single-Worker baseline for this exact CPU-layer workload.

## Result/Dashboard validation

Every required completed run contains private-mode (`0600`) artifacts:

- `config.json`
- `events.jsonl`
- `requests.csv`
- `responses.jsonl`
- `summary.json`

All summaries use schema version 2. Every `requests.csv` has exactly 19 columns. CSV and response-journal row counts equal the physical request counts: 5, 6, 8, and 8. All four runs are `completed` with independent `measurement_quality=clean`; no duplicate power warning was emitted.

Live browser acceptance at `http://127.0.0.1:8080/#results` confirmed:

- Controller is shown separately from Worker cards
- Jetson is ONLINE / CUDA-ready and Pi is ONLINE / OpenBLAS-ready
- Pi card and result environment show `POWER NORMAL`
- all four strategy groups and exact run IDs are selectable
- broadcast shows six responses, per-Worker metrics, and exact-hash agreement
- cumulative and individual sweep charts show scenario values, speedup, and efficiency
- dashboard PNG and publication export controls remain available
- raw prompt/response views use the durable `responses.jsonl` records

Hardware acceptance exposed two Dashboard result-inspection defects. After correction, changing the experiment filter clears the previously selected run instead of showing stale responses, and sweep responses are grouped by `scenario_id + logical_request_id` rather than merging equal logical IDs across scenarios. A post-fix browser check showed eight distinct response cards with `nodes-1` and `nodes-2` labels.

## Functional failures vs performance observations

Functional acceptance passed for every required strategy. Lower Pi throughput, non-linear scaling, and cumulative speedup below 1.0 are performance observations only.

A non-destructive missing-model smoke used `followup06-not-installed.gguf`; it did not alter a model file or reach inference:

- corrected verification run ID: `20260821_143630_cd74e1`
- status/code: `failed` / `MODEL_MISSING`
- node-level and final failure codes: both `MODEL_MISSING`
- Pi power evidence: `0x0`, `clean`, non-blocking

This proves the blocking model failure remains authoritative while power quality is additive evidence.

## Cleanup

- Models were unloaded from both Workers after each required strategy and at final cleanup.
- Final `clusterctl status` reported both SSH/project/API paths healthy and model `-`.
- Worker APIs remain running on TCP 8000 under repository lifecycle control.
- TCP 50052 and 18080 were absent on both accepted Workers.
- Dashboard bootstrap reported no active experiment, active action, or queued/running action.
- Model binaries and durable result artifacts were preserved.
- No Worker was rebooted, reflashed, power-cycled, or reconfigured.
- No native RPC process was started.
- The legacy Jetson workspace was not modified or deleted.

## Defects and correction commits

1. `23182d1a45f7340372295dc74dd6914a537d5a44` — `fix(results): keep response inspection scoped to selected run`
   - clears stale response inspection when changing result experiment
   - separates node-sweep responses by scenario and logical request
   - adds deterministic Dashboard regression fixtures and cache-version updates

2. `9e4805f9d22f45a2b748867faacd8c0b967f75cc` — `fix(failures): classify model loading 404 consistently`
   - maps a Worker model-loading HTTP 404 to `MODEL_MISSING` before the generic `OSError`/offline branch
   - adds a deterministic structured-failure regression test

Both corrections were made on the Mac source-of-truth branch, passed focused and full tests, and were pushed to `origin/codex/mac-control-plane`. The first correction is Controller-only UI code; the second is Controller benchmark error normalization. Neither required direct Worker editing or Worker redeployment.

Tests and gates:

- focused Dashboard syntax/export fixtures: pass
- focused result/failure tests: 7/7 pass
- live browser regression for filter clearing and scenario grouping: pass
- full Mac regression after hardware and both corrections: 279/279 pass
- `git diff --check`: pass

## Readiness for RPC

**READY for FOLLOWUP 07 prerequisites, but RPC was not started in this phase.**

The accepted pair is online, source/model parity is verified, both platform backends are ready, logical/physical replicated semantics are proven, per-Worker result and power-quality evidence is durable, and cleanup is clean. Native RPC remains experimental, unauthenticated on the private LAN, and requires its own explicit FOLLOWUP 07 execution and cleanup acceptance.

Remaining risks:

- This is a short functional smoke, not a sustained thermal or saturation result.
- Mixed tests used a common zero-GPU-layer configuration, so they do not measure Jetson GPU-offload advantage.
- The Worker sync payload included harmless macOS `.DS_Store` metadata; excluding it is a deployment-hygiene improvement, not an acceptance blocker.
- `pi-worker-01` remains outside this acceptance because it was unstable in FOLLOWUP 05; `pi-worker-02` is the accepted Raspberry Pi participant.

FOLLOWUP 06 is complete. FOLLOWUP 07 has not been started.
