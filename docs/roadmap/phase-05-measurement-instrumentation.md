# Roadmap Phase 05 — Measurement Instrumentation

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: Roadmap Phase 05 only; no pilot, campaign runner, formal campaign, or
Phase 06 work was started

## 1. Outcome

Formal-run instrumentation now records prefill/decode proxies, token counts,
power/energy, thermal/frequency, network body bytes, telemetry overhead, and
RPC lifecycle timing without changing the legacy request CSV. New measurements
are versioned and durable in `measurements.jsonl`, summarized additively in
`summary.json`, and readable through the Dashboard API.

Unavailable measurements remain null with an explicit source and reason. The
implementation never converts a missing Pi power sensor, unavailable RTT,
unexposed coordinator wait, insufficient energy samples, or an unfrozen
steady-state rule to zero.

## 2. Git checkpoints

| Item | Value |
|---|---|
| Feature branch | `codex/roadmap-phase-05` |
| Instrumentation checkpoint | `9e53fe6` (`feat(research): add formal measurement instrumentation`) |
| Non-intrusive telemetry checkpoint | `847100f` (`fix(telemetry): cache worker samples outside inference requests`) |
| Timing-boundary checkpoint | `554c3fa` (`fix(metrics): exclude telemetry probes from measured wall time`) |
| Parent Phase 04 checkpoint | `db6da43` |
| Remote branch | `origin/codex/roadmap-phase-05` |

All three implementation checkpoints were pushed to GitHub. `main` was not
changed and no force push was used.

## 3. Additive artifact contract

Each new run may contain:

```text
measurements.jsonl
```

Record types are:

- `request_metrics`: request/scenario/node identity, monotonic timing,
  input/prefill/decode/total-token fields, connection setup, HTTP body bytes,
  effective bandwidth, and per-metric availability;
- `telemetry_sample`: UTC and monotonic time, scenario/node identity, power,
  temperatures, CPU frequency, host network counters, Pi current-throttle
  state, Controller collection overhead, and Worker collection overhead;
- `rpc_lifecycle`: RPC device/coordinator model-load distribution, cleanup
  attempts/duration/status, and unavailable coordinator wait.

The schema is `config/research/measurement_artifact.schema.json`. Files are
0600 inside the existing 0700 run directory and contain no prompt or response
text.

The existing `requests.csv` remains exactly 19 columns. Legacy summaries and
runs without `measurements.jsonl` continue to load and return an empty
measurement list.

## 4. Metric definitions and precision

The normative definitions, units, clock boundaries, formulas, precision
labels, and known limitations are documented in:

```text
docs/research/measurement-instrumentation.md
```

Important interpretations:

- Worker `input_tokens` is a deterministic fallback-prompt tokenizer proxy and
  is marked `input_tokens_exact=false`; RPC is exact only when llama-server
  usage reports prompt tokens.
- `prefill_time_s` is a disclosed TTFT proxy that includes first-token decode.
- `decode_time_s` starts at the first streamed token; decode throughput uses
  tokens after the first.
- `energy_j` is trapezoidally integrated inside each scenario and then summed;
  node-sweep cooldown gaps are not bridged.
- average power is time weighted; idle power is a descriptive post-warmup
  snapshot and is never subtracted.
- Pi energy remains null because the current Pi stack has power-integrity state
  but no board power sensor.
- RTT, native coordinator queue wait, and steady-state start remain explicitly
  unavailable until a dedicated probe/runtime signal or Phase 09 rule exists.

## 5. Intrusion control

The first hardware smoke exposed that inline Worker health collection was too
expensive relative to a very short request. Phase 05 therefore added a
one-second Worker-side background cache for portable sensors and Pi firmware
power integrity. HTTP inference and Controller sampling read the cache instead
of executing sensor commands inline.

Controller cache-read time and Worker sensor-collection time are recorded
separately. Run-level `wall_s` is now the sum of `ScenarioExecutor` request
intervals, so sampler start/stop, event writes, and node-sweep gaps do not
dilute request throughput. The final hardware runs confirmed run and scenario
wall times are identical.

The observed Controller collection overhead remains material for these tiny
LAN smoke runs. Phase 09 must use pilot evidence to freeze the sampling interval
and verify that telemetry traffic does not alter the selected workload. This
does not hide or coerce the overhead; the raw observations remain durable.

## 6. Dashboard/export reader

The authenticated additive endpoint is:

```text
GET /api/runs/{run_id}/measurements
```

It validates the run, returns schema version 1 and the JSONL records, returns
an empty list for a valid legacy run, and preserves existing response/result
routes. Publication/export code can consume the same repository reader without
parsing the 19-column CSV differently.

## 7. Final hardware smoke

No package was reinstalled. Source was synchronized, existing environments and
models were reused, and Worker APIs were restarted. All six enabled Workers
then reported API online, verified deployment trees, and final source commit:

```text
554c3fafacbbb6a6f4d044373c682232f2c874a6
```

Environment checks passed on `jetson-worker-01` (CUDA, jtop, MAXN_SUPER) and
`pi-worker-02` (OpenBLAS). Both also reported the pinned llama-cpp-python
0.3.20 and native RPC commit `f49e9178767d557a522618b16ce8694f9ddac628`.

The final smoke used Qwen2.5 1.5B Q4_K_M, one physical request, eight output
tokens, context 4096, no warmup, and a temporary result directory outside the
repository.

| Observation | Jetson Orin Nano | Raspberry Pi 5 |
|---|---:|---:|
| Run ID | `20260823_172306_48807a` | `20260823_172322_d28502` |
| Status | completed | completed |
| Run/scenario wall | 1.417502 / 1.417502 s | 4.257882 / 4.257882 s |
| CSV columns | 19 | 19 |
| Measurement rows | 4 | 5 |
| Input / generated / total tokens | 21 / 8 / 29 | 21 / 8 / 29 |
| Prefill proxy | 0.291148 s | 0.905999 s |
| Decode | 0.345965 s | 2.188905 s |
| HTTP body sent / received | 179 / 830 B | 179 / 830 B |
| Idle power | 4.54 W | unavailable |
| Energy | 7.861989031 J | unavailable |
| Temperature start / peak / end | 43.62 / 43.75 / 43.75 °C | 59.8 / 60.4 / 59.8 °C |
| Current throttling samples | unavailable on Jetson | 0 |
| Frequency samples | 2 | 3 |
| Measurement quality | legacy non-Pi field absent | warning (historical bits only) |

The Pi current undervoltage/throttling flags remained clear. Historical
`0x50000` bits were retained as the existing non-blocking warning, per the
user's previously approved policy. Pi power and energy were null, not zero.

## 8. Test gates

- Phase 05 instrumentation and compatibility fixtures: PASS;
- full project regression in the existing project virtual environment:
  385/385 PASS in 53.155 seconds;
- Jetson and Pi final hardware inference/instrumentation smoke: PASS;
- all six Worker SSH/project/API checks: PASS;
- all six deployment manifests at final commit and verified source tree: PASS;
- Dashboard JavaScript syntax and export fixtures: PASS;
- Python compileall: PASS;
- project shell scripts `bash -n`: PASS;
- all research JSON parse checks: PASS;
- `git diff --check`: PASS.

No dependency was installed or upgraded. The already-pinned project `.venv`
was used. The only emitted warning in the test suite is the existing upstream
Starlette/httpx deprecation warning.

## 9. Files added or changed

Primary implementation:

- `cluster/benchmark/instrumentation.py`
- `cluster/benchmark/core.py`
- `cluster/benchmark/persistence.py`
- `cluster/benchmark/transport.py`
- `cluster/benchmark/rpc.py`
- `cluster/benchmark/runner.py`
- `cluster/worker/inference.py`
- `cluster/worker/routes.py`
- `cluster/worker/telemetry.py`
- `cluster/infrastructure/storage.py`
- `cluster/dashboard/services.py`
- `cluster/dashboard/routes.py`

Contracts and tests:

- `config/research/measurement_artifact.schema.json`
- `docs/research/measurement-instrumentation.md`
- `cluster/tests/test_measurement_instrumentation.py`
- additive storage, Worker, benchmark, and public-route regression updates.

## 10. Safety and stop boundary

- No formal or pilot campaign was started.
- Hardware smoke artifacts were written only under `/tmp` and were not
  committed or copied into formal results.
- No model was downloaded, deleted, or changed.
- No package, CUDA, OpenBLAS, RPC runtime, or virtual environment was rebuilt.
- Existing Worker models and runtime directories were preserved.
- The Dashboard and Worker auth policy was not changed.
- No Phase 06 durable campaign runner work was started.

Phase 05 is complete. The next allowed roadmap step is Phase 06, but this
checkpoint stops here.
