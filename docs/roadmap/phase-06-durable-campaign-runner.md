# Roadmap Phase 06 — Durable Campaign Runner

## 1. Outcome

Phase 06 is complete on branch `codex/roadmap-phase-06`.

The project now has a durable, one-cell-at-a-time formal campaign execution
layer with deterministic seeded block order, complete manifests, pause/resume,
cancel, explicit retry, cooldown, fresh drift/thermal gates, durable job
integration, restart recovery, and duplicate-execution prevention.

No pilot or formal campaign was started. The checked-in formal matrix remains
fail-closed until Phase 09 supplies the repeat-count and cooldown/thermal
evidence and refreshes the final runtime/source lock.

## 2. Checkpoints

| Checkpoint | Commit |
|---|---|
| Durable campaign runner, gates, job integration, result trace | `8499f98` |
| Exact Worker model-path freeze for restart reconstruction | `a3f09eb` |

Both commits were pushed to `origin/codex/roadmap-phase-06` immediately after
creation.

## 3. Deterministic scheduling and manifest

`cluster/research/scheduler.py` expands the 72 base cells by the pilot-selected
repeat count. For every repeat it ranks:

1. `(platform_cohort, order_block)`;
2. cells inside each block.

Ranks use SHA-256 over a versioned namespace, seed `20260823`, repeat, and
immutable identity. The order is independent of Python's pseudo-random
implementation and is identical when rebuilt with the same inputs.

The executable campaign manifest freezes:

- every `campaign_cell_id`, one-based repeat, and global order index;
- exact Worker node set and strategy;
- exact model-lock key and Worker-relative GGUF path;
- prompt, condition, runtime, and measurement-quality identities;
- every attempt, durable job ID, run ID, failure, warning, drift, cleanup, and
  quality state;
- retry and cooldown policies;
- exact coverage plus expected runtime, timeout envelope, and storage.

For the minimum permitted repeat count, an opened fixture gate produces 720
unique ordered cells. The maximum remains 2,160. The shipped gate does not
produce a manifest because the Phase 09 decision does not yet exist.

## 4. Durable state and recovery

Artifacts are private and atomic:

```text
.run/controller/campaigns/<campaign-id>/
├── manifest.json     # 0600, atomic replace + fsync
├── events.jsonl      # 0600, append + fsync
└── .campaign.lock    # 0600, process lock
```

The campaign directory is `0700`; symbolic-link repository, campaign, manifest,
and event paths are rejected.

A cell is durably claimed before an external job can start. Two concurrent
runner instances cannot both claim it. A newly constructed runner after a
Dashboard restart reads the same manifest and inspects the exact durable job.
It never starts a replacement while the remote state is uncertain. Completed
cells are omitted from future scheduling permanently.

If a claim never acquires a durable job identity, it becomes
`CAMPAIGN_INTERRUPTED` only after a bounded recovery grace. Failed or cancelled
cells are not retried automatically. An operator must supply a manual retry
reason, and all prior attempts remain in the manifest.

Pause waits for an active attempt to become terminal before preventing the next
cell. Cancel targets the exact durable job and preserves cleanup evidence.
Cleanup failure changes the effective cell outcome to failed. Campaign-level
cooldown survives process restart as an absolute timestamp.

## 5. Fresh formal gates

Each claimed cell runs fresh preflight and thermal checks before job start.
The implementation records and blocks on missing or mismatched model SHA,
deployment source, runtime lock/fingerprint/version, RPC commit, Jetson power
mode, `jetson_clocks`, prompt set, condition profile, backend verification, NTP,
storage, or active Pi power fault.

Historical Raspberry Pi power bits remain the approved non-blocking
`PI_POWER_HISTORY` warning. Active Pi undervoltage/throttling at formal
preflight is blocking.

Blocking drift records a blocked attempt, leaves the cell pending, and pauses
the campaign. It is not auto-repaired and is not converted into a clean run.

## 6. Existing job and result compatibility

`DurableJobRunBackend` connects cells to the existing independent child-process
job service. `CampaignJobDocumentFactory` reconstructs one exact model/run job
from the manifest and frozen lock documents. It validates that the mapped GGUF
basename is the approved locked binary.

Formal run config, events, completed summaries, and failed summaries add:

```text
research_identity
  campaign_id
  campaign_cell_id
  campaign_attempt_id
  repeat_index
  order_index
  experiment/model/prompt/runtime/condition identities
  measurement_quality_policy
```

Legacy ad-hoc runs omit the field. Existing routes and job records remain
additive. The 19-column `requests.csv` schema is unchanged.

## 7. Connected hardware read-only verification

No code, package, model, environment, power mode, or RPC binary was installed,
rebuilt, synchronized, or removed in this phase. Existing environments were
reused.

All six registered Workers were inspected through the existing inventory and
status paths:

| Worker | Address | SSH | Project | API | Deployment tree |
|---|---|---:|---:|---:|---:|
| jetson-worker-01 | 192.168.0.26 | PASS | PASS | PASS | verified |
| jetson-worker-02 | 192.168.0.19 | PASS | PASS | PASS | verified |
| jetson-worker-03 | 192.168.0.6 | PASS | PASS | PASS | verified |
| pi-worker-02 | 192.168.0.14 | PASS | PASS | PASS | verified |
| pi-worker-03 | 192.168.0.9 | PASS | PASS | PASS | verified |
| pi-worker-04 | 192.168.0.5 | PASS | PASS | PASS | verified |

Every Worker reports llama-cpp-python `0.3.20` and native llama.cpp RPC commit
`f49e9178767d557a522618b16ce8694f9ddac628`. Jetsons share runtime fingerprint
`05e2c27b3bf0eff2`; Pis share `b4387053e655722a`.

The read-only drift check intentionally reports `SOURCE_FINGERPRINT_MISMATCH`
for all six Workers:

- deployed source is the Phase 05 measurement checkpoint
  `554c3fafacbbb6a6f4d044373c682232f2c874a6`;
- the still-frozen Phase 03 runtime lock expects
  `6dc88f8b2feeb8d7531d966dc11e4e25bc66c093`.

This is correct fail-closed behavior, not a Worker failure. Runtime backend
fingerprints and RPC commits still match. Phase 09 must relock the final source
after the pilot decision; Phase 06 does not rewrite scientific locks to hide
drift.

## 8. Test gates

Final HEAD validation:

- full Python project regression with local Controller socket lifecycle:
  **400/400 PASS** in **54.178 seconds**;
- Phase 06 campaign/matrix focused gate: **33/33 PASS**;
- campaign-specific scheduler, repository, recovery, concurrency, drift,
  retry, cancellation, cooldown, job adapter, and model mapping tests:
  **15/15 PASS**;
- Dashboard JavaScript syntax: PASS;
- Dashboard export fixtures: PASS;
- Python compileall: PASS;
- all project shell scripts `bash -n`: PASS;
- research JSON parse: PASS;
- `git diff --check`: PASS.

The only warning is the existing upstream Starlette/httpx deprecation warning.
No dependency was installed or upgraded.

## 9. Main files

Campaign core:

- `cluster/research/campaign.py`
- `cluster/research/scheduler.py`
- `cluster/research/eligibility.py`
- `cluster/research/protocol.py`
- `cluster/integrations/campaign_jobs.py`

Trace and layout:

- `cluster/domain/experiment.py`
- `cluster/domain/identifiers.py`
- `cluster/domain/layout.py`
- `cluster/integrations/runtime_layout.py`
- `cluster/benchmark/core.py`
- `cluster/benchmark/persistence.py`

Contracts and evidence:

- `config/research/campaign_manifest.schema.json`
- `config/research/formal_experiment_matrix.json`
- `docs/research/durable-campaign-runner.md`
- `cluster/tests/test_research_campaign.py`
- `cluster/tests/test_research_matrix.py`

## 10. Stop boundary and remaining work

- Phase 07 campaign/compare Dashboard UX was not started.
- Phase 09 pilot, repeat-count decision, thermal stabilization rule, final
  source relock, and formal gate opening were not started.
- No campaign was created in the live results/runtime directories.
- No pilot/formal requests were sent to Workers.
- No Worker was restarted or modified.

Phase 06 stops here. The next roadmap step is Phase 07 only when explicitly
requested.
