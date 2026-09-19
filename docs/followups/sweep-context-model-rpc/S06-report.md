# S06 — Durable sweep runner

2026-09-19 (Asia/Seoul). Workstream: WS-S06.

S06 — Software COMPLETE within the durable exploratory sweep supervisor scope.
Hardware: **NOT RUN — user-operated**. CI: **NOT CHECKED**. S07 has not been
started.

## Authorization and baseline

The user requested the next task after S05 and repeatedly authorized continued
work. S06 started from clean commit
`e285e7ee1d9a11d5deb715c1bec0b1d52b610dad` on
`codex/current-source-pilot-v6`, matching its origin branch. The integrated
Master Spec, Data Contracts, Integration Notes, S00–S05 reports,
CONTRIBUTING.md, sweep domain/compiler/model/RPC bindings, JobService/resource
coordinator, child process, SuiteRunner, formal Campaign boundary and storage
locking were reviewed. No applicable AGENTS.md exists.

No research lock, formal inventory, runtime inventory, model, result,
credential or approval artifact was changed. No real Worker, SSH connection,
model download/load, inference or native RPC process was used.

## Implemented behavior

- `SweepRepository` stores a private atomic manifest plus append-only event
  journal per sweep. The manifest freezes the verified resolved-plan snapshot,
  plan hash and revision, generation/execution order, seed, dispatch policy,
  realized dispatch order, resource overlap decisions, trial/attempt/job/run
  links, coverage, snapshots, drift, pause/cancel/retry reasons and cleanup
  outcome. Every read re-verifies the plan snapshot and manifest invariants.
- `SweepSupervisor` is an explicit `tick()` state machine. It creates no daemon
  scheduler and executes no benchmark in the Dashboard process. Every attempt
  claim, deterministic JobService job ID and suite ID are durable before Start.
  A restart inspects that same job ID; a lost Start response recovers the same
  attempt, and uncertain inspection becomes `needs_reconciliation` without a
  replacement launch.
- `DurableSweepJobBackend` and `SweepJobDocumentFactory` submit one model-bound
  child through the existing JobService/process/SuiteRunner path. Each child has
  its own sweep trace and exactly one model, so the suite loop cannot re-expand
  the sweep's model axis. Requests and warmups remain fields of that cell and
  are not converted into sweep repeats.
- JobService's start fingerprint and resource attempt owner now include the
  sweep/trial/attempt identity. Existing Campaign fields and formal exclusivity
  are unchanged.
- Sequential execution remains the default. Opt-in `disjoint_parallel` is
  capped at two and compares the resolved cell's Worker IDs and physical
  endpoint identities. Overlapping model/context cells wait; Workers are never
  reassigned. JobService's S05 atomic reservation remains the final admission
  authority.
- Execution policy now explicitly records strict versus bounded backfill and a
  bounded window. Generation order stays independent from as-listed or seeded
  execution order. The manifest records the seed, actual claim order and every
  overlap resource ID used to defer dispatch.
- Pause stops new dispatch and lets active children reach their existing safe
  cleanup/release boundary. Cancel addresses only active job IDs owned by the
  selected sweep. A completed child does not make a trial or sweep terminal
  until cleanup/release evidence is present; orphaned or quarantined resources
  pause for reconciliation.
- Failure policy is explicit. `stop` preserves the failure, skips undispatched
  trials and lets already-active disjoint children drain before the manifest
  becomes terminal. `continue_ready` preserves the failed attempt and allows
  other ready/disjoint work. OOM or another child failure is never projected
  onto different models or contexts.
- Retry is manual and requires a reason. It creates a new attempt linked to the
  old attempt while preserving history. The official-summary rule is the latest
  completed attempt. Plan snapshots are immutable; an edited plan must use a
  new plan revision/new sweep. An injected fresh-evidence gate runs before every
  claim and pauses on drift. The supervisor has no download or relock operation.
- Plans with at least one resolved ready trial now advertise durable sweep
  execution capability. Blocked/unknown cells remain non-runnable rows, and
  plan integrity verification still rejects altered capability or executable
  state.

## Fake/offline recovery and compound coverage

The new S06 suite injects a fake durable backend and temporary filesystem
repositories. It covers manifest failure before and after atomic write, crash
after attempt claim, Start response loss, supervisor reconstruction, completed
trial non-relaunch, inspection uncertainty, orphan/quarantine behavior, cleanup
before lease release, pause at a safe boundary, selected-sweep cancellation,
manual retry lineage, drift before claim, Worker overlap serialization and two
disjoint jobs continuing under `continue_ready`.

The compound case uses the S04 resolved matrix containing concurrency, context,
two model identities and multiple layer/row RPC profiles. The S06 job factory
proves one-model child binding and exact RPC trace/config, while the existing
S04 injected fake-native tests prove those profiles produce distinct native
calls and cleanup behavior. The entire compound plan is then driven through a
new supervisor instance at each boundary; every trial completes once with a
unique durable job ID. No network or hardware RPC is involved.

## Actual validation

| Check | Actual result |
|---|---|
| New S06 supervisor suite | **9 tests, OK** (included in full discovery) |
| Focused sweep/resource/job/campaign regression | **147 tests, OK, 4.429s** |
| Full Python discovery in restricted sandbox | **651 tests run; 649 passed, 2 localhost-bind environment errors**, 20.657s |
| Full Python discovery with localhost lifecycle permission | **651 tests, OK, 123.167s** |
| `compileall -q cluster scripts` | exit 0 |
| repository validator | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| offline wheel build | PASS with system Python; SHA-256 `cb4332836cce38e15800818b4c2714f333e5551a2c9679a1cfe47c3eddda600d` |
| `bash -n` and Git whitespace check | exit 0 |
| npm syntax, fixtures, publication PNG and Playwright | PASS; **3 browser tests passed** |
| credential-pattern scan over changed product/test files | no matches |
| ShellCheck | NOT RUN — no shell source changed |

The first targeted command used system Python and could not import the
repository's FastAPI test dependency; rerunning with `.venv/bin/python` passed.
The first wheel command used that venv, which intentionally lacks setuptools;
the documented system-Python offline build passed. The restricted full Python
run could not bind two localhost launcher ports. Its permitted rerun passed all
651 tests. The restricted npm run reached the publication renderer but macOS
denied Chromium's Mach-port registration; the permitted rerun passed the PNG
fixture and all three Playwright tests. Starlette emitted its existing
TestClient/httpx deprecation warning.

## Compatibility, safety and next boundary

The formal Campaign schema and gates remain separate. S06 reuses only the
shared durable job and S05 resource ownership path; it does not weaken formal
approval or lock requirements. The plan additions are optional-default policy
fields, and existing blocked/unknown readiness semantics remain intact.

S07 may expose this application lifecycle through authenticated Dashboard APIs.
No S07 route, service or UI work is included here. Actual Worker ownership,
model loading, inference performance, native RPC behavior and physical cleanup
remain unverified until an operator runs the later hardware phase.

Only S06 source, fake/offline tests and this report are included. Commit and
normal feature-branch push results are reported after Git completes.

STOPPED after S06. S07 has not been started. Hardware: **NOT RUN**. CI:
**NOT CHECKED**.
