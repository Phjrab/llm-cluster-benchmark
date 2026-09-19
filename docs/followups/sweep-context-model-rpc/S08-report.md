# S08 — Dashboard sweep builder and multi-run control

2026-09-19 (Asia/Seoul). Workstream: WS-S08.

S08 — Software COMPLETE within the Dashboard UI and fake-browser acceptance
scope. Hardware: **NOT RUN — user-operated**. CI: **NOT CHECKED**. S09 has not
been started.

## Authorization and baseline

The user explicitly approved starting the next phase after S07. Work started
from clean commit `5cff103c7fbb10c69488464d162c5240c0c24b49` on
`codex/current-source-pilot-v6`, matching its origin branch. The integrated
Master Spec, Data Contracts, S08 phase, acceptance matrix, S00–S07 reports,
existing Dashboard templates/modules/tests and the actual S07 API contract were
reviewed. No applicable AGENTS.md exists in this repository.

No formal lock, research inventory, runtime inventory, model/result artifact or
credential was changed. No real Worker, SSH connection, model download/load,
inference or native RPC process was used.

## Implemented user flow

The existing Dashboard now has a separate **Sweep** navigation section placed
next to Experiment. It is a structured builder rather than a JSON editor.

Basic controls cover stable Worker IDs, installed models, strategy,
combination, context capacities, request concurrency, output-token values and
independent repeats. Advanced controls cover request and warmup counts, GPU
layers, threads, batch size, sampling, dispatch mode, `max_parallel_jobs`,
order, failure/cooldown policy, budgets, response privacy and input variants.
The copy distinguishes context capacity from actual rendered input length and
request concurrency from child-job parallelism.

The model selector separately exposes vendor/family/quantization/size,
installation coverage, download-source availability, runtime evidence and
formal approval. Missing models are disabled with a reason and a link to the
Model Library. The sweep path never calls a model install API. Exploratory
sweeps are explicitly labelled as separate from formal Campaign evidence.

The RPC Profile Editor binds ordered Worker sets, coordinator, split mode,
policy, custom weights and `rpc_gpu_layers` to stable IDs. Each card shows the
selected-mode capability, model-on-coordinator coverage, safe-memory evidence
and context list. Two- and three-node profiles can coexist. Changing the
Builder Worker set clears all profiles and ratios with a visible explanation,
so stale custom ratios cannot be silently reused.

## Preview, approval and recovery

Preview and explicit readiness refresh use the S07 endpoints. The UI renders
base/unique/trial counts, physical requests, warmup calls,
valid/blocked/unknown/duplicate/excluded counts, budgets and capability reasons.
Users can select a cell, record its exclusion reason and generate a new
revision. A changed input clears approval. Editing a previously saved plan
advances the revision and requires a new Sweep ID instead of overwriting the
old draft.

The approval is bound to the exact preview SHA-256. Start remains disabled
unless the current browser session approved that saved hash and the preview was
executable. Start/Resume/Retry still perform the S07 fresh server preflight;
browser state has no authority to bypass model/backend/identity/resource or
formal gates. Pi historical power warnings remain visible but are not treated
as a standalone blocker.

Active Sweeps restores the durable server list and manifest after reload. Each
card presents text plus an icon for status, progress, current model/context/RPC
profile, occupied Workers and a waiting/failure reason. Start, pause, resume,
targeted cancel and manual trial retry call the exact sweep/trial routes with
revision, plan hash and a new idempotency key. Cancelling one card does not
remove another card or its event log.

Per-sweep event streams use the header-authenticated fetch path, preserve a
separate replay cursor in session storage, page the journal before reconnecting
and retain bounded owner-specific logs. Cursor recovery and browser reload use
server state. Source-drift conflicts remain attached to the affected run card.
Trial details and retry controls live in a separate results module; S09 result
comparison/export behavior was not added.

The implementation is split across `sweep-builder.js`, `sweep-control.js` and
`sweep-results.js`. The existing app remains the bootstrap/presentation facade
and no frontend framework was replaced. All actionable controls have labels,
disabled reasons or text status in addition to color, with responsive layouts
for narrow screens.

## Browser acceptance evidence

The new Playwright scenario uses only intercepted fake API data. It verifies:

- two installed models × three context values × three concurrency values × two
  output limits × three repeats = **108 trials**;
- a disabled, downloadable 14B candidate is shown without invoking install;
- two- and three-node RPC profiles, custom ratios and an unsupported row mode;
- profile/ratio invalidation when the Worker set changes;
- two disjoint active jobs, targeted cancel, pause/resume and reload recovery;
- an exact source-drift reason attached to one card without removing another;
- Pi historical power warning with an executable preview.

The generated browser screenshot is test evidence only and is named
`S08_FAKE_DATA_sweep-dashboard.png` under the ignored Playwright artifact
directory. It contains no real inventory or benchmark result and is not
committed.

## Actual validation

| Check | Actual result |
|---|---|
| Focused S08 fake Browser E2E | **1 test, PASS, 7.3s** |
| Full npm syntax/fixtures/publication/Playwright gate | PASS; **4 browser tests passed, 26.8s** |
| Focused sweep API/Dashboard/packaging Python regression | **47 tests, OK, 10.123s** |
| Final full Python discovery | **670 tests, OK, 132.641s** |
| `compileall -q cluster scripts` | exit 0 |
| Repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| Offline wheel build | PASS; SHA-256 `7e7f841be1a7c957c0f650679c77fb6b3919b99e14521df4b1b7c4e1aa78bfaf`; all three sweep modules present |
| Git whitespace and credential-pattern checks | run before commit; results recorded with delivery |
| ShellCheck | NOT RUN — no shell source changed |

The first full Python run was intentionally run inside the restricted sandbox.
It reached 670 tests but two launcher cases could not bind a localhost ephemeral
port (`PermissionError: [Errno 1]`). The same unchanged suite was rerun with
localhost test permission and all 670 tests passed. The suite's fake SSH/RPC
messages are injected unit-test output; no physical endpoint was contacted.
Starlette emitted the existing TestClient/httpx deprecation warning.

## Remaining physical boundary

The production resolver continues to require exact server-side prompt
preparation evidence. When that evidence is absent, the UI shows an unknown
cell and cannot authorize Start; it does not substitute a browser token
estimate. Actual model fit, RPC row/layer support, large-model behavior,
inference performance and hardware cleanup remain unverified until the
operator-controlled hardware phase.

Only S08 Dashboard source, fake/offline tests, packaging assertions and this
report are included. Commit and normal feature-branch push results are reported
after Git completes.

STOPPED after S08. S09 has not been started. Hardware: **NOT RUN**. CI:
**NOT CHECKED**.
