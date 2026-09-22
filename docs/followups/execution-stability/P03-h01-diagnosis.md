# P03 — H01 Pi-only Pilot Failure Diagnosis

## Status and scope

**COMPLETE (offline evidence analysis only).**

P03 was performed on branch `codex/current-source-pilot-v6` from baseline commit
`79fb0a9bb7cb21a803878e6fb7d2532544a8972e`. The branch and its upstream were
identical and the working tree was clean before the phase began.

This phase only read the existing H01/v7 plan, durable pilot state, and local
result artifacts. It did not reconnect to a Worker, rerun an experiment, download
or load a model, perform inference or RPC, change an inventory, or edit a research
lock. Product code was not changed. Prompt and response bodies were not used or
copied into this report.

## Evidence set

The checked-in plan is `config/research/pilot_plan.v7_pi_only.json`. The durable,
ignored runtime evidence is under
`.run/controller/pilots/formal-study-v1-pi-only-revalidation-v7/`.

The following evidence was present locally:

- `manifest.json` and `analysis.json`;
- per-run `config.json`, `summary.json`, `responses.jsonl`, `requests.csv`,
  `events.jsonl`, and `measurements.jsonl` for the 19 completed observations and
  the cleanup-failed attempt;
- `config.json` and `events.jsonl` for interrupted order 15;
- the 19-column `requests.csv` compatibility contract for completed runs;
- the checked-in H01 report and current pilot runner reconciliation code.

The following evidence was absent or could not be durably linked:

- order 8 has no `run_id`, summary, request ledger, or pilot-specific log linked
  from the manifest;
- a partial orphan result directory exists, but the manifest supplies no identity
  that proves it belongs to order 8, so it was not used to assign a cause;
- no Controller or Worker log file could be established as specific to these v7
  attempts;
- the stored measurement stream contains temperature, frequency, network, and
  power observations, but no process or system memory series;
- failed timeout responses contain no Worker-side lock wait, TTFT, model hash, or
  effective request values because no Worker response arrived.

The manifest records 19 declared observations and 22 attempts: 19 completed, one
failed during cleanup, and two interrupted. This report preserves the official
attempt failure rate of `3 / 22 = 13.64%`; it does not remove operator interruption
or replace failed observations with successful retries. It also keeps v7 separate
from v5 and v6.

## Fixed execution identity and parameters

All completed failing runs used the planned Qwen 2.5 1.5B Instruct Q4_K_M model.
The newer artifacts verify SHA-256
`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`.
The requested context was 4096, output limit 128 tokens, request concurrency 4,
and timeout 600 seconds. Loaded configurations report context 4096, batch 256,
four CPU threads, zero GPU layers, OpenBLAS, and no automatic context or GPU-layer
adjustment. Model loading succeeded before each completed run discussed below.

The evidence therefore does not show a model identity, context, or load-parameter
mismatch. No `MODEL_LOAD` or OOM failure was recorded. The missing memory series
means an unreported resource exhaustion cannot be disproved, but it cannot be
claimed from the available data either.

## Attempt and request accounting

“Completed run” and “all requests succeeded” are separate states. Five completed
runs fell below the planned `0.95` request-success threshold. The cleanup-failed
attempt also produced a completed run summary with low request success before its
cleanup failed.

| Order | Cell / repeat | Attempt state | Run ID | Physical request result | Direct classification |
| ---: | --- | --- | --- | --- | --- |
| 8 | round robin / 2 | interrupted | none | unavailable | orphaned durable attempt; cause unknown |
| 9 | single / 2 | completed | `20260830_084554_527200` | 16/20, `0.80` | four connection resets |
| 10 | broadcast / 2 | failed | `20260830_090830_5b4c84` | 17/60, `0.283333` | four timeouts, 39 network-unreachable failures, cleanup failure |
| 14 | single / 4 | completed | `20260830_122435_f40c25` | 12/20, `0.60` | eight request timeouts |
| 15 | broadcast / 4 | interrupted | `20260920_140922_82992e` | measurement not started | operator interruption during warmup |
| 17 | broadcast / 5 | completed | `20260920_151854_6d91dd` | 51/60, `0.85` | nine request timeouts |
| 18 | single / 5 | completed | `20260920_154058_fdfdbe` | 16/20, `0.80` | first four requests timed out |
| 19 | round robin / 5 | completed | `20260920_160906_13ef68` | 16/20, `0.80` | first four requests timed out across two Workers |

The durable UTC intervals provide the time correlation used below:

| Order | Durable UTC interval or failure start |
| ---: | --- |
| 8 | attempt start `2026-08-28T06:04:04Z`; reconciled `2026-08-29T23:40:54Z` |
| 9 | run `2026-08-29T23:45:54Z`–`2026-08-30T00:03:29Z`; failed requests began `23:55:27Z` |
| 10 | run `2026-08-30T00:08:30Z`–`00:21:33Z`; first timed-out request began at `00:08:46Z`, three more at `00:11:17Z`; network-unreachable burst at `00:21:30Z` |
| 14 | run `2026-08-30T03:24:35Z`–`03:53:57Z`; timeout groups began `03:28:59Z`, `03:31:26Z`, and `03:41:26Z` |
| 15 | attempt `2026-09-20T05:09:22Z`–`05:57:24Z`; stored run events stop at `05:09:30Z` during warmup |
| 17 | run `2026-09-20T06:18:54Z`–`06:35:22Z`; all nine failed requests began near `06:25:18Z` |
| 18 | run `2026-09-20T06:40:58Z`–`07:03:46Z`; all four failed requests began near `06:41:15Z` |
| 19 | run `2026-09-20T07:09:06Z`–`07:25:21Z`; all four failed requests began near `07:09:22Z` |

Orders 20–22 are distinct successful retry observations. They do not rewrite or
cancel the earlier attempt and request failures. Broadcast success rates above are
physical replica rates. They are not round-robin user throughput, and this report
makes no throughput comparison between those strategies.

## Failure traces

### Order 8 — interrupted attempt without a durable run link

The manifest records a running attempt that was later reconciled as
`PILOT_INTERRUPTED`, without a `run_id`. The pilot reconciliation function uses
that code when it finds an attempt still marked running after its owning process
is gone; the code does not identify why the process ended.

- **CONFIRMED:** execution ended while the durable attempt remained running.
- **INSUFFICIENT_EVIDENCE:** operator stop, Controller/Dashboard termination,
  process crash, Worker failure, and network failure cannot be distinguished.
- **Missing for diagnosis:** request/event ledger, Worker assignment, stage timing,
  power and thermal observations, cleanup result, and pilot-specific logs.

The unlinked partial directory is retained as evidence but is not assigned to the
attempt without a durable identifier.

### Order 9 — single-node connection reset

The run used Pi 02 and completed, but request IDs 14, 16, 17, and 18 returned four
`WORKER_OFFLINE` failures at the inference stage with `Connection reset by peer`.
Their E2E durations span about 66 to 207 seconds; two failures contain TTFT values,
showing that at least some connections reset after response progress had begun.
Controller executor queue waits were below one millisecond. Failed responses have
no Worker lock-wait value; successful requests in the run show lock waits up to
about 140 seconds.

The preflight, pre-measurement, and postflight power observations were historical
`0x50000`: previous undervoltage and throttling were latched, while every current
bit was false. The peak recorded temperature was about 60.4 °C. There is no
pilot-specific Worker log or crash record.

- **CONFIRMED:** connection/transport loss during inference caused four request
  failures.
- **SUPPORTED_HYPOTHESIS:** serialized inference contention explains long waits in
  successful requests and may have increased exposure to a connection failure.
- **INSUFFICIENT_EVIDENCE:** Worker process crash, transient network loss, power,
  or memory exhaustion as the underlying reset cause.

### Order 10 — cluster-wide network loss followed by cleanup failure

All three models loaded and requests initially succeeded. Three Pi 02 requests then
timed out near 601 seconds; another partially progressed request ended after about
756 seconds. Another 39 physical requests across Pi 02–04 failed
immediately with `Network is unreachable`. Telemetry failed in the same interval
with network/address/route errors, and postflight power reads were unavailable on
all three Workers. Cleanup then failed for all three with the same network-unreachable
condition, producing the attempt-level `CLEANUP_FAILED` result.

Before measurement, Pi 02 carried historical `0x50000` with no current bits;
Pi 03 and Pi 04 reported `0x0`. Peak recorded temperatures were approximately
59.5, 65.9, and 59.8 °C. The power probes do not cover the later network-loss
interval because postflight collection was unavailable.

- **CONFIRMED:** request timeouts preceded a Controller-to-Worker network outage;
  the outage affected inference, telemetry, and cleanup.
- **CONFIRMED:** cleanup could not be verified and correctly remained failed; the
  resources must not be treated as safely released from this artifact alone.
- **INSUFFICIENT_EVIDENCE:** the root of the route loss, including adapter, switch,
  host networking, Worker reboot/crash, or Controller network state.

### Order 14 — single-node timeout burst

Eight Pi 02 requests failed with `REQUEST_TIMEOUT` at the inference stage. Their
E2E values meet or exceed the configured 600-second timeout. Controller queue
waits remained below one millisecond. Failed responses have no Worker lock-wait
or TTFT value; successful responses show lock waits up to about 101 seconds.

Model load and the planned context were confirmed. Power probes carried the same
historical `0x50000` with all current bits false, and the peak temperature was
about 60.9 °C.

- **CONFIRMED:** eight inference requests exceeded the timeout.
- **SUPPORTED_HYPOTHESIS:** serialized Worker inference and a stuck or very slow
  request batch could create head-of-line delay.
- **INSUFFICIENT_EVIDENCE:** the failed requests' actual Worker lock time, an
  inference-runtime stall, memory pressure, transport stall, or power as the root
  cause.

### Order 15 — operator interruption during warmup

The event stream ends after model loading during warmup, before measurement or a
request ledger was written. The contemporaneous H01 operational record states
that the operator reported a suspected adapter issue and requested a pause. The
attempt remains `PILOT_INTERRUPTED`; its later retry is a separate observation.

- **CONFIRMED:** this was an operator interruption during warmup, not a completed
  run or a measured inference failure.
- **INSUFFICIENT_EVIDENCE:** whether an adapter fault was actually present at the
  interruption. A suspicion is not a measurement.

### Orders 17–19 — post-reboot timeout bursts

Order 17 timed out nine broadcast replicas at approximately 600 seconds across
all three Workers and logical requests 10–12. Order 18 timed out the first four
Pi 02 requests at approximately 600 seconds, after which 16 requests succeeded.
Order 19 timed out the first four requests, split evenly between Pi 02 and Pi 03,
after which 16 requests succeeded. All are recorded at the inference stage.

Controller queue waits remained below one millisecond. Failed responses contain
no Worker lock-wait or TTFT values. Successful-request lock waits reached roughly
129, 143, and 43 seconds in orders 17, 18, and 19 respectively. The model identity
and planned load parameters were verified.

All participating Workers reported `0x0` before measurement and after the run,
with no current or historical power bits and no sampled throttling. Recorded peak
temperatures were approximately 68.1 °C or lower. These discrete observations do
not prove that no transient occurred between samples, but they do not support an
active power fault. The simultaneous pattern on multiple Workers also does not
identify a single failed Worker.

- **CONFIRMED:** the request-success blockers are timeout bursts, not model-load,
  controller-queue, or recorded power-gate failures.
- **SUPPORTED_HYPOTHESIS:** a common request-lifecycle, transport, or concurrent
  batch coordination stall is more consistent with synchronized multi-Worker
  timeouts than an isolated Worker fault. Serialized inference can amplify the
  stall.
- **INSUFFICIENT_EVIDENCE:** the exact layer at which each timed-out request
  stopped, because failed responses have no Worker-side timing and no matching
  Controller/Worker logs are retained.

## Cross-cutting conclusions

| Candidate | Classification | Evidence boundary |
| --- | --- | --- |
| Connection/transport errors | **CONFIRMED** | Order 9 reset; order 10 network-unreachable errors across inference, telemetry, and cleanup |
| Request timeout | **CONFIRMED** | Orders 10, 14, and 17–19 contain `REQUEST_TIMEOUT` at inference |
| Cleanup failure | **CONFIRMED** | Order 10 has three cleanup errors after network loss |
| Operator interruption | **CONFIRMED** | Order 15 H01 operational record and warmup-only event stream |
| Controller executor queue saturation | **INSUFFICIENT_EVIDENCE**, and available evidence weighs against it | Failed request queue waits are sub-millisecond |
| Worker inference serialization contributing to delay | **SUPPORTED_HYPOTHESIS** | Large successful-request lock waits; failed-request lock waits unavailable |
| Adapter/power fault causing request failures | **INSUFFICIENT_EVIDENCE** | Earlier runs have historical-only bits; later timeout runs have clean sampled `0x0`; order 15 records suspicion only |
| Thermal throttling | **INSUFFICIENT_EVIDENCE** | No active thermal/throttle bit in available samples; moderate recorded peaks |
| Model load, OOM, or context mismatch | **INSUFFICIENT_EVIDENCE**, with no affirmative indicator | Loads and effective context succeeded; memory was not sampled |
| Root cause of order 8 interruption | **INSUFFICIENT_EVIDENCE** | No run link or dedicated logs |

The 13.64% attempt failure remains the formal pilot result. Separately, the five
completed runs below 0.95 and the request failures in order 10 remain request-level
quality blockers. Treating operator interruptions separately may help operations,
but it must not lower or replace the preregistered attempt-failure result.

## Minimal follow-up proposals

These are proposals for a separately approved implementation or new preregistered
pilot. No fix or rerun is part of P03.

1. Persist a bounded Controller request lifecycle trace and correlate it with a
   Worker request ID. Record accept time, inference-lock acquisition, first token,
   completion, connection close, and cancellation. Redact prompt and response
   text. Tests should inject timeout before and after lock acquisition and prove
   that the stage remains attributable.
2. Add periodic memory and process-liveness measurements to the existing telemetry
   stream. Tests should preserve missing values as missing and prevent a post-run
   sample from being applied retroactively.
3. Persist pilot-specific Controller and Worker log references, or an anonymized
   bounded diagnostic excerpt, with attempt/run correlation. Tests should reject
   logs whose run identity does not match.
4. Preserve cleanup uncertainty and quarantine after a transport outage until the
   existing reconciliation path proves ownership and process state. A regression
   should cover network loss between result write and cleanup.
5. If H03 is approved later, use a new pilot ID and declare whether timeout or
   concurrency changes are being tested. Do not edit v7, discard failed attempts,
   or pool v5/v6/v7. Capture continuous-enough power, network, memory, and
   Worker-side stage timing to distinguish the current hypotheses.

Changing the 600-second timeout now would alter the planned condition without
identifying the cause. A future plan may test a different timeout, but it must
state the reason and version before execution.

## Validation results

| Check | Result |
| --- | --- |
| Offline artifact reconciliation | **PASS** — 22 attempts; 19 completed, one failed, two interrupted; affected request counts and the 19-column CSV contract verified |
| `.venv/bin/python -m unittest -v cluster.tests.test_phase09_pilot` | **PASS — 21 tests** |
| `.venv/bin/python scripts/ci/validate_repository.py` | **PASS** — 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |

The first ad hoc reconciliation command referenced a nonexistent nested
`failure_policy` key and stopped with `KeyError`; it did not alter any file. The
corrected check used the stored top-level `failure_rate` and passed.

## Compatibility, hardware, and formal state

No runtime behavior or data contract changed. The existing logical/physical
request distinction, broadcast semantics, retry identity, 19-column CSV, response
privacy, result immutability, cleanup uncertainty, and formal thresholds are
preserved.

No hardware action was run in P03. H01 remains a Pi-only hardware record and does
not supply Jetson evidence. The analysis remains `freeze_ready=false`, and
`config/research/formal_experiment_matrix.json` remains
`formal_execution_allowed=false`. No relock or formal approval was performed.
