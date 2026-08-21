# Follow-up 04 — Pi Power Regression, Security, and Compatibility Audit

Date: 2026-08-21
Phase: FOLLOWUP 04 only
Baseline: `1c48b33 feat(dashboard): display Raspberry Pi power measurement quality`

## Baseline commits

The audit started on `codex/mac-control-plane`, not `main`. The worktree was
clean, and local `HEAD` and `origin/codex/mac-control-plane` both resolved to
`1c48b3324a90af42c90d2cac84dfa11e64db3853`.

The complete pushed Follow-up chain was present:

| Follow-up | Commit | Purpose |
|---:|---|---|
| 00 | `9f1dd5c` | read-only Pi power baseline and boundaries |
| 01 | `5dd93ac` | pure decoder, Pi telemetry, additive Worker health |
| 02 | `7de648e` | non-blocking policy, events, durable result context |
| 03 | `1c48b33` | Dashboard and result presentation |

`git diff origin/codex/mac-control-plane...HEAD --stat` was empty before this
phase. The legacy Jetson remote remained fetch-only with push URL `DISABLED`.
No tracked `.run`, model, GGUF, token, key, PID, log, local inventory, or local
settings artifact was found. Thirteen files under `outputs/` remain tracked
historical benchmark outputs from pre-reconciliation commits (`60b3741`,
`f04e867`, and `19d1790`); this phase did not create, modify, or remove them.

## Test delta

| Checkpoint | Tests | Failed | Skipped |
|---|---:|---:|---:|
| Follow-up 00 baseline | 241 | 0 | 0 |
| After Follow-up 01 | 254 | 0 | 0 |
| After Follow-up 02 | 274 | 0 | 0 |
| After Follow-up 03 / before this phase | 274 | 0 | 0 |
| Follow-up 04 final | 278 | 0 | 0 |

Four focused Python tests were added. One test exhaustively evaluates all 256
combinations of the eight documented current/history bits, so the assertion
matrix is larger than the test-method delta. The existing JavaScript fixture
was extended with the real transition-event envelope.

## Decoder matrix

| Input class | Expected status | Verified behavior |
|---|---|---|
| `0x0` | `ok` | clean, no reasons, `blocking=false` |
| current `0x1/2/4/8` | `active_degraded` | each current condition maps to its stable active code |
| history `0x10000/20000/40000/80000` | `history_warning` | each history condition maps to its stable history code |
| exact `0x50000` | `history_warning` | historical undervoltage plus throttling; no current condition |
| current plus history | `active_degraded` | current condition has precedence; reasons remain deterministic |
| unknown bit | `history_warning` | `PI_POWER_UNKNOWN_BITS`; never silently clean |
| malformed, negative, extra text, out of range | parse failure → unavailable at telemetry boundary | Worker health remains successful; public message omits stderr/path |
| all 256 known-bit combinations | status derived from current/history precedence | deterministic serialization, exact reason count, no unknown bits, never blocking |

`cluster.domain.power` remains pure: no subprocess, FastAPI/Pydantic,
filesystem, socket, URL, or operating-system import/call is present. Observation
time is an explicit decoder argument or an injected telemetry clock.

## Admission matrix

| Existing Worker condition | Power condition | Admission |
|---|---|---|
| online, backend verified, model ready | `ok` | accept |
| online, backend verified, model ready | `history_warning` | accept plus non-blocking warning |
| online, backend verified, model ready | `active_degraded` | accept plus non-blocking warning |
| online, backend verified, model ready | `unavailable` | accept plus unknown/unavailable context |
| offline | any | reject with existing offline failure |
| model missing or checksum mismatch | any | reject with existing model preflight failure |
| backend not ready | any | reject with existing backend failure |

Power does not participate in `inference_ready`, environment readiness,
telemetry readiness/degraded, job creation, or the Run button's blocking
decision. Power warnings remain separate from failures and are deduplicated by
Worker/code/message. Existing offline, model, checksum, platform, and backend
checks remain authoritative.

## Measurement quality matrix

| Observation scenario | Quality | Run-status effect |
|---|---|---|
| all clean | `clean` | none |
| history only | `warning` | none |
| active during measurement | `degraded` | none |
| new history bit at postflight | `degraded` | none |
| all unavailable | `unknown` | none |
| clean plus unavailable sample | `warning` / incomplete | none |
| mixed Workers | deterministic worst explicit severity | none |
| failed run with power evidence | recorded on failed summary | remains `failed` |
| cancelled run with power evidence | recorded on cancelled summary | remains `cancelled` |
| mixed suite | additive worst quality/counts | suite status unchanged |

A golden comparison runs the same one-request plan with and without clean power
sampling under a fixed clock. Request/logical/physical counts, success and
failure counts, token totals, throughput, p50/p95 latency, answer agreement,
per-Worker metrics, and the exact CSV header are identical. Power sampling is
outside the measured request wall interval and adds no inference request.

## Event/durability audit

- Status-monitor observations use `system`; run snapshots, transitions, and
  final quality use `experiment`. Node-operation routing remains separate.
- Poll events are transition-only and repeated identical observations are
  deduplicated. Active-to-history recovery is a semantic transition.
- Preflight, pre-measurement, and postflight snapshots are journalled. Bounded
  measurement observations contribute to the summary without writing an event
  for every sample.
- The existing append/fsync repository path is unchanged. A preflight snapshot
  survives in `events.jsonl` even when no final summary exists.
- Failed and cancelled summaries retain the last safe power evidence without a
  causal claim. Durable job restart/recovery tests remain green.
- Event evidence contains normalized status, raw numeric/hex values, stage,
  timestamp, booleans, codes, and safe messages—not credentials, commands,
  stderr, or filesystem/model paths.

## Result compatibility

- `schema_version` remains `2`.
- `requests.csv` retains its exact 19-column header:
  `request_id,logical_request_id,scenario_id,replica_index,node,assigned_node,node_host,started_at,ok,ttft_s,e2e_s,server_ttft_s,server_generation_s,generated_tokens,tokens_per_s,output_chars,output_sha256,error,warmup`.
- The artifact names remain `config.json`, `events.jsonl`, `requests.csv`,
  `responses.jsonl`, and `summary.json`; no power-only result file was added.
- Request and response record semantics are unchanged. Power context is
  additive only in events and summary.
- Legacy Worker health without the field normalizes to non-blocking
  `unavailable`. Legacy result summaries without fields remain readable and
  display `NOT RECORDED`, never clean-by-assumption.
- New private result/runtime artifacts remain mode `0600` inside private
  directories; the Phase 14 permission tests pass.

## Benchmark invariant report

The full regression suite preserves:

- all-to-one `single_node` placement and extra-node rejection;
- deterministic selected-Worker ordering and balanced replicated round robin;
- logical-group concurrency and logical/physical counts for broadcast;
- cumulative/individual sweep planning, speedup, and scaling efficiency;
- explicit Worker RPC coordinator selection, Pi-only topology, failure codes,
  cancellation cleanup, and cleanup-failure non-completion;
- warmup exclusion and cancellation-aware bounded submission;
- p50/p95 interpolation, failed-latency exclusion, throughput, answer
  agreement, and per-Worker serialization;
- ordered model suites, unload after every model, cooldown cancellation,
  partial/failure durability, and recovery.

The new power observer does not change strategy plans, thread-pool
concurrency, warmup count, physical/logical work units, stream request count,
model placement, scheduling order, RPC lifecycle, or metric formulas.

## Dashboard audit

Static fixtures and a read-only local browser smoke both passed.

- Controller cards never receive Pi power presentation; Pi applicability is
  platform-scoped.
- Exact `0x50000` is visibly described as historical and not current.
- Active, unavailable, unknown, normal, warning, and degraded states retain
  independent text/icon/ARIA presentation and do not reuse run-status colors.
- A real rendered warning banner stated that the quality context does not
  block execution. The Run button remained enabled while `POWER WARNING ·
  HISTORY` was visible.
- Completed/failed/cancelled status and measurement quality are displayed as
  independent axes. Legacy results show `NOT RECORDED`.
- Node names are normalized before display, warnings are terminal-deduplicated,
  and system-channel power polling does not enter the experiment console.
- The page rendered without template breakage or horizontal viewport overflow
  at the available desktop viewport.

The browser smoke used a temporary Controller bound only to
`127.0.0.1:53640`. It read existing local Controller state and Worker health;
it did not invoke an action, change a Worker, install anything, start/stop a
remote service, run inference, or start a benchmark. The temporary tab and
server were closed after inspection.

## Security audit

- The Pi probe is fixed argv `vcgencmd get_throttled`, `shell=False`, no sudo,
  two-second timeout, captured output, and no user-controlled command/path.
- Probe errors do not crash health and do not expose raw stderr, command output,
  credentials, or paths.
- No new route, unauthenticated mutation, external network client, model
  download, reboot, clock/config change, or adapter-brand inference was added.
- Existing Dashboard/Worker constant-time token checks, query-token rejection,
  private token/result permissions, SSH quoting/allowlists, model/path
  containment, and exact process-identity cleanup gates remain green.
- Power does not change prompt persistence or raw-response behavior. Evidence
  is restricted to safe normalized telemetry values.

## Corrections

One in-scope compatibility defect was found and corrected.

`RunPowerIntegrityTracker` emits `power_integrity_changed` with a human message
and an `evidence` object. The Follow-up 03 browser handler incorrectly read a
nonexistent `power_integrity` object, so a real transition would be printed as
`unknown` and its semantic message would be lost. A small presentation helper
now consumes the actual event contract, safely normalizes Worker/status/raw
values, preserves the transition/recovery message, and has a JavaScript fixture
using the exact durable event shape. No API or event schema changed.
Static asset version markers were advanced together so a deployed browser
cannot combine the corrected coordinator with a cached older power helper.

## Full gates

| Gate | Result |
|---|---|
| `.venv/bin/python -m unittest discover -s cluster/tests -v` | 278 passed, 0 failed, 0 skipped |
| `python3 -m compileall -q cluster` | passed |
| `node --check` over all 6 Dashboard JavaScript files | passed |
| `node cluster/tests/test_dashboard_exports.js` | passed |
| `bash -n` over all 7 `cluster`/`scripts` shell files plus root start/stop | passed |
| `cluster.clusterctl --help` | passed |
| `cluster.benchmark.runner --help` | passed |
| `cluster.application.job_process --help` | passed |
| parse all 2 `cluster/config/*.json` files | passed |
| packaging metadata, wheel contents/install, isolated import | 3 passed |
| Controller lifecycle and launcher compatibility | 6 passed |
| `git diff --check` | passed |
| local browser smoke | passed; no remote mutation |

The only warning was the existing Starlette/httpx deprecation notice from the
FastAPI test client. It did not skip or fail a test.

## Hardware readiness for Follow-up 05

**READY to begin hardware acceptance, but not hardware-accepted in this
phase.** Local correctness, security, compatibility, packaging, lifecycle, and
Dashboard gates are green. Follow-up 05 still needs the prescribed real
Raspberry Pi single-Worker checks: deployed revision, backend and model
preflight, actual `vcgencmd` observation, load/inference/unload, result
artifacts, and cleanup.

This phase performed no SSH command, remote package operation, code/model sync,
remote process lifecycle, remote filesystem write, power/clock configuration,
reboot, inference, benchmark, or RPC run.

## Remaining risks

- The bounded sampling policy cannot report a transient outside its observation
  points; that is an intentional measurement limitation, not a readiness gate.
- Actual Raspberry Pi firmware output, device lifecycle, inference, and result
  collection remain Follow-up 05 acceptance work.
- Multi-Worker and RPC hardware behavior remain Follow-up 06–07 work.
- The transition UI contract is fixture-tested; inducing an actual power fault
  solely for visual testing is prohibited and was not attempted.
- Historical tracked benchmark outputs remain under the earlier reconciliation
  preservation policy and were not reclassified in this phase.

## Definition of Done

- [x] decoder and exhaustive known-bit matrix
- [x] fixed-command and non-Pi safety audit
- [x] non-blocking admission matrix
- [x] measurement-quality matrix including failed/cancelled/suite cases
- [x] event routing, deduplication, recovery, and durability
- [x] schema, CSV, artifact-name, legacy-reader, and permission compatibility
- [x] benchmark strategy and metric goldens
- [x] Dashboard fixture and local browser smoke
- [x] security and privacy audit
- [x] no new tracked runtime/model/secret artifact
- [x] full test suite and all repository gates
- [x] in-scope correction and focused regression
- [x] report
- [x] checkpoint commit and push (see Git history)

FOLLOWUP 05 has not been started.
