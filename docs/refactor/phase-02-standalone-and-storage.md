# Phase 02 — Standalone Foundations + Storage Boundaries

Date: 2026-08-20 (Asia/Seoul)

Implemented:

- Added one repository-root and runtime-path compatibility adapter:
  `cluster.integrations.runtime_layout`.
- Extended `ProjectLayout` with the existing worker/dashboard token and future
  job-registry paths without changing the canonical legacy runtime location.
- Added filesystem repository protocols and implementations for inventory,
  settings, environment reports, experiment definitions, runs, suites, and
  future durable jobs.
- Moved runner run-artifact persistence (`config.json`, `events.jsonl`,
  `requests.csv`, `summary.json`) through `FilesystemRunRepository`.
- Moved dashboard settings, inventory writes, environment reports, experiment
  definitions, suite summaries, and run-summary reads through filesystem
  repository adapters.
- Replaced repeated Python `Path(__file__).resolve().parents[...]` root
  discovery in the dashboard, controller CLI, benchmark runner, and worker API
  with the runtime-layout adapter.
- Added controller/worker requirements entry points and storage compatibility
  tests.

Changed behavior:

- JSON config/summary/settings/environment files now use one atomic,
  fsync-backed write helper. Existing files retain their current mode; new
  settings, inventory, and environment files default to `0600`, while the
  environment directory defaults to `0700`.
- Events are flushed and fsynced after each append, improving crash durability
  without changing event JSON lines or their names.
- Corrupted JSON is represented as `StorageCorruptionError` at the repository
  boundary. Dashboard settings keep their existing fail-closed behavior.

Backward compatibility:

- Canonical runtime remains `<project>/.run/cluster`.
- `CLUSTER_RUNTIME_DIR`, `CLUSTER_INVENTORY`, and `CLUSTER_RESULTS_DIR` keep
  their existing override behavior.
- Inventory remains the same ten-column CSV; legacy JSON/CSV filenames and
  result artifact names remain unchanged.
- Existing benchmark planning, scheduling, metrics, CSV column data, API, and
  CLI behavior are unchanged. This phase only routes persistence through a
  boundary.

Tests passed:

- New temp-directory storage tests: 6/6.
- Full Python suite: 91 tests run, 88 passed, 3 macOS platform skips, 0 failed.
- Dashboard JavaScript syntax and export fixture: passed.
- Bash syntax: 12/12 repository scripts passed.
- `clusterctl` and benchmark runner CLI help smoke tests: passed.
- `git diff --check`: passed.

Tests not run / reason:

- Linux `/proc`/pidfd launcher tests remain skipped on macOS.
- No Jetson/Pi service, model, RPC, or filesystem deployment mutation was run;
  Phase 02 changes local controller storage boundaries only.

Remaining issues:

- `web.app` is now repository-local source (`web/app.py`) but remains the
  legacy worker inference implementation. Its interface extraction belongs to
  the worker-runtime phase.
- `scripts/llm-cluster` remains a legacy Jetson/Linux launcher with a fixed
  path/user. macOS lifecycle replacement is intentionally deferred to Phase 04.
- The current runtime still preserves legacy `head` inventory behavior. Actual
  Controller/Worker operational migration is deferred to later phases.
- `FilesystemJobRepository` is ready but not wired to a scheduler until the
  durable-job phase.

Next phase readiness:

- **READY for Phase 03.** Storage and runtime layout have explicit boundaries,
  filesystem compatibility is covered by temp-directory tests, and standalone
  blockers are documented.
- Phase 03 has not been started by this change.

## Dependency and standalone inventory

| Dependency | Current location | Phase 02 disposition |
|---|---|---|
| Runtime path discovery | dashboard/runner/CLI/worker modules | Centralized in `runtime_layout` |
| Inventory/settings/environment/results persistence | dashboard and runner | Filesystem repositories |
| Worker inference implementation | `web/app.py` in this repository | Explicit legacy implementation; Phase 05 adapter target |
| Controller lifecycle launcher | `scripts/llm-cluster` in this repository | Documented Linux/Jetson legacy path; Phase 04 migration target |
| GGUF model root | `<project>/models` | Derived from `ProjectLayout` |

The repository no longer requires a parent workspace for the new domain,
storage, and runtime-layout modules. Hardware-specific setup and inference
dependencies remain intentionally outside the macOS controller requirement
set.
