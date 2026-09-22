# PR draft

## Title

Add durable context/model/RPC sweeps and harden formal execution safety

## Base and head

- Base: `main` (`eebb8f134ac2fe251f5e9dd5a723bb653db9a840` at P04 review)
- Head: `codex/current-source-pilot-v6`

## Description

The Controller could execute individual experiments, but it did not have a typed,
bounded way to plan and preserve context/model/topology sweeps. Follow-up review
also found that Sweep progress depended on browser-triggered detail GET requests
and that formal eligibility compared observed source trees with each other without
directly comparing them to the locked expected tree.

This change adds the complete exploratory Sweep path while preserving the existing
benchmark, JobService, Worker ownership and formal Campaign engines. A saved and
approved Sweep now progresses through later Trials without browser polling while
the Dashboard server remains running. Sweep GET/list/results/events/SSE remain
read-only. Formal execution now fails closed when a selected Worker's observed
source tree is missing, invalid, unverified or different from the expected tree in
the runtime lock.

### Sweep, context, model and RPC implementation

- Adds immutable typed Sweep specifications, bounded grid/OAT planning and exact
  workload accounting.
- Preserves requested/resolved/factory/effective context and runtime parameters,
  prepared-input identity and existing single inference-slot behavior.
- Resolves exact catalog/model identity, installation, runtime compatibility and
  formal approval as separate states.
- Adds explicit RPC topology profiles with Worker-selected coordinators, stable
  Worker IDs, pinned runtime identity and targeted session cleanup.
- Adds durable Worker reservations, fencing, physical-host overlap prevention,
  targeted cancellation and quarantine on uncertain cleanup.
- Adds the durable Sweep supervisor, token-protected lifecycle API, Dashboard
  builder/control/results UI, event replay, manual retry and privacy-aware exports.
- Keeps default sequential execution and permits at most two explicitly requested
  disjoint jobs.

### Reliability and model follow-ups

- Fails energy integration closed when sampled coverage has missing or oversized
  gaps instead of interpolating unsupported energy.
- Keeps Raspberry Pi warnings nonblocking for ordinary exploratory work while
  preserving the stricter fresh formal preflight policy.
- Connects existing formal Campaign Start/Pause/Resume/Cancel/Retry controls without
  adding another scheduler or permitting the client to open the formal gate.
- Supports exact ordered multipart GGUF artifact sets through the existing model
  install engine.
- Separates installed, compatible, runtime-observed and formally approved model
  evidence in API and Dashboard projections.
- Retains the existing 19-column `requests.csv`, additive result readers and legacy
  compatibility exports.

### Execution-stability follow-ups

- Runs approved Sweep progress from a Dashboard-owned background loop so closing
  the browser does not stop later Trial dispatch.
- Recovers only previously authorized `ready`/`running` durable Sweep state after
  Dashboard restart and does not auto-start draft, paused or terminal executions.
- Leaves detached child jobs alive on Dashboard shutdown; automatic new dispatch
  while the Dashboard server is stopped remains outside the supported scope.
- Keeps Sweep GET/list/results/events/SSE free of claims, process creation,
  model/RPC lifecycle actions and execution approval.
- Rechecks the formal gate before every new Campaign cell.
- Directly compares each fresh observed source tree with the selected Worker's
  locked expected source tree while retaining commit/runtime/RPC checks.

### Before and after

Before, finishing Trial 1 without an active browser poll left Trial 2 pending until
a detail GET happened. After Start, the Dashboard-owned loop observes the same
durable state machine and starts Trial 2 without another browser request.

Before, matching commits and matching but wrong Worker source-tree hashes could
avoid a direct expected-tree comparison. After this change, even one Worker—or all
Workers sharing the same wrong tree—produces `SOURCE_FINGERPRINT_MISMATCH` and
blocks formal eligibility.

## Safety and compatibility

- The Mac remains Controller-only and never becomes an inference or RPC participant.
- RPC coordinators remain selected Workers.
- Strategy meanings and logical/physical request accounting are unchanged.
- Existing child-process identity, resource ownership, fencing, cleanup and
  quarantine paths are reused.
- Existing results, events, measurements and responses are not rewritten.
- Missing prompt/response content is never reconstructed for exports.
- No model binary, credential, private runtime, inventory or raw research result is
  included in this branch.

## Validation

P04 validated a clean archive of head `0814e77acfafd97bd298bc7431581b613deb8ff3`:

- focused Sweep/Campaign/source/model/RPC/resource suite: **177 tests passed**;
- full Python discovery: **718 tests passed**;
- `npm test`: syntax, fixtures, publication PNG and **8 Playwright tests passed**;
- Python compile check: **passed**;
- repository JSON/lock/workflow/security validation: **passed**;
- `bash -n`: **7 scripts passed**;
- packaging regression: **3 tests passed**;
- offline wheel build and required Sweep asset inspection: **passed**;
- diff whitespace check: **passed**.

The Controller `.venv` does not contain `setuptools.build_meta`, so its direct
no-isolation wheel command failed at the build-backend import. The same source
built with the host's existing offline Python build environment, and the dedicated
isolated-install packaging tests passed. Hosted Required CI installs the declared
build tooling and runs the authoritative Linux wheel gate.

## Readiness

- **Software:** complete for the described Sweep/context/model/RPC and P01/P02
  contracts, subject to Required CI on the final PR head.
- **Hardware:** not accepted by P04. A 12-step manual procedure is provided in
  `docs/manual/execution-driver-and-sweep-acceptance.md`.
- **Measurement:** no new measurement was performed. H01/v7 remains a Pi-only,
  non-freezable diagnostic record.
- **Formal:** the checked-in gate remains closed with
  `formal_execution_allowed=false`; no lock was regenerated or approved.

## Reviewer guide

1. Review typed planning and workload bounds in the Sweep domain/planner.
2. Review Worker reservation, fencing and durable supervisor transitions.
3. Review the token-protected API and read-only query boundary.
4. Review Dashboard builder/control/results and privacy-aware exports.
5. Review Campaign gate enforcement and direct locked source-tree comparison.
6. Review integration, fault-injection, packaging and browser tests.
7. Confirm that research locks, real inventory, model binaries and private results
   are absent from the final P04 commit.
