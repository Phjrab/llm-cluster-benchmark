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

- Raspberry Pi Worker package installation, model load with a real GGUF,
  OpenBLAS runtime, worker process lifecycle, SSH model synchronization, and
  RPC action were not run.  Pi 1 has no project virtual environment, native
  build toolchain, FastAPI/Uvicorn/psutil installation, or GGUF model yet.

## Hardware validation — 2026-08-20

- Jetson `192.168.0.26` was validated with a temporary, isolated archive of
  implementation commit `acf1123` under `/tmp`. The existing project checkout
  (`main@98d88c2`) and its 32 unrelated uncommitted changes were not modified.
- The isolated Worker used the existing project virtual environment and models,
  bound only to `127.0.0.1:18000`, and was unloaded, stopped, and removed after
  the test. The project Worker port `8000` was never started or changed.
- Platform confirmation: Jetson Orin Nano Super, Ubuntu 22.04.5, CUDA 12.6,
  llama-cpp-python 0.3.20 with GPU offload available, and jtop service active.
- `/cluster/health` reported CUDA backend verified and inference ready. jtop
  telemetry was unavailable to the Worker process, so the tested psutil
  fallback correctly reported `telemetry_degraded=true` while inference stayed
  ready.
- `/api/models` listed nine GGUF files. The relative-ID containment rule
  correctly rejected a basename-only request, then loaded
  `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf` with `n_ctx=512`,
  `n_gpu_layers=8`, and `n_batch=256` without adjustment.
- `/cluster/chat/stream` produced eight SSE tokens and a `done.metrics` event:
  TTFT `0.744735 s`, end-to-end `1.245638 s`, and eight generated tokens.
- Raspberry Pi 1 (`192.168.0.16`) is Ubuntu 24.04.4/aarch64 with 8 GiB RAM and
  ample free disk, but is not ready for Worker validation. Its `vcgencmd
  get_throttled` result was `0x50000`, showing a historical undervoltage and
  throttling event. Resolve the power-supply warning before benchmarking.

## Remaining issues

- The legacy standalone `web.app` UI remains as a compatibility surface and is
  not yet deleted.  It is no longer a Worker runtime dependency.
- Worker setup shell lifecycle remains a legacy process wrapper; broader
  lifecycle hardening is outside this phase.

## Next phase readiness

- **READY for Phase 06.** Worker inference and telemetry are explicit,
  mockable boundaries, and benchmark scheduling/math has not been changed.
