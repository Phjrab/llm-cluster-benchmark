# Roadmap Phase 09 — Pilot Experiment

## 1. Outcome

Phase 09 was resumed on 2026-08-24 and continued on 2026-08-25 on branch
`codex/roadmap-phase-09-complete`, but remains **incomplete**. The earlier v3
stop boundary and artifacts remain preserved; current observations are written
only to the separated v5 pilot identity.

The pilot did not freeze the formal repeat count, run duration, cooldown, or
campaign matrix. Phase 10 formal execution remains blocked. No pilot request
or failed attempt was admitted to a formal result pool.

The historical durable v3 pilot contains 7 of 28 declared attempts:

- 6 completed runs with 100% request success;
- 1 user-interrupted Raspberry Pi run, preserved as
  `USER_CANCELLED_PHASE09`;
- 21 unstarted declared runs;
- `freeze_ready=false`.

The running Pi model was unloaded successfully after cancellation. No Worker
or RPC process was left by the stopped run. The current v5 pilot contains 8 of
28 completed observations, 20 pending observations, and no failures. All eight
completed runs also unloaded their model successfully.

## 2. Preregistered design

The pilot uses a separate experiment identity and result root. Its observations
cannot be pooled with a formal campaign and cannot be selectively deleted.

The declared workload per run is:

| Parameter | Value |
|---|---:|
| Requests | 20 |
| Logical concurrency | 4 |
| Maximum generated tokens | 128 |
| Warmup requests per Worker | 1 |
| Context | 4096 |
| Temperature | 0.0 |
| Top-p | 0.9 |
| Seed | 42 |
| Request timeout | 600 seconds |

The 28-run plan consists of 8 cooldown-calibration runs and 20 independent
variance runs. The variance cells cover one Jetson, one Pi, a two-Pi
round-robin cluster, and a three-Pi broadcast cluster. Every cell is an exact
subset of the frozen formal matrix and uses the approved Qwen2.5 1.5B Q4_K_M
artifact and locked Korean prompt.

Run-level coefficient of variation is the independent precision unit. Request
records remain nested descriptive observations and may not be used as
pseudoreplicates. The repeat decision is the smallest integer in 10–30 that
meets every declared 95% CI relative half-width target. Precision still unmet
at 30 must be reported rather than hidden.

## 3. Preserved remediation history

Three pilot identities were retained instead of overwriting failed evidence.

### v1 — stale Worker deployment

`formal-study-v1-phase09-pilot-v1` ran one Jetson attempt on stale Worker source
`554c3faf…`. The Worker produced 4 successful and 16 failed requests, terminated
with a CUDA error, and could not unload. The attempt is preserved as
`CLEANUP_FAILED`.

The Controller deployment flow also found and fixed two source-selection
defects before redeployment:

- local `node_modules` symlinks incorrectly blocked source manifests;
- generated `.artifacts` data incorrectly entered Worker deployments.

All six Workers were then source-verified on one commit without reinstalling
their Python environments or models.

### v2 — llama context race reproduced

`formal-study-v1-phase09-pilot-v2` reproduced the CUDA termination on a fully
verified source cohort. Its one Jetson attempt again completed only 4 of 20
requests and ended `CLEANUP_FAILED`.

Separated smoke runs showed that short runs could pass while longer concurrent
stream workloads terminated unpredictably. The Worker backend allowed llama
tokenizer access outside generation serialization, and the streamed generator
used a thread-owned `RLock`. Distinct StreamingResponse requests can reuse the
same AnyIO thread, so that lock can be re-entered by a different request.

### v3 — streaming-safe context serialization

The Worker now uses one non-reentrant `Lock` for every load, unload, tokenize,
and generation operation on a llama context. This also permits a streamed
generator to resume on a different worker thread without an owner-release
violation.

Before v3 pilot execution, an isolated 20-request smoke passed 20/20 requests
and model cleanup. The v3 pilot then completed six consecutive full runs with
no CUDA termination or cleanup failure.

## 4. Live v3 evidence

Completed calibration runs:

| Order | Cohort | Cooldown | Requests | Wall time | Worker telemetry overhead |
|---:|---|---:|---:|---:|---:|
| 1 | Jetson 02 | baseline | 20/20 | 163.109 s | 3.23% |
| 2 | Jetson 02 | 3 s | 20/20 | 164.041 s | 3.62% |
| 3 | Jetson 02 | 15 s | 20/20 | 167.469 s | 3.51% |
| 4 | Jetson 02 | 30 s | 20/20 | 169.407 s | 3.69% |
| 5 | Pi 02 | baseline | 20/20 | 986.242 s | 12.39% |
| 6 | Pi 02 | 3 s | 20/20 | 986.588 s | 12.21% |

Order 7, the Pi 15-second condition, was interrupted by user request and is not
treated as a successful run. Its partial requests and telemetry remain in the
separate pilot result directory.

The successful-run median measurement time is 168.438 seconds because four of
the six completed runs are Jetson runs. The two completed Pi runs each took
about 16.44 minutes. Those platform-specific durations must not be replaced by
the pooled median when planning the formal campaign.

## 5. Thermal and instrumentation decisions

No cooldown was frozen.

- Jetson 3, 15, and 30-second candidates all met the declared start-temperature
  recovery and peak-temperature conditions in the observed runs.
- Pi 3 seconds failed recovery: baseline start was 53.73 °C and the next run
  started at 57.30 °C, above the +2 °C tolerance.
- Pi 15 seconds was interrupted and Pi 30 seconds was never measured.
- Unmeasured candidates are explicitly false, never implicitly accepted.

The Worker-internal telemetry collection fraction is the perturbation gate.
Controller HTTP probe elapsed time is retained only as a descriptive contention
indicator because it includes time waiting behind inference.

- Jetson maximum observed Worker collection overhead: 3.69%, under the 5% cap.
- Pi maximum observed Worker collection overhead: 12.39%, over the 5% cap.

Therefore instrumentation has a live Raspberry Pi blocker. The formal protocol
must not be frozen until Pi telemetry sampling is made cheaper, sampled less
frequently under a preregistered rule, or the overhead policy is revised with a
new justified pilot before formal data collection.

## 6. Precision and failure decisions

No variance cell reached the required five successful independent pilot runs.
The analyzer conservatively reports a provisional 30-repeat cap with every
variance metric precision-limited; this is an incomplete-data sentinel, not a
valid final repeat recommendation.

Across v3 attempts, the user-interrupted run yields a 1/7 attempt failure rate
(14.29%), above the 5% pilot target. Completed runs had a 100% request success
rate, above the 95% per-run floor. User cancellation is preserved and counted;
it is not silently removed to improve the rate.

## 7. Checkpoints

| Checkpoint | Commit |
|---|---|
| Phase 09 pilot preregistration and durable analyzer | `6a6de94` |
| Exclude frontend dependencies from Worker deployment | `5039ef5` |
| Exclude generated test artifacts from Worker deployment | `571bfc9` |
| Preserve v1 and preregister verified-deployment v2 | `5ab4748` |
| Define Worker overhead and failure quality gates | `7570367` |
| Serialize tokenizer and generation access; preregister v3 | `cd978f6` |
| Use a non-reentrant streaming-safe llama context lock | `90082e7` |

Each checkpoint was pushed immediately to
`origin/codex/roadmap-phase-09`.

## 8. Test gates

Focused gates passed for:

- v1, v2, and v3 preregistration and matrix-subset validation;
- pilot identity separation from formal campaigns;
- deterministic 8-run calibration and 20-run variance ordering;
- run-level precision and high-variance cap behavior;
- request and attempt failure gates;
- Worker-internal versus Controller-wait telemetry overhead;
- unmeasured cooldown rejection;
- non-overlap of tokenizer and generation;
- non-reentrant llama streaming lock;
- deterministic deployment exclusion of `node_modules` and `.artifacts`.

The 13 focused Phase 09 tests, Python compile check, Dashboard JavaScript
syntax/export fixtures, repository validator (14 JSON documents, 72 formal
cells, 13 pinned actions, and 7 shell scripts), and whitespace gate passed.
The repository-wide local regression was stopped with the long-running pilot
validation at user request and is not represented as passed. The pushed commit's
hosted CI result is the authoritative full-regression checkpoint. Live hardware
evidence is described above and is not represented as a completed 28-run pilot.

## 9. Historical stop boundary and resumption

Phase 09 originally stopped here by explicit user request. It resumed under a
new pilot identity after addressing the instrumentation blocker described
below. The v3 manifest and its interruption remain unchanged.

The following remain blocked:

- final repeat count;
- formal run-duration budget;
- Raspberry Pi cooldown minimum;
- Pi telemetry overhead acceptance;
- matrix/protocol/analysis-plan freeze;
- Phase 10 formal campaign execution.

Starting Phase 10 implementation preparation is possible, but collecting
formal results is not scientifically valid until `freeze_ready=true`.

## 10. v4 resumption checkpoint

### 10.1 Instrumentation remediation

The v4 pilot, `formal-study-v1-phase09-pilot-v4`, supersedes v3 for the
declared reason `TELEMETRY_INTRUSION`. It retains the same 28-run matrix,
approved model, prompt, workload, and statistical policy. It changes only the
preregistered Worker telemetry collection interval:

- Jetson Workers: 1 second;
- Raspberry Pi Workers: 10 seconds.

Repeated Controller reads of one cached Worker sample are deduplicated by the
Worker `sampled_at` value. The analyzer verifies the observed interval against
the platform policy and fails closed on a mismatch. v4 writes to its own
result root and cannot be pooled with v1-v3 or a formal campaign.

### 10.2 Live v4 evidence

Five calibration observations have completed:

| Order | Cohort | Cooldown | Wall time | TPS | TTFT p50 | Start / peak temp | Interval | Worker overhead |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Jetson 02 | baseline | 163.690 s | 15.639 | 16.748 s | 39.41 / 46.84 °C | 1 s | 3.29% |
| 2 | Jetson 02 | 3 s | 161.862 s | 15.816 | 16.475 s | 41.50 / 48.31 °C | 1 s | 3.75% |
| 3 | Jetson 02 | 15 s | 165.689 s | 15.451 | 17.665 s | 46.69 / 50.00 °C | 1 s | 3.70% |
| 4 | Jetson 02 | 30 s | 169.527 s | 15.101 | 16.924 s | 47.09 / 50.25 °C | 1 s | 3.77% |
| 5 | Pi 02 | baseline | 984.328 s | 2.601 | 96.200 s | 55.65 / 61.15 °C | 10 s | 2.94% |

Every run completed 20/20 requests. Pi 02 reported no current undervoltage,
frequency cap, throttling, or thermal limit before, during, or after the run.
Its historical undervoltage/throttling bits remain recorded as a non-blocking
measurement-quality warning, per the frozen power-integrity policy.

The instrumentation remediation passes the 5% perturbation cap on both
platforms: the maximum v4 value so far is 3.77% on Jetson, while the Pi baseline
is 2.94%. This removes the v3 Pi instrumentation blocker.

### 10.3 Remaining blockers

The v4 analyzer still reports `freeze_ready=false`:

- only 5 of 28 observations are complete;
- Pi 3, 15, and 30-second cooldown candidates are not yet measured;
- none of the four variance cells has the required five independent runs;
- Jetson 3, 15, and 30-second candidates did not recover to the v4 baseline
  start-temperature condition, so no global cooldown is currently selectable;
- repeat count and formal wall-time budget therefore remain provisional.

The provisional 30 repeats emitted with incomplete variance cells is an
explicit sentinel, not a frozen formal recommendation. Phase 10 execution
remains blocked until a superseding complete pilot returns
`freeze_ready=true`.

After this checkpoint, v4 was closed as insufficient for thermal-policy
selection rather than spending the remaining 23 runs on a pilot that could no
longer pass its freeze gate. The five completed observations remain immutable
evidence; no pending observation was represented as attempted or deleted.

### 10.4 v5 thermal-range remediation

The next separated pilot identity is
`formal-study-v1-phase09-pilot-v5`. It supersedes v4 for the declared reason
`THERMAL_RECOVERY_RANGE` and expands only the cooldown candidates from
3/15/30 seconds to 60/180/300 seconds. The conservative variance-stage
fallback is 300 seconds until the completed calibration selects the smallest
candidate that passes both platform cohorts.

Model, prompt, workload, precision, failure, power-integrity, and low-intrusion
telemetry policies are byte-for-byte unchanged in meaning. v5 has its own
manifest and result directory, and v1-v4 observations remain excluded from v5
precision estimates and from the formal pool.

The first v5 hardware batch completed all four Jetson 02 calibration runs with
20/20 successful requests and successful model cleanup:

| Cooldown | Start temperature | Peak temperature | Worker overhead | Jetson result |
|---:|---:|---:|---:|---|
| baseline | 42.84 °C | 50.50 °C | 3.35% | reference |
| 60 s | 46.22 °C | 51.03 °C | 4.80% | fail |
| 180 s | 43.97 °C | 50.88 °C | 3.20% | pass |
| 300 s | 43.50 °C | 51.19 °C | 3.92% | pass |

The smallest passing Jetson candidate is therefore 180 seconds. At that
checkpoint it was not yet global because the Raspberry Pi calibration cohort
was still unmeasured. The completed cross-platform decision is recorded below.

### 10.5 Completed v5 calibration decision

The Raspberry Pi 02 calibration was executed on 2026-08-25 after a read-only
environment preflight verified the Raspberry Pi 5, Ubuntu 24.04, Python 3.12,
OpenBLAS-backed `llama-cpp-python` 0.3.20, three local GGUF files, and the pinned
llama.cpp RPC runtime. No packages, models, or runtimes were installed during
that preflight.

All four Raspberry Pi observations completed 20/20 requests and unloaded the
model successfully:

| Cooldown | Start temperature | Peak temperature | TPS | TTFT p50 | Worker overhead | Pi result |
|---:|---:|---:|---:|---:|---:|---|
| baseline | 57.22 °C | 63.35 °C | 3.636 | 70.116 s | 2.23% | reference |
| 60 s | 56.75 °C | 63.10 °C | 3.643 | 70.174 s | 2.52% | pass |
| 180 s | 53.73 °C | 61.15 °C | 3.725 | 68.075 s | 2.10% | pass |
| 300 s | 53.15 °C | 60.90 °C | 3.720 | 67.353 s | 1.85% | pass |

No active thermal throttling was observed in any Raspberry Pi calibration
run. Historical undervoltage/throttling bits remain preserved as non-blocking
quality warnings; current undervoltage and current throttling were false.

The preregistered thermal decision now passes 180 and 300 seconds across both
platform cohorts and rejects 60 seconds because Jetson 02 did not recover to
the baseline start-temperature tolerance. The selected minimum cooldown is
therefore **180 seconds**. This is the smallest passing candidate; the longer
300-second candidate is not substituted merely because it also passed.

The official instrumentation gate is total Worker-internal collection time
divided by measurement wall time, not the largest individual sample divided by
its collection interval. The maximum observed v5 gate value is 4.80%, below
the preregistered 5% limit. Individual Raspberry Pi sample ratios reached about
7.8% and remain descriptive evidence, but they do not replace the frozen
aggregate gate after observing results.

The v5 calibration stage is now 8/8 complete with zero failed attempts.
`freeze_ready` remains false because all four variance cells still require five
successful independent runs each. The current 30-repeat recommendation is an
incomplete-data sentinel and is not frozen. Phase 10 formal execution remains
blocked until those 20 observations complete and the analyzer returns
`freeze_ready=true`.

### 10.6 Resume checkpoints

| Checkpoint | Commit |
|---|---|
| Preregister platform-specific low-intrusion telemetry | `7aeb9fe` |
| Add bounded, resumable Phase 09 execution batches | `1c9a323` |
| Preregister the expanded v5 thermal recovery range | `dfd0482` |

All six registered Workers were source-verified at `1c9a323…` before live
execution. Only source synchronization and Worker restart were performed; no
Python environment, model, or RPC runtime was reinstalled.
