# Phase 11 — Per-Worker Models, Catalog, Distribution, and Preflight

Date: 2026-08-20 (Asia/Seoul)

## Goal

Make model management a Worker-owned concern: aggregate independently verified
Worker inventories, keep catalog metadata separate from filesystem state, make
installation/deletion explicit node operations, and reject an unsafe experiment
before its durable job is created.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `aee2330 docs(refactor): record phase 10 event channels checkpoint`
- Implementation commit: `013e630 feat(models): add worker inventory and strict preflight`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added a pure model domain with typed inventory records, catalog records,
  SHA-256 validation, quantization inference, and deterministic
  platform/memory/quantization recommendation rules.
- Added a checked-in local catalog and optional runtime cache reader. Catalog
  metadata remains usable when remote metadata is absent; no external network
  request is needed for browsing, recommendation, or preflight.
- Extended the Worker `/cluster/models` API with independent per-Worker model
  metadata: `id`, `filename`, `size_bytes`, `sha256`, `quantization`, and
  `checksum_valid`.
- Added Worker model verify, delete, and direct-download install endpoints.
  Direct download requires a credential-free HTTP(S) URL and exact SHA-256,
  writes a `.part` file, fsyncs it, verifies it, then atomically replaces the
  target. It does not require the Controller to hold the GGUF.
- Kept the existing explicit Controller-cache → rsync path as a second
  distribution adapter. It now hashes the source and remote file, removes a
  checksum-mismatched target, and only reports READY after verification.
- Added explicit CLI operations: `sync-models`, `delete-models`, and
  `install-model-url`. They are control operations, not benchmark work.
- Added model progress markers with `queued`, `downloading`, `verify`,
  `ready`, `deleting`, `deleted`, and `failed` states plus bytes/percent.
  Dashboard routes these as `node_ops` events and displays them in the node
  environment/control console only.
- Replaced Controller-root model scanning with Worker-inventory aggregation.
  `/api/bootstrap` and additive `/api/models` now expose model aggregation,
  raw Worker inventories, catalog records, and deterministic recommendations.
- Added a final model preflight immediately before job creation. It requires
  online Worker inventory, environment/backend readiness from existing checks,
  installed model files, valid SHA-256, and matching checksums across replicated
  Workers. RPC requires the model only on its resolved Worker coordinator.
- Missing/corrupt model failures are returned as structured `MODEL_MISSING` or
  `MODEL_CORRUPTED` failures. Telemetry degradation remains non-blocking.

## Changed behavior

- The Controller's own `models/` directory is no longer scanned as an
  inference inventory or model catalog source.
- New model inventory requests hash GGUF files on the Worker, using a
  size/mtime cache so `/cluster/health` remains lightweight.
- A model transfer now fails rather than leaving a checksum-mismatched GGUF
  eligible for a later experiment.
- Experiment creation performs a full Worker inventory/checksum preflight
  before creating a job or beginning benchmark timing. It never invokes
  `sync-models`, download, or install implicitly.

## Backward compatibility

- Existing Worker `/api/models` and `/cluster/models` remain available; fields
  such as `id`, `name`, `path`, `size_mb`, and `is_loaded` are preserved, with
  inventory fields added.
- Existing `sync-models` remains the supported Controller-cache rsync command.
- Existing SSE/action event names remain valid. `model_progress` is additive
  and explicitly uses the Phase 10 `node_ops` channel.
- Existing benchmark configs/results/CSV fields and strategy semantics are not
  changed. The new preflight only prevents a job that cannot use its requested
  Worker model file safely.

## Benchmark invariants

- No planner, request scheduling, warmup exclusion, aggregation, throughput,
  latency, scaling, answer agreement, cancellation, suite order, RPC cleanup,
  or result metric schema was changed.
- Model install/sync/delete stays outside experiment execution and benchmark
  timing. The job runner still only loads a model that preflight has approved.

## Tests executed

- Focused Phase 11 Worker/model/preflight/infrastructure tests:
  `.venv/bin/python -m unittest cluster.tests.test_models cluster.tests.test_worker_runtime cluster.tests.test_core cluster.tests.test_infrastructure -q`
  - 72 passed, 0 failed, 0 skipped.
- Full test suite:
  `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 167 passed, 0 failed, 0 skipped.
- `python3 -m compileall -q cluster/domain cluster/application cluster/benchmark cluster/infrastructure cluster/dashboard cluster/worker cluster/tests`
  - Passed.
- `bash -n` for every shell script under `cluster/` and `scripts/`
  - Passed.
- `.venv/bin/python -m cluster.clusterctl --help`,
  `.venv/bin/python -m cluster.benchmark.runner --help`, and
  `.venv/bin/python -m cluster.application.job_process --help`
  - Passed.
- `node --check cluster/dashboard/static/app.js` and
  `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- `git diff --check`
  - Passed.

## Tests added

- Worker inventory field contract, model verification, deletion protection for
  a loaded model, and direct-download install contract.
- Inventory parsing rejects malformed checksum records.
- Catalog/inventory separation and cache-over-local catalog fallback.
- Deterministic platform, memory, and quantization recommendations.
- Preflight success with degraded telemetry, model-missing rejection,
  checksum-corruption rejection, replicated checksum consistency, and RPC
  coordinator-only model requirement.
- Controller aggregation excludes legacy Controller/head inventory and model
  operations are asserted to remain on the node event channel.
- Static regression that experiment creation contains no implicit sync/download.

## Hardware tests not executed

- Jetson/Pi SSH deployment, real GGUF rsync/direct download, model deletion,
  checksum hashing of production-size files, and live inference: **NOT RUN**.
  This phase did not connect to or mutate a remote Worker.

## Remaining risks

- Full SHA-256 verification of multi-GB models intentionally occurs before an
  experiment and can take time on a Pi; it is not included in benchmark timing.
- Direct Worker download trusts the explicit URL and expected digest supplied
  by the operator/catalog. A future catalog-management phase can add signed
  source policy without weakening the required digest check.
- Older Workers that do not yet return SHA-256 inventory fields are treated as
  not ready until code deployment upgrades them; this is safer than accepting
  unverified binaries.

## Deferred work

- Model library browsing, install/delete forms, and richer progress visuals are
  intentionally left to Dashboard-focused Phase 12/13 work. The required API,
  CLI, inventory, and node-event boundaries exist now.
- No model binary, token, SSH credential, result artifact, or remote cache was
  committed.

## Next phase readiness

- **READY for Phase 12, but Phase 12 has not been started.**
- Phase 11 stops after this report checkpoint and GitHub push.
