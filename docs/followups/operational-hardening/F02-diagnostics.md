# F02 — Bounded request lifecycle and resource diagnostics

## Status and scope

F02 implements a software path for retaining and correlating failure evidence.
It does **not** establish or fix the historical H01 timeout root cause. F03 is
not started. The authorized stopping point is F02, followed by a non-force push
of `codex/operational-observability-hardening`.

Base: F01 `0a17c9d7afe5ece53a3faf31180927bcae82f9bd`, with the integration merge
`e4ffb2cee254fab14f424914eb73f350cafbfdec` retained. Worktree:
`/Users/hajoonpark/자율설계/llm-cluster-benchmark-f01`.

No live Worker SSH/API, operational Dashboard GET, model operation, inference,
RPC, experiment/pilot/Sweep/Campaign, deployment, reboot, power change, or
hardware workflow dispatch was performed. All requests in validation used
in-process fake Workers or fixture-only localhost services.

## Implementation boundaries

- `cluster/infrastructure/request_diagnostics.py`: bounded optional lifecycle
  journal, strict identity/event allowlists and resource observation validation.
- `cluster/benchmark/diagnostics.py`: per-run correlation, pure diagnosis helper,
  explicit post-job collection CLI, and offline summaries.
- Existing `core`, `transport`, `persistence`, and `instrumentation` hooks attach
  identity, observable Controller boundaries and cached resource observations.
- Existing Worker app lifespan owns the writer thread; routes and inference
  report observable stages. Existing telemetry sampling adds API process RSS
  and start time, and reports cache age using the Worker's monotonic clock.
- Packaging validation found the integrated UI's font directory absent from the
  wheel. The package-data allowlist now includes those existing files and their
  licenses; the existing wheel test checks them and the two new Python modules.
  Frontend design and source assets are unchanged.

No engine, scheduler, independent daemon or Worker orchestration was added.
JobService, child execution, fencing, resource exclusivity, cancel, recovery,
quarantine and formal gate code is unchanged. Request concurrency, timeouts,
metric formulas, logical/physical accounting and the 19-column `requests.csv`
contract remain unchanged. Diagnostics are additive to `config.json`,
`responses.jsonl`, `measurements.jsonl`, and separate private artifacts.

## Identity and observable boundaries

Each physical transport invocation gets a fresh UUID and carries the existing
run, job, logical request, physical request, replica, scenario and warmup IDs.
Sweep/campaign attempt, cell and trial IDs are reused where present. Ordinary
runs receive a fresh diagnostic attempt UUID. No prompt-derived ID is used.
Broadcast replicas have distinct physical IDs; a new attempt/run cannot reuse
another attempt's evidence. Each event has UTC correlation time, host-local
monotonic time and recorder instance identity.

| Observer | Boundaries |
| --- | --- |
| Controller | submitted at transport invocation, transport start, response headers received, first token received, terminal completion/timeout/transport error/Worker error/incomplete stream |
| Controller cancellation | existing cancellation flag observed on the internal journal writer tick (100 ms idle wait) or terminal boundary; observation does not stop a socket or Worker |
| Worker route | accepted, input preparation started/completed, first yielded token, generation completed, exception, producer stream closed, consumer disconnect observed |
| Worker backend | inference lock wait/acquired, inference call started, lock released |

These are observations, not inferred native-runtime milestones. Input token
counting can itself wait inside the existing preparation path: a last stage of
`input_preparation_started` does not prove which internal lock/tokenizer action
stopped. Fake/old/custom backends without callbacks have UNKNOWN lock stages.
A yielded token is the first token observed at the existing backend API boundary.
`stream_closed` and `lock_released` do not prove durable ownership cleanup.

Native RPC retains only Controller transport observations. RPC Worker/native
process stages are unsupported and UNKNOWN. No RPC execution, new RPC
instrumentation protocol, or remote native log reader was added.

Durations are calculated only between observed boundaries in one host/instance.
Cross-host latency is unavailable because clock synchronization is not
established. Monotonic values from different hosts/instances are never subtracted.

## Storage, bounds and gaps

| Artifact | Location / bound |
| --- | --- |
| Controller journal | `<results>/<run_id>/diagnostics/controller/events.jsonl` plus `events.1.jsonl`–`events.3.jsonl`; 1 MiB each, 4 MiB total per run |
| Worker journal | `<worker-project>/.run/cluster/diagnostics/`, same fixed four-file, 4 MiB policy across requests/runs |
| Optional recovery artifacts | `<results>/<run_id>/diagnostics/collections/000.json`–`255.json`; each at most 128 KiB, 32 MiB per run |
| In-memory journal queue | at most 256 encoded events, each at most 4096 bytes; payload ceiling 1 MiB plus bounded Python bookkeeping |
| Active correlated requests | at most 1024 tracked entries; saturation makes counts incomplete rather than inventing a count |
| Request query | fixed journal files only, at most 32 events; supplementary collection reads at most 128 KiB |

The writer is an internal daemon **thread**, not an independent service. A
nonblocking process file lock permits one writer per journal directory. Rotation
and writes run outside inference locks. There is no per-token recording or fsync.
Short writes are completed; files use mode 0600 and newly created directories
0700. Request IDs never select a filesystem path or shell command. Collection
slots use exclusive creation so concurrent collectors cannot exceed the limit.
No collection replaces or deletes earlier collection evidence.

Buffer saturation, oversized events, write failures, unreadable/truncated files,
retention rotation, event truncation, missing records and pending flushes are
reported as diagnostic gaps. A later successful event carries prior buffer/write
loss. Across recorder instances, earlier in-memory counters may be lost:
`PRIOR_PROCESS_GAPS_UNKNOWN` explicitly preserves that uncertainty. Instance
changes identify a recorder restart; they do not prove a host reboot or OOM.
`UNFLUSHED_CRASH_LOSS_POSSIBLE` is always disclosed. This optional asynchronous
journal does not guarantee every enqueued event survives a sudden process/power
loss. Disk-full and writer-start failure do not turn a successful inference into
failure. Existing mandatory result-journal and cleanup failure policies are not
weakened.

## Resource observations and settings

`ExperimentConfig.diagnostics_mode` accepts `lifecycle` (default) or `off`.
The Worker setting `CLUSTER_DIAGNOSTICS_MODE=off` disables its lifecycle journal;
`lifecycle` is the default. Existing periodic telemetry remains independent of
this lifecycle switch. Controller config records schema version 1, mode, queue,
size and sampling policies; diagnostics mode is included in the normal config
fingerprint. Worker health advertises `request_diagnostics_v1` and journal mode.
Configured Controller mode alone is not evidence that a particular Worker is
recording. Old Workers remain compatible and may return no evidence.

The existing telemetry interval is reused: Pi default 10 seconds, other default
1 second. Each cached sample retains its actual interval and observation time.
`resource_diagnostics` adds:

- available/total system memory and used swap in bytes;
- Worker API process RSS in bytes, PID and process start time (Unix seconds);
- explicit availability, source, sample time, and worker-local cache age;
- unavailable separate model-process state (`SEPARATE_PROCESS_NOT_OBSERVED`).

Missing age/time, unavailable values, and age over `max(3 × interval, 3 s)` produce
null/unavailable, never measured zero. Known zero swap stays zero. Worker cache
age is measured locally; Controller clock skew is not used to judge freshness.
Process RSS is not added to system memory. No OOM inference is made from memory
pressure. Native in-process model allocations are included in API process RSS;
no separate native process identity is fabricated.

Journal queries also return timestamped counts for **correlated requests only**:
active requests, observed lock waiters and pending diagnostic events. They do not
represent requests without diagnostic headers. `activity_at_recovery` is explicitly
post-run/current observation, not a retroactive measurement for the failure.
Lifecycle events retain their nearby cached resources; later recovery does not
replace those historical samples.

## Recovery and summary workflow (instructions only)

The following commands were implemented and tested against fakes, not run against
actual Workers. Select the same Controller runtime, inventory and results used
by the original job. Use existing authentication configuration; do not paste
credentials into command arguments or reports.

Obtain the diagnostic UUID from that run's `responses.jsonl` or bounded Controller
journal. If the final response was never saved, the Controller journal can still
contain the transport identity. If retention removed that identity, recovery
fails closed instead of guessing a run/attempt binding.

```bash
# Offline; no Worker connection, no inventory read, no execution state mutation.
CLUSTER_RUNTIME_DIR="$F02_RUNTIME" .venv/bin/python -m cluster.benchmark.diagnostics \
  --results-root "$F02_RESULTS" --run-id "$F02_RUN_ID" \
  --diagnostic-id "$F02_DIAGNOSTIC_ID" --inventory "$F02_INVENTORY" \
  --jobs-dir "$F02_JOBS" --summary-only

# Explicit one-shot read after connectivity is restored and the job is terminal.
# Remove --summary-only to acquire one request's Worker journal evidence.
CLUSTER_RUNTIME_DIR="$F02_RUNTIME" .venv/bin/python -m cluster.benchmark.diagnostics \
  --results-root "$F02_RESULTS" --run-id "$F02_RUN_ID" \
  --diagnostic-id "$F02_DIAGNOSTIC_ID" --inventory "$F02_INVENTORY" \
  --jobs-dir "$F02_JOBS"
```

The second command requires a known durable job, validates the run/suite/attempt/
node/reservation binding, resolves the node from the selected inventory and uses
existing Worker token and historical ownership headers. It refuses nonterminal
jobs and redirects. The one GET uses a 3-second socket timeout (clamped to at most
5 seconds); there is no measurement polling, automatic retry or inference call.
Direct runs without durable jobs support offline summary only through this CLI.

Worker GET `/cluster/diagnostics/{diagnostic_id}` reuses existing API auth and
compares the full identity plus the saved owner/lease/fencing digest. Historical
read access after lease release does not grant execution authority or release a
current lease. Headerless legacy requests retain existing behavior. The new
endpoint cannot read arbitrary files, paths, shell output, or other run records.

Each collection stores status, UTC acquisition time, a sanitized bounded evidence
set, and a summary. Missing/old Worker endpoint, auth/transport failures, invalid
or mismatched evidence, and gaps remain explicit. A record not acquired is not
proof the Worker did not execute. Offline summaries can reuse the latest acquired
matching evidence; subsequent failed collections do not delete it.

The summary includes the last Controller/Worker stage and observation time,
Controller failure code, observed Worker outcome, same-host duration availability,
resources, cleanup status and missing evidence. Cleanup is separately bound to
the durable job's existing reservation state. Stream completion cannot change
UNKNOWN/UNCERTAIN cleanup to VERIFIED. A later Worker completion is supplementary
evidence; the original timeout, response ledger and performance summary are never
rewritten or recomputed. Legacy/off results report `NO_DIAGNOSTIC_RECORD`.

Observed terminal events are `CONFIRMED`; root cause defaults to
`INSUFFICIENT_EVIDENCE`. `SUPPORTED_HYPOTHESIS` is a separate human analysis level,
not automatically assigned merely because lock wait was the last event. This
implementation makes no new claims about P03's missing historical Worker logs.

## Validation

Validated on 2026-10-23 KST from the base above plus the source in this
F02 delivery. The final source/test/package file manifest is
`.artifacts/f02/source-delivery.json`; its canonical SHA-256 is
`d7afe941cbbeb6028119fb55dae621dfe175903d1a9a96f29a9b66cfbf1c4a6d`. The commit containing this report is the
F02 delivery commit; its exact Git hash and push result are reported in the final
handoff. No runtime/results/model files enter that commit.

| Executed check | Actual result / exit | Evidence under `.artifacts/f02/` |
| --- | --- | --- |
| Focused `unittest` (diagnostics, Worker, instrumentation, core, failures) | **88 passed**, exit 0; includes **28 new F02 tests** | `focused-delivery.log` |
| `.venv/bin/python -m unittest discover -s cluster/tests -v` | **759 passed**, exit 0 | `python-delivery.log` |
| `npm test` (syntax, fixtures, publication PNG, browser E2E) | **PASS**, **39 E2E passed**, exit 0 | `npm-test.log` |
| `.venv/bin/python -m compileall -q cluster scripts` | **PASS**, exit 0 | executed on final Python sources |
| `.venv/bin/python scripts/ci/validate_repository.py` | **PASS**, exit 0: 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts | `repository-delivery.log` |
| `bash -n` over all tracked shell scripts | **7 passed**, exit 0 | `shell-validation.log` |
| `.venv/bin/python -m unittest cluster.tests.test_packaging -v` | **3 passed**, exit 0; isolated install/import | `packaging-final.log` (also included in final full regression) |
| `PIP_NO_INDEX=1 ... pip wheel . --no-deps --no-build-isolation` | **PASS**, exit 0 | `wheel-delivery.log`, `wheels-delivery/` |
| Wheel content byte comparison | **30 files passed**, including fonts/licenses/new modules | `wheel-content.log` |
| `git diff --check` | **PASS**, exit 0 | final pre-stage inspection |

Full regression command uses `env -u CLUSTER_RUNTIME_DIR` with
`CLUSTER_INVENTORY=$PWD/.artifacts/f02/runtime/nodes.local.csv` (empty) and
`CLUSTER_RESULTS_DIR=$PWD/.artifacts/f02/results`. The default runtime root is
this **isolated worktree's** `.run`, not the operating checkout. Focused tests use
`CLUSTER_RUNTIME_DIR=$PWD/.artifacts/f02/runtime`. Browser tests use
`DASHBOARD_TEST_PORT=4273`, separate fixture runtime/results and
`TYPOGRAPHY_OUTPUT_DIR=$PWD/.artifacts/f02/typography` so historical captures remain
unchanged. ShellCheck was not separately run locally; hosted CI owns that check.

The final wheel SHA-256 is `7c28a87ee1d4478e3fe0df919fe131daf8538756d2e6431f5c14ebc5d223d555`.
Non-failing tool warnings: Starlette/httpx deprecation, and pip cache unavailable
in the sandbox (offline build succeeded without that cache).

The full regression covers existing P01/P02, Sweep/Campaign, ownership,
cancellation, quarantine, CSV, privacy and formal-gate tests. No historical test
counts are reused. The new F02 tests cover success linkage, timeout before and
after lock acquisition, reset after first token, no-final-response disconnect,
late completion, restart gaps, failed/recovered collections, replica/retry/run
mismatches, stale memory, disk full, rotation, saturated queues, private data
allowlists, uncertain cleanup, disjoint cancellation and unavailable writer.
They use events, fake monotonic values and short bounded waits, not a 600-second
hardware timeout.

The synthetic bound test enqueues 3000 lifecycle events into an eight-event
fixture queue, checks allocation peak below 2 MiB for that fixture, verifies
rotation and the configured disk ceiling. This is not a production RSS bound or
a hardware overhead measurement. Instrumentation must be revalidated before
future formal research; no research lock or formal gate was automatically changed.

Earlier validation attempts remain in `.artifacts/f02/`: the first full run had
three auth-fixture failures caused by injecting `CLUSTER_RUNTIME_DIR`, which
intentionally overrides the tests' patched default paths. All three passed when
that override was removed; final runs use this isolated worktree's own `.run`
with empty inventory and separate result paths. An intermediate import-placement
error was caught by focused tests and corrected. The missing wheel font files
were a packaging defect, fixed through package metadata and the existing test.

## Operational preservation and phase boundary

The operating checkout remains
`/Users/hajoonpark/자율설계/llm-cluster-benchmark`, branch
`codex/pi4-model-benchmark-20261014`, HEAD
`e81e2b5ce84d9f9bc338e0da131f115d9ad8166e`. All 11 pre-existing dirty/untracked source
file hashes match the F01 snapshot. Local process inspection still finds
Dashboard PID 49634, started 2026-10-19 12:18:51 KST. No operational API was queried.
Its loaded Python/frontend version, active job status and current Worker state
are **UNKNOWN** in this phase; a running PID does not prove code identity.

| Dimension | F02 status |
| --- | --- |
| Software implementation | Complete for F02; F03 not started |
| Automated validation | See executed evidence above |
| Git / CI delivery | Feature-only commit/push; exact delivery hash and observed CI state in final response |
| Deployment | Not performed; push does not deploy |
| Hardware acceptance | Not performed |
| Formal eligibility | Existing closed gate and research locks preserved; no reapproval |

Using the new Worker journal operationally requires deployment of the changed
Worker Python code and a Worker restart under a separately authorized window.
The Controller backend also requires restart to load the hooks. No new packages,
monitoring agent, model download or UI build is required. Check active jobs and
ownership before any future restart; retain results, inventories, models and
runtime state. The existing acceptance/deployment documents remain applicable.
F03's detailed rollout, rollback and UI handoff are not claimed here.

**STOP after F02 commit/push.**
