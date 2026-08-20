# Phase 04 — macOS Controller Runtime + CLI

Date: 2026-08-20 (Asia/Seoul)

## Implemented

- Added `scripts/setup-controller`, which creates the project-local `.venv`,
  installs only pinned Controller dependencies, initializes an empty Worker
  inventory when needed, and installs a user-level `llm-cluster` symbolic link.
- Added `scripts/setup-worker` as a Linux-only wrapper for the existing Worker
  runtime installer.  It refuses macOS before it can invoke any Worker setup.
- Replaced the fixed Jetson/Linux `/proc` launcher with a portable Controller
  lifecycle command at `cluster.cli.controller`, reached through
  `scripts/llm-cluster`.
- Added `start`, `stop`, `restart`, `status`, and bounded `logs` handling for
  the Dashboard process only.  It uses a lock plus PID, executable, cwd, argv,
  process start time, and user identity before signaling a recorded process.
- Added the additive `/api/controller/status` and health fields that explicitly
  identify the Dashboard host as a non-inference Controller.
- Made Dashboard startup create/accept a Worker-only, including empty,
  inventory.  The legacy `clusterctl` default still requires its one legacy
  head row, preserving its public compatibility behavior.

## Changed behavior

- A fresh Mac Controller no longer needs `cluster/setup_head.sh` or a synthetic
  local inference head before the Dashboard can start.
- `llm-cluster` binds its local Dashboard health/lifecycle process to
  `127.0.0.1:8080`.  It never starts, stops, or signals a remote Worker API.
- Controller requirements now include `psutil` for cross-platform identity
  checks and `httpx` for Dashboard HTTP/TestClient support.  They include no
  CUDA, llama-cpp-python, OpenBLAS inference, jtop, or JetPack dependency.

## Backward compatibility

- `python -m cluster.clusterctl ...` remains unchanged as the Worker-oriented
  compatibility CLI and continues to require the legacy head inventory by
  default.
- Existing `cluster/dashboard/start.sh` and `stop.sh` continue to delegate to
  `scripts/llm-cluster`.
- Existing inventory CSV columns and legacy head rows remain readable.  The
  Dashboard accepts both legacy one-head inventories and new Worker-only ones.
- No Worker source, deployment path, model runtime, benchmark scheduling, or
  result schema was changed.

## Tests passed

- Actual macOS lifecycle test using a temporary Dashboard process:
  `start → idempotent start → status → controller-status API → stop → restart
  → logs → stop`.
- Stale/tampered PID identity test confirms an unrelated `sleep` process is
  never signaled.
- Full Controller virtualenv Python suite: 96 passed.
- Dashboard JavaScript syntax/export fixture, all 12 shell syntax checks,
  `clusterctl` help, runner help, and `git diff --check` passed.
- Standalone command smoke in an empty parent directory: temporary
  `~/.local/bin/llm-cluster` symbolic link resolved to this checkout; `/tmp`
  successfully ran `start`, `status`, `restart`, `logs`, and `stop`; health was
  unreachable after stop.

## Tests not run / reason

- No Jetson/Raspberry Pi package install, Worker API lifecycle, inference,
  model, SSH, rsync, telemetry, power, or RPC action was executed.  Jetson was
  offline and Phase 04 deliberately excludes Worker mutation.

## Remaining issues

- The dashboard still contains legacy head-aware compatibility code.  Its full
  role migration and Worker-only selection semantics belong to later phases.
- The Controller lifecycle intentionally binds loopback only in this phase.
  LAN/TLS exposure is not added implicitly.

## Next phase readiness

- **READY for Phase 05.** The repository can now be cloned and launched as a
  Controller without a parent workspace or a local inference runtime.
