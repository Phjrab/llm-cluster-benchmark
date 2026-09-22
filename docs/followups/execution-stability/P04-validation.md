# P04 — Integrated Regression, Dashboard Acceptance, and PR Preparation

## Status and baseline

**COMPLETE (software validation and review preparation).**

P04 started from a clean checkout on branch `codex/current-source-pilot-v6` at
`0814e77acfafd97bd298bc7431581b613deb8ff3`. Its upstream pointed to the same commit.
The fetched `origin/main` remained
`eebb8f134ac2fe251f5e9dd5a723bb653db9a840`; the feature branch was 39 commits ahead
and zero behind. No open or closed PR targeted this head branch.

P04 did not connect to a Worker, alter hardware, download/load/delete a model, run
inference or native RPC, dispatch a hardware workflow, change research locks or
inventory, or execute an actual Sweep/Campaign. Product code was not changed in
this phase. Validation used a clean `git archive` under `/private/tmp`, the existing
local test runtimes, synthetic files, mocks, and localhost-only test servers.

## Integrated implementation review

### Browser-independent progress while Dashboard is running

The P01 implementation follows the later user-approved operating model rather than
creating an independent daemon. `SweepService` owns process-local background loops
that call the existing `SweepSupervisor.tick()`. `ResearchService` continues to own
the existing `CampaignRunner` loop. Start/Resume are the authority boundaries;
draft, paused, completed and cancelled state are not started by reads or startup.

Sweep GET/list/results/export/events/SSE only project durable state. Tests prove that
three or more Trial executions continue without further HTTP polling or SSE, that
repeated GET does not dispatch, and that recovery does not rerun a completed Trial.
Dashboard shutdown stops the progress loops without broadly cancelling child jobs.
Automatic dispatch while the Dashboard server is stopped remains outside the
approved P01 scope.

Both loops reuse existing JobService child processes, resource reservations,
physical-Worker overlap checks, fencing, targeted cancellation, cleanup and
quarantine. The progress-loop thread itself is not counted as an inference job.

### Formal source identity and Campaign safety

The P02 formal path validates the locked expected source tree independently for
each selected Worker and directly compares it with the fresh observed source tree.
Missing, invalid, unverified or mismatched values fail closed using the existing
source-fingerprint issue vocabulary. Git commit, runtime fingerprint/version and
pinned RPC commit checks remain active.

A Campaign fresh snapshot cannot bypass eligibility by being empty. Start/Resume/
Retry also require the separate formal execution gate. The Dashboard loop checks
that gate before each new cell dispatch; active child reconciliation remains on the
existing safe path if the gate closes.

### Existing Sweep, model and RPC contracts

The integrated diff retains:

- Mac as Controller only and Worker-only inference/RPC coordination;
- `single_node`, `replicated_round_robin`, `broadcast_compare`, `node_sweep`, and
  `model_parallel_rpc` meanings;
- logical versus physical request accounting and the 19-column `requests.csv`;
- default one job and explicit disjoint parallel maximum of two;
- immutable attempts/results, manual retry identity and privacy-aware exports;
- exact single/multipart model identity, installed/runtime/formal state separation;
- native RPC profile identity and targeted cleanup;
- formal serial execution, fresh preflight and gate policy.

## Dashboard status boundaries

The UI and API review found the following distinct sources of truth:

| Concern | Source of truth | P04 interpretation |
| --- | --- | --- |
| Dashboard connection | Controller process/health | Proves HTTP availability only |
| progress loop | post-Start durable events and state transitions in the serving Dashboard process | Browser-independent while Dashboard runs; old results do not prove liveness |
| child job | current Trial/cell attempt, job identity and event/log | Independent from browser and not automatically cancelled by Dashboard stop |
| Worker status | fresh Worker health/capability | Must not be derived from Dashboard process state |
| resource/quarantine | reservation/ownership and reconciliation evidence | Never released from TTL/heartbeat alone |
| ordinary execution | Sweep readiness/preflight | Does not imply formal eligibility |
| formal execution | Research execution gate plus fresh lock eligibility | Currently closed |
| model installed | exact Worker artifact inventory | Does not imply runtime verification |
| runtime verified | exact execution/backend evidence | Does not imply formal approval |
| formal approved | research locks, protocol and open gate | Separate from catalog/install/runtime labels |

The current Dashboard does not expose a first-class cross-process progress-loop
liveness badge. Operators must not infer liveness from a visible result or a stale
`running` label. The acceptance guide therefore requires new event/attempt movement
and treats a stalled loop as `UNKNOWN` until Controller logs and process identity
are checked. This is an observability limitation, not an independent-driver claim.

## Automated validation

All successful commands below ran against clean archived source for baseline
`0814e77acfafd97bd298bc7431581b613deb8ff3`.

| Check | Actual result |
| --- | --- |
| P01/P02/Sweep/Campaign/model/RPC/resource focused suite | **PASS — 177 tests, 12.017s** |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | **PASS — 718 tests, 146.099s** |
| `npm test` | **PASS — syntax, Dashboard fixtures, publication PNG, Playwright 8/8 in 50.2s** |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | **PASS** |
| `.venv/bin/python scripts/ci/validate_repository.py` | **PASS — 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts** |
| repository shell scripts `bash -n` | **PASS — 7 scripts** |
| `.venv/bin/python -m unittest -v cluster.tests.test_packaging` | **PASS — 3 tests** |
| system `python3 -m pip wheel . --no-deps --no-build-isolation` | **PASS — wheel SHA-256 `e33416389da86ed4a05c96e4c0906d1c1d0c8139bdee34c52fb69eba1d89dd8e`** |
| wheel Sweep assets inspection | **PASS — stylesheet, three Sweep modules and template present** |
| ShellCheck | **NOT RUN — executable not installed locally; hosted Required CI runs it** |
| `git diff --check` | **PASS** |

The first focused-suite invocation ran under the restricted sandbox and one of 177
tests could not bind its localhost fake Worker (`PermissionError`). The exact suite
was rerun with localhost permission and all 177 tests passed. No external address
was contacted.

The Controller `.venv` wheel command reached metadata generation but failed because
that environment lacks `setuptools.build_meta`. The same clean source then built
successfully with the repository host's offline system Python build environment,
and the dedicated three packaging tests passed in the Controller environment. Both
outcomes are recorded rather than classifying the first command as a product pass.

Test output mentioning SSH, model download, RPC, cleanup or node names came from
mocks and synthetic fixtures. The Playwright server used isolated runtime/result/
inventory paths and localhost port 4173; it did not reuse or stop the user's
operational Dashboard.

## Manual acceptance deliverable

`docs/manual/execution-driver-and-sweep-acceptance.md` provides the requested
12-step operator sequence. Every step includes expected behavior, evidence to
capture, and a stop criterion. It explicitly updates the server-stop expectation
to the approved P01 boundary: an existing child may continue, but automatic next
condition dispatch while Dashboard is stopped is not promised; restart recovery
must preserve attempt identity and avoid duplicates.

The guide ends with four independent outcomes:

1. software contract;
2. hardware functionality;
3. measurement quality;
4. formal eligibility.

No automated PASS substitutes for the hardware steps.

## Main diff and repository hygiene

After fetching, `origin/main...HEAD` contained 125 changed files with 18,852
insertions and 332 deletions before the P04 documentation commit. The scope consists
of the S00–S10 Sweep/context/model/RPC implementation, R02–R07 reliability/model/
Campaign follow-ups, H01/P00–P03 evidence, tests, and documentation.

Review found no tracked `.run` directory, model binary, credential/token file,
private key, `node_modules`, build or dist artifact in the branch diff. No binary
numstat entry was present. Repository validation and diff whitespace checks passed.
The real ignored H01 result tree remains local and was not staged.

The prepared PR draft separates the earlier S/R implementation from P01/P02
execution-stability changes and P03/P04 evidence. It states that software checks
pass, hardware acceptance is not run in P04, H01 remains Pi-only and blocked, and
the formal gate remains closed. P04 did not create, merge, or push a PR to main.

## Hardware and formal readiness

- **Software:** validated by local isolated gates. The final feature-branch Required
  CI result is reported with the commit because this report cannot contain the
  future run for its own commit.
- **Hardware:** **NOT RUN** in P04. The manual procedure is ready for a separately
  authorized H02 scope.
- **Measurement:** no new measurement was taken; P03 evidence limitations remain.
- **Formal:** `freeze_ready=false` for H01/v7 and
  `formal_execution_allowed=false` in the checked-in matrix. No relock or gate
  change was made.

## Remaining limits

- Actual Mac/Worker behavior for the 12-step acceptance sequence is unverified in
  P04.
- Progress-loop liveness has no dedicated cross-process Dashboard indicator.
- Dashboard-down automatic next-Trial/cell dispatch and Mac/network outage survival
  are outside the approved architecture.
- H01 order 8 and timeout root causes still lack Worker-side correlated logs and
  memory/process telemetry.
- Jetson evidence, current source/runtime hardware revalidation and formal relock
  remain absent.
