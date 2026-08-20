# Phase 05 — Worker Runtime / Inference Adapter / Telemetry

Date: 2026-08-20 (Asia/Seoul)

## Git checkpoint

- Branch: `codex/mac-control-plane`
- Implementation commit: `acf1123 refactor(worker): extract standalone inference and telemetry runtime`
- Implementation push: `SUCCESS` to `origin/codex/mac-control-plane`

## Implemented

- Added the Worker-owned `InferenceBackend` protocol and repository-local
  `LlamaCppInferenceBackend`.  It retains the legacy GGUF discovery, model
  containment validation, CUDA-layer/context/batch retry schedule, GPU cache
  release, chat-template fallback, seed handling, and tokenization behavior.
- Added `LegacyWebInferenceBackend` as the explicit compatibility adapter for
  the legacy chat UI.  `web.app` now consumes this Worker-owned backend rather
  than owning a competing model-manager implementation.
- Split Worker HTTP models, SSE rendering, and routes into
  `cluster.worker.schemas` and `cluster.worker.routes`; routes call an injected
  backend and telemetry service instead of a global manager.
- Replaced the old Worker app that mutated an imported `web.app` FastAPI
  instance with a standalone `create_app()` factory and Worker-only ASGI app.
- Added `GenericPsutilTelemetry`, `JetsonTelemetry`,
  `RaspberryPiTelemetry`, and `TelemetryService`.
- Added health fields that distinguish `inference_ready` from
  `telemetry_ready`/`telemetry_degraded`.  A jtop import/service mismatch now
  falls back to psutil while inference remains available.

## Changed behavior

- `cluster.worker.app` no longer imports or mutates `web.app`; a Worker can be
  imported and served without loading the legacy chat application.
- Worker health retains existing fields and adds telemetry/inference readiness
  detail.  Legacy `/health`, `/api/models`, `/api/select-model`,
  `/api/unload-model`, `/api/chat/stream`, `/cluster/health`,
  `/cluster/models`, and `/cluster/chat/stream` remain available.
- The Pi telemetry contract explicitly returns unavailable GPU utilization and
  power as `None`, never a fabricated zero.

## Backward compatibility

- Existing `web.app` exports `ModelManager`, request schemas, SSE helper, and
  `manager` aliases backed by the extracted implementation.
- Existing model-load retry order, chat fallback, route paths, worker token
  middleware, SSE token/done/error events, benchmark input contract, and
  result schema remain intact.  Cluster stream `done.metrics` remains additive.
- Controller requirements remain free of llama.cpp, CUDA, OpenBLAS inference,
  jtop, and JetPack dependencies.

## Security considerations

- The existing Worker token middleware remains the single access-control path;
  the extraction does not add a second authentication mechanism or expose a
  new network listener.
- Model identifiers are still resolved beneath the configured `models/`
  directory before loading. Backend and telemetry services receive no shell
  command or credential input from Worker HTTP routes.
- jtop is imported and used opportunistically. A missing service, permission
  error, or version mismatch degrades only telemetry to psutil rather than
  disabling inference or reporting fabricated accelerator values.

## Tests passed

- Mock backend route contract: model listing, load, unload, seeded streaming,
  auth protection, and SSE metrics.
- Worker health contract: inference-ready with telemetry-degraded state.
- Telemetry provider selection, jtop mismatch fallback, and Pi `None` GPU/power
  semantics.
- llama.cpp retry schedule and chat-template fallback compatibility fixture.
- Worker source/import test confirming no `web.app` runtime dependency.
- Full Python suite, dashboard JavaScript/export fixture, shell syntax, and
  legacy CLI help smoke tests (recorded with the Phase checkpoint):
  `105 passed`.

## Tests not run / reason

- No Jetson/Raspberry Pi Worker package installation, model load with a real
  GGUF, jtop service, CUDA/OpenBLAS runtime, worker process lifecycle, SSH,
  model synchronization, or RPC action was executed.  Hardware is unavailable
  and Phase 05 does not authorize deployment.

## Remaining issues

- The legacy standalone `web.app` UI remains as a compatibility surface and is
  not yet deleted.  It is no longer a Worker runtime dependency.
- Worker setup shell lifecycle remains a legacy process wrapper; broader
  lifecycle hardening is outside this phase.

## Next phase readiness

- **READY for Phase 06.** Worker inference and telemetry are explicit,
  mockable boundaries, and benchmark scheduling/math has not been changed.
