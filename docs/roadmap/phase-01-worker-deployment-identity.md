# Roadmap Phase 01 — Worker Deployment Manifest and Source Identity

Date: 2026-08-23 (Asia/Seoul)

Status: COMPLETE

Scope: Roadmap Phase 01 only; no model approval or later-phase campaign work

## 1. Phase contract

This phase implements a reproducible Worker source identity without copying Git
metadata to any Worker. It preserves the normal smoke-experiment path and adds a
fail-closed source/runtime comparison only to formal research eligibility.

The implementation was tested locally, pushed to the feature branch, deployed
through the Controller to all six registered Workers, and verified through each
Worker's live `/cluster/health` response.

## 2. Git checkpoint

| Item | Value |
|---|---|
| Repository | `https://github.com/Phjrab/llm-cluster-benchmark.git` |
| Feature branch | `codex/roadmap-phase-01` |
| Phase 00 parent | `3786442` |
| Deployment implementation | `0b56e5e` |
| macOS rsync compatibility fix | `58d3a2b` |
| Worker project-path finalization fix | `af23793` |
| Live deployed source commit | `af23793323716ec0412b333ddd59e491ab63a104` |

No commit was made on or pushed directly to `main`. No force-push was used.

## 3. Implemented contract

Each successful Worker source deployment now creates:

```text
.run/cluster/deployment-manifest.json
```

The private JSON artifact contains:

- exact Controller Git commit and clean-state evidence;
- a canonical SHA-256 over every deployed source file's path, byte length,
  executable bit, and content SHA-256;
- a self-hash covering the complete manifest, including deployment time;
- exact hashes for Controller, Worker, and shared runtime requirement files;
- Worker-local `llama-cpp-python` version and normalized runtime fingerprint;
- pinned native llama.cpp RPC commit;
- deployment timestamp, verification state, and canonical source file list.

The source-tree SHA remains stable across equivalent deployments. The manifest
self-hash changes with deployment time and detects mutation of any manifest
field.

## 4. Deterministic deployment boundary

The source collector and rsync command share one centralized exclusion policy.
The following are never included in source identity or copied as source:

```text
.git, .venv, .run, models, outputs, results,
Python/tool caches, build/dist/egg-info, local nodes inventory
```

The deployment sequence is fail-closed:

1. build the Controller manifest from the clean source checkout;
2. harden the remote runtime directory;
3. remove the exact previous deployment manifest before changing source;
4. reconcile source with rsync while preserving runtime/model/result paths;
5. rebuild the Controller manifest and reject a concurrent source change;
6. transfer the private manifest and set mode `0600` over SSH;
7. finalize it using the Worker's project virtual environment;
8. recompute the Worker source tree and record its native runtime identity;
9. report success only when Worker source verification is true.

Legacy source files that were absent from the current product tree were removed
from the registered Worker project roots. Models, virtual environments, runtime
state, outputs, experiment results, tokens, and RPC builds were preserved.

## 5. Worker and Controller behavior

`GET /cluster/health` now exposes an additive `deployment` object and
`capabilities.deployment_verified`. An old Worker or missing manifest remains
readable and healthy for backward compatibility, but reports deployment as
unavailable/unverified.

Controller status and Dashboard node probes retain the additive deployment
object. Existing table and public endpoint behavior is unchanged.

Formal eligibility now blocks on:

- missing, invalid, tampered, dirty, or unverified deployment identity;
- source commit/tree mismatch against the runtime lock;
- cross-Worker source drift;
- llama runtime fingerprint or package-version mismatch;
- native RPC commit mismatch.

The normal smoke path does not call this formal-only gate and retains its prior
admission semantics.

## 6. Live six-Worker acceptance

All six registered Workers were reachable through SSH, received the committed
source, finalized their manifests, started their Worker APIs, and reported
`verified=true`.

Common live identity:

| Field | Observed value |
|---|---|
| Source commit | `af23793323716ec0412b333ddd59e491ab63a104` |
| Source tree SHA-256 | `de0f46371adfc9342c81b3fc16e1d7d91864b074715e809ba7d0386408a4693b` |
| Source files | 154 |
| `llama-cpp-python` | `0.3.20` |
| RPC commit | `f49e9178767d557a522618b16ce8694f9ddac628` |
| Worker requirement SHA-256 | `fbd9a542316c260b4f641da695f9da710b39cf9a0895ab95d6d52bb3e7310431` |
| Shared runtime requirement SHA-256 | `c27be7a7f9bcb8455b875276c427b3c875aa6002556978746d5a1812ce9519c4` |
| Working tree | clean on all six manifests |
| Source tree verification | true on all six Workers |
| API status | online on all six Workers |

Per-platform runtime identity:

| Workers | Runtime fingerprint | Result |
|---|---|---|
| `jetson-worker-01`, `jetson-worker-02`, `jetson-worker-03` | `05e2c27b3bf0eff2` | identical within Jetson cohort |
| `pi-worker-02`, `pi-worker-03`, `pi-worker-04` | `b4387053e655722a` | identical within Pi cohort |

The six manifest self-hashes differ by design because each deployment has its
own timestamp; their commit, source tree, requirements, package version, RPC
pin, and platform-cohort runtime fingerprints match.

Additional live safety checks:

| Check | Result |
|---|---|
| `.git` absent on every Worker | 6/6 PASS |
| manifest mode is `0600` | 6/6 PASS |
| Worker source recomputation matches manifest | 6/6 PASS |
| SSH/project/API status | 6/6 PASS |
| Jetson CUDA runtime discovered during finalization | 3/3 PASS |

Worker APIs were started for this acceptance check. No boot-time automatic
start policy was enabled.

## 7. Hardware-discovered compatibility fixes

Two failures were intentionally left fail-closed and corrected before the
successful deployment:

1. macOS system rsync rejected `--chmod=F600`. The manifest transfer now uses
   portable rsync arguments and an exact remote `chmod 600` step.
2. SSH commands start in the remote user's home directory, so the Worker could
   not import the source-checkout package during finalization. The finalizer now
   receives the validated project directory as a fixed `PYTHONPATH` argument.

Both fixes have focused regression coverage and were committed/pushed before
the successful live deployment. Failed attempts never produced a verified
manifest.

## 8. Test gates

### Full regression

```text
Ran 347 tests in 42.609s
OK
```

- Passed: 347
- Failed: 0
- Skipped: 0
- Known warning: Starlette's compatibility `TestClient` warns about future
  `httpx2` migration.

### Focused and static gates

| Gate | Result |
|---|---|
| Phase 01 deployment tests | 9/9 PASS |
| Deployment + model-rsync compatibility after first live fix | 11/11 PASS |
| Python `compileall` / targeted `py_compile` | PASS |
| Dashboard JavaScript syntax | PASS |
| Dashboard export fixture | PASS |
| Product Bash syntax | PASS |
| Python launcher syntax | PASS |
| `git diff --check` | PASS |

The Phase 01 tests cover deterministic hashing and exclusions, content/file-set
drift, private atomic storage, timestamp and runtime tampering, Worker runtime
finalization, old-Worker health compatibility, rsync/finalization sequencing,
formal matching, missing identity, unverified identity, and cross-Worker drift.

## 9. Compatibility and security result

- No public CLI command was removed.
- No existing Dashboard or Worker route was removed.
- Worker health changes are additive.
- Benchmark strategies, metrics, 19-column request CSV, result durability,
  cancellation, suite cleanup, and RPC cleanup behavior remain unchanged.
- No secret, token, SSH key, model binary, `.run`, or result artifact was
  committed.
- The deployment never copies `.git`.
- The manifest and containing runtime directory are private (`0600`/`0700`).

## 10. Remaining formal blockers

Phase 01 resolves the live Worker source-identity blocker. It does not rewrite
the checked-in formal runtime lock or approve models.

The formal campaign remains ineligible until later phases address at least:

1. runtime lock deployment commit/fingerprint update under a new lock version;
2. at least one exact GGUF candidate promoted from `source_locked` to
   `approved` after Worker verification;
3. official model checksum, tokenizer/chat-template, provenance, and license
   evidence;
4. Jetson power/runtime cohort alignment or an explicitly separated condition.

These are Phase 02 and later concerns and were not started here.

## 11. Completion report

```text
Implemented:
- deterministic Worker source/deployment manifest
- private Worker-local runtime finalization
- additive Worker/Dashboard deployment health
- formal-only live deployment lock comparison
- missing/tampered/mismatch compatibility tests

Live acceptance:
- 6/6 Workers report identical committed source/tree and verified manifests
- 3/3 Jetsons share one CUDA runtime fingerprint
- 3/3 Raspberry Pis share one platform runtime fingerprint
- .git absent and manifest mode 0600 on all six

Tests passed:
- full Python regression: 347/347
- Phase 01 focused: 9/9
- macOS rsync compatibility focused: 11/11
- JavaScript, Python, Bash, export, and diff gates: PASS

Remaining issues:
- formal runtime lock still references the earlier unverified deployment state
- approved model count remains zero
- model provenance/identity and Jetson cohort blockers remain

Next phase readiness:
- READY for Roadmap Phase 02 Formal Model Approval
- Phase 02 was not started
```
