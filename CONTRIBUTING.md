# Contributing and Test Guide

## Change boundaries

Preserve the macOS Controller and Jetson/Raspberry Pi Worker roles. The
Controller never becomes an inference participant. Keep existing strategy
semantics and result readers compatible, make schema changes additive, and do
not modify or delete runtime data, models, results, tokens, or SSH keys.

New work uses `WS-XX` Workstream names; existing `phase-*` history is not
renamed. Create focused commits and document behavior, compatibility, tests,
and hardware availability. Never claim a hardware pass when no suitable Worker
or self-hosted runner was available.

## Local quality gates

Set up the Controller environment first:

```bash
./scripts/setup-controller
```

Run the core gates from the repository root:

```bash
.venv/bin/python -m unittest discover -s cluster/tests -v
.venv/bin/python -m compileall -q cluster scripts
.venv/bin/python scripts/ci/validate_repository.py
python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/llm-cluster-wheel
bash -n cluster/rpc/runtime.sh cluster/worker/start.sh
npm test
```

Run ShellCheck over source shell scripts when it is installed. Hosted Linux CI
does this automatically and excludes generated `build`, `dist`, `.venv`, and
`node_modules` trees.

The Python suite includes the wheel isolated-install/import test. Browser tests
retain failure traces, screenshots, video, HTML, and JUnit output below
`.artifacts/playwright`. The macOS gate exercises the real local lifecycle and
loopback health endpoint. Required CI keeps its logs and wheel as downloadable
artifacts even when a gate fails.

## Hardware gates

Normal pull requests use hosted Linux, macOS, and Chromium jobs. Live Jetson,
Raspberry Pi, and RPC checks run only on a self-hosted runner labelled
`llm-cluster-hardware`. Manual and nightly absence is reported as
`unavailable`; a `v*-rc*` release-candidate run is blocked unless hardware
acceptance passes, including RPC cleanup.

Do not download a real GGUF, change Worker packages or power modes, run
inference, or start RPC merely to satisfy a unit test. Hardware-changing tests
require an intentional operator-approved run and must record Controller/Worker
commits, model SHA-256, platform/backend, power state, run ID, and cleanup
status.

## Compatibility surfaces

Preferred new code uses `cluster.domain`, `cluster.application`,
`cluster.infrastructure`, `cluster.dashboard.service_layers`, and
`cluster.research`. The following remain supported compatibility boundaries
and must not be removed in an unrelated refactor:

- `cluster.clusterctl` for the established operational CLI;
- `cluster.benchmark.runner` for the benchmark facade and CLI;
- explicit names in `cluster.dashboard.services.COMPATIBILITY_EXPORTS`;
- legacy inventory readers under `cluster.integrations`;
- additive readers for older result schemas.

Contributions are accepted under the repository's
[Apache License 2.0](LICENSE). By submitting a contribution, contributors
agree that it may be distributed under those terms.
