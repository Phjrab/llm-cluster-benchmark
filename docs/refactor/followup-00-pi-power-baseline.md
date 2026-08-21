# Follow-up 00 — Pi Power Baseline

Date: 2026-08-21 (Asia/Seoul)
Scope: read-only baseline and design map only. No product code, remote device,
model, process, package, benchmark, RPC, or Worker state was changed.

## Git baseline

| Item | Value |
|---|---|
| Repository | \`/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark\` |
| Branch | \`codex/mac-control-plane\` |
| Commit before work | \`7680841 feat(nodes): group workers by platform without a fixed limit\` |
| Working tree before work | clean |
| Authoritative remote | \`origin\` → \`https://github.com/Phjrab/llm-cluster-benchmark.git\` |
| Legacy Jetson remote | fetch-only; push URL is \`DISABLED\` |

The original \`MASTER_SPEC.md\`, Follow-up master/baseline documents, original
baseline/reconciliation, and Phase 01–15 reports were read before inspection.
The Phase 15 architecture remains the baseline: the Mac is Controller-only;
Jetson and Raspberry Pi machines are inference Workers only. Pi and multi-worker
acceptance remain pending; this phase did not contact any hardware.

## Existing telemetry flow

| Item | Actual symbol / file | Current behavior | Extension point |
|---|---|---|---|
| Generic telemetry | \`GenericPsutilTelemetry\`, \`cluster/worker/telemetry.py\` | Best-effort psutil CPU/memory/disk/network/temperature; nullable GPU/power. | Define non-Pi unavailable power-integrity result without changing null watts. |
| Jetson telemetry | \`JetsonTelemetry\` | jtop background enrichment; failure falls back to psutil and degrades telemetry only. | Keep jtop/nvpmodel semantics separate from Pi power integrity. |
| Pi telemetry | \`RaspberryPiTelemetry\` | Inherits generic telemetry; Pi GPU/power stay \`None\`. | Add dedicated Pi power probe/serialization here. |
| Existing \`vcgencmd\` use | \`temperature_snapshot()\` | Fixed argv \`vcgencmd measure_temp\`, no shell/sudo, 2s timeout, suppressed stderr. | \`get_throttled\` is not currently used and must retain these safety properties. |
| Provider selection | \`TelemetryService.for_platform()\` | Selects generic/Jetson/Pi provider. | Add provider/service \`power_integrity()\` returning typed, normalized data. |
| Lifecycle | \`cluster.worker.app.create_app()\` lifespan | Starts/stops telemetry around ASGI lifetime. | Probe must not require a new background daemon. |

Telemetry returns dictionaries at the Worker boundary. Follow-up 01 should keep
the \`vcgencmd\` parser/status objects typed and pure in a domain module, then
serialize them at the existing telemetry/route boundary.

### Readiness decision

\`power_integrity\` is an independent additive axis. It does not redefine
\`inference_ready\`, \`telemetry_ready\`, \`telemetry_degraded\`, backend readiness,
or environment readiness. This matches the existing split between
\`InferenceBackend.readiness()\` and \`TelemetryService.status()\`.

## Worker API contract

The compatibility-critical route is \`GET /cluster/health\` in
\`cluster/worker/routes.py\`. Its current fields are:

| Field | Existing meaning | Required power behavior |
|---|---|---|
| \`ok\` | Worker route replied. | Never false solely due to a power bit/history/probe failure. |
| \`node\`, \`profile\` | Identity/platform/runtime profile. | Unchanged. |
| \`capabilities.inference_ready\` | Backend can serve inference. | Never derived from power integrity. |
| \`capabilities.telemetry_ready/degraded\` | Existing telemetry-provider health/fallback. | Do not overload with power state. |
| \`backend_verified\`, \`gpu_offload\`, models/current | Runtime/model capability. | Unchanged. |
| \`metrics\` | Latest telemetry dict. | Keep existing nullable Pi GPU/power metrics. |

**Exact insertion point:** add top-level \`power_integrity\` beside \`metrics\` in
\`/cluster/health\`, not inside \`capabilities\` or \`metrics.power\`. It should use
the Follow-up contract: availability, status
(\`ok|history_warning|active_degraded|unavailable\`), \`blocking:false\`, source,
raw integer/hex, current/history bit maps, reason codes, and observed time.

Controller normalization for an older Worker that lacks this field is a
non-blocking \`{status: "unavailable", blocking: false}\`. Do not change the
existing \`telemetry_version >= 2\` compatibility check in \`probe_node()\`.

## Controller aggregation

\`cluster.dashboard.services.probe_node(node)\` calls
\`clusterctl.request_json(<api>/cluster/health, timeout=4.0)\`, validates Worker
identity/schema, then keeps \`metrics\`, \`profile\`, \`capabilities\`, model IDs,
and current-model data in the status record. Failure uses existing read-only SSH
discovery and preserves the current offline meaning.

\`StatusMonitor.refresh_now()\` polls all registered nodes concurrently (maximum
32), updates its in-memory snapshot, publishes \`cluster_status\` on \`system\`,
and repeats every five seconds. \`DashboardFacade.bootstrap()\` and \`/api/status\`
expose that monitor state.

Follow-up 02 should normalize the new field here. The monitor’s whole-snapshot
comparison is not power-warning deduplication: normal metric churn changes the
snapshot each poll. Add a separate per-node transition cache keyed by
availability, status, raw value, and reason-code set; emit only transitions.

## Preflight boundary

\`validate_experiment_environment()\` requires a fresh environment report,
verified backend, online API, platform agreement, and required models.
\`DashboardFacade.start_experiment()\` is the final admission boundary before
definition persistence and durable job creation; it resolves RPC coordinator,
validates strategy/environment, checks Pi GPU layers and verified models, then
calls \`experiments.start()\`.

\`test_models.py\` explicitly proves degraded telemetry is non-blocking. Power
must be treated identically: a fresh informational snapshot may be collected
but cannot reject a job, disable Run, or alter a job/run status by itself.

\`BenchmarkRunner.run()\` in \`cluster/benchmark/core.py\` is the right child-process
execution boundary for run-local observations: it owns \`run_started\`, warmup,
scenario execution, summary, and cleanup. A small injected observer should take
preflight, pre-warmup, bounded measurement-period, and postflight snapshots
without changing plans, executor concurrency, warmup exclusion, or metrics.

## Event flow

\`cluster.domain.events.ClusterEvent\` / \`EventChannel\` provide typed,
wire-compatible \`system\`, \`node_ops\`, and \`experiment\` events. \`EventBus\` owns
SSE publication; \`RunPersistence.emit()\` writes run events with the experiment
channel; \`SuiteRunner\` preserves it; browser \`eventChannel()\` routes it.

| Observation | Channel | Proposed durable rule |
|---|---|---|
| Normal status-poll transition | \`system\` | Transition only; no event for every poll. |
| Explicit future diagnostic | \`node_ops\` | Read-only action output only. |
| Run transition / final aggregate | \`experiment\` | State/availability transition and final aggregate only. |

Current benchmark warnings are \`List[str]\` and \`warning\` events contain a
string message. Follow-up 02 needs a narrow run-local aggregator and monitor
transition tracker keyed by \`(node, status, availability, raw_hex, reason_codes)\`
to prevent repeated historical warnings.

## Result persistence

\`FilesystemRunRepository\` writes private run directories and preserves this
schema-v2 layout:

\`\`\`text
config.json
events.jsonl
requests.csv       # fixed 19-column, metric-only compatibility contract
responses.jsonl
summary.json
\`\`\`

\`RunPersistence\` fsyncs events/responses; \`BenchmarkRunner.run()\` currently
adds string \`warnings\`, topology, actual model config, parameters, and scenario
summaries. There is no measurement-quality or power metadata.

**Storage decision:** retain schema version \`2\`, keep \`warnings: list[str]\`,
and add nested summary fields rather than put power data in request rows or
convert warnings to objects:

\`\`\`json
{
  "measurement_quality": "warning",
  "measurement_quality_reasons": ["PI_POWER_HISTORY"],
  "power_integrity": {
    "pi-01": {
      "preflight": {"status": "history_warning", "raw_hex": "0x50000"},
      "measurement": {"sample_count": 0, "valid_sample_count": 0,
        "unavailable_sample_count": 0, "active_warning_samples": 0,
        "worst_status": "history_warning"},
      "postflight": {"status": "history_warning", "raw_hex": "0x50000"}
    }
  }
}
\`\`\`

One human-readable warning string per node may be appended after deduplication;
the nested metadata carries codes/evidence. Missing power metadata in legacy
results renders as quality \`unknown\`, never a failure.

The reducer must follow the Follow-up precedence: observed active condition or
a history bit newly present after preflight → \`degraded\`; history only →
\`warning\`; all unavailable → \`unknown\`; incomplete observations plus clean
samples → \`warning\`/\`PI_POWER_OBSERVATION_INCOMPLETE\`; otherwise \`clean\`.
Quality never changes the independent run status.

## Dashboard modules

| Surface | Current location | Follow-up placement |
|---|---|---|
| Worker cards/platform tabs | \`renderNodes()\` in \`static/app.js\` | Display Power Quality; never remove node selection or disable Run. |
| Node detail | \`renderNodeDetail()\` in \`static/app.js\` | Display state, raw hex, current/history explanation, timestamp, availability, and non-blocking text near Thermal/Power. |
| Presentation helpers | \`static/js/utils.js\` | Add a separate power-quality presentation helper; do not reuse run-status semantics. |
| Experiment terminal | \`eventChannel()\`, \`connectEvents()\`, \`console.js\` | Experiment transitions only; diagnostics stay Node Operations. |
| Result table/highlight/charts | \`renderRuns()\` and chart/export helpers in \`static/app.js\` | Show independent Run Status and Measurement Quality badges. |
| Response/failure inspector | \`static/js/results.js\` | Render additive selected-run quality details without reconstructing answers. |
| DOM/styles | \`templates/index.html\`, \`static/styles.css\` | Add only labelled placeholders/styles; no framework replacement. |

No new browser module is needed. The existing split is sufficient. UI must show
icon, visible text, explanation, raw value, and timestamp rather than color
alone.

## Test baseline

All checks were local and read-only. No SSH/deploy, Worker start/stop, package
operation, model operation, remote \`vcgencmd\`, benchmark, or RPC was run.

| Command | Result |
|---|---|
| \`.venv/bin/python -m unittest discover -s cluster/tests -q\` | 241 passed, 0 failed, 0 skipped with permitted local loopback. Initial sandbox run had two expected \`PermissionError\` cases for test-only localhost binds. |
| \`python3 -m compileall -q cluster\` | passed |
| \`node --check cluster/dashboard/static/app.js\` | passed |
| \`node cluster/tests/test_dashboard_exports.js\` | passed (\`dashboard export fixtures: OK\`) |
| \`bash -n\` over all project shell scripts | 12 scripts passed |
| Controller CLI, benchmark runner, durable-job process \`--help\` | passed |
| JSON parse of every \`cluster/config/*.json\` | passed |
| \`git diff --check\` before report creation | passed |

Relevant existing boundaries: \`test_worker_runtime.py\` (provider/health/lifespan),
\`test_models.py\` and \`test_dashboard_backend.py\` (non-blocking telemetry and
preflight), \`test_events.py\` (channels), \`test_benchmark_core.py\`/\`test_core.py\`
(metrics/schema), \`test_results_failures.py\`/\`test_storage.py\` (durable
artifacts), \`test_durable_jobs.py\` (recovery), \`test_dashboard_exports.js\`
(frontend), and Phase 14 security/process/packaging suites.

## Exact files/symbols to change in Follow-up 01–04

| Follow-up | Actual modification candidates | Protected public contract | Key tests |
|---:|---|---|---|
| 01 | New \`cluster/domain/power.py\`; \`domain/__init__.py\`; \`worker/telemetry.py\`; \`worker/routes.py\`; Worker tests | Existing Worker health paths/fields; readiness independence; no shell/sudo/mutation | Bit fixtures (\`0x0\`, \`0x50000\`, active/history, malformed/unavailable); health additive-field tests. |
| 02 | \`benchmark/core.py\`, \`benchmark/persistence.py\`, narrow observer helper; \`dashboard/services.py\`; result/event tests | schema v2, 19-column CSV, warning strings, suite/job state, benchmark math | Quality precedence, snapshot/dedup, transition-only events, legacy summary fallback, no power admission block. |
| 03 | \`dashboard/static/app.js\`, \`static/js/utils.js\`, \`static/js/results.js\`, template/styles, JS fixture | API/SSE compatibility, Controller exclusion, chart/export semantics | Node/result presentation, legacy unknown, Run remains enabled, terminal routing. |
| 04 | Focused \`cluster/tests/\` and report only | All Phase 14 contracts | Full Python/compile/JS/shell/CLI/JSON gates; fixed-argv/no-stderr/no-sudo checks. |

## Compatibility risks

- \`warnings\` is string-based today; converting it to objects would break joins
  and frontend callers. Keep structured power detail additive.
- \`probe_node()\` must tolerate a missing new field from older Workers.
- Snapshot churn is not warning deduplication.
- Nullable \`metrics.power_w\` is not a power-health signal.
- Child-process measurements must not trust only the Dashboard’s five-second
  cache.
- Historical result readers/export must interpret absent fields as \`unknown\`.

## Hardware acceptance prerequisites

Follow-up 00 performed no hardware work. Before Follow-up 05, the Pi needs the
existing private-LAN/key-authenticated Worker registration, deployed source,
project-local environment, verified OpenBLAS backend, Worker API, checksum-valid
model, load/unload, and single-node benchmark path. \`vcgencmd\` can be absent;
that is a valid non-blocking unavailable observation. Acceptance records actual
bitmasks and actual Worker/API/inference behavior only—never adapter branding,
clock/config changes, reboot, or legacy-workspace deletion.

## Phase mapping

| Follow-up | Actual modification candidates | Protected public contract | Key test / acceptance |
|---:|---|---|---|
| 01 | Domain parser, Pi telemetry, Worker health | Additive health/readiness independence | Unit and Worker-route contracts |
| 02 | Observation/dedup/events/summary | schema v2, CSV, benchmark math | Pure quality/result tests |
| 03 | Nodes/Experiment/Results UI | API/SSE and chart compatibility | JS/export fixtures |
| 04 | Regression/security/report | Phase 14 boundaries | Full local gates |
| 05 | Hardware only/report: Pi single-node | Worker/model/inference behavior | Functional pass plus recorded quality |
| 06 | Hardware only/report: multi-worker | Strategy/metric/schema invariants | Replicated/broadcast/sweep acceptance |
| 07 | Hardware only/report: RPC/recovery | RPC cleanup/auth and durable jobs | RPC/recovery acceptance |

## Follow-up 00 Definition of Done

- [x] Actual telemetry/API/preflight/event/result/UI map.
- [x] Additive insertion point and readiness independence decision.
- [x] Sampling, persistence, and dedup boundaries.
- [x] Actual Follow-up 01–07 mapping and current test baseline.
- [x] No product/remote/hardware change.
- [ ] Commit and push this report after final diff review.

FOLLOWUP 01 has not been started.
