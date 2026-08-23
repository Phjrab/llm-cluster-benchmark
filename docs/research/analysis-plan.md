# Formal Analysis Plan v1

Status: preregistered draft; Phase 09 must fill the CI precision target before
formal execution.

## Analysis hierarchy

```text
request → run → matrix cell → runtime/model cohort
```

The run is the independent inferential unit. Request observations describe the
within-run distribution and are never promoted to independent repeats. For
broadcast, replies are additionally grouped by run, scenario, and logical
request ID.

## Outcomes

Primary outcomes are run-level cluster generated tokens/s, successful
requests/s, success rate, and the nested TTFT/E2E request distributions.
Strategy-specific outcomes are:

- round-robin: throughput, speedup versus the Pi 02 single-node baseline, and
  scaling efficiency;
- broadcast: all-replicas success, exact output-hash agreement, and per-node
  latency;
- single node: throughput, TTFT, and E2E.

Physical broadcast throughput is never labelled as logical user throughput.

## Descriptive statistics and intervals

Every applicable output reports count, mean, median, standard deviation, IQR,
minimum, maximum, p50, p95, 95% CI, coefficient of variation, and failure rate.

The default interval is a percentile bootstrap with 10,000 resamples, fixed
seed `20260823`, and run-level resampling within a matrix cell. Phase 09 fixes
the target half-width and final repeat count before formal collection. Families
of declared primary contrasts use Holm adjustment; unadjusted effect sizes and
confidence intervals remain visible.

## Primary and sensitivity sets

| Platform | Primary | Sensitivity | Excluded from estimates |
|---|---|---|---|
| Jetson | clean | clean + warning | degraded, unknown |
| Raspberry Pi | clean + history warning | clean only | degraded, unknown |

Excluded runs remain in coverage and failure tables. No value is imputed.

## Predefined exclusion reasons

Whole-run exclusions are restricted to lock/model/source/runtime/cohort/prompt/
condition mismatches, active power integrity faults, thermal stabilization
violations, primary instrumentation failures, and cleanup failure. There is no
automatic statistical outlier removal. Robust summaries are reported alongside
means, and investigations require an existing reason code.

Failed request latency is omitted only from the successful-request latency
distribution. The request still contributes to physical request count and
failure rate.

## Contrasts and interpretation

1. Jetson versus Pi single-node results are descriptive comparisons between
   complete hardware/runtime cohorts. They do not identify a pure platform
   causal effect.
2. Pi 1/2/3-node scaling is the primary homogeneous scaling contrast. Models,
   prompts, and repeat indices are matched.
3. Broadcast agreement reports exact output SHA-256 equality among successful
   replica groups and all-replicas success separately.
4. Jetson MAXN_SUPER versus 15W is exploratory and descriptive because the
   modes occur on different physical Workers.

The two approved models differ in both size and family. Their contrast is a
model-identity × platform result, not a model-size effect. RPC and heterogeneous
formal estimates do not exist in matrix v1.

## Experiment-type separation

Smoke and pilot observations receive their own analyses. They are never pooled
with formal cells, even when their node/model/prompt values happen to match.
