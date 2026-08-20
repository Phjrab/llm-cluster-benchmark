# Phase 09 — Result Durability / Raw Responses / Structured Failures

Date: 2026-08-20 (Asia/Seoul)

## Goal

Persist each completed benchmark request before end-of-run aggregation, retain
the generated model response for research review, and attach deterministic
structured failures without removing legacy error strings or result files.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `4952df7 docs(refactor): record phase 08 durable jobs checkpoint`
- Implementation commit: `d8e2882 feat(results): persist raw responses and structured failures`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added durable `responses.jsonl` entries at every `request_completed` event,
  before final summary or CSV aggregation.
- Each response entry contains request/logical request/scenario identifiers,
  model and node, request-level TTFT/E2E/generation/tokens/TPS metrics,
  success state, output SHA-256, raw response, legacy error string, additive
  error code, and additive structured failure record.
- Added `persist_prompt` to the experiment configuration and Dashboard request
  payload. It defaults to `true` for local research review.
- With `persist_prompt=false`, raw prompt text is omitted from both
  `responses.jsonl` and the persisted `config.json`; a SHA-256 representation
  is kept instead.
- Added `FilesystemRunRepository.read_responses()` and
  `RunPersistence.recover_records()` so already-durable completed requests are
  available after a partial/crashed run even without final `summary.json`.
- Kept `requests.csv` metric-only and its existing fixed 19-column header.
  Raw responses and structured failure data are deliberately stored separately.
- Added `FailureGuide` mapping for every stable `ErrorCode`; guidance is fully
  deterministic and never invokes an LLM.
- Normalized exception/message failures to `FailureRecord` with code, stage,
  node, model, message, evidence, and operator solutions.
- Added structured failure fields to request failures, model-load events,
  failed run summaries, and failed-run events while retaining the public
  string `error` field.
- Added Worker API error-code headers and SSE failure payloads. Existing HTTP
  error message bodies remain strings for compatibility.

## Changed behavior

- A crash after a completed request no longer requires final CSV/summary output
  to preserve that request's response and request-level metrics.
- A failed model selection can return an appropriate HTTP status and the stable
  `X-Cluster-Error-Code` header, while preserving the original `detail` string.
- Response journals are created with mode `0600` because they may contain model
  output and, when enabled, research prompts.

## New abstractions

- `cluster.domain.failures.FAILURE_GUIDE`: complete deterministic code-to-action
  guidance.
- `failure_from_exception`, `failure_from_message`, and
  `http_status_for_failure`: side-effect-free normalization boundaries.
- `FilesystemRunRepository.append_response/read_responses`: durable raw-response
  journal access.

## Backward compatibility

- Existing `config.json`, `events.jsonl`, `requests.csv`, and `summary.json`
  names and meanings are preserved; `responses.jsonl` is additive.
- Existing response-less run directories remain readable and return an empty
  response journal.
- Existing `requests.csv` header and legacy string `error` field are unchanged.
- Summary `error_code`, `failure`, and `failures` fields are additive.
- `persist_prompt` defaults to `true`, so older clients and stored configs keep
  their existing behavior.

## Benchmark invariants

- No planner, scheduling, warmup exclusion, request mapping, aggregation,
  percentile, throughput, scaling, answer-hash, suite ordering, cancellation,
  or cleanup behavior was changed.
- The response journal is written from the existing completed-request event;
  it does not create new benchmark requests or alter metric calculations.

## Security considerations

- Failure diagnosis is a static mapping only; no model or external service is
  asked to infer a cause or solution.
- `responses.jsonl` is private (`0600`). Prompt persistence is explicit and can
  be disabled per experiment.
- No credentials, token, password, SSH key, model binary, runtime artifact, or
  benchmark output is committed.

## Tests executed

- `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 151 passed, 0 failed, 0 skipped.
- Focused Phase 09 and regression suite:
  `.venv/bin/python -m unittest cluster.tests.test_results_failures cluster.tests.test_worker_runtime cluster.tests.test_benchmark_core cluster.tests.test_core cluster.tests.test_storage -q`
  - 79 passed, 0 failed, 0 skipped.
- `python3 -m compileall -q cluster/domain cluster/application cluster/benchmark cluster/infrastructure cluster/dashboard cluster/worker cluster/tests`
  - Passed.
- `node --check cluster/dashboard/static/app.js` and
  `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `bash -n` for every shell script under `cluster/` and `scripts/`.
  - Passed.
- `git diff --check`
  - Passed.

## Tests skipped

- None in the final local suite.

## Hardware tests not executed

- Real Jetson/Pi inference and intentional process crash: **NOT RUN — Phase 09
  verified fsync-backed journaling with isolated run directories and mocks, and
  did not alter or interrupt a remote Worker.**
- No SSH connection, deployment, package installation, model transfer, power
  change, or remote filesystem mutation was performed.

## Remaining risks

- `responses.jsonl` is append-only. Long research campaigns need a future
  retention/export policy rather than automatic deletion.
- The Dashboard has the data through durable run artifacts, but richer
  response-review UI and failure cards belong to a later dashboard-focused
  phase; this phase intentionally establishes the compatible persistence/API
  boundary first.
- A response record written immediately before catastrophic storage failure may
  still be absent; each append is flushed and fsynced, but no filesystem can
  guarantee recovery after hardware-level media loss.

## Deferred work

- Dashboard result browsing, model catalog/distribution, and subsequent UI
  phases were not started.
- Raspberry Pi hardware acceptance remains deferred until the user resumes it.

## Next phase readiness

- **READY for Phase 10, but Phase 10 has not been started.**
- Phase 09 stops after this report checkpoint and GitHub push.
