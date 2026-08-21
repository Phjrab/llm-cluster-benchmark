# FOLLOWUP 07 — Native RPC, Cleanup, Dashboard Restart, Durable Recovery Acceptance

FOLLOWUP 07 is complete. This phase exercised the pinned native llama.cpp RPC
runtime on two prepared inference Workers, verified automatic and explicit
Worker coordinator selection, persisted successful responses and topology,
tested cooperative cancellation and controlled failure cleanup, and restarted
only the Mac Dashboard while a durable RPC job remained live. No Controller was
used for inference, no Worker was rebooted, no global service or firewall rule
was created, and the legacy Jetson workspace was not modified or deleted.

## Participants/source revisions

- Controller: macOS, branch `codex/mac-control-plane`.
- Phase baseline: `207eed83815d9ee09768f68e826f69e091adff37`.
- Final implementation revision before this report: `80f380f`.
- `jetson-worker-01`: `192.168.0.26`, Jetson Orin Nano, CUDA Worker,
  project `/home/jetson_orin_nano/project/llm/llm-cluster-benchmark-worker`.
- `pi-worker-02`: `192.168.0.14`, Raspberry Pi 5, OpenBLAS Worker,
  project `/home/pi2/project/llm/local_llm_bench`.
- Both Worker deployment directories are rsync-managed source snapshots and do
  not contain their own Git metadata. RPC preparation synchronized the phase
  source before building the project-local native runtime. The subsequent
  correction commits change only Mac durable-job/Dashboard recovery code and
  did not require Worker source redeployment.
- Other registered Workers were not selected and were not changed.

## Security/acknowledgement

- `acknowledge_experimental_rpc=true` was explicit for every RPC run.
- Both participants use private RFC1918 addresses.
- Dashboard token auth and Worker API auth were OFF, as required by the current
  unauthenticated llama.cpp RPC contract.
- Native RPC listeners were ephemeral and existed only during each experiment.
- Transport was recorded as `TCP LAN` with
  `rpc_security=unauthenticated_ephemeral_private_lan`.
- No password, token, SSH private key, sudoers entry, global package install,
  firewall mutation, or automatic reboot was introduced.

## Runtime commit/build

- Pinned llama.cpp commit on both Workers:
  `f49e9178767d557a522618b16ce8694f9ddac628`.
- Jetson runtime: project-local native llama.cpp RPC build with CUDA.
- Raspberry Pi runtime: project-local native llama.cpp RPC build with
  OpenBLAS/CPU support.
- `cluster/rpc/runtime.sh check` passed on both Workers after preparation.
- Preparation was idempotent with respect to the project-local runtime; it did
  not reinstall already-ready Python Worker environments.

## Coordinator selection

- Automatic selection passed in run `20260821_155738_efb8fc`: the selected
  coordinator was `jetson-worker-01` and `pi-worker-02` was the remote RPC
  device.
- Explicit selection passed in run `20260821_163424_761948`:
  `rpc_coordinator_node=pi-worker-02`. The Jetson was a remote device and the Pi
  started its required loopback CPU device before serving as coordinator.
- The explicit Pi topology persisted resolved device order
  `[jetson-worker-01, pi-worker-02]` and endpoints
  `[192.168.0.26:50052, 127.0.0.1:50052]`.
- The Worker-only inventory contains no Controller row. Domain validation also
  rejected `rpc_coordinator_node=mac-controller` because a coordinator must be
  one of the selected inference Workers.

## Model identity

- Model ID: `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf`.
- SHA-256 on Jetson:
  `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`.
- SHA-256 on Pi:
  `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`.
- The coordinator model path remained below the validated project `models/`
  directory. No model was downloaded, copied, or modified during benchmark
  execution.

## Successful RPC run

Primary automatic-coordinator smoke:

- run ID: `20260821_155738_efb8fc`
- status: `completed`, 1/1 request successful
- coordinator: `jetson-worker-01`
- model load: 162.848598 seconds
- generated tokens: 16
- TTFT: 3.021643 seconds
- E2E: 7.729290 seconds
- generation rate: 3.398725 tokens/second
- cluster rate: 2.068097 tokens/second
- response: `Edge AI refers to the use of artificial intelligence (AI) and machine learning (`

Explicit Pi-coordinator smoke:

- run ID: `20260821_163424_761948`
- status: `completed`, 1/1 request successful
- coordinator: `pi-worker-02`
- model load: 92.192068 seconds
- generated tokens: 8
- TTFT: 1.706194 seconds
- E2E: 4.159885 seconds
- response: `Edge inference is a machine learning technique used`

Both runs persisted `model_placement=sharded`, exact participant roles, pinned
runtime commit, RPC endpoints/device order, request timing, output SHA-256, raw
response journal, and structured measurement quality.

**RPC Functional Acceptance: PASS**

## Power/measurement quality

- Raspberry Pi power integrity was sampled at preflight, pre-measurement,
  measurement, and postflight for successful runs.
- Every accepted Pi snapshot reported `raw_hex=0x0`, no active or historical
  undervoltage/frequency-cap/throttling/soft-temperature flags, and no unknown
  bits.
- Successful automatic, durable-recovery, and explicit-coordinator results all
  finalized with `measurement_quality=clean`.
- Power state did not alter participant order, coordinator selection, tensor
  split, or pass/fail admission. The non-blocking power-warning policy remained
  intact.

## Cancellation

- job ID: `job_20260821_163051_597f5e`
- run ID: `20260821_163052_a6d9c9`
- cancellation was requested through `POST /api/experiments/cancel` after both
  RPC roles had started and while the coordinator was loading the model.
- Durable state immediately recorded `cancel_requested=true` and
  `phase=cancelling`.
- Final job status: `cancelled`.
- Final suite status: `cancelled`.
- Completed inference requests: 0.
- The child performed cooperative RPC cleanup; both Workers subsequently had
  no listener on 50052 or 18080 and both Worker APIs remained healthy.

## Controlled failure/cleanup

- run ID: `20260821_163701_716e6e`
- controlled condition: safe, contained model ID
  `llama3.2-1b/missing-followup07.gguf` was absent on the explicit Jetson
  coordinator.
- The Pi RPC device was started before coordinator model preflight, exercising
  cleanup of an already-attempted remote device.
- Result status: `failed` with structured code `RPC_MODEL_LOAD_FAILED`, stage
  `rpc_model_preflight`, coordinator node, model ID, evidence, and solution.
- No inference request was sent.
- Cleanup succeeded and neither Worker retained a 50052/18080 listener.
- Cleanup failure was not hidden; no cleanup warning/error occurred in this
  controlled failure.

## Dashboard restart recovery

- durable job: `job_20260821_162700_346457`
- suite: `suite_20260821_162700_346457`
- run: `20260821_162700_135268`
- old Dashboard PID: 42186
- new Dashboard PID: 42683
- child process PID before and after restart: 42422
- state at restart: `running`, RPC coordinator/device started, native model load
  in progress
- post-restart Dashboard API: same job ID, same child PID, same run ID,
  `matching_count=1`, `nonterminal_count=1`
- final job/suite status: `completed` / `completed`
- final requests: 2/2 successful, 64 generated tokens
- persisted responses: 2
- final cluster rate: 3.739600 tokens/second
- the Dashboard restart did not spawn a duplicate child, interrupt inference,
  mutate the Worker topology, or lose durable progress.

The live test exposed macOS Framework Python identity and watcher handoff gaps.
After the correction commits below, the same restart procedure passed end to
end.

## Port/process cleanup

- Pre-run baseline: no listener on 50052 or 18080.
- Successful automatic RPC cleanup: both ports closed.
- Successful durable-recovery RPC cleanup: both ports closed.
- Successful explicit Pi-coordinator cleanup: both ports closed.
- Cancellation cleanup: both ports closed.
- Controlled failure cleanup: both ports closed.
- Final SSH checks on both Workers found no 50052/18080 listener; both Worker
  health endpoints were reachable.
- The stale same-project Dashboard PID 97595 on port 53640 was identified by
  exact executable, cwd, full argv, creation time, and user, then only that PID
  received TERM. No wildcard process cleanup was used.

## Durable artifacts

Each successful run contains private `config.json`, `events.jsonl`,
`requests.csv`, `responses.jsonl`, and `summary.json` artifacts. Cancellation
and pre-inference failure runs contain the applicable config/events/summary
subset without fabricating request rows. Dashboard APIs successfully re-read
run `20260821_162700_135268` and returned both persisted raw responses.

Relevant run IDs:

- `20260821_155738_efb8fc` — automatic Jetson coordinator success
- `20260821_162700_135268` — live Dashboard restart recovery success
- `20260821_163052_a6d9c9` — durable cancellation
- `20260821_163424_761948` — explicit Pi coordinator success
- `20260821_163701_716e6e` — controlled missing-model failure cleanup

## Defects/correction commits

All correction commits were kept separate from this report and pushed to
`origin/codex/mac-control-plane` immediately:

1. `7905973` — preserve fresh queued child handoff.
2. `a84d5eb` — cover the running identity handoff gap.
3. `a580277` — guard the pre-spawn registry handoff.
4. `ac5e1cf` — retry transient process inspection.
5. `96fa3aa` — enforce a single durable job watcher per Dashboard process and
   stop it on shutdown/reload.
6. `80f380f` — recognize the exact macOS Python.framework child re-exec identity
   without weakening PID/cwd/argv/creation-time/user checks.

Post-correction gates:

- full Python regression: 285/285 passed
- focused RPC/durable/process/results/power/Dashboard regression: 113/113 passed
- durable jobs focused regression: 18/18 passed
- Dashboard JavaScript syntax: passed
- Dashboard export fixtures: passed
- Python compileall: passed
- all tracked shell scripts `bash -n`: passed
- clusterctl and benchmark runner CLI help: passed
- all FOLLOWUP 07 runtime JSON files: valid
- `git diff --check`: passed

The only emitted test warning was the already-known Starlette/httpx TestClient
deprecation warning; it did not fail a test or affect hardware acceptance.

## Legacy deletion gate readiness

- Pi acceptance: PASS (`FOLLOWUP 05`, stable `pi-worker-02`).
- Multi-Worker acceptance: PASS (`FOLLOWUP 06`, Jetson + Pi replicated,
  broadcast, cumulative, and individual sweep).
- RPC acceptance: PASS (this phase).
- Durable recovery: PASS (this phase).
- Phase 01 recovery archive: 159/159 SHA-256 entries passed again; the complete
  three-ref Git bundle passed `git bundle verify` again.
- Phase 15 pre-deployment archive: 82/82 SHA-256 entries passed again.
- Exact legacy workspace was not changed or deleted.

**Legacy cleanup gate: READY_FOR_EXPLICIT_USER_APPROVAL**

This status is readiness only. No deletion is authorized or performed. A later
destructive action still requires re-confirming the exact legacy path and a new
explicit user approval.

## Remaining risks

- llama.cpp RPC remains proof-of-concept, unauthenticated TCP and private-LAN
  only. It must not be exposed to an untrusted network.
- The smoke runs are functional acceptance, not sustained thermal, saturation,
  or statistical performance studies.
- RPC model loading dominated each short run; throughput values must not be
  interpreted as long-duration scaling conclusions.
- Power integrity was clean for these observations, but future warnings remain
  non-blocking quality evidence rather than automatic hardware diagnosis.
- Worker source snapshots are rsync-managed without per-Worker Git metadata;
  the Controller branch and pinned native runtime/model hashes are the durable
  reproducibility anchors.

FOLLOWUP 07 is complete. No later phase was started.
