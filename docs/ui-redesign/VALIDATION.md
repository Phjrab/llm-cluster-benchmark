# Validation — Benchmark Research Lab

Repository cwd: `llm-cluster-benchmark`; branch `codex/ui-llm-cluster-research-lab`.
Baseline is `9245e24`; implementation `3cc6c609cda28de1dc5ffb3f58e1027e1fc4fb16`. Final capture source SHA is recorded per file in
[captures/CAPTURE_MANIFEST.csv](captures/CAPTURE_MANIFEST.csv); source was committed before final after capture.
Tests use existing Node 22.17.1, npm 11.18.0, Playwright 1.62.1/Chromium and `.venv`.
No dependency installation or runtime/schema migration was needed.

## Required gates

| Status | Command (repository cwd) | Exit / result | Evidence |
|---|---|---|---|
| PASS | `DASHBOARD_TEST_PORT=46176 npm test` | 0; syntax, fixture calculations, publication PNG; 24 browser tests | [final-npm.log](logs/final-npm.log) |
| PASS | `.venv/bin/python -m unittest discover -s cluster/tests -v` | 0; 720 tests | [final-python.log](logs/final-python.log) |
| PASS | `DASHBOARD_TEST_PORT=46175 npm run test:e2e -- cluster/tests/e2e/research-lab.spec.js` | 0; 11 tests including close glyph/model filter contrast | [final-dialog-e2e.log](logs/final-dialog-e2e.log) |
| PASS | `.venv/bin/python -m unittest cluster.tests.test_dashboard_research -v` | 0; 16 tests after final partial-evidence guard | [final-projection.log](logs/final-projection.log) |
| PASS | `.venv/bin/python scripts/ci/validate_repository.py` | 0; research documents/cells, action pins, shell scripts | [final-repository.log](logs/final-repository.log) |
| PASS | `node --check scripts/ui-redesign/capture.js` / `git diff --check` | 0 | Source/capture script and final local diff audit |
| PASS | `npm run capture:ui -- before` / `-- after` | 0; 30 real browser PNGs + native SVG/PNG per stage | [before-capture.log](logs/before-capture.log), [after-capture.log](logs/after-capture.log) |
| BLOCKED_ENVIRONMENT (resolved) | Initial Chromium and Python loopback execution | macOS Mach-port restriction and loopback bind denial; later scoped execution permission passed | [BASELINE.md](BASELINE.md), baseline logs |
| BLOCKED_ENVIRONMENT (resolved) | First final E2E launch on default port 4173 | Existing listener; no process terminated; later separate port passed | [final-npm-port-conflict.log](logs/final-npm-port-conflict.log) |
| FAIL (resolved) | Python discover before final evidence generation | 720 tests; 1 README local-link failure because after full-page PNG not yet generated; regenerated and rerun below | [final-python-before-evidence.log](logs/final-python-before-evidence.log) |
| NOT_RUN | Browser native 200% zoom / manual screen reader / other browser engines | 720×450 equivalent reflow tested; native zoom and assistive software not claimed | `research-lab.spec.js` |
| NEEDS_HARDWARE | Real SSH, install/download, benchmark/campaign, power/telemetry accuracy | Intentionally not run; fixture tests cannot establish device readiness/performance | [HANDOFF.md](HANDOFF.md) |

Final gate failures: none after evidence generation and rerun. Log copies retain actual failures/warnings with only machine-specific home/temp paths and ANSI control formatting removed. Existing Starlette/httpx deprecation and Node color-env warnings remain visible in logs; they are not UI failures.

## Browser and visual evidence

Matched fixture `research-lab-v1`, locale ko-KR, reduced motion, and viewports
1440×900, 1366×768, 1024×768, 390×844. Capture routes: Matrix, Compare,
Worker inventory/detail, Sweep preview blockers, Research formal blocked and empty state.
1440 full-page Matrix and Compare additionally expose the linked run table.
Each stage has 30 PNG screenshots plus one native SVG and PNG publication export;
64 actual files are indexed. Every screen bears the synthetic-fixture watermark.
These are actual renderer outputs, with no image edits or invented performance data.

The PNGs and publication export were opened directly for visual review. The review
found and repaired overlapping publication subtitle/legend, a clipped Y label,
white close glyph/model filter on white surfaces, and pale error text. Horizontal
tables scroll within their containers; no document-level overflow is allowed.
Representative text samples pass 4.5:1 and essential control boundaries pass 3:1.
Close glyph normal/hover and selected model filter pass 4.5:1. This is targeted
accessibility inspection, not full certification of every element or assistive tool.

[Before Matrix](captures/before-matrix-1440.png) · [After Matrix](captures/after-matrix-1440.png) ·
[Before Compare/table](captures/before-compare-full-1440.png) · [After Compare/table](captures/after-compare-full-1440.png) ·
[Worker detail](captures/after-worker-detail-1440.png) · [Mobile Matrix](captures/after-matrix-390.png) ·
[Publication PNG](captures/after-physical-throughput-paper.png)

`before-browser-observations.json` and `after-browser-observations.json` collect
console/page errors and API request paths: errors are empty. All origins except
loopback assets are aborted; strict fixture APIs deny management/execution writes.
Only synthetic Sweep preview POST is allowed during capture. Existing E2E also
uses explicitly mocked execution/export/trash handlers and empty isolated runtime paths.
Intentional 503 and offline cases are tested separately and are expected failures.

## Acceptance checklist verdicts

| Item | Verdict | Evidence |
|---|---|---|
| Saved plan/Matrix and condition comparison replace hero/orbit | PASS | Template, `research.js`, before/after Matrix/Compare |
| Actual strategy enum, no invented Tensor/Pipeline strategy | PASS | `cluster/domain/strategy.py`, [INVENTORY.md](INVENTORY.md) |
| Physical/effective throughput, quality, phase, gate, provenance separate | PASS | `lab.js`, read-only projection, compare/Matrix tests |
| Controlled axes, incompatibility, ratio/zero/null tested | PASS | `test_research_lab.js`, projection tests, Compare browser test |
| Hash/input state, preview approval invalidation, durable contracts | PASS | Existing `dashboard.spec.js`, Sweep tests; stable mounted DOM |
| Formal/remote/trash protections and original research data kept | PASS | Gate/mock-write tests, protected purge/export; no writer/lock changes |
| Initial checkout/instructions/clean baseline inspected | PASS | [BASELINE.md](BASELINE.md), [INVENTORY.md](INVENTORY.md) |
| Every existing feature has an access path | PASS | Inventory mapping of nine pages and dialogs |
| Existing app actually modified | PASS | Committed FastAPI/Jinja/vanilla assets; no framework replacement |
| Loading/empty/error/stale/disconnected/disabled states | PASS | Read failures, empty/offline, late response and closed gate tests |
| Keyboard/focus/contrast/long text/mobile/reflow | PASS (tested scope) | Matrix/Worker dialog Escape/focus return, browser contrast/viewport tests; native 200% and screen reader NOT_RUN |
| Results distinguish failures/unrun/hardware limits | PASS | Gate table above and logs |
| Real PNGs reviewed and manifest source recorded | PASS | Captures, observation JSON, manifest and source commit |
| Private data/tokens/operational processes protected | PASS | Synthetic RFC5737 hosts, read-only static capture, sanitized machine paths in logs |
| Source/tests/docs/rollback/handoff locally committed | PASS | Local commit history; [ROLLBACK.md](ROLLBACK.md), [HANDOFF.md](HANDOFF.md) |
| No remote push/merge/release/deploy | PASS | Work completed locally |

Legacy incomplete tokenizer/workload/measurement/source evidence remains UNKNOWN.
Runs stay visible, but ratios are N/A; the UI never rewrites stored measurements to
make comparisons possible. Formal permission remains false and 1,080 remains planned.
