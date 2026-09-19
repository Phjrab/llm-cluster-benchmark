# S05 — Worker reservations and isolated multi-job control

2026-09-19 (Asia/Seoul). Workstream: WS-S05.

S05 — Software COMPLETE within the Controller/Worker ownership and durable job
scope. Hardware: NOT RUN — user-operated. CI: NOT CHECKED. S06 has not been
started.

## Authorization and baseline

The user explicitly requested the next phase after the S04 stop. Work started
from clean commit `504781a91da97560996ef2dd0a00c225de5a8393` on
`codex/current-source-pilot-v6`, matching its origin branch. The integrated
Master Spec, Data Contracts, Integration Notes, S00–S04 reports,
CONTRIBUTING.md, JobService, child/suite lifecycle, Dashboard actions and
snapshot routes, campaign adapter, Worker mutation routes, process identity
guard and storage locking were reviewed. No applicable AGENTS.md exists.

No research lock, formal inventory, runtime inventory, model, result, credential
or approval artifact was changed. No real Worker, SSH connection, model
download/load, inference or native RPC process was used.

## Implemented behavior

- One private Controller registry now serializes every mutation with `flock`
  and atomic replacement. A reservation records the stable Worker IDs,
  canonical physical hosts/endpoints, host-scoped RPC ports, owner job and
  attempt, fencing epoch, hold reason, state, heartbeat and evidence. Corrupt
  registries fail closed.
- Reservation of the complete Worker set is atomic. Stable-ID, physical-host,
  API endpoint and applicable RPC-port overlap rejects the request without a
  partial hold. RPC ports are keyed by host, so unrelated hosts do not share a
  false global port mutex.
- `max_parallel_jobs=1` remains the request default. Opt-in value 2 is admitted
  only when both active and incoming jobs opted in, their resources are
  disjoint, and every selected Worker advertises `resource_ownership_v1`.
  Values above 2 are rejected. Legacy Workers remain usable only through the
  exclusive single-job behavior and report an upgrade requirement for parallel
  admission.
- Formal campaign jobs acquire a Controller-global exclusive reservation only
  after their existing formal gates reach backend start. Formal readiness by
  itself holds nothing. A formal-versus-ordinary start race is decided by the
  same atomic registry.
- JobService now lists all active jobs and supports explicit job lookup,
  cancellation, pause and resume. The legacy no-ID cancel still works for one
  active job and returns `AMBIGUOUS_ACTIVE_JOB` when more than one is active.
  Watcher/SSE and Dashboard snapshots include the active-job set, while the
  legacy single `active` field remains additive-compatible.
- Duplicate Start with the same job document is idempotent and cannot spawn a
  second child. A mismatched reuse of a job ID is rejected. Pre-spawn failure
  releases a reservation with local evidence; loss after spawn or an uncertain
  process identity quarantines it.
- The child heartbeats its exact lease, holds it through model preparation,
  suite execution, unload/RPC cleanup and configured resource cooldown, and
  releases only after cleanup evidence. A finished suite without an
  authoritative release becomes orphaned and requires reconciliation.
- Heartbeat age never releases a resource. Stale heartbeat, process loss,
  cleanup failure and unknown state become `quarantined`. Explicit reconcile
  requires the exact lease/fencing epoch and operator evidence.
- Worker health advertises an additive ownership capability. Ownership is
  durable on the Worker and guards model load/unload, exact-input preparation,
  inference, model verify/delete/install. An old owner cannot mutate or release
  a newer owner. Existing inference slot=1 behavior remains unchanged.
- Dashboard control actions reserve the same resources and heartbeat while
  running. Model mutation, setup/sync, lifecycle, environment and power actions
  therefore conflict with overlapping jobs. Direct CLI mutations inspect the
  same registry before remote work; passive inventory/status/discovery and
  power reads remain available.
- Node update/rename/delete and campaign cancellation inspect all active jobs;
  campaign cancellation now targets its exact durable job.

## Fake/offline concurrency and failure coverage

The new process-based tests start independent Python processes against one
temporary registry. They prove A+B versus B+C admits exactly one side, A+B
versus C+D admits both under cap 2, and formal versus ordinary admits exactly
one side. Other fake tests cover physical aliases, host-scoped RPC keys,
default/opt-in admission, targeted A cancellation while B stays running,
ambiguous legacy cancellation, pause/resume targeting, duplicate Start,
pre-spawn failure, post-spawn crash, stale owner replay, heartbeat loss,
registry corruption, RPC cleanup uncertainty, cooldown retention, explicit
reconcile, model-delete conflict and CLI versus Dashboard conflict.

Existing exact `ProcessIdentity` and launcher tests continue to cover PID reuse
and tampered PID records without signalling an unrelated process. No test opens
a Worker connection or native RPC port; temporary child and Controller launcher
tests use injected processes or localhost only.

## Actual validation

| Check | Actual result |
|---|---|
| New S05 resource suite | **16 tests, OK, 1.084s** |
| Focused resource/job/Worker/Dashboard/campaign suite | **106 tests, OK, 7.108s** |
| Full Python discovery in restricted sandbox | **642 tests run; 640 passed, 2 localhost-bind environment errors** |
| Focused localhost Controller launcher outside that restriction | **6 tests, OK, 1.390s** |
| `compileall -q cluster scripts` | exit 0 |
| repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| offline wheel build | PASS; SHA-256 `66bb2940f06f1a8ab689a9d2d7bf6f47ae4629f2d9e7d66e003b81dc7aebafb5` |
| bash syntax and diff whitespace | exit 0 |
| npm syntax, fixtures, publication PNG and Playwright | PASS; **3 browser tests passed** |
| ShellCheck | NOT RUN — not installed |

The unrestricted full-Python rerun was rejected by automatic approval review
because that broad test command includes mocked SSH/RPC-oriented cases and the
user prohibited real Worker/RPC activity. The materially narrower localhost
launcher module was approved and passed; the other 636 non-launcher tests had
already passed in the restricted full run. The first npm run was blocked by
Chromium's macOS Mach-port restriction; the same npm suite passed outside that
restriction. Starlette emitted its existing TestClient/httpx deprecation
warning.

## Compatibility, safety and next boundary

The job/result/suite schemas remain readable and existing `active` and no-ID
cancel behavior remain available in their unambiguous legacy case. The resource
registry and Worker ownership documents are separate versioned artifacts.
Reservation/fencing values coordinate ownership and are not authentication;
the existing Worker API token middleware still protects the HTTP boundary.

The Dashboard builder and durable sweep supervisor are not implemented here.
S06 will consume these reservations while scheduling trials and attempts. S07
may add a dedicated authenticated operator API for listing and reconciling
quarantined leases; S05 provides the application operation and evidence
contract. Hardware ownership capability, crash recovery and performance remain
unverified until an operator runs the later manual phase.

Only S05 source, fake/offline tests and this report are included. Commit and
normal feature-branch push results are reported after Git completes.

STOPPED after S05. S06 has not been started. Hardware: NOT RUN. CI: NOT CHECKED.
