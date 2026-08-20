# Phase 13 — Dashboard Frontend / Model Library / Results / Charts

Date: 2026-08-20 (Asia/Seoul)

## Goal

Present the Mac Controller as a control-plane-only component, make Jetson and
Raspberry Pi workers the only selectable inference devices, and make the
existing durable model/result contracts useful from the Dashboard without
changing benchmark semantics or backend wire contracts.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `6dd82bd docs(refactor): record phase 12 dashboard checkpoint`
- Implementation commit: `be74b57 feat(dashboard): separate controller and result workflows`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Reworked navigation to the five control-plane surfaces: Overview, Nodes,
  Models, Experiment, and Results.
- Added a separate Controller panel showing macOS, Dashboard, Scheduler, and
  Storage readiness. The Controller is never rendered as an inference card or
  selectable benchmark participant.
- Kept worker cards to Jetson/Pi records only, and displayed inference
  readiness separately from environment readiness and telemetry degradation.
- Added a searchable Model Library using the existing `/api/models` and
  bootstrap contracts. It shows catalog metadata, GGUF size/quantization,
  worker installation state, deterministic recommendation context, install
  (Controller-cache sync), delete, and existing model-progress events.
- Split new frontend responsibilities into explicit browser modules:
  `static/js/utils.js`, `console.js`, `models.js`, and `results.js`. The
  existing app remains the compatibility orchestration entry point; the new
  Model Library, terminal controls, and raw-result viewer do not add logic to
  that legacy flow.
- Added separate bounded Node Operations and Experiment terminals with
  auto-scroll, clear, copy, and a 200-line client-side buffer. Node actions,
  model progress, and environment activity stay out of the experiment console.
- Added a searchable experiment picker whose labels include strategy, run
  count, latest completion state, and legacy marker.
- Added a result inspector driven by the Phase 12
  `GET /api/runs/{run_id}/responses` endpoint. It renders persisted prompts and
  responses, groups broadcast replicas by logical request, shows exact output
  hash agreement, and renders structured failure code/node/model/evidence/
  deterministic solution/raw-log information when present.
- Retained compact two-column, interactive dashboard charts and the existing
  publication export path. Chart selection remains data-driven: throughput
  bars/lines, latency comparisons, node scaling/speedup lines, and per-node or
  RPC-coordinator views are only shown when their durable metrics exist.

## Changed behavior

- Dashboard wording now uses `Controller` rather than `HEAD · CONTROL +
  INFERENCE` and no longer exposes the legacy head row in worker cards or
  benchmark selection.
- The controller status is fetched from the existing
  `/api/controller/status` contract in parallel with bootstrap data.
- Result table rows now include an `응답 보기` action that reads durable raw
  responses; it does not regenerate, normalize, or otherwise alter answers.
- Model Library installation uses the existing safe `sync-models` action and
  deletion uses the existing safe `delete-models` action. Both operate only on
  selected workers and preserve the backend confirmation/action safeguards.

## Backward compatibility

- No FastAPI route, request payload, backend service, worker API, persistence
  schema, result JSON/CSV field, benchmark strategy, chart export format, or
  experiment/job lifecycle behavior changed.
- The legacy inventory `head` row remains readable for migration safety; only
  its Dashboard presentation is replaced by the separate Controller status.
- Existing `app.js` helpers stay available while focused browser modules use a
  narrow presentation façade. Existing static URL `/static/app.js` remains the
  entry point.
- Existing model/action security and token behavior remains backend-owned; the
  frontend adds no direct worker URL, shell command, token, or model path
  handling.

## Tests passed

- Full regression gate:

  ```bash
  .venv/bin/python -m unittest discover -s cluster/tests -q
  ```

  - `171` passed, `0` failed, `0` skipped.

- Python compile gate for domain/application/benchmark/infrastructure/
  dashboard/worker/tests: passed.
- `bash -n` for every shell script under `cluster/` and `scripts/`: passed.
- CLI help surfaces for `clusterctl`, benchmark runner, and job process:
  passed.
- JavaScript syntax checks for `app.js` and every new `static/js/*.js` module:
  passed.
- `node cluster/tests/test_dashboard_exports.js`: passed. The fixture now also
  covers Controller exclusion, worker-only topology, status palette helpers,
  terminal buffer bound, response grouping, raw-hash viewer wiring, module
  loading, and the existing chart/publication regression cases.
- Dashboard template unique-ID and CSS-brace checks: passed.
- `git diff --check`: passed.

## Tests not run / reason

- No Jetson/Pi connection, model installation/deletion, model transfer, live
  SSE stream, or inference benchmark was run. Phase 13 is a Controller-side
  frontend change and remote hardware must not be mutated without an explicit
  hardware acceptance request.
- No browser visual automation was run. The requested phase did not explicitly
  request browser QA, and the implementation was protected by static/template
  and JavaScript regression tests instead.

## Remaining issues

- The Dashboard intentionally does not synthesize ECDF, histogram, stacked
  failure, or telemetry time-series charts when a legacy result lacks the raw
  request/telemetry samples required to draw them faithfully. Existing compact
  charts continue to select only semantically supported durable data.
- The legacy frontend coordinator remains substantial; the new module boundary
  prevents further Model/Result/terminal feature growth in it. A later focused
  frontend-only cleanup can extract the older node and chart renderers without
  changing their public behavior.

## Next phase readiness

- **READY for the next explicitly requested phase.**
- Phase 13 is complete; no later phase was started by this checkpoint.
