# S03 — model identity and variant sweep binding

2026-09-19 (Asia/Seoul). Workstream: WS-S03.

S03 — Software COMPLETE within the model resolution and concrete-child helper scope.
Hardware: NOT RUN — user-operated. CI: NOT CHECKED. S04 has not been started.

## Authorization and baseline

The user explicitly authorized both an S01 review and S03 implementation. The S01
review was completed first and pushed as `1b0082d`. S03 used that commit as its
tested parent on `codex/current-source-pilot-v6`. The tree was clean; no applicable
AGENTS.md exists; CONTRIBUTING.md and the integrated Master Spec, Data Contracts,
Integration Notes and S03 phase were reviewed. No reset, force push, merge, main
push or hardware workflow dispatch was performed.

## Implemented and reused

- New `application/sweep_models.py` is a bounded, read-only adapter over caller-
  supplied server catalog, Worker inventory, cached runtime/memory evidence and
  prompt variants. It performs no filesystem/network access, refresh, download,
  tokenization, model load or dispatch. Unknown catalog and missing installations
  remain visible as blocked candidates.
- Model references now bind catalog/model ID, exact revision, quantization, size,
  SHA-256, architecture, chat-template hash and tokenizer metadata hash. These
  fields participate in cell identity. A same filename never substitutes for the
  checksum, and conflicting Worker template evidence is not resolved arbitrarily.
- Existing `validate_model_preflight`, catalog download eligibility, memory-fit
  calculation and Dashboard license/source fingerprint are reused. The fingerprint
  was moved to the application model service and the existing Dashboard helper
  delegates to it without changing stored acceptance semantics.
- Candidate, downloadable, installed, runtime-verified and formal approval states
  remain separate. Formal approval is reported as `not_assessed` and does not gate
  exploratory cells. Gated repository access remains separate from project license
  acceptance. The sweep never invokes the existing downloader.
- Worker/model checks are recorded per installation, runtime and context-specific
  memory estimate. Ordinary replicated/broadcast/node-sweep cells require the exact
  model on every selected Worker. RPC preview checks installation only on the chosen
  coordinator. Replicated memory estimates are deliberately `unknown` for RPC until
  S04 profile-aware preflight, while catalog context-limit violations still block.
- Runtime compatibility requires cached backend evidence, an exact runtime commit,
  verified platform and verified/recommended catalog status. Installed files alone
  never become runtime-verified. Memory decisions use file/KV/buffer/available-RAM
  evidence and never parameter count; synthetic 14B and 70B labels are not filtered.
- `build_cell_config` converts exactly one valid non-RPC Trial to one strict
  ExperimentConfig. It re-verifies the plan, private prompt hash and full model
  identity. Sweep traces deep-copy nested identity, reject tampering and compare
  loaded model ID/SHA/size before warmup. Worker load metadata now reports observed
  file size alongside its existing SHA.
- Every model axis value remains an independent cell, including broadcast and
  node-sweep strategies. A sweep-bound Suite child cannot receive a second model
  list, preventing accidental multiplication with the existing SuiteRunner model
  loop. Existing SuiteRunner unload/cooldown behavior is otherwise unchanged.
- S01 wire compatibility is additive: old ModelReference and WorkerReference shapes
  omit the new optional evidence fields, and an empty model-check list is omitted.
  The planner remains side-effect-free and every plan still carries
  `SWEEP_EXECUTION_NOT_IMPLEMENTED`; this work does not authorize execution.

## Explicit limits and deferred work

Multipart catalog entries remain blocked because the repository has no ordered
artifact-set loader/manifest contract. This blocks only those models. S03 does not
invent a multipart checksum or duplicate the downloader.

RPC native argv, profile-specific allocation and session lifecycle remain S04.
Reservations, durable execution, Start authorization/API, Dashboard builder and
comparison/export remain S05–S09. The concrete-child helper is an internal tested
boundary, not a user-facing runner. Fresh evidence and resource ownership must be
rechecked by S06/S07 before any future Start.

## Actual tests

All Python checks used the existing `.venv`, `PYTHONDONTWRITEBYTECODE=1`, a
header-only inventory and runtime/results under `/private/tmp`. Synthetic four-byte
GGUF fixtures and an injected fake Llama factory were used. No real Worker, SSH,
model download, native runtime, inference or RPC was contacted.

| Check | Actual result |
|---|---|
| Focused S03/planner/context/model/suite regression | **114 tests, OK, 2.125s** |
| Full `unittest discover -s cluster/tests -q` | **620 tests, 18.519s**; 615 passed, 3 environment-precedence failures and 2 sandbox bind errors |
| Three auth/token fixtures with global runtime variables unset | **3 tests, OK, 0.060s** |
| Localhost lifecycle fixture with loopback permission | **2 tests, OK, 1.448s**; temporary fake dashboard processes stopped |
| `compileall -q cluster scripts` with temporary pycache | exit 0 |
| repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| bash syntax for Worker/RPC scripts | exit 0 |
| npm syntax and export fixtures | PASS |
| diff whitespace and direct-I/O import boundary | PASS |
| ShellCheck | NOT RUN — not installed |

The full-discovery command itself did not exit successfully. Its three failures are
the previously documented tests whose temporary DEFAULT_SETTINGS/token paths are
overridden by global `CLUSTER_RUNTIME_DIR`; they passed with that override removed.
The two errors were sandbox denials while binding `127.0.0.1`; the exact class passed
with loopback permission and cleaned up its temporary processes. Starlette emitted
its existing TestClient/httpx deprecation warning. Packaging's isolated offline
wheel build/import is included in discovery. Browser E2E, PNG rendering and hosted
CI were not run because no UI changed.

New tests cover two concrete models and model-specific fake templates/token counts,
same filename with different SHA, quantization/size/architecture/revision/checksum
mismatches, Worker template conflicts, missing/catalog-only models, duplicate cached
evidence, license/gated separation, runtime evidence, context/memory, 14B/70B names,
coordinator changes, multipart blocking, broadcast/node-sweep isolation, nested
Suite expansion, prompt/plan/trace tampering and a pure adapter import boundary.

Research locks, golden fixtures, actual inventory, real models/results/runtime,
credentials and formal approval state were not modified. Only S03 source, tests and
this report are staged after diff and credential-pattern review. Commit and normal
feature-branch push results are reported in the final response.

STOPPED after S03. S04 has not been started. Hardware: NOT RUN. CI: NOT CHECKED.
