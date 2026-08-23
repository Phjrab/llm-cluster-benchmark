# Formal Experiment Protocol v1

## Scope and freeze boundary

This protocol defines how a future formal campaign is executed. It does not
authorize a run. Formal execution remains blocked until Phase 05 supplies all
required instrumentation and Phase 09 freezes repeat count, cooldown, and
precision targets.

Smoke, pilot, and formal results use different campaign IDs and result
directories. Pilot observations may determine the formal repeat count, but
they never enter formal estimates.

## Experimental unit and repetition

- One independently scheduled run is one independent repeat.
- Requests are nested observations within a run; they are not independent
  experimental replicates.
- Each formal run contains one model, one prompt, one exact node set, and one
  strategy.
- The same fixed prompt and seed are repeated for the 20 logical requests in
  that run.
- Repeat indices start at 1. Phase 09 selects 10–30 repeats before any formal
  result is observed.

## Seeded execution order

The campaign runner will generate the complete order manifest before the first
formal run using seed `20260823`.

```text
block = repeat_index × platform_cohort × order_block
shuffle target = cell_id
node order = preserved
```

Formal runs are serialized. This avoids concurrent campaigns competing for
Workers, LAN capacity, or shared power conditions. Changing order after viewing
results is prohibited.

## Per-run lifecycle

1. Revalidate the formal lock, approved model identity, deployment fingerprint,
   runtime cohort, backend, condition profile, NTP, and free storage.
2. Load the exact GGUF on every selected Worker.
3. Verify actual context, batch, thread, GPU-layer, and backend settings.
4. Send one warmup request to each selected Worker.
5. Apply the pre-measurement power, thermal, and stability gate.
6. Measure requests with a monotonic clock.
7. Capture the post-measurement quality state.
8. Unload the model from every selected Worker.
9. Apply the pilot-frozen cooldown and thermal stabilization rule.

Model download, checksum verification, model load, warmup, unload, and cooldown
are outside the request measurement boundary. Downloads during a benchmark are
forbidden.

## Fixed workload

```text
logical requests per run  20
logical concurrency        4
maximum output tokens    128
warmup per Worker           1
request timeout           600 s
temperature                 0.0
top_p                       0.9
seed                         42
context                    4096
```

Platform-specific CUDA/OpenBLAS, threads, batch, and GPU-layer values come only
from `experiment_conditions.json`. An actual-value mismatch blocks the run.

## Quality handling

For Jetson, the exact locked nvpmodel setting and `jetson_clocks=OFF` are
mandatory. Drift blocks the run.

For Raspberry Pi, a historical power warning is recorded and allowed. An
active undervoltage or throttling condition preserves the attempt but marks it
`degraded`. Unknown quality is excluded from both primary and sensitivity
estimates.

## Failures, retries, and cleanup

- Every attempt, failure, raw response, and exclusion reason is retained.
- Failed requests contribute to failure rate but not successful-request latency
  distributions.
- There is no silent automatic retry in the formal campaign.
- An approved retry has a new attempt ID and retains the failed attempt.
- Cleanup failure makes a run non-clean and stops further scheduling on the
  affected nodes.
- Selective result deletion is prohibited for the frozen campaign archive.

A critical product defect stops the campaign. A repair requires a new commit,
updated identity locks if affected, and a repeated pilot before formal execution
resumes.
