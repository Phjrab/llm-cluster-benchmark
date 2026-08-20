# Phase 12 — Dashboard Backend Modularization

Date: 2026-08-20 (Asia/Seoul)

## Goal

Reduce the Dashboard's God `app.py` to Controller application wiring while
retaining every existing Dashboard HTTP endpoint and connecting durable jobs,
results, model inventories, failures, and events through explicit route and
service boundaries.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `db52a69 docs(refactor): record phase 11 models checkpoint`
- Implementation commit: `80001cf refactor(dashboard): modularize backend wiring`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Reduced `cluster/dashboard/app.py` from 1,993 lines to FastAPI application
  construction only: static/template wiring, dependency state registration,
  router registration, lifespan recovery, shutdown, and the existing generic
  exception response.
- Added `cluster/dashboard/schemas.py` for Pydantic transport payloads. The
  existing Dashboard request validations remain wire-level validation; services
  explicitly convert experiment payloads into `ExperimentConfig` before any
  Worker selection or job creation.
- Added `cluster/dashboard/dependencies.py` for FastAPI-only authentication and
  `DashboardFacade` dependency lookup. Token verification remains constant-time
  and dashboard-auth-off behavior remains unchanged.
- Added grouped routers in `cluster/dashboard/routes.py` for web/health,
  controller, nodes, environment, actions, models, settings, events,
  experiments, and results. Each route only translates HTTP concerns and
  delegates to the service facade.
- Moved Controller-side persistence, inventory/environment/model operations,
  status monitoring, control action process ownership, durable suite/job
  recovery, and experiment admission into `cluster/dashboard/services.py`.
  It reuses the existing filesystem repositories, Worker HTTP/SSH adapters,
  and `JobService`; it imports no FastAPI or Pydantic types.
- Registered lifespan startup recovery through `DashboardFacade.startup()` and
  shutdown monitor cleanup through `DashboardFacade.shutdown()`. Durable job
  recovery and interrupted-suite reconciliation stay application-service owned,
  never request-handler owned.
- Preserved the historical `cluster.dashboard.app` helper imports as aliases
  for callers during migration while moving the test seams to the explicit
  service module.
- Added `GET /api/runs/{run_id}/responses`, an additive API that returns the
  already-durable `responses.jsonl` records. It does not alter result JSON,
  `requests.csv`, benchmark calculations, or raw prompt/response persistence.

## Changed behavior

- The same Dashboard URLs now use grouped `APIRouter` adapters and an
  application-service facade instead of route-local control logic.
- `/api/runs/{run_id}/responses` makes persisted prompt/response inspection
  available to the Dashboard API. It returns `404` for an unknown run and an
  empty response list when a known legacy run has no response journal.
- Controller status remains separate from Worker status: `/api/controller/status`
  declares `role=controller` and `inference_enabled=false`; `/api/status`
  remains a Worker-monitor snapshot and the Controller is not inserted into its
  node selector data.

## Backward compatibility

- Existing Dashboard paths, request payloads, response fields, dashboard token
  behavior, SSE event names/channels, settings behavior, status/model APIs,
  environment/action APIs, experiment/job APIs, and run summary API are kept.
- Existing filesystem formats, repository paths, permissions, durable job
  registry, suite summaries, result summaries, CSV schema, and benchmark
  scheduling/aggregation semantics are unchanged.
- The raw response endpoint is additive; it reads the existing Phase 9
  `responses.jsonl` artifact without rewriting it.
- No Dashboard frontend redesign, Worker API change, model distribution change,
  hardware configuration change, or benchmark math change was made.

## Tests passed

- Phase 12 focused Dashboard service/router tests:
  `.venv/bin/python -m unittest cluster.tests.test_dashboard_backend cluster.tests.test_core cluster.tests.test_events cluster.tests.test_models cluster.tests.test_durable_jobs -q`
  - 76 passed, 0 failed, 0 skipped.
- Full test suite:
  `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 171 passed, 0 failed, 0 skipped.
- `python3 -m compileall -q cluster/domain cluster/application cluster/benchmark cluster/infrastructure cluster/dashboard cluster/worker cluster/tests`
  - Passed.
- `bash -n` for every shell script under `cluster/` and `scripts/`
  - Passed.
- `.venv/bin/python -m cluster.clusterctl --help`,
  `.venv/bin/python -m cluster.benchmark.runner --help`, and
  `.venv/bin/python -m cluster.application.job_process --help`
  - Passed.
- `node --check cluster/dashboard/static/app.js` and
  `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `git diff --check`
  - Passed.

## Tests not run / reason

- Jetson/Pi SSH deployment, Worker API traffic, real model inventory hashing,
  result-response retrieval from production artifacts, and live inference were
  not run. Phase 12 is Controller backend modularization and did not connect to
  or mutate remote hardware.

## Remaining issues

- The existing Controller service module remains intentionally substantial
  because it preserves proven legacy storage/action/job behavior. Future
  phases can split its internal inventory, environment, and action facades
  further without changing route contracts.
- The Dashboard frontend remains in its current form by design; Phase 13 owns
  frontend modularization and redesign.

## Next phase readiness

- **READY for the next explicitly requested phase.**
- Phase 12 is complete; no later phase was started by this checkpoint.
