# Handoff — completed UI task

Target: `Phjrab/llm-cluster-benchmark`.
Branch: `codex/ui-llm-cluster-research-lab`; starting source `9245e24`.
Stages 01–06 complete within isolated UI scope. Implementation source commit: `3cc6c609cda28de1dc5ffb3f58e1027e1fc4fb16`.
The subsequent `docs(ui): record verified redesign captures and handoff` commit
contains final evidence; resolve its ID with `git log -1 --format=%H`. No push or deployment.

## Reproduce

Use the existing pinned Node 22+/Playwright environment and `.venv` from repository cwd.

```bash
DASHBOARD_TEST_PORT=46176 npm test
.venv/bin/python -m unittest discover -s cluster/tests -v
npm run capture:ui -- before
npm run capture:ui -- after
```

A browser launch/loopback permission may be needed in macOS sandbox execution.
Choose an unused test port; do not kill an existing listener. Capture runs its own
ephemeral static loopback server and closes its browser/server in `finally`.
Before renders `9245e24` assets without modifying the checkout; after requires
committed Dashboard assets. Every API is intercepted and management/execution
writes are denied; synthetic preview is the only allowed capture POST.

## What to review

[Final report](FINAL_REPORT.md), [validation](VALIDATION.md), [inventory](INVENTORY.md),
[manifest](captures/CAPTURE_MANIFEST.csv), [rollback](ROLLBACK.md), and actual
[full Compare/table](captures/after-compare-full-1440.png).
Tests pass: npm required gates / 24 E2E; full Python / 720.
Captured data are synthetic and must never be cited as device benchmark results.

## Remaining verification boundaries

No requested UI work remains. Real Worker health, SSH, download/install, durable
execution, telemetry/power accuracy and hardware performance are NEEDS_HARDWARE.
Formal gate remains false; 1,080 runs remain planned. Opening formal execution
requires the existing research source/runtime/license/instrumentation process.
Manual screen reader, other engines and native browser 200% zoom are NOT_RUN;
representative focus/contrast and equivalent narrow reflow were verified.

No deployment/server process was modified. Own ephemeral test/capture resources
were closed, existing listeners preserved. No persisted preference/schema changes.
