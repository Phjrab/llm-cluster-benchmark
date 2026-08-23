# Measurement Instrumentation Contract

Status: Phase 05 contract, schema version 1

This contract adds research measurements without changing the existing
19-column `requests.csv`. Each run may now contain a private
`measurements.jsonl`; `summary.json` may contain the additive
`measurement_instrumentation` object. Older runs without either field remain
valid and readable.

## 1. Timing boundary and clocks

- Request, prefill/decode proxy, RPC lifecycle, and collection overhead use
  Python's monotonic `time.perf_counter`.
- Each durable sample also has an RFC 3339 UTC `sampled_at` value for human and
  cross-artifact correlation.
- Monotonic values are process-local. They must not be compared across a
  Controller restart or across machines.
- Telemetry samples use the midpoint of Controller probe start/finish as the
  monotonic sample estimate. The complete probe duration is retained as
  `collection_overhead_s`.
- Model loading, warmup, the pre-measurement idle snapshot, and telemetry probe
  completion are outside legacy request wall-time metrics.
- Telemetry is sampled once per second, plus a scenario boundary sample. Energy
  is integrated within each scenario and then summed; unsampled cooldown time
  between node-sweep scenarios is never bridged.

## 2. Request and prefill/decode metrics

| Metric | Unit | Collection point | Interpretation |
|---|---:|---|---|
| `input_tokens` | tokens | Worker tokenizer or llama-server usage | Worker chat path is a tokenizer-backed deterministic fallback-prompt proxy (`input_tokens_exact=false`); RPC usage is exact when llama-server reports it |
| `prefill_time_s` | s | server TTFT (Worker) or client TTFT (RPC) | Proxy that includes first-token decode; the source field prevents an exact-prefill claim |
| `prefill_tokens_per_s` | tokens/s | input tokens divided by prefill proxy | Null if either input tokens or prefill proxy is unavailable |
| `decode_time_s` | s | first streamed token to stream completion | Excludes the first-token interval |
| `decode_tokens_per_s` | tokens/s | tokens after the first divided by decode time | Null for zero-duration decode |
| `total_tokens` | tokens | input plus generated tokens | Null if input tokens are unavailable |

The existing `server_ttft_s`, `server_generation_s`, `generated_tokens`, and
`tokens_per_s` fields retain their previous meanings in `requests.csv`.

## 3. Energy metrics

| Metric | Unit | Definition |
|---|---:|---|
| `idle_power_w` | W | Descriptive post-warmup, pre-measurement snapshot; never subtracted from energy |
| `average_power_w` | W | Time-weighted measured energy divided by covered scenario duration |
| `peak_power_w` | W | Maximum valid measurement sample |
| `energy_j` | J | Trapezoidal integration of consecutive valid power samples inside each scenario |
| `generated_tokens_per_j` | tokens/J | Successful generated tokens divided by energy |
| `requests_per_j` | requests/J | Successful physical requests divided by energy |

Energy requires at least two valid power samples in every measured scenario.
If one node lacks power telemetry, cluster-wide energy and efficiency are null;
available per-node values remain intact. Cluster power values are sums of the
per-node values. Peak is therefore a conservative sum of node peaks rather
than a claim that every peak occurred simultaneously.

## 4. Thermal, throttling, and frequency

| Metric | Unit | Definition |
|---|---:|---|
| `start_temperature_c` | °C | Maximum sensor value in the first valid sample |
| `mean_temperature_c` | °C | Arithmetic mean of per-sample maximum sensor temperatures |
| `peak_temperature_c` | °C | Maximum observed sensor value |
| `end_temperature_c` | °C | Maximum sensor value in the final valid sample |
| `throttling_sample_count` | samples | Count of active Raspberry Pi firmware fault samples; null on platforms without this signal |
| `frequency_samples` | MHz + monotonic s | Raw CPU frequency observations, not an inferred average |
| `steady_state_start` | — | Null until Phase 09 freezes a pilot-derived steady-state rule |

No thermal or throttling value is fabricated for Jetson/Pi sensors that do not
expose it. Pi historical fault bits remain in the existing independent
`power_integrity` quality artifact.

## 5. Network and RPC lifecycle

| Metric | Unit | Definition |
|---|---:|---|
| `connection_setup_s` | s | Controller request start until HTTP response headers are received |
| `bytes_sent` / `bytes_received` | bytes | HTTP request and streamed response body bytes; excludes TCP/IP/TLS headers |
| `effective_bandwidth_bytes_s` | bytes/s | request plus response body bytes divided by request E2E, or host-counter delta divided by sampled duration |
| `RTT` (`rtt_s`) | s | Null in schema v1 because no dedicated RTT probe is enabled |
| `model_load_distribution_s` | s map | Native RPC device-start durations plus coordinator model-load duration |
| `cleanup_s` | s | Complete RPC stop sequence duration, including a failed stop attempt |
| `coordinator_wait_s` | s | Null because the pinned llama-server does not expose internal coordinator queue wait |

Host telemetry network counters include unrelated traffic on the Worker and
are supporting context, not a replacement for per-request body-byte counts.

## 6. Availability and zero semantics

Every nullable metric has an adjacent `availability` entry with `available`,
`source`, and `reason`. A measured `0` is available. A missing sensor, an
unsupported runtime counter, insufficient samples, or an unfrozen policy is
`null` and unavailable. Analysis code must never coerce null to zero.

## 7. Artifacts and readers

```text
<run>/config.json
<run>/events.jsonl
<run>/responses.jsonl
<run>/requests.csv          # unchanged 19 columns
<run>/measurements.jsonl    # additive schema v1
<run>/summary.json          # additive measurement_instrumentation
```

The JSON Schema is
`config/research/measurement_artifact.schema.json`. The Dashboard/export read
surface is `GET /api/runs/{run_id}/measurements`; it returns an empty list for
a valid legacy run without the new artifact. Measurement files are mode 0600
inside a mode 0700 run directory and contain no prompt or response text.

## 8. Known precision limits before the pilot

- Worker prefill is a disclosed TTFT proxy, not a native prompt-eval timer.
- Exact Worker input token count remains unavailable across chat templates in
  the pinned llama-cpp-python API; the deterministic fallback prompt is counted.
- One-second polling can miss short peaks.
- Pi has no board power sensor in the current stack, so energy is normally
  unavailable even though undervoltage/throttling integrity remains visible.
- RTT and internal RPC coordinator wait remain unavailable.
- Phase 09 must freeze the steady-state rule and decide whether the sampling
  interval is sufficient before Phase 10 formal execution.
