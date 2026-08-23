# Roadmap Phase 04 — Formal Experiment Matrix and Analysis Plan

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: Roadmap Phase 04 only; no benchmark campaign or Phase 05 instrumentation was started

## 1. Outcome

The frozen Phase 03 lock set now has a machine-validated formal matrix,
execution protocol, analysis plan, campaign manifest schema, and publication
table/figure plan. The matrix fails closed on unapproved models, unknown or
Controller participants, cross-cohort node sets, unlocked profiles, prompt
drift, smoke/pilot mixing, and workload-accounting drift.

The matrix is intentionally marked non-executable. Phase 05 instrumentation
and the Phase 09 pilot remain mandatory before any long formal campaign.

## 2. Git checkpoint

| Item | Value |
|---|---|
| Feature branch | `codex/roadmap-phase-04` |
| Implementation checkpoint | `9102728` (`feat(research): define formal experiment matrix`) |
| Parent Phase 03 checkpoint | `49b378e` |
| Remote branch | `origin/codex/roadmap-phase-04` |

The branch was pushed to GitHub. `main` was not changed and no force push was
used.

## 3. Active matrix

The matrix references lock ID `formal-study-v1` and fingerprint:

```text
3d5c7f2429c42d02db60ab635a263db6edd86fb4527078eb8d5ff80fc30744a5
```

Only the approved Qwen2.5 1.5B and Granite 3.3 2B GGUF entries and all four
locked prompts are active.

| Group | Base cells |
|---|---:|
| Three cohort-specific Jetson single-node groups | 24 |
| Pi 02/03/04 single-node groups | 24 |
| Pi 2- and 3-node replicated round-robin | 16 |
| Pi 3-node broadcast agreement | 8 |
| **Total** | **72** |

Every active multi-node cell is contained in the single homogeneous Pi cohort.
The Controller is absent from every expanded node set.

## 4. Deferred rather than hidden

- Heterogeneous Jetson/Pi performance remains exploratory because formal
  cross-cohort pooling is prohibited.
- Pure model-size interaction remains blocked because the two approved models
  differ in both family and size.
- RPC remains blocked under the current exact profile: it requires an explicit
  Jetson coordinator and at least two Workers, but all current Jetson formal
  cohorts contain one Worker.
- MAXN_SUPER versus 15W is descriptive only because the modes are on different
  physical Workers. A causal claim needs a separately approved randomized
  within-Worker crossover protocol.

These questions and their unlock conditions remain in the matrix instead of
being silently removed as inefficient or inconvenient experiments.

## 5. Repeat, order, and analysis policy

- The independent repeat is a run; requests are nested observations.
- Phase 09 selects 10–30 repeats from pilot run-level variance and a frozen CI
  half-width target.
- Seed `20260823` creates the complete randomized block order before formal
  execution.
- Formal runs are serialized.
- Smoke, pilot, and formal campaigns cannot be pooled.
- Required summaries include mean, median, SD, IQR, p50, p95, and 95% CI.
- Confidence intervals use 10,000 run-level percentile bootstrap resamples.
- Failed attempts and exclusions are retained with no imputation or automatic
  outlier removal.
- Pi history warning is non-blocking; active faults are degraded. Primary and
  clean-only sensitivity analyses are separate.

## 6. Planned volume

| Quantity | Per matrix repeat | Minimum (10) | Maximum (30) |
|---|---:|---:|---:|
| Runs | 72 | 720 | 2,160 |
| Logical measured requests | 1,440 | 14,400 | 43,200 |
| Physical measured requests | 1,760 | 17,600 | 52,800 |
| Warmup requests | 112 | 1,120 | 3,360 |

The provisional five-minute successful-run planning value gives 60–180 hours.
The deliberately conservative timeout envelope is 720.6–2,161.8 hours and is
not an expected duration. Raw-response and run-metadata allowance is 320–960
MiB; the protocol reserves at least 5 GiB for telemetry, exports, and archives.

## 7. Files created

Machine-readable research contracts:

- `config/research/formal_experiment_matrix.json`
- `config/research/experiment_protocol.json`
- `config/research/analysis_plan.json`
- `config/research/campaign_manifest.schema.json`

Pure validation and regression coverage:

- `cluster/research/matrix.py`
- `cluster/tests/test_research_matrix.py`

Research documentation:

- `docs/research/formal-experiment-matrix.md`
- `docs/research/formal-experiment-protocol.md`
- `docs/research/analysis-plan.md`
- `docs/research/publication-table-and-figure-plan.md`

No benchmark runner, Worker, Dashboard, result schema, lock content, model, or
runtime state was modified.

## 8. Test gates

- Phase 04 focused tests: 18/18 PASS;
- research lock + matrix tests: 48/48 PASS;
- full project regression in the existing project virtual environment:
  374/374 PASS in 51.280 seconds;
- dashboard JavaScript syntax and export fixtures: PASS;
- Python compileall: PASS;
- all project shell scripts `bash -n`: PASS;
- all research JSON parse checks: PASS;
- `git diff --check`: PASS.

The system Python did not contain the already-pinned Controller dependencies;
the authoritative full gate therefore used the existing project `.venv`. No
dependency was reinstalled. Local-socket lifecycle tests required the normal
macOS test permission boundary and passed there.

## 9. Safety and stop boundary

- No benchmark, smoke, pilot, or formal request was sent to a Worker.
- No Worker was contacted, changed, restarted, or reconfigured.
- No model was downloaded, removed, loaded, or unloaded.
- No result, runtime secret, key, token, or `.run` artifact was committed.
- No Phase 05 code or instrumentation work was started.

Phase 04 is complete. The next allowed roadmap step is Phase 05, but this
checkpoint stops here.
