# S02 — context, input preparation and Worker runtime parameters

2026-09-19 (Asia/Seoul). Workstream: WS-S02.

S02 — Software COMPLETE within the runtime/helper scope below.
Hardware: NOT RUN — user-operated. CI: NOT CHECKED. S03 has not been started.

## Authorization and baseline

The user's explicit “승인 진행” after the completed S01 correction was interpreted
as approval for the next single phase, S02. The integrated Master Spec, Data
Contracts, Integration Notes and S02 requirements govern this change. Later
phase instructions are planning context, not authorization to execute them.

Tested parent: `04cb9cb4cbe0fca4b25b0ec84fd7fedb169a6e25`.
Branch: `codex/current-source-pilot-v6`. Initial working tree clean. Applicable
AGENTS.md: none; repository CONTRIBUTING.md reviewed. Origin feature matched
that parent; main remained `eebb8f134ac2fe251f5e9dd5a723bb653db9a840`.
No reset, main push, force push, merge or hardware CI dispatch.

## Implemented and existing behavior reused

- Optional strict `n_threads` (1–1024) and `n_batch` (1–16384) travel through
  Dashboard payload, ExperimentConfig, existing runner selection HTTP request,
  Worker schema and Llama factory. Omission keeps legacy three-positional-argument
  backend calls and defaults. RPC rejects these options pending S04.
- The existing backend owns model load and its single inference lock. Its cache
  identity now covers binary SHA, template metadata, requested context/GPU,
  requested/resolved threads and batch. File hash cache tracks ctime and inode
  as well as mtime/size, including same-size replacement with restored mtime.
- `requested_config`, `resolved_load_config`, `factory_config`, `effective_config`,
  `effective_sources` and `adjustment_reasons` are distinct. Context/batch/threads
  are read from backend attributes/methods. Physical GPU layer placement is null:
  a supplied GPU argument is not evidence of actual placement. Existing top-level
  load fields remain for compatibility and describe selected factory settings.
- Legacy context/GPU/batch retries remain for omitted settings. Explicit batch is
  bounded by candidate context, with a disclosed adjustment. Explicit new options
  require supported applied evidence; old Workers cannot silently ignore them.
  Sweep load adjustment (including default-batch retry) fails before warmup even
  when require_uniform_config=false. No adjusted condition becomes a success.
  Load evidence is retained in failed summaries; preparation failures preserve only
  an allowlisted reason code, not arbitrary HTTP/error response text.
- `ExperimentConfig.sweep` accepts a strict separate trace of IDs, hashes, repeat
  number and prompt mode/target. Formal/pilot identity cannot be mixed with it.
  None-valued additive fields do not change legacy config fingerprints or the
  positional constructor order of existing fields.
- Explicit POST `/cluster/input/prepare` prepares on the already-loaded Worker.
  It validates model/prompt/template identity, exact token count, optional profile
  target and input+output reserve against backend-reported context. No padding,
  truncation, model load or generation occurs in preparation. No GET invokes it.
  The existing Worker auth middleware covers this endpoint.
- Prepared token IDs and completion stop settings stay in a bounded 32-entry
  Worker-private memory cache, cleared on unload/reload. Streaming reuses the
  exact prepared tokens; changed input/history, stale preparation or a larger
  output reserve is rejected. A preparation ID binds input; it is not a lease or
  authentication token. Original prompt whitespace/hash is retained for sweep.
- The existing runner prepares and verifies every Worker before warmup and
  measurement. Server timing also excludes the legacy input-count operation.
  Early EOS finish reason, requested/effective context/output limit and input
  proof flow through SSE, transport, responses and measurement JSON. Existing
  requests.csv headers are unchanged. Output re-tokenization remains a proxy
  (`output_tokens_exact=false`); a requested limit never means actual output length.
- Native RPC transport now retains reported finish_reason. Its unreported effective
  context/output limit remain null. No RPC command/lifecycle change was made.
- Offline planner threads/batch readiness now depends on cached Worker load_profile
  evidence. No Worker probe occurs. Every plan still has executable=false; missing
  evidence blocks new load options. Default Worker serialization stays compatible
  with S01. Runtime trace does not turn a plan hash into operator approval.

## Exact preparation support and limitations

Repository `cluster/worker_setup.sh` pins llama-cpp-python 0.3.20. The adapter only
accepts the selected formatter-generated handler with an inspectable template on
that version. It invokes rendering/tokenization through a facade exposing no
native evaluator, captures completion arguments at that boundary, and hashes the
actual formatter template. Special/BOS token behavior follows that handler.
The prepared completion preserves stop settings and chat repeat-penalty defaults.
See pinned [formatter source](https://raw.githubusercontent.com/abetlen/llama-cpp-python/v0.3.20/llama_cpp/llama_chat_format.py)
and [Llama source](https://raw.githubusercontent.com/abetlen/llama-cpp-python/v0.3.20/llama_cpp/llama.py).

Custom/multimodal handlers, templates without verifiable identity, other runtime
versions and unavailable effective context fail closed with safe reason codes.
This is not a claim that every installed model can be prepared. Ordinary chat
fallback remains available outside the strict prepared path. Offline exactness
remains unknown until an explicit preparation succeeds. No tokenizer was installed
on the Controller and no GGUF was loaded by this development session.

For prepared requests, effective_max_tokens denotes the limit passed to completion
with a proven non-overflowing budget, not generated length or backend usage.
Legacy/native RPC paths without that proof report null. Physical GPU placement
remains unobserved even when the factory argument matches.

Deferred to the specified phases: server-owned catalog resolution S03, RPC prepared
context/profile application S04, cross-process ownership/fencing S05, durable Sweep
Runner S06, saved-plan approval/idempotency API S07, UI S08 and comparison S09.
No parallel scheduling or user-facing sweep feature was enabled. The new low-level
preparation primitive is not advertised as S05 resource isolation or S07 approval.

## Actual tests

All Python commands used existing .venv, PYTHONDONTWRITEBYTECODE=1, temporary
runtime/results under `/private/tmp/s00-sweep-audit/s02-*` and header-only inventory
`/private/tmp/s00-sweep-audit/empty.csv`. Native models, SSH, inference and RPC were
replaced by fake backends. Logs under `/private/tmp` are not staged.

| Command/check | Actual outcome |
|---|---|
| Existing focused Worker/domain/planner/core/measurement modules | 119 PASS, 0.917s |
| Initial new S02 module | 19 tests; two subtest errors from omitted required prompt in fixture; corrected |
| First full `python -m unittest discover -s cluster/tests -q` | 596 tests, 16.993s; 591 passed, 3 environment precedence failures, 2 sandbox bind errors |
| Final full discovery after runtime corrections and integration tests | 600 tests, 16.882s; 595 passed, same 3+2 environment failures/errors |
| Auth/token fixture rerun with its own temp defaults | 3 PASS, 0.003s |
| `python -m unittest cluster.tests.test_launcher.ControllerLifecycleTests -q` with local loopback permission | 2 PASS, 1.141s; fake local processes stopped |
| Final targeted six-module regression below | 145 PASS, 1.511s, including all 26 new S02 tests (three added after discovery) |
| `PYTHONPYCACHEPREFIX=/private/tmp/s02-pycache .venv/bin/python -m compileall -q cluster scripts` | exit 0 |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `bash -n cluster/rpc/runtime.sh cluster/worker/start.sh` | exit 0 |
| `npm run test:syntax`, `npm run test:fixtures` | PASS |
| diff whitespace and added-line credential-pattern review | PASS before commit |

Final targeted command:

```sh
.venv/bin/python -m unittest cluster.tests.test_context_runtime cluster.tests.test_sweep_planner cluster.tests.test_worker_runtime cluster.tests.test_domain cluster.tests.test_benchmark_core cluster.tests.test_measurement_instrumentation -q
```

New tests cover two factory values for each load parameter, two sampling/output
values, legacy omission, binary/template/profile reload, explicit/default batch
adjustment, context/GPU retries, unknown actual settings, model-specific template
and token lengths, exact special-token capture, budget/target/mismatch failures,
stale input, early EOS, Pi constraints, trace roundtrip/formal separation, privacy,
RPC finish reason and fake HTTP runner ordering/persistence. A fake integration
verifies one warmup and one measured request using prepared token IDs. Invalid
proof is rejected before executor warmup even with uniform checking disabled.

The integration fixtures initially used an invalid `/fake` project path (two
errors), then expected a returned failed summary where the existing runner raises
(one error). Fixtures were corrected to `/home/fake/project` and assertRaises;
no product behavior or assertion was weakened to hide a failure.

The three environment-sensitive tests are the same pre-existing cases recorded
in S01: test_core.PlatformPlanTests.test_worker_api_auth_is_disabled_by_default_and_configurable,
and test_phase14_security.TokenAndArtifactSecurityTests tests for corrupt settings
and private token mode. Dashboard services were initialized in temporary runtime;
CLUSTER_RUNTIME_DIR was then unset only while their own temp-default mocks ran.
The two lifecycle errors were sandbox denial of 127.0.0.1 bind. The successful
reruns supplement discovery; discovery itself did NOT exit successfully.

Offline wheel build/install/import is included in full discovery. Browser E2E,
PNG rendering and ShellCheck were not rerun for this backend change. This is not
a claim that npm test or hosted CI passed. Mock logs saying download/SSH/RPC refer
to synthetic fixtures, not hardware actions.

## Delivery and stop

Only S02 modules/tests/report are staged after diff/staged-diff/secret review.
Research locks, golden fixtures, actual inventory, model/runtime/result files and
credentials are untouched. Commit and normal feature push results are reported
in the final response; this report does not embed its own future commit SHA.

STOPPED after S02. S03 has not been started. Hardware: NOT RUN. CI: NOT CHECKED.
