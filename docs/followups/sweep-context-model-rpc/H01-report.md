# H01 — Current-source Raspberry Pi pilot checkpoint

Date: 2026-09-20

Branch: `codex/current-source-pilot-v6`

Tested source baseline: `3f6b8431587f6231171270a4f25c11ffdff6852b`

## Outcome

H01 hardware work was explicitly authorized after R07. Only four Raspberry Pi
systems were connected, so the predeclared
`formal-study-v1-pi-only-revalidation-v7` plan was resumed. It is a separated
Pi-only pilot with `formal_pooling_allowed=false`; it does not replace the
missing Jetson part of v6.

The durable v7 manifest has no pending runs. It contains 22 attempts: 19
completed, one failed cleanup attempt, and two interrupted attempts. Each of
the three variance cells has five completed observations. The final analysis is
still `freeze_ready=false`, so no source/runtime relock was written and the
formal execution gate remains closed.

## Source, runtime, and model evidence

Pi 02–04 were updated to the tested source and restarted before the resumed
pilot. All three Worker health records reported a verified deployment, clean
source tree, and the following common identities:

| Identity | Verified value |
|---|---|
| Source commit | `3f6b8431587f6231171270a4f25c11ffdff6852b` |
| Source tree SHA-256 | `f0d4ac73e4a3d569586c2c812b11937df159c17315c799ef9947cc852b6b04d8` |
| Runtime fingerprint | `b4387053e655722a` |
| `llama-cpp-python` | `0.3.20` |
| Pinned RPC commit | `f49e9178767d557a522618b16ce8694f9ddac628` |
| Backend | Raspberry Pi 5 / OpenBLAS, verified |

The exact approved model was installed and verified on Pi 02–04 before
execution:

| Model identity | Verified value |
|---|---|
| Lock key | `qwen2.5-1.5b-instruct-q4-k-m-official` |
| Artifact | `qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| Size | `1117320736` bytes |
| SHA-256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| Source revision | `91cad51170dc346986eccefdc2dd33a9da36ead9` |
| Architecture / quantization | `qwen2` / `Q4_K_M` |
| Metadata contract | `gguf-metadata-v1`, inspected |

No model, runtime, result, credential, or inventory artifact is staged in Git.

## Operational record

The first Pi 04 model-install client connection remained in `SYN_SENT` and no
install POST reached the Worker. It was stopped without a remote partial file;
the immediate retry downloaded and verified the exact locked artifact.

During the first resumed order 15 warmup, the operator reported a suspected
power-adapter problem and requested a pause. The runner was stopped, Pi 03–04
were unloaded, and Pi 02 was unreachable. After the operator replaced the
adapter and rebooted the Raspberry Pis, Pi 02–04 Worker APIs were restarted and
the pilot resumed. The interrupted order 15 attempt remains immutable evidence
and its retry was recorded separately as order 22.

After reboot, Pi 02–04 repeatedly reported `vcgencmd get_throttled = 0x0`, with
no current or historical undervoltage, frequency cap, throttling, or soft
temperature limit. Load-time spot checks and final cleanup checks also reported
`0x0`. Some completed summaries contain
`PI_POWER_OBSERVATION_INCOMPLETE` because a postflight query was temporarily
unavailable; immediate post-cleanup health reads were available and clean. The
warning remains in the result and is not rewritten.

The repository had moved from `/Users/hajoonpark/Documents/자율설계` to
`/Users/hajoonpark/자율설계`. A repository symlink at the original path restores
the absolute paths stored by earlier durable observations. Result contents and
manifest observation identities were not rewritten.

## Final pilot analysis

| Item | Result |
|---|---|
| Declared observations | 19 |
| Recorded attempts / observations | 22 / 22 |
| Completed observations | 19 |
| Failed / interrupted attempts | 1 / 2 |
| Attempt failure rate | `0.13636363636363635` (limit `0.05`) |
| Pending runs | 0 |
| Selected minimum cooldown | 180 seconds |
| Maximum telemetry collection overhead | `0.02990843317914778` (limit `0.05`) |
| Selected formal repeats | 30 |
| Formal pooling allowed | `false` |
| Freeze ready | `false` |

| Variance cell | Completed | Median tokens/s | Throughput CV | Median TTFT | Median E2E |
|---|---:|---:|---:|---:|---:|
| Pi 02 single node | 5 | 2.389260 | 0.431171 | 69.949811 s | 184.189828 s |
| Pi 02–03 round robin | 5 | 5.847950 | 0.346374 | 38.243101 s | 80.983588 s |
| Pi 02–03–04 broadcast | 5 | 8.271826 | 0.117916 | 55.123639 s | 148.846496 s |

The final blockers are:

- the pilot attempt failure rate exceeds the predeclared maximum;
- broadcast repeat 5 has request success below 0.95;
- round-robin repeat 5 has request success below 0.95;
- single-node repeats 2, 4, and 5 have request success below 0.95.

Throughput precision also reaches the 30-repeat cap for the single-node and
round-robin cells, and broadcast TTFT reaches that cap. These observations do
not authorize changing the historical v5 decision or opening the formal gate.

## Gate and relock decision

H01 is **hardware executed, relock blocked**. The Pi-only scope cannot satisfy
the missing Jetson evidence, and the v7 failure and request-success policies do
not pass. The checked-in research locks, formal matrix, protocol, and freeze
decision remain unchanged. `CURRENT_SOURCE_PILOT_REVALIDATION` and
`RUNTIME_SOURCE_RELOCK` therefore remain applicable.

The next checkpoint must define a new preregistered recovery plan or restore
the required Jetson scope. It must not silently retry successful or
policy-failing observations, pool v6/v7 evidence, or edit the existing runtime
results.

## Safety and cleanup

The pilot used serialized execution and the existing durable manifest. It did
not start a formal campaign or native RPC session. Final cleanup verified that
the managed RPC Worker port 50052 and coordinator port 18080 were stopped on Pi
02–04, and the Workers were left with no model loaded.

## Verification

| Check | Actual result |
|---|---|
| Pi 02–04 `environment-check` | READY on all three; OpenBLAS backend verified, model count 1, pinned RPC runtime ready |
| Final v7 `analyze` | Exit 2 as expected for a blocked freeze; 22 observations, 19 completed, `freeze_ready=false` |
| Final Worker health | Pi 02–04 unloaded; power status `ok`, raw `0x0`, no reason codes |
| `rpc-cleanup-check` | PASS on Pi 02–04; ports 50052 and 18080 stopped |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | 702 tests, PASS, 141.117 s |
| `npm test` | PASS; syntax, Dashboard fixtures, publication PNG, Playwright 8/8 |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `bash -n` | 7 repository shell scripts, PASS |
| Offline wheel build and asset inspection | PASS via `pip wheel --no-deps --no-build-isolation`; SHA-256 `ba379e584920e73e3c0fbc4bb30f5c6d62dc68d264cd171a1f524a5097666c4a` |
| `git diff --check` | PASS |

The local `python -m build --wheel --no-isolation` frontend was unavailable.
The first invocation was also shadowed by the repository's generated `build/`
directory. The offline `pip wheel` fallback completed without dependency
installation and verified `sweeps.css`, the three Sweep JavaScript modules, and
the Dashboard template in the wheel.

## Git scope

Only H01 documentation and current status reconciliation are intended for this
checkpoint. Product code, research lock JSON, formal matrix, actual inventory,
runtime results, installed model files, and credentials are excluded.
