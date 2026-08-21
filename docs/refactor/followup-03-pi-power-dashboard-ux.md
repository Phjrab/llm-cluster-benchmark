# Follow-up 03 — Pi Power Dashboard UX

Date: 2026-08-21
Phase: FOLLOWUP 03 only
Baseline: `7de648e feat(results): record non-blocking power measurement quality`

## Scope

Added a presentation-only Raspberry Pi power-integrity surface to the existing
Dashboard. The implementation consumes the additive Worker-health and durable
run-summary fields from Follow-up 01–02; it does not add a route, change a
payload, alter benchmark scheduling, change admission policy, or contact any
hardware.

## UI surfaces

### Worker cards and details

- Raspberry Pi Worker cards now show a separate text-and-icon Power Quality
  pill: `NORMAL`, `WARNING`, `DEGRADED`, or `UNKNOWN`.
- The pill is independent from inference, environment, telemetry, online, and
  run-status pills. Jetson cards omit the Raspberry Pi-specific pill.
- The Worker detail dialog has a dedicated **Raspberry Pi Power Integrity**
  panel with current conditions, historical conditions, raw value, and observed
  timestamp. Each state includes text, icon, tooltip, and `aria-label`; color
  alone is not the signal.
- Missing, null, partial, raw-only, and unknown-future payloads normalize to a
  safe `UNKNOWN` display. They never assume node names are strings.

### Exact `0x50000` wording

For a historical `0x50000` condition the detail panel states:

> `0x50000` 같은 과거 저전압·스로틀 기록입니다. 현재 상태가 감지된 것은 아니며 실험은 계속할 수 있습니다.

The card labels this as `POWER WARNING · HISTORY`; it does **not** call it a
current undervoltage, current throttling, offline Worker, or not-ready Worker.
An active condition is separately labelled `POWER DEGRADED · ACTIVE` and says
that it can affect result interpretation while the experiment continues.

### Experiment

- A selected-Worker banner summarizes active, historical, and unavailable Pi
  observations. It explicitly says this is measurement-quality context and
  does not block a run.
- Power warnings do not disable the Run button, clear node selection, add a
  confirmation step, or replace normal blocking failures such as a missing
  model or offline Worker.
- Additive warnings returned by `POST /api/experiments` are shown in the banner
  and appended once to the bounded experiment terminal. Warning keys are
  deduplicated by Worker, code, and message.
- Only `channel=experiment` power transition/final-quality events write to the
  experiment terminal. The Controller's system-channel polling transitions are
  intentionally not routed to either terminal.

### Results

- The raw response inspector now includes a **Measurement Environment** section
  before prompt/response records.
- It renders run status and measurement quality as separate palettes, allowing
  combinations such as `COMPLETED` plus `DEGRADED`.
- Per-Pi Worker rows show before/during/after observations, valid and total
  sample counts, active-warning sample count, and that Worker's quality. This
  preserves mixed multi-node outcomes without changing charts or throughput.
- Failed, partial, and cancelled runs can show their final power context, but
  the UI explicitly says it does not establish power as the cause of failure.
- Legacy summaries without the additive fields render `NOT RECORDED`; they are
  never misrepresented as clean.

## Module boundary

- Added `cluster/dashboard/static/js/power.js` for bounded payload
  normalization, quality presentation, current/history language, warning
  deduplication, badges, and measurement-environment rendering.
- Kept `cluster/dashboard/static/app.js` as the compatibility coordinator for
  selection, the existing terminal, and existing API calls.
- Extended `cluster/dashboard/static/js/results.js` only to place the durable
  measurement-environment renderer before existing response/failure rendering.
- Updated the template, stylesheet, and existing JS export fixture. No Python
  Dashboard, Worker, benchmark, result-persistence, or API code changed.

## Compatibility

- Existing Worker health and Dashboard route contracts are unchanged.
- Existing result records, `requests.csv`, `responses.jsonl`, charts, model
  library, response viewer, and failure viewer remain intact.
- No inference readiness, telemetry readiness, environment readiness,
  preflight/admission, run status, or result quality algorithm is changed by
  this UI layer.
- Static asset version query strings were advanced to ensure the new UI is not
  combined with a cached older coordinator or stylesheet.

## Tests

Focused Dashboard fixture additions cover:

- historical `0x50000` normalization with no current condition;
- active, unavailable, raw-only, future/missing, and non-Pi normalization;
- accessible power pill output;
- selected-node banner handling a non-string name safely;
- duplicate start-warning suppression;
- completed/failed run measurement-environment rendering and legacy
  `NOT RECORDED` fallback.

Final local gates:

- `274` Python tests passed with actual local loopback permissions.
- Python `compileall` passed.
- `node --check` passed for `app.js` and every `static/js/*.js` module.
- `node cluster/tests/test_dashboard_exports.js` passed.
- all repository shell syntax checks passed.
- Controller/clusterctl/runner CLI compatibility checks and configuration JSON
  parsing passed.
- `git diff --check` passed.

## Visual QA and hardware acceptance

Visual browser QA was not completed. A pre-existing tracked Dashboard PID was
unhealthy; restarting it would require terminating that existing process, so it
was intentionally left untouched. Static rendering is covered by the Dashboard
fixture instead.

Hardware acceptance was **not run**. No SSH, remote Worker lifecycle,
`vcgencmd`, model transfer, inference, benchmark, RPC, package installation,
or remote filesystem mutation was performed.

## Remaining risks

- Power status is observational and intentionally not sampled at high
  frequency; the UI cannot reveal a transient that was not captured by the
  bounded Follow-up 02 snapshots.
- A browser visual pass should be repeated once the existing local Dashboard
  lifecycle is deliberately recovered by its owner.
- Optional result filtering by power quality remains deferred; the dashboard
  always preserves all run records and shows the recorded quality context.

## Definition of Done

- [x] Worker Power Quality badge
- [x] current/history distinction
- [x] correct `0x50000` wording
- [x] active degraded and unavailable wording
- [x] non-blocking experiment warning banner
- [x] Run remains independent from power warning
- [x] existing blockers remain authoritative
- [x] transition-only experiment-terminal output
- [x] Results Measurement Environment
- [x] completed plus degraded-quality representation
- [x] multi-node per-Worker quality
- [x] legacy fallback and no causal inference
- [x] frontend module boundary and regression gates
- [x] report
- [x] checkpoint commit and push (see Git history)

FOLLOWUP 04 has not been started.
