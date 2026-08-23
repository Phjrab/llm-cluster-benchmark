# Durable Campaign Runner

## Scope

Roadmap Phase 06 adds the durable execution layer for the frozen formal matrix.
It does not choose the final repeat count, change any research lock, or start a
formal campaign. Phase 09 pilot evidence must first freeze the repeat count and
cooldown/thermal stabilization rule.

## Deterministic order

The scheduler expands every base matrix cell for every one-based repeat. The
protocol seed is applied to two immutable ranks per repeat:

1. `(platform_cohort, order_block)` rank;
2. `cell_id` rank inside that block.

Both ranks are SHA-256 over a versioned namespace, the integer seed, repeat,
and immutable identity. This implements seeded randomized blocks without
depending on a Python pseudo-random implementation. The resulting
`campaign_cell_id`, `repeat_index`, and global `order_index` are frozen in the
manifest before the first run.

## Durable artifacts

Campaign state is stored under:

```text
.run/controller/campaigns/<campaign-id>/
├── manifest.json
├── events.jsonl
└── .campaign.lock
```

The directory is mode `0700`; documents, journal, and lock are mode `0600`.
Manifest replacement is atomic and fsynced. Symbolic-link campaign paths and
journals are rejected. The manifest is the source of truth for:

- campaign status and current phase;
- every ordered cell and its coverage state;
- every attempt, including blocked, failed, cancelled, and manual retry
  attempts;
- durable job ID and run ID;
- drift, warning, cleanup, and measurement-quality evidence;
- cooldown deadline, pause request, and cancel request;
- expected run count, runtime envelope, and storage.

The manifest also freezes the exact Worker-relative GGUF path for every active
model-lock key. The basename must match the approved locked binary. This makes
job reconstruction independent of Dashboard memory after restart and prevents
a model key from being remapped to a different GGUF between cells.

## Lifecycle

```text
ready -> running -> completed
                  -> failed
                  -> paused -> ready
                  -> cancelled
```

Only one cell may be `running`. A cell is atomically claimed and its attempt is
persisted before an external job can start. A second Dashboard or campaign
runner instance sees that claim and cannot launch a duplicate.

After a Dashboard restart, the new runner inspects the persisted durable job.
It records a terminal result if one exists, waits if the job is still running,
and never launches a replacement when remote state is uncertain. A claim that
never acquired a durable job ID becomes `CAMPAIGN_INTERRUPTED` only after the
bounded start-recovery grace period.

Completed cells are never eligible for scheduling again. Automatic retry is
forbidden. A failed or cancelled cell can be made pending only by an explicit
manual retry reason, and prior attempts remain immutable evidence.

Pause stops scheduling after the active cell becomes terminal. Cancel is sent
to the exact durable job and remaining pending cells become cancelled only
after the active attempt reaches a terminal state. Cleanup failure changes the
effective attempt/cell outcome to failed.

## Fresh gates before each run

Every claimed cell passes fresh preflight and thermal gates before job start.
The blocking drift family includes:

```text
MODEL_SHA_MISMATCH
SOURCE_FINGERPRINT_MISMATCH
RUNTIME_LOCK_MISMATCH
JETSON_POWER_MODE_MISMATCH
JETSON_CLOCKS_MISMATCH
PROMPT_SET_MISMATCH
CONDITION_PROFILE_MISMATCH
BACKEND_NOT_VERIFIED
NTP_NOT_SYNCHRONIZED
STORAGE_INSUFFICIENT
PI_POWER_ACTIVE
```

Missing model, source, or preflight evidence fails closed. Historical
Raspberry Pi power bits remain a non-blocking `PI_POWER_HISTORY` warning.
Active Pi power faults at the formal preflight boundary block a run.

Blocking drift leaves the cell pending, records the blocked attempt, and pauses
the campaign. It is never silently accepted or automatically repaired.

## Existing durable job integration

`DurableJobRunBackend` maps campaign attempts onto the existing independent
child-process job service. `CampaignJobDocumentFactory` creates exactly one
model/run job from the frozen cell, prompt, model mapping, platform profile,
and benchmark profile. Job recovery, cancellation, suite cleanup, and process
identity protection remain owned by the existing durable job service.

Formal run `config.json`, events, and success/failure summaries add a
`research_identity` trace containing:

- campaign, cell, and attempt IDs;
- repeat and order indices;
- experiment/model/prompt/runtime lock identities;
- condition profile and measurement-quality policy.

Legacy ad-hoc runs omit this field. The existing 19-column `requests.csv`
schema is unchanged.

## Formal execution gate

The shipped matrix currently remains:

```text
formal_execution_allowed = false
blocking_phases = [9]
```

Manifest construction therefore fails before writing or starting anything.
Phase 09 must provide the pilot-derived repeat-count evidence, freeze the
cooldown/thermal rule, refresh the final runtime/source lock as required, and
open the execution gate. A caller cannot use the Phase 06 API to bypass this
state.
