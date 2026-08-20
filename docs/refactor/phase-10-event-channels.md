# Phase 10 — Typed Events and Channel Separation

Date: 2026-08-20 (Asia/Seoul)

## Goal

Separate Controller node-operation events from benchmark experiment events with
a typed, explicit channel while retaining the existing SSE event fields used by
Dashboard clients and durable result readers.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `1615898 docs(refactor): record phase 09 results checkpoint`
- Implementation commit: `2e28ec5 refactor(events): separate node and experiment channels`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added the transport-neutral `ClusterEvent` envelope and centralized
  `EventChannel` enum with `node_ops`, `experiment`, and `system` values.
- The envelope retains explicit context for node, run, suite, experiment,
  model, scenario, message, evidence, and arbitrary payload data.
- Updated Dashboard `EventBus` publishing and initial SSE system messages to
  serialize the typed envelope.
- Routed provisioning/control work—including environment checks and installs,
  setup, code/model sync, worker lifecycle, and action output—to
  `node_ops`.
- Routed experiment lifecycle events delivered by durable jobs to
  `experiment`; status/settings remain `system`.
- Marked persisted benchmark run events and suite-runner events with the
  additive `experiment` channel, including request completion, warmup,
  measurement, scenario, suite, warning, and failure events.
- Kept EventBus subscribers bounded (default 100 events) and made the limit
  injectable for deterministic tests.
- Updated the existing Dashboard SSE consumer so node-operation output is
  shown only in the environment/control log. Only `experiment` events update
  the RUN CONTROL console.

## Changed behavior

- A model sync, worker setup, or environment installation log can no longer
  appear as benchmark RUN CONTROL output.
- Durable `events.jsonl` now records `channel: "experiment"` for new runs.
  This is metadata only; it does not change benchmark scheduling or metrics.
- System events (`connected`, token-auth state changes, settings, and status)
  are explicitly marked as `system`.

## Backward compatibility

- The wire representation keeps legacy top-level `type`, `at`, and payload
  fields. `channel` is additive and its typed value cannot be overwritten by
  a payload key.
- The browser recognizes old channel-less SSE messages using conservative
  event-type mapping, so an older Controller remains usable.
- Existing clients that ignore unknown JSON keys continue to receive the same
  event names and payload structure.
- Existing run directories remain readable; only newly emitted events contain
  the additive `channel` field.

## Benchmark invariants

- No planner, scheduling, request mapping, warmup exclusion, aggregation,
  percentile, throughput, scaling, answer-agreement, cancellation, RPC, or
  suite cleanup behavior was changed.
- The event channel is descriptive routing metadata. It never creates a
  benchmark request or alters persisted metrics/results.

## Tests executed

- Focused event and affected regression tests:
  `.venv/bin/python -m unittest cluster.tests.test_events cluster.tests.test_core cluster.tests.test_durable_jobs cluster.tests.test_results_failures -q`
  - 69 passed, 0 failed, 0 skipped.
- Final Phase 10 event tests:
  `.venv/bin/python -m unittest cluster.tests.test_events -q`
  - 6 passed, 0 failed, 0 skipped.
- Full test suite:
  `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 157 passed, 0 failed, 0 skipped.
- `python3 -m compileall -q cluster/domain cluster/application cluster/benchmark cluster/infrastructure cluster/dashboard cluster/worker cluster/tests`
  - Passed.
- `bash -n` for every shell script under `cluster/` and `scripts/`
  - Passed.
- `.venv/bin/python -m cluster.benchmark.runner --help`,
  `.venv/bin/python -m cluster.clusterctl --help`, and
  `.venv/bin/python -m cluster.application.job_process --help`
  - Passed.
- `node --check cluster/dashboard/static/app.js` and
  `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `git diff --check`
  - Passed.

## Tests added

- Typed event serialization and legacy flat-field compatibility.
- Typed fields take precedence over colliding payload keys.
- Bounded EventBus subscriber buffering and node/experiment routing.
- Dashboard producer channel assignments, including action logs and
  environment changes.
- Dashboard source regression proving node-operation output is not sent to the
  experiment console.
- Durable run journal experiment-channel metadata.

## Hardware tests not executed

- Jetson/Pi SSH, worker provisioning, model synchronization, live inference,
  and RPC execution: **NOT RUN**. Phase 10 changes local Controller event
  routing and durable event metadata only; it does not require a remote
  mutation or service restart.

## Remaining risks

- EventBus buffering is intentionally best-effort UI transport. Durable run
  journals remain the recovery source when a browser disconnects or its
  bounded queue drops old messages.
- Historical event journals have no channel field; consumers must retain the
  legacy event-type fallback during the transition.

## Deferred work

- A large event/log visual redesign is explicitly deferred to Phase 13.
- Cross-process durable controller action logs are outside this phase; durable
  benchmark/suite events retain their existing repositories plus the channel
  metadata added here.

## Next phase readiness

- **READY for Phase 11, but Phase 11 has not been started.**
- Phase 10 stops after this report checkpoint and GitHub push.
