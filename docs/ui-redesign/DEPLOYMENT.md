# macOS local deployment — 2026-10-07

This is a subsequent deployment explicitly requested by the user after the
original UI task was committed. The original FINAL_REPORT/VALIDATION describe
its earlier isolated implementation scope; this record describes the live service.

## Deployed service

- Checkout: `Phjrab/llm-cluster-benchmark`, branch `codex/ui-llm-cluster-research-lab`.
- Deployed code/evidence HEAD: `6f483789314d3d87636c59f6a04705ac05d67a4d`; UI source commit `3cc6c60`.
- Existing `.venv` and Controller launcher reused. No dependency installation.
- Prior state: Dashboard stopped, port 8080 unused. No unrelated process stopped.
- Command: `./scripts/llm-cluster start`; exit 0. Detached Controller service is
  intentionally left running at **http://127.0.0.1:8080/**.
- `./scripts/llm-cluster status`: exit 0 / running. Health checked at
  `2026-10-07T07:04:37.282839+00:00`: `ok=true`, `service=cluster-dashboard`,
  `role=controller`, `inference_enabled=false`.
- Served CSS/JS byte-for-byte match the current checkout: styles, sweeps, app,
  lab, research and navigation. Dynamic page titles preserve the existing
  `page title · LLM Cluster Benchmark` navigation contract.

## Verification

- Read-only live API smoke: HTTP 200 for root, health, static assets,
  research compare/campaigns/readiness. Existing 29 run summaries remain visible.
- 8 registered Workers retained; no queued/running job, active campaign or Sweep
  manifest existed before startup. No execution was resumed or started.
- Stored campaigns: 0. Matrix correctly presents the empty state instead of
  turning the formal plan into apparent executed results.
- Formal gate remains `formal_execution_allowed=false`. Incomplete legacy
  comparison identity displays `N/A`.
- Actual Chromium check: all 9 hash pages visible, no document overflow above
  2 px at 1440×900, no console/page error, no unexpected or write request.
  The browser check aborted every non-GET request and non-loopback origin.
- Live Matrix/Compare screen captures were opened for visual inspection. They
  contain actual local data, have no synthetic watermark, and remain in ignored
  `.artifacts/ui-deployment/` only. They are not published/committed or performance
  claims. Sanitized smoke details and private local captures are separate from
  the committed synthetic before/after evidence.

The first local browser smoke asserted the bare product title; its expectation
was corrected to the established navigation title before all 9 routes passed.
No product code change was needed during deployment. Prior regression remains
24 E2E + 720 Python tests; live smoke is additional deployment verification.

## Operation and rollback

From repository cwd:

```bash
./scripts/llm-cluster status
./scripts/llm-cluster logs
./scripts/llm-cluster stop
./scripts/llm-cluster start
```

`llm-cluster` is not currently on the shell PATH; the repository launcher above
works without installing global links. The server is detached and survives this
chat/process, but login/reboot auto-start was not added. Bind remains loopback;
there is no public exposure, reverse proxy, release or GitHub push.

For a UI rollback, stop this managed Dashboard, follow [ROLLBACK.md](ROLLBACK.md)
to revert only the UI implementation commit, then start the Dashboard again.
Review any future active jobs before restarting because ordinary application
startup can recover previously authorized Sweep/campaign manifests. No runtime
inventory, settings, result, model or lock deletion is part of rollback.

SSH management, Worker installation/restart, model download, benchmark/campaign
execution and power changes were not performed. Normal Controller status polling
reads Worker health APIs; it does not use SSH discovery or change Worker state.
Hardware performance and formal readiness remain outside this deployment check.
