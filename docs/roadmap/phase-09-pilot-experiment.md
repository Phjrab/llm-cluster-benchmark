# Roadmap Phase 09 — Pilot Experiment

## 1. Outcome

Phase 09 is **stopped incomplete by user request** on branch
`codex/roadmap-phase-09`.

The pilot did not freeze the formal repeat count, run duration, cooldown, or
campaign matrix. Phase 10 formal execution remains blocked. No pilot request
or failed attempt was admitted to a formal result pool.

The durable v3 pilot contains 7 of 28 declared attempts:

- 6 completed runs with 100% request success;
- 1 user-interrupted Raspberry Pi run, preserved as
  `USER_CANCELLED_PHASE09`;
- 21 unstarted declared runs;
- `freeze_ready=false`.

The running Pi model was unloaded successfully after cancellation. No Worker
or RPC process was left by the stopped run.

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

## 9. Stop boundary and resumption

Phase 09 stops here by explicit user request.

The following remain blocked:

- final repeat count;
- formal run-duration budget;
- Raspberry Pi cooldown minimum;
- Pi telemetry overhead acceptance;
- matrix/protocol/analysis-plan freeze;
- Phase 10 formal campaign execution.

The v3 manifest is resumable and preserves all remaining declared runs. Before
resumption, the Pi instrumentation blocker should be resolved under a new
source commit and the pilot version/provenance policy applied consistently.
Starting Phase 10 implementation preparation is possible, but collecting
formal results is not scientifically valid until `freeze_ready=true`.
