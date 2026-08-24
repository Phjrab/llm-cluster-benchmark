# Roadmap Phase 12 — Compatibility Facade Split

## 1. Outcome

Phase 12 completed a compatibility-preserving, incremental split of the three
remaining large composition surfaces. No route, CLI command, result schema,
benchmark strategy, or live hardware state was changed.

The legacy public modules remain importable:

- `cluster.dashboard.services.DashboardFacade`;
- the existing `cluster.dashboard.app` compatibility exports;
- `cluster.clusterctl.Node`, `load_nodes`, and `select_nodes`;
- the existing classic-script Dashboard globals used by feature modules.

## 2. Characterization-first gate

The first checkpoint froze the existing Dashboard facade method set, explicit
compatibility exports, transport-neutral service boundary, and route-to-facade
dependency rule. Extraction work began only after this gate passed.

The new Phase 12 tests also verify that extracted service modules import no
FastAPI, Starlette, or subprocess implementation and that `clusterctl` public
inventory symbols are exact re-exports of the extracted implementation.

## 3. Dashboard backend split

New transport-neutral modules:

```text
cluster/dashboard/service_layers/
├── errors.py
├── research_service.py
├── result_service.py
└── settings_service.py
```

Responsibilities moved:

- campaign listing/detail and cross-run research projections;
- formal research-readiness projection;
- run, response, and telemetry reads;
- recoverable result deletion and suite reconciliation;
- Dashboard/Worker authentication setting transitions and rollback.

`DashboardFacade` now composes these services and preserves every established
method name and response dictionary. Dependencies are injected as callables or
repositories, so result, research, and settings behavior can be tested without
FastAPI, network access, Worker hardware, or Dashboard process globals.

The dynamic `experiments.active()` seam is intentionally resolved at call time
to preserve existing test and durable-manager replacement behavior.

## 4. Frontend split

New classic-script modules:

```text
cluster/dashboard/static/js/
├── state.js
├── api.js
└── events.js
```

The shared state object, authenticated JSON API client, and header-authenticated
SSE reader moved out of `app.js`. The template loads them before the legacy
composition script. No module bundler or build step was introduced, and the
existing CSP/network/token behavior remains unchanged. The SSE token remains a
header and is never moved into a query string.

`app.js` decreased from 2,676 to 2,561 lines. Further node/onboarding and
experiment rendering extraction remains safe future work, but was deliberately
not combined into this Phase checkpoint.

## 5. clusterctl split

Legacy CSV inventory parsing and validation moved to:

```text
cluster/integrations/legacy_inventory_runtime.py
```

It owns the compatibility `Node` record, CSV parsing, enabled/head policy,
selection, private-LAN host validation, SSH user validation, identity reference
validation, and safe project-directory validation. `clusterctl.py` re-exports
the exact objects, so all current imports and positional `Node(...)` callers
remain valid.

`clusterctl.py` decreased from 2,174 to 2,009 lines. Remote execution,
environment installation, model transfer, RPC, power, and lifecycle commands
were not mechanically split together because each has a separate safety and
cleanup contract.

## 6. Full-regression correction

The full regression gate exposed a pre-existing deadlock in the Worker model
installation path: `install_model()` held the backend's deliberately
non-reentrant inference lock and then called the public `verify_model()` method,
which attempted to acquire the same lock again. Verification logic now has a
lock-owning public wrapper and a lock-already-held helper. Both new-download and
already-present installation paths use the helper, while the public method and
its response contract remain unchanged.

## 7. Size and responsibility change

| Surface | Before | After | Extracted responsibility |
|---|---:|---:|---|
| `dashboard/services.py` | 2,537 | 2,397 | result/research/settings application services |
| `clusterctl.py` | 2,174 | 2,009 | legacy inventory runtime |
| `dashboard/static/app.js` | 2,676 | 2,561 | state/API/SSE transport |
| `dashboard/static/styles.css` | 619 | 619 | unchanged; already bounded and cohesive |

The total line count is not the success metric: extracted code adds explicit
interfaces and tests. The measurable improvement is that these responsibilities
can now be imported and exercised independently of the large compatibility
facades.

## 8. Compatibility and safety

- Dashboard method names and routes are unchanged.
- CLI command names, flags, and inventory CSV columns are unchanged.
- `Node` positional construction and properties are unchanged.
- Existing result, suite, response, telemetry, and campaign artifacts require
  no migration.
- Settings rollback on Worker restart admission failure is preserved.
- Active-suite result deletion remains blocked.
- Result deletion remains recoverable and emits the established event.
- Private host, SSH identity, and broad project-path validation remain intact.
- Dashboard authentication never accepts query-string credentials.
- Model installation no longer self-deadlocks while preserving checksum and
  metadata verification before a model becomes ready.
- No Worker connection, package operation, model load, experiment, RPC process,
  power-mode change, or result mutation was performed.

## 9. Verification

Focused verification includes:

- Phase 12 characterization and extracted-service unit tests;
- Dashboard backend and research routes;
- Dashboard and Worker security boundaries;
- inventory, domain, benchmark-core, infrastructure, and project-removal tests;
- GGUF metadata identity and atomic model-install verification;
- JavaScript syntax and Dashboard export fixtures;
- public `clusterctl --help` compatibility;
- Python compilation and repository diff integrity.

Local release evidence:

- 453 Python regression tests passed in the project virtual environment;
- 9 Phase 12 facade/extracted-service tests passed;
- 6 GGUF identity and model-install tests passed;
- 3 wheel/package-isolation tests passed;
- 2 Playwright Dashboard E2E flows passed;
- JavaScript syntax, Dashboard export fixtures, and publication PNG fixtures
  passed;
- `clusterctl --help`, Python compilation, and `git diff --check` passed.

The full Python run required ordinary localhost socket permission for two
Dashboard launcher lifecycle tests. The same tests fail closed under the
restricted sandbox before opening a socket and pass under the release-test
permission profile. No external network or Worker hardware was used.

GitHub Required CI is the authoritative cross-platform full regression gate.

## 10. Stop boundary

Phase 12 stops after these bounded responsibility moves. The remaining
compatibility modules intentionally continue to orchestrate coupled lifecycle
operations. A later maintenance pass may extract node onboarding, experiment
admission, environment installation, model transfer, and RPC command services
one at a time under the same characterization-first policy.

Phase 13 was subsequently completed in
[`phase-13-security-deployment-retention.md`](phase-13-security-deployment-retention.md).
