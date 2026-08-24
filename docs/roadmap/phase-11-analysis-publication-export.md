# Roadmap Phase 11 — Analysis and Publication Export

## 1. Outcome

Phase 11 is complete as an analysis-tooling phase. The repository now produces
deterministic statistics, CSV/JSON tables, publication SVG figures, 300/600 DPI
PNG figures, input provenance, checksums, and an immutable ZIP without manual
spreadsheet editing.

No formal scientific result was created. Phase 10 remains blocked by the
incomplete Phase 09 pilot, so the only real-data acceptance artifact generated
in this phase is explicitly labelled `pilot` and has
`formal_claim_allowed=false`.

## 2. Statistical contract

- The independent unit is a run.
- Request rows are retained only as nested descriptive observations.
- Each run-level metric reports count, mean, median, sample SD, IQR, minimum,
  maximum, p50, p95, coefficient of variation, and a deterministic 95%
  percentile-bootstrap interval.
- The checked-in analysis plan supplies seed `20260823` and 10,000 resamples.
- Formal sensitivity subsets are clean-only, clean-plus-warning,
  degraded-inclusive, and all-completed.
- Missing telemetry and RPC stages remain absent; they are never zero-filled.
- Failed, interrupted, and missing-summary attempts remain in outcome and
  exclusion evidence.
- Prompt and model-response text are excluded from publication tables and
  figures.

## 3. Generated tables and figures

The bundle contains run, nested-request, cell-summary, exclusion, telemetry,
node-contribution, and RPC-stage tables.

The vector-first figure system supports:

1. run-level throughput;
2. request-latency ECDF and boxplot;
3. energy efficiency and peak temperature;
4. preserved attempt outcomes;
5. cluster scaling for matching model/prompt/platform identities;
6. multi-node generated-token contribution;
7. platform-separated power and temperature time series;
8. RPC model-distribution, coordinator-wait, and cleanup durations.

Optional figures are generated only when eligible data exists. The Phase 09
pilot contains single-node runs and no measured RPC stages, so its final bundle
correctly omits scaling, multi-node contribution, and RPC figures. It contains
separate Jetson and Raspberry Pi temperature series and a Jetson power series.

SVG files use a white background and Okabe–Ito colours at a physical width of
180 mm. The renderer creates 300 and 600 DPI PNG files with matching `pHYs`
metadata and records the exact Playwright and Chromium versions.

## 4. Reproducibility and safety

- Pilot, smoke, and formal roots cannot be pooled implicitly.
- Pilot export requires an explicit non-formal acknowledgement.
- Formal export requires a completed `formal_campaign` manifest; the current
  closed Phase 10 gate is rejected before output creation.
- Output directories and ZIP paths are immutable by default.
- ZIP entry order and timestamps are deterministic.
- Every output is listed by size and SHA-256.
- Exact source, renderer, matrix, protocol, analysis plan, model/prompt/runtime
  locks, conditions, and pilot/campaign evidence are copied into the bundle.
- Runtime directories are mode `0700`; files and archives are mode `0600`.
- Result roots and locked inputs may not be symbolic links.

## 5. Real pilot acceptance artifact

Authoritative local artifact:

`.run/controller/publications/formal-study-v1-phase09-pilot-v3-phase11-v5.zip`

Archive SHA-256:

`33d1e71867a7c04003b73a7c913e4e69efe44c48bafdbdf462830cc2d4efb96f`

It preserves seven attempts: six completed runs and one missing terminal
summary from the user-interrupted attempt. The attempt failure rate is 1/7,
and 120 nested request rows are retained. This artifact is ignored runtime
evidence, not a Git-tracked publication result.

Visual inspection confirmed that the 300 DPI throughput, latency, power, and
platform-separated temperature figures are legible and do not mix Jetson and
Raspberry Pi time scales.

## 6. Verification

Focused Phase 11 gates cover:

- deterministic seeded statistics and sample-SD semantics;
- experiment-type mixing rejection and formal identity enforcement;
- explicit pilot acknowledgement and completed-campaign authorization;
- immutable, private, byte-identical bundles;
- prompt/response omission;
- data-driven scaling, node, telemetry, and RPC outputs;
- real Chromium 300/600 DPI rendering, dimensions, and PNG resolution chunks;
- Python/JavaScript syntax and repository diff integrity.

The authoritative full regression gate remains GitHub Required CI. No Worker
package, model, power mode, process, or experiment was changed in Phase 11.

## 7. Stop boundary

Phase 11 stops after implementing and accepting the publication pipeline.
Formal estimates remain prohibited until a revised complete pilot freezes the
repeat count, cooldown, and instrumentation policy and Phase 10 admits and
finishes a formal campaign. Phase 12 may consume this pipeline, but it may not
promote the current pilot bundle to formal evidence.
