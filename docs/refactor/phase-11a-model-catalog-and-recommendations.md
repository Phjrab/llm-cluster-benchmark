# Phase 11A — Edge LLM Model Catalog and Deterministic Recommendation Policy

Date: 2026-08-21 (Asia/Seoul)

## Goal

Extend the existing per-Worker model inventory with a reproducible, offline-first
catalog and a conservative recommendation/preflight policy. The catalog is not a
download queue: it describes candidate GGUF artifacts, while the Worker
filesystem remains the only source of installed-model facts.

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Baseline commit: `d3a6c8c docs(acceptance): record RPC and durable recovery validation`
- Implementation commit: `d84c028 feat(models): add deterministic edge model catalog and recommendations`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Replaced the one-record catalog with schema version 2 and 17 explicitly tiered
  candidate records:
  - `core_stable`: Qwen2.5 0.5B/1.5B/3B/7B Q4_K_M, SmolLM2 1.7B Q4_K_M,
    Granite 3.3 2B Q4_K_M.
  - `core_modern`: Qwen3 0.6B/1.7B Q8_0 and 4B/8B Q4_K_M.
  - `experimental_edge`: LFM2.5 1.2B/2.6B and Gemma 4 E2B QAT Q4_0.
  - `optional_reference`: Phi-4 mini, Llama 3.2 1B/3B, and Granite 3.3 8B.
- Added typed catalog provenance, tier, parameter reporting, context, reasoning,
  quantization, platform-role, license/access, Korean reason/caution, immutable
  source-lock, and runtime-verification fields. Gemma records total and effective
  parameter counts separately.
- Kept every static entry at `candidate` status. It has no pinned revision and
  SHA-256 yet, so it cannot become an installation-ready or `recommended` record
  merely by appearing in the catalog.
- Added deterministic Worker-only recommendation output. The Mac Controller is
  always excluded. A record can be `recommended` only when the Worker platform,
  pinned runtime fingerprint, immutable source identity, and smoke verification
  all agree. Otherwise it is explicitly `candidate`, `compatible`,
  `stress_test`, `rpc_only`, or `unsupported`, with evidence and cautions.
- Added conservative memory admission based on actual installed GGUF bytes,
  KV-cache bytes per token, compute buffers, backend overhead, metadata overhead,
  currently available Worker RAM, and a reserve of `max(20% total RAM, 1024 MB)`.
  Parameter count alone is never used to claim fit.
- Set the default experiment/model context to 4096 and allow 8192 or 16384 only
  through the existing execution preflight. The preflight rejects an advertised
  context-limit or safe-memory violation before a durable benchmark job exists.
- Extended Worker inventory with recorded source revision, architecture, chat
  template hash, license-acceptance, and metadata-inspection facts. Direct Worker
  installation still requires an explicit credential-free URL and exact SHA-256;
  optional provenance metadata is recorded only after checksum verification.
- Added catalog-aware integrity checks for quantization and, once a catalog entry
  is source-locked, revision, architecture, and license acceptance. Existing
  unmanaged installed models remain readable for backward compatibility, but are
  not promoted by the new recommendation policy.
- Added Model Library recommendation badges, deterministic memory-fit evidence,
  provenance/source-lock and license indicators, Korean reasons/cautions, and
  starter packs. Starter packs only select already installed Worker models for an
  experiment; they never download or install models.
- Extended `/api/bootstrap` and additive `/api/models` responses with
  `model_recommendations`, `model_starter_packs`/`starter_packs`, and catalog
  policy data without removing previous fields.

## Installation and provenance policy

- No GGUF was downloaded, synced, deleted, or installed during this phase.
- The static catalog deliberately contains no invented revision, file checksum,
  or model-size claim. An operator must review license/access, choose a source,
  pin its revision, inspect GGUF metadata, and provide the exact SHA-256 before a
  direct install can become reproducible.
- Benchmark creation never invokes a download, install, or model synchronization.
  Those remain explicit control operations and are excluded from timing.

## Compatibility

- Legacy `ModelCatalogEntry`, `ModelInventoryEntry`, `recommend_models`, Worker
  install endpoint, and CLI invocation remain usable. New inventory/provenance
  fields are additive.
- Existing installed model files without provenance metadata continue to appear
  in inventory and can follow the legacy preflight path. They are not labeled
  smoke-verified recommendations simply because they are installed.
- Existing result schemas, request scheduling, strategies, model loading,
  benchmark metrics, and raw response artifacts were not changed.

## Tests executed

- Focused model/domain/dashboard-backend tests:
  `.venv/bin/python -m unittest cluster.tests.test_models cluster.tests.test_domain cluster.tests.test_dashboard_backend -q`
  - 60 passed, 0 failed.
- Full regression suite, including loopback-port launcher lifecycle checks:
  `.venv/bin/python -m unittest discover -s cluster/tests -q`
  - 291 passed, 0 failed.
- `python3 -m compileall -q cluster`
  - Passed.
- `bash -n` for shell scripts under `cluster/` and `scripts/`
  - Passed.
- `node --check cluster/dashboard/static/app.js`,
  `node --check cluster/dashboard/static/js/models.js`, and
  `node cluster/tests/test_dashboard_exports.js`
  - Passed (`dashboard export fixtures: OK`).
- Catalog/default JSON validation, both public CLI help surfaces, and
  `git diff --check`
  - Passed.

## Tests added or extended

- Catalog cohort/tier regression, invalid repository/quantization rejection, and
  candidate/unpinned identity rules.
- Deterministic memory equation, Worker-only recommendation, and pinned
  runtime/smoke promotion gates.
- Context-limit and installed-GGUF memory admission failures.
- Worker provenance manifest persistence and additive dashboard API response
  contract coverage.
- Dashboard export fixture coverage for model starter packs, status badges, and
  experiment model-selection handoff.

## Hardware and source acceptance not run

- No Jetson, Raspberry Pi, SSH worker, GGUF source, model repository, or remote
  model metadata was contacted or modified.
- Therefore no catalog record is runtime-smoke-verified yet, and no entry is
  truthfully emitted as `recommended` by default.

## Remaining risks and follow-up

- Actual GGUF byte size is authoritative only after the file exists on a Worker;
  uninstalled catalog candidates intentionally retain unknown fit status.
- Source revision/checksum lock values should be added only after a human reviews
  the upstream artifact and license/access terms. Doing so will enable strict
  provenance preflight for that exact artifact.
- Live Jetson CUDA and Raspberry Pi OpenBLAS smoke tests remain an operational
  acceptance step, not something inferred from catalog metadata.

## Next phase readiness

- **Phase 11A is complete and stops here. No later phase was started.**
