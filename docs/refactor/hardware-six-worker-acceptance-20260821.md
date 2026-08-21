# Six-Worker Hardware Acceptance — Jetson 3 + Raspberry Pi 3

Date: 2026-08-21  
Branch: `codex/mac-control-plane`  
Status: **COMPLETE_WITH_MEASUREMENT_WARNINGS**

This acceptance used three Jetson and three Raspberry Pi Workers. The unstable
`pi-worker-01` remained registered but disabled and was not contacted by any
benchmark. Platform-separated runs are the primary results. Mixed Jetson/Pi
runs are retained only as interoperability and experimental RPC evidence, not
as the default comparison workflow.

## Inventory and isolation

Enabled Jetson group:

1. `jetson-worker-01` — `192.168.0.26`, Orin Nano Super, CUDA, `MAXN_SUPER`
2. `jetson-worker-02` — `192.168.0.19`, Orin Nano Super, CUDA, `MAXN_SUPER`
3. `jetson-worker-03` — `192.168.0.6`, Orin Nano Super, CUDA, `15W`

Enabled Raspberry Pi group:

1. `pi-worker-02` — `192.168.0.14`, Raspberry Pi 5, OpenBLAS
2. `pi-worker-03` — `192.168.0.9`, Raspberry Pi 5, OpenBLAS
3. `pi-worker-04` — `192.168.0.5`, Raspberry Pi 5, OpenBLAS

The pre-change runtime inventory was preserved as
`.run/cluster/nodes.local.pre-hardware-acceptance-20260821.csv`. Only the
`pi-worker-01` enabled flag was changed to false. No Worker was deleted,
rebooted, reflashed, or power-cycled.

All six selected Workers passed SSH, project, API, system-package, project
virtual-environment, pinned Python dependency, native inference backend, model,
and RPC readiness checks:

- Jetson: Ubuntu 22.04.5, Python 3.10.12, llama-cpp-python 0.3.20,
  CUDA 12.6 / SM 8.7, jtop service/client 7.2.1
- Raspberry Pi: Ubuntu 24.04.4, Python 3.12.3,
  llama-cpp-python 0.3.20, OpenBLAS

Execution-critical source parity after deployment:

- `cluster/clusterctl.py` SHA-256:
  `e8d4b30eed8354bf88a60e457a6b6a7d1d41d4d438421c67731d1418adefe9c0`
- `cluster/rpc/runtime.sh` SHA-256:
  `34cdfee764ec1bd5aa48c7dd39f861c014058ebfed461bca988541288d5bf68f`

Both hashes matched on the Mac and all six Workers.

## Model parity

All six Workers independently verified the same selected model:

- ID: `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf`
- size: 807,694,464 bytes
- SHA-256:
  `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
- checksum status: valid on every Worker

The missing copies were synchronized before timing. No model transfer or
download occurred inside a benchmark run.

## Node configuration now includes RPC

Worker `environment-install` and `prepare` now include the pinned native RPC
runtime. The operation first runs `runtime.sh check`; an exact ready build is
reused, and compilation runs only when binaries are missing, invalid, or at the
wrong commit. Native artifacts remain under the Worker project's
`.run/cluster`; they are not installed globally.

- pinned llama.cpp commit on all six Workers:
  `f49e9178767d557a522618b16ce8694f9ddac628`
- existing builds reused: `jetson-worker-01`, `pi-worker-02`
- first project-local builds completed: `jetson-worker-02`,
  `jetson-worker-03`, `pi-worker-03`, `pi-worker-04`
- post-build `environment-check`: six of six `READY`
- explicit idempotency smoke on `pi-worker-03`: existing build verified and
  reused in 2.12 seconds without recompilation

Implementation commit `2b653d4` was fully tested and pushed before hardware
acceptance continued.

## Default platform-separated workflow

The Dashboard now defaults to the enabled Jetson group. Selecting the Jetson or
Raspberry Pi topology tab selects only enabled Workers from that platform.
The all-Workers view preserves the current group and permits an explicit mixed
selection, which is labelled exploratory. Commit `6603338` contains this
behavior and its deterministic JavaScript regression fixture.

## Common workload

The platform-separated functional runs used the same 1B Q4_K_M model, context
512, temperature 0, top-p 0.9, seed 42, one warmup request, and 8 generated
tokens. Jetson runs requested 30 GPU layers; Pi runs requested zero GPU layers.
Warmup was excluded from measured request rows.

These short runs prove orchestration, assignment, response persistence, and
cleanup. They are not sustained thermal or statistically stable performance
studies.

## Jetson-only results

### Replicated round-robin

- run: `20260821_183115_052085`
- status: completed, 6/6 requests successful
- assignment: two requests per Jetson
- generated tokens: 48
- cluster throughput: 65.734860 tok/s
- TTFT p50: 0.134666 s
- E2E p95: 0.366320 s

### Broadcast comparison

- run: `20260821_183129_a2ec4b`
- status: completed
- logical/physical/successful: 2 / 6 / 6
- all-replica success: 100%
- exact response agreement: 100%
- duplicate physical aggregate: 36.577183 tok/s
- TTFT p50 / E2E p95: 0.083648 / 0.824827 s

The aggregate is duplicate replica work, not user-answer throughput.

### Cumulative sweep

- run: `20260821_183146_2fbdde`
- status: completed, 9/9 requests successful
- one Jetson: 26.389768 tok/s
- two Jetsons: 59.791155 tok/s, speedup 2.265695, efficiency 1.132847
- three Jetsons: 50.907906 tok/s, speedup 1.929077, efficiency 0.643026

The two-node super-linear value and lower three-node value are short-smoke
observations, not scaling conclusions. In addition, `jetson-worker-03` remained
at 15W while the other two used MAXN_SUPER. Passwordless sudo was unavailable,
so the Controller correctly did not change it. A future power-uniform study
must first run the following command directly on Jetson 3 and verify the result:

```bash
sudo /usr/sbin/nvpmodel -m 2
```

No automatic reboot is performed.

## Raspberry-Pi-only results

### Replicated round-robin

- run: `20260821_183201_a41323`
- status: completed, 6/6 requests successful
- assignment: two requests per Pi
- generated tokens: 48
- cluster throughput: 31.541329 tok/s
- TTFT p50: 0.163504 s
- E2E p95: 0.813944 s

### Broadcast comparison

- run: `20260821_183217_1d8415`
- status: completed
- logical/physical/successful: 2 / 6 / 6
- all-replica success: 100%
- exact response agreement: 100%
- duplicate physical aggregate: 33.109016 tok/s
- TTFT p50 / E2E p95: 0.142620 / 0.724213 s

### Cumulative sweep

- run: `20260821_183232_74239d`
- status: completed, 9/9 requests successful
- one Pi: 11.055747 tok/s
- two Pis: 16.275393 tok/s, speedup 1.472121, efficiency 0.736060
- three Pis: 25.825911 tok/s, speedup 2.335972, efficiency 0.778657

## Power-integrity evidence

`pi-worker-02` and `pi-worker-03` reported historical undervoltage and
throttling bits (`0x50000`) during the acceptance sequence. Current
undervoltage, frequency-cap, throttling, and soft-temperature flags were false
at recorded run boundaries. `pi-worker-04` remained clean (`0x0`).

The system correctly retained these as non-blocking measurement-quality
warnings. Functional run status remained completed, but affected throughput
must not be treated as clean publication evidence until the power supply/cable
condition is corrected and the historical state is cleared by a controlled
reboot. The warning was not silently discarded even though functional testing
continued as requested.

## Mixed interoperability evidence

The mixed runs are deliberately separated from primary platform results.

- six-node individual sweep: `20260821_175621_87fc25`, 12/12 successful
- six-node round-robin: `20260821_175649_cfdfad`, 12/12 successful
- six-node broadcast: `20260821_175710_92e370`, 12/12 successful,
  exact agreement 100%
- corrected six-node cumulative sweep: `20260821_182911_694807`,
  36/36 successful; every selected node received measured work in the six-node
  scenario

These runs establish interoperability only. The earlier three-request
cumulative run `20260821_175724_ea7203` is retained but superseded because it
could not assign a request to every Worker in the larger scenarios.

## Six-node native RPC smoke

- run: `20260821_182318_f8339a`
- placement: one sharded model across all six Workers
- coordinator: `jetson-worker-01`
- RPC devices: Jetson 2, Jetson 3, Pi 2, Pi 3, Pi 4
- pinned runtime commit: `f49e9178767d557a522618b16ce8694f9ddac628`
- status: completed, 1/1 request successful
- model load: 194.561556 s
- generated tokens: 8
- TTFT / E2E: 5.055588 / 11.337352 s
- generation rate: 1.273528 tok/s
- whole-request cluster rate: 0.705081 tok/s
- persisted response: `Edge inference allows for the efficient and secure`
- output SHA-256:
  `cb83ab8a1fbb9adccc6d9ce792a0c8c0b965c18680bff3c798ad9d7dcdc6d845`

All five device listeners used ephemeral TCP 50052 and the coordinator used
18080. Before and after the run, all six Workers had neither port open. This is
an experimental unauthenticated private-LAN path; it is not the default
platform benchmark and its low throughput is a valid network/distribution cost
observation.

## Artifacts and cleanup

The RPC result directory contains `config.json`, `events.jsonl`,
`requests.csv`, `responses.jsonl`, and `summary.json`. The directory is 0700
and every file is 0600. Its request CSV has the header plus one measured row,
and the response journal has one row.

Final cleanup state:

- all six Worker APIs online
- all six model managers unloaded
- no TCP 50052 or 18080 listener on any selected Worker
- model files and durable results preserved
- `pi-worker-01` still disabled and untouched
- no global RPC installation, systemd service, sudoers rule, or auto-start
  introduced

## Automated gates and Git checkpoints

- focused RPC/environment tests: 10/10 passed
- full Python regression: 294/294 passed
- Dashboard JavaScript syntax: passed
- Dashboard export/selection fixture: passed
- Python compile checks: passed
- `git diff --check`: passed

Pushed checkpoints:

1. `2b653d4` — `feat(onboarding): prepare RPC runtime with worker environment`
2. `6603338` — `feat(dashboard): default experiments to platform groups`

Functional acceptance is complete. Publication-quality performance work still
requires uniform Jetson power modes, clean Raspberry Pi power-integrity state,
longer workloads, repetitions, cooldown control, and confidence intervals.
