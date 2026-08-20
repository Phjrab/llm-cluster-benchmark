# Phase 08 — Durable Experiment Jobs / Dashboard Restart Recovery

Date: 2026-08-20 (Asia/Seoul)

## Goal

Move experiment and multi-model suite execution out of the Dashboard daemon's
in-memory thread lifecycle. Persist every job under the Controller runtime,
execute it in a dedicated child process, recover state after Dashboard restart,
and retain the existing graceful cancellation and cleanup semantics.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `c835cac docs(refactor): record phase 07 RPC coordinator checkpoint`
- Implementation commit: `1047c5d refactor(jobs): make experiment execution durable`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added a durable job registry at
  `.run/controller/jobs/<job-id>.json` with the required states:
  `queued`, `running`, `completed`, `failed`, `cancelled`, and `orphaned`.
- Added a private, append-only per-job event journal and private child-process
  log beside each job record. Job directories use mode `0700`; job, event, lock,
  and log files use mode `0600`.
- Added an `ExperimentRunner` facade for exactly one model/run and a
  `SuiteRunner` for ordered multi-model execution, continue/stop policy,
  cooldown, cancellation propagation, per-model unload, cleanup accounting,
  and suite persistence.
- Replaced Dashboard-owned benchmark daemon threads with a dedicated
  `cluster.application.job_process` child process. The Dashboard owns only job
  admission, registry observation, recovery, cancellation requests, and SSE
  publication.
- Persisted exact child process identity: PID, executable, working directory,
  argument vector, process start time, and user.
- Reused the Phase 03 process inspector boundary and required the complete
  identity to match before any termination signal can be delivered.
- Added startup and polling recovery from the job registry, latest event journal
  entry, terminal suite summary, and live process identity.
- Added crash-window recovery for a child that has been spawned but has not yet
  written its full identity, while still requiring its exact command,
  executable, and working directory to match.
- Added explicit `orphaned` reconciliation for nonterminal jobs whose expected
  process is missing, stale, or identity-mismatched.
- Made cancellation durable before signalling: the Controller writes
  `cancel_requested=true`; the child observes it and follows the normal
  benchmark cancellation/unload path. `SIGTERM` and then `SIGKILL` are bounded,
  identity-checked fallbacks only after grace periods.
- Added additive `jobs` data to Dashboard bootstrap and experiment-list API
  responses. Internal command, process identity, log path, and full config are
  excluded from the public payload.
- Preserved legacy non-job suite restart reconciliation while excluding suites
  that belong to a currently live durable job.
- Prevented node identity mutation or deletion while that node belongs to a
  nonterminal durable experiment job.

## Changed behavior

- Restarting the Dashboard no longer ends a running experiment. A new Dashboard
  process reconstructs the active state from disk and the independently running
  child process.
- A completed, failed, partial, or cancelled suite can restore the terminal job
  state even when the Dashboard was unavailable when the child finished.
- A stale registry PID is never trusted as a running experiment. It becomes an
  `orphaned` job with explicit recovery evidence.
- Cancelling from the Dashboard is a cooperative request first, not an immediate
  process kill.
- The latest terminal job remains available through the existing
  `active_experiment` compatibility field when no job is running, matching the
  previous Dashboard behavior of retaining the last suite state.

## New abstractions

- `cluster.application.jobs.JobService`: durable job admission, recovery,
  cancellation, and process identity policy.
- `cluster.application.job_process`: standalone child-process entry point for a
  durable experiment job.
- `cluster.application.suite_runner.ExperimentRunner`: one model/run benchmark
  facade.
- `cluster.application.suite_runner.SuiteRunner`: ordered multi-model lifecycle
  and suite persistence.
- `cluster.infrastructure.process.ProcessInspector` and
  `PsutilProcessInspector`: cross-platform inspect/signal boundary.
- `FilesystemJobRepository`: process-safe job updates and event journaling.

## Moved / extracted code

- The multi-model loop, cooldown, continue-on-error handling, per-model cleanup,
  and suite summary construction moved out of `cluster.dashboard.app` into the
  application layer.
- The Dashboard's former in-memory `ExperimentManager` is now a thin facade over
  `JobService` and contains no benchmark executor thread.
- Job filesystem and process lifecycle details are owned by infrastructure and
  application services instead of FastAPI routes.

## Backward compatibility

- Existing experiment start, cancel, bootstrap, experiment-list, and SSE route
  shapes remain usable. The `jobs` collection is additive.
- Existing suite artifact schema, run directory layout, summary fields,
  request CSV schema, and response/event artifacts are unchanged.
- Compatibility helper functions for suite documents remain importable from the
  Dashboard module and delegate to the new application implementation.
- Existing experiment strategy, model order, request planning, aggregation,
  and RPC coordinator behavior are unchanged.
- The private job document stores the full execution config required for child
  recovery, but that config is not returned by the public Dashboard API.

## Benchmark invariants

- Each selected model still runs independently in the user's selected order.
- Model unload runs after every attempted model, including the final model.
- Cleanup failure cannot produce a completed suite.
- `continue_on_model_error`, cooldown, and cancellation behavior retain their
  existing meaning.
- Warmup exclusion, logical/physical request counts, deterministic mapping,
  p50/p95, throughput, speedup, scaling efficiency, answer hash agreement, and
  fixed result fields are unchanged.
- Native RPC cleanup remains part of the benchmark runner's existing safe
  finalization path; Phase 08 does not weaken it.

## Security considerations

- The child command is a fixed argv list; no shell, `eval`, or user-controlled
  command string is used.
- Process termination requires complete persisted-versus-observed identity
  equality and never uses `pkill`, `killall`, or wildcard matching.
- Job records and logs can include local research configuration and error
  details, so they are kept in a private Controller runtime directory and are
  excluded from the public API.
- No credential, token, password, SSH key, model binary, runtime job artifact,
  benchmark output, or remote device state is included in this checkpoint.

## Tests executed

- `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 143 passed, 0 failed, 0 skipped.
  - Includes queued → running → completed child-process execution, terminal
    failure, cancellation, orphan recovery, stale PID recovery, exact spawned
    process adoption, Dashboard service restart recovery, event replay, partial
    suite failure, continue/stop policy, cooldown cancellation, and cleanup
    failure.
- `python3 -m compileall -q cluster/application cluster/dashboard cluster/domain cluster/infrastructure cluster/integrations cluster/tests`
  - Passed.
- `node --check cluster/dashboard/static/app.js`
  - Passed.
- `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `bash -n` for every `*.sh` file under `cluster/` and `scripts/`.
  - Passed.
- `.venv/bin/python -m cluster.benchmark.runner --help`,
  `.venv/bin/python -m cluster.clusterctl --help`, and
  `.venv/bin/python -m cluster.application.job_process --help`
  - Passed; existing and new CLI entry points remain loadable.
- `git diff --check`
  - Passed after removing the only trailing blank line before the implementation
    checkpoint.

## Tests skipped

- None in the final local suite.

## Hardware tests not executed

- Real Jetson/Pi inference job: **NOT RUN — Phase 08 validates Controller-side
  durability and process recovery; it does not require changing a Worker or
  consuming a long-running model benchmark.**
- Dashboard crash/restart was exercised with isolated repositories and real
  child processes in the automated suite, not by interrupting an active remote
  hardware experiment.
- No SSH connection, package installation, source deployment, model transfer,
  power-mode change, worker process change, or remote filesystem mutation was
  performed in Phase 08.

## Remaining risks

- A forcibly killed child cannot execute model unload itself. The registry will
  retain terminal/orphan evidence, but remote resource reconciliation after a
  hard kill remains an operator-visible recovery action rather than an unsafe
  blind cleanup.
- The filesystem lock protects job documents and journals across processes, but
  the Controller architecture still assumes one authoritative Dashboard service;
  Phase 08 does not add distributed leader election.
- Job event journals are append-only and currently have no automatic retention
  policy. The bounded API reads prevent unbounded response payloads, but a later
  maintenance policy may be useful for long research campaigns.
- Structured failure serialization is still incomplete by design and belongs to
  Phase 09.

## Deferred work

- Full structured failure records, deterministic diagnosis, evidence, and
  solution mapping belong to Phase 09 and were not started.
- Worker model catalog/distribution and later Dashboard UX phases were not
  started.
- Raspberry Pi hardware acceptance remains deferred until the user resumes it.

## Next phase readiness

- **READY for Phase 09, but Phase 09 has not been started.**
- Phase 08 stops after this report checkpoint and GitHub push.
