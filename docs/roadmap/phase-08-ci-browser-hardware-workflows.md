# Roadmap Phase 08 — CI, Browser E2E, and Hardware Acceptance Workflows

## 1. Outcome

Phase 08 is complete on branch `codex/roadmap-phase-08` and is proposed to
`main` in [PR #1](https://github.com/Phjrab/llm-cluster-benchmark/pull/1).

The repository now has three required hosted validation gates and one isolated
self-hosted hardware workflow:

- GitHub-hosted Linux quality and packaging;
- GitHub-hosted macOS Controller lifecycle;
- GitHub-hosted Chromium Dashboard E2E;
- opt-in Jetson, Raspberry Pi, homogeneous two-node, and RPC-cleanup smoke.

Phase 09 pilot execution was not started. No formal campaign, inference job,
Worker installation, model synchronization, power-mode change, or RPC process
was started by Phase 08.

## 2. Checkpoints

| Checkpoint | Commit |
|---|---|
| Hosted CI, Playwright E2E, hardware workflow, and repository gates | `9ab883d` |
| Explicit wheel build backend for hosted Python | `02e720f` |
| Source-only warning-level ShellCheck gate and shell hygiene | `af0a464` |

Every checkpoint was pushed immediately to
`origin/codex/roadmap-phase-08`.

## 3. Required hosted CI

`.github/workflows/ci.yml` runs for pull requests, `main`, `codex/**`, and
manual dispatch. Every third-party action is pinned to a full commit SHA and
the workflow has read-only repository contents permission.

| Required check | Hosted runner | Contract |
|---|---|---|
| `Linux quality gate` | Ubuntu 24.04 | full Python suite, compileall, wheel build/install, JSON and scientific lock validation, workflow security scan, Bash syntax, ShellCheck |
| `macOS controller gate` | macOS 15 | Controller-only setup, start/idempotent start/status/restart/logs/stop, health and port release, stale-PID protection, CLI compatibility |
| `Browser E2E` | Ubuntu 24.04 + Chromium | JS syntax, existing export fixture, actual Dashboard server, deterministic Playwright flows |

Every hosted job uploads its diagnostic evidence with `if: always()`. Linux
preserves test logs and the built wheel, macOS preserves lifecycle reports and
Dashboard logs, and browser CI preserves JUnit, HTML, traces, screenshots, and
videos when present. These artifacts are private to the GitHub Actions run and
have bounded retention.

The repository validator rejects:

- duplicate or invalid shipped JSON;
- invalid research locks, protocol, analysis plan, or a zero-cell matrix;
- `pull_request_target` workflows;
- third-party actions not pinned to a 40-character commit SHA;
- missing workflow-level `contents: read` permission;
- broad `pkill -f`, `pgrep -f`, or `killall` process control.

The validator currently covers 11 JSON documents, 72 expanded formal cells,
13 pinned action uses, and 7 source shell scripts.

## 4. Dashboard browser E2E

Playwright is locked to `@playwright/test` 1.62.1 in `package-lock.json`.
The browser gate starts the actual FastAPI Dashboard and serves the repository's
real HTML, CSS, and JavaScript. API responses are intercepted with isolated,
deterministic fixtures, so the test cannot alter the user's inventory, results,
Worker files, or hardware.

The automated flows verify:

- Jetson and Raspberry Pi Worker-card rendering;
- Pi `POWER WARNING · HISTORY` presentation;
- Model Library catalog and installed-Worker rendering;
- experiment creation and exact submitted model/node selection;
- Dashboard reload recovery of an active measurement;
- durable prompt, generated answer, and participant-node result inspection;
- terminal run deletion followed by refreshed empty state;
- default-safe Worker disconnect confirmation with
  `remove_worker_files=false`;
- removal of the disconnected Worker card only after API success.

The E2E runtime, inventory, results, and traces are rooted below the ignored
`.artifacts/playwright` directory. It never reads the user's live runtime.

## 5. Live Dashboard read-only visual verification

The local Dashboard was safely restarted, inspected in the Codex in-app
browser at `http://127.0.0.1:8080/`, and left running after verification.

The desktop view rendered without broken templates or missing layout regions.
It showed:

- Mac Control Plane as Controller-only;
- 6 / 6 Workers online;
- 3 Jetson cards followed by 3 Raspberry Pi cards;
- the Model Library and experiment navigation;
- Pi historical power warning on `pi-worker-02`;
- the existing Dashboard theme, typography, orbit visualization, and fixed
  sidebar without document-level corruption.

This was a read-only presentation check. No live node action or experiment was
submitted.

## 6. Self-hosted hardware acceptance

`.github/workflows/hardware.yml` is intentionally separate from pull-request
CI. It runs only through:

- manual dispatch;
- the scheduled nightly trigger;
- a `v*-rc*` release-candidate tag.

The hardware runner must be online, idle, self-hosted, and labelled
`llm-cluster-hardware`. The preflight queries the GitHub runner inventory and
records an explicit unavailable reason instead of silently claiming success.

The smoke matrix requires the selected GGUF to already exist on the Workers
and runs:

1. one Jetson single-node request;
2. one Raspberry Pi single-node request with `n_gpu_layers=0`;
3. one replicated request over two Workers from the same platform;
4. one model-parallel RPC request over the same homogeneous pair, requiring a
   completed RPC cleanup status.

This preserves the project's normal experimental policy: Jetson and Raspberry
Pi cohorts are tested separately instead of creating a mixed-platform pair.
Each case uses one request, eight output tokens, no warmup, deterministic
sampling, and a separate result directory.

Manual and nightly runs report unavailable hardware as a visible skip. A
release-candidate run fails if the runner is unavailable, any case fails, or
RPC cleanup is incomplete. The complete hardware report and raw result
artifacts are retained for 30 days.

The repository currently has **0 self-hosted runners**. Therefore no hardware
job was scheduled in Phase 08. GitHub only permits manual dispatch of a new
workflow after that workflow exists on the default branch, so the first manual
hardware run is deliberately deferred until PR #1 is merged and a labelled
runner is registered. The planner's unavailable path and the complete
four-case matrix are covered by unit tests now; this is not represented as a
hardware pass.

## 7. Main protection and PR checks

`main` was unprotected at the start of Phase 08. It now requires a strict,
up-to-date success for:

```text
Linux quality gate
macOS controller gate
Browser E2E
```

Force pushes and branch deletion are disabled, and unresolved review
conversations block completion. Administrator enforcement remains disabled so
repository recovery remains possible, but the normal PR path displays and
requires all three checks.

Final PR execution:

- [Required CI run 32690903369](https://github.com/Phjrab/llm-cluster-benchmark/actions/runs/32690903369)
- `Linux quality gate`: PASS in 2m25s;
- `macOS controller gate`: PASS in 43s;
- `Browser E2E`: PASS in 52s.

## 8. Failure reconciliation

Two real workflow-environment defects were found and corrected before the
final green run:

1. GitHub's Python 3.13 image did not expose `setuptools.build_meta` to a
   no-isolation wheel build. The workflow now installs `setuptools` and
   `wheel` explicitly.
2. ShellCheck initially scanned wheel-generated `build/lib` copies and then
   found one unused setup variable, one unquoted executable path, and two EXIT
   trap false positives. Generated trees are now excluded, the source issues
   are fixed, and only the two indirect trap functions carry narrow
   `SC2317,SC2329` annotations. ShellCheck still runs at its normal warning
   level.

No failing check was ignored, changed to `continue-on-error`, or removed from
the required matrix.

## 9. Test gates

Final local validation:

- full Python project regression with Controller socket lifecycle:
  **417/417 PASS** in **55.061 seconds**;
- new Phase 08 workflow and hardware-planning tests: **6/6 PASS**;
- Playwright Dashboard E2E: **2/2 PASS**;
- Dashboard JavaScript syntax and existing export fixture: PASS;
- repository JSON, scientific lock, workflow pinning, and static security
  validation: PASS;
- Python compileall: PASS;
- all source shell scripts `bash -n`: PASS;
- all source shell scripts ShellCheck 0.11.0 at warning level: PASS;
- real macOS Controller lifecycle and health gate: PASS;
- `git diff --check`: PASS.

The only emitted dependency warning is the existing upstream
Starlette/httpx TestClient deprecation warning.

Final GitHub validation:

- [push Required CI run 32690684674](https://github.com/Phjrab/llm-cluster-benchmark/actions/runs/32690684674): all three jobs PASS;
- [PR Required CI run 32690903369](https://github.com/Phjrab/llm-cluster-benchmark/actions/runs/32690903369): all three required jobs PASS;
- every job retained evidence through an `if: always()` upload step.

## 10. Main files

Hosted and hardware workflows:

- `.github/workflows/ci.yml`
- `.github/workflows/hardware.yml`

CI and hardware entrypoints:

- `scripts/ci/validate_repository.py`
- `scripts/ci/macos_controller_gate.py`
- `scripts/ci/hardware_smoke.py`

Browser automation:

- `package.json`
- `package-lock.json`
- `playwright.config.js`
- `cluster/tests/e2e/dashboard.spec.js`

Regression contracts:

- `cluster/tests/test_phase08_ci.py`

## 11. Stop boundary and remaining work

- Phase 09 pilot execution was not started.
- No repeat-count, cooldown, thermal policy, or pilot acceptance decision was
  made.
- No live hardware smoke has passed because no labelled self-hosted runner is
  registered; release-candidate tags will be blocked until this is resolved.
- The hardware workflow cannot be manually dispatched until PR #1 places the
  workflow on the default branch.
- PR #1 remains open and was not merged automatically.

Phase 08 stops here. The next roadmap step is Phase 09 only when explicitly
requested.
