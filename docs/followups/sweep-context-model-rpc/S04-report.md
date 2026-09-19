# S04 — RPC topology, model and context sweep binding

2026-09-19 (Asia/Seoul). Workstream: WS-S04.

S04 — Software COMPLETE within the concrete RPC cell and native lifecycle scope.
Hardware: NOT RUN — user-operated. CI: NOT CHECKED. S05 has not been started.

## Authorization and baseline

The user explicitly authorized S04 after the S03 stop. Work started from clean
commit `6ca20798df37fa47a866672d483f7905d3ce47cb` on
`codex/current-source-pilot-v6`, already matched by its origin branch. The integrated
Master Spec, Data Contracts, Integration Notes, S00–S03 reports, CONTRIBUTING.md,
RPC implementation and tests were reviewed. No applicable AGENTS.md exists.

The pinned native source remains
`f49e9178767d557a522618b16ce8694f9ddac628`. Its exact `tools/server/README.md`
was checked through the official ggml-org/llama.cpp repository: the pin documents
`--split-mode {none,layer,row}`, `--tensor-split`, integer/`all` GPU layers,
read-only `GET /props`, `POST /apply-template` and `POST /tokenize`. No latest-main
claim was substituted for pinned evidence.

## Implemented behavior

- A strict additive `rpc_gpu_layers=all|integer[0,999]` field now travels from the
  transport schema and `RpcProfile` through a concrete `ExperimentConfig` to the
  pinned native coordinator argv. The ordinary `n_gpu_layers` range and meaning
  are unchanged. Omitted RPC values preserve the existing all-layers meaning.
- `build_cell_config` now binds one valid RPC Trial to its atomic profile: explicit
  ordered Worker set, selected Worker coordinator, layer/row mode,
  auto/equal/custom policy, node-keyed custom weights and RPC GPU layers. The
  profile is copied into the tamper-checked sweep trace and must exactly match the
  concrete config before a child can run.
- Custom weights remain keyed by stable Worker ID. Runtime startup preserves the
  requested map, reorders values only for the native device order, and records both
  the resolved order and tensor array. Auto stays auto and passes no tensor split;
  requested ratios are never reported as actual placement. Actual placement is
  explicitly `null`.
- Each backend start creates a new session ID and records
  `new_session_per_cell`, `reload_per_cell`, requested model/context/GPU values,
  model-load timing and cleanup timing. Startup refuses a residual coordinator or
  device process/port instead of silently overlapping the next cell.
- The runtime check now reports capabilities derived from the exact pinned binary
  help and commit. A requested row mode, integer GPU policy or exact-input path that
  is not reported is rejected before any device start. `runtime.sh` also validates
  context, split mode and GPU-layer input before constructing native argv.
- All attempted devices are registered before SSH start returns. Failure cleanup
  still covers response loss/timeouts. Stop is followed by independent exact
  process/port assertions for coordinator and every attempted device; either stop
  or residual verification failure makes cleanup fail.
- Coordinator model presence remains coordinator-scoped. Sweep starts additionally
  compare the file SHA-256 with the bound artifact, then compare the pinned
  server's selected model path, effective context and canonical single-template
  metadata hash. Multipart models remain independently blocked by the existing
  planner because no artifact-set loader exists.
- Before warmup, RPC sweep cells use the pinned server's `/props`,
  `/apply-template` and `/tokenize` endpoints. The exact input count plus output
  reserve must fit `n_ctx`; token-length targets must match. Evidence stores only
  IDs, hashes, counts and sources, never the raw rendered prompt.
- Catalog preview now emits profile-specific participant memory checks. Equal and
  custom profiles apportion mapped model bytes by requested weights while retaining
  participant-local KV, compute, backend/RPC buffer and OS reserve. Auto becomes
  valid only under the conservative proof that every participant can fit the full
  estimate; otherwise it remains unknown rather than inventing a placement.
  Aggregate RAM is never treated as an execution guarantee.

## Fake/offline coverage

The new injected-native suite expands twelve concrete cells across two models,
two contexts and three RPC profiles. It proves distinct model path, `n_ctx`,
`rpc_gpu_layers`, layer/row and auto/equal/custom argv. It covers two- and
three-Worker sets, reordered custom `3:1:2` weights, fresh session identity,
private input preparation evidence and post-session process assertions. Existing
RPC tests continue to cover Jetson and Raspberry Pi coordinator selection, Pi
loopback devices, partial start/SSH timeout, model-load/generation/cancellation and
cleanup failure.

Failure injection covers unreported pinned capability, pre-start residual ports,
coordinator checksum mismatch, runtime template metadata mismatch, exact context
overflow and cleanup verification. Each post-start failure attempts coordinator
and every attempted device cleanup. A cleanup uncertainty cannot yield a completed
summary.

## Actual validation

No real Worker, SSH connection, model download, GGUF load, inference or native RPC
process was used. Tests used cached synthetic evidence, tiny byte fixtures and
injected fake runtime/HTTP/SSH adapters.

| Check | Actual result |
|---|---|
| Focused S04/model suite | **23 tests, OK, 0.266s** |
| Full `unittest discover -s cluster/tests -q` | **626 tests, OK, 122.203s** |
| `compileall -q cluster scripts` | exit 0 |
| repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| offline wheel build | PASS |
| bash syntax for RPC/Worker launchers | exit 0 |
| npm syntax, fixtures, PNG and Playwright | PASS; **3 browser tests passed** |
| diff whitespace | PASS |
| ShellCheck | NOT RUN — not installed |

The first full Python run inside the restricted sandbox had two localhost bind
errors; the same 626-test command passed with loopback permission. The first npm
run inside the sandbox failed when Chromium could not register its macOS Mach port;
the same full npm command passed outside that restriction. Starlette emitted its
existing TestClient/httpx deprecation warning. These were environment restrictions,
not hidden test skips. The hardware-unavailable fixture retained its explicit
synthetic skip message.

## Limits and next boundary

Hardware capability and performance remain unverified. Runtime capability output
proves only that the pinned built binary advertises the required options; it does
not claim a particular model/architecture's observed layer placement. Auto
placement and any profile without enough participant evidence remain unknown.
Mixed/hybrid or unimplemented multipart behavior is not promoted to verified.

S04 does not remove the global JobService guard, add reservations, schedule
parallel cells, implement durable sweep execution, add a Start API or expose the
Dashboard builder. Those belong to S05–S08. Research locks, actual inventory,
models, results, credentials and formal approval state were not modified.

Only S04 source, fake/offline tests and this report are included. Commit and normal
feature-branch push results are reported after Git completes.

STOPPED after S04. S05 has not been started. Hardware: NOT RUN. CI: NOT CHECKED.
