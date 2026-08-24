# Roadmap Phase 10 — Formal Experiment Campaign

## 1. Outcome

Phase 10 was entered on branch `codex/roadmap-phase-10`, but formal execution
was **not admitted**. This is the required fail-closed outcome, not a campaign
failure.

The Phase 09 v3 pilot was stopped by explicit user request with 7 of 28
declared attempts observed. Its durable analysis reports `freeze_ready=false`.
The checked-in formal matrix and protocol independently remain in their
planned-pending-pilot state. No campaign manifest, formal run, formal request,
or formal result was created.

## 2. Admission evidence

The durable pilot state at Phase 10 entry was:

| Evidence | Value |
|---|---:|
| Pilot | `formal-study-v1-phase09-pilot-v3` |
| Declared attempts | 28 |
| Observed attempts | 7 |
| Successful attempts | 6 |
| User-interrupted attempts | 1 |
| Attempt failure rate | 14.29% |
| Predeclared maximum attempt failure rate | 5% |
| Freeze ready | `false` |

All six completed runs had 100% request success. This does not replace the
missing independent run-level variance evidence and cannot authorize formal
execution.

## 3. Blocking decisions

### Repeat count

No variance cell has the five successful independent pilot runs required by
the preregistered plan. The analyzer's provisional 30-repeat cap is an
incomplete-data sentinel, not a frozen repeat decision.

### Cooldown and thermal recovery

No cooldown is valid across every calibration cohort:

- Jetson candidates at 3, 15, and 30 seconds passed in the observed runs.
- Raspberry Pi 3 seconds failed the recovery tolerance.
- Raspberry Pi 15 seconds was interrupted.
- Raspberry Pi 30 seconds was not measured.

The selected minimum cooldown therefore remains `null`.

### Instrumentation

The preregistered Worker-internal telemetry collection cap is 5% of wall time.
The maximum observed Jetson fraction was 3.69%, while Raspberry Pi reached
12.39%. Instrumentation affecting a primary outcome is an explicit formal
stop condition.

### Formal lock state

The checked-in artifacts remain intentionally closed:

- matrix status: `planned_pending_phase_09_pilot`;
- `formal_execution_allowed=false`;
- blocking phase: `9`;
- protocol status: `planned_pending_pilot`;
- `formal_start_blocked_until_frozen=true`.

The repository's campaign builder rejects this state before creating a
manifest. Manually changing these fields without complete pilot evidence would
violate the preregistered protocol and is not an accepted workaround.

## 4. Formal campaign scope preserved

If the gate is opened by a completed revised pilot, the frozen matrix currently
expands to 72 base cells and 10–30 independent repeats:

| Quantity | Minimum | Maximum |
|---|---:|---:|
| Formal runs | 720 | 2,160 |
| Logical requests | 14,400 | 43,200 |
| Physical requests | 17,600 | 52,800 |
| Warmup requests | 1,120 | 3,360 |
| Model loads/unloads | 720 each | 2,160 each |
| Estimated result storage | 320 MiB | 960 MiB |

The execution order remains:

1. Jetson single-node;
2. Raspberry Pi single-node;
3. homogeneous replicated/scaling cells;
4. broadcast consistency;
5. separately locked exploratory or RPC work only when eligible.

Pilot artifacts remain physically and semantically separate from this formal
result pool. Failed and interrupted pilot evidence was not deleted or promoted.

## 5. Safety boundary

No live-hardware action was performed in Phase 10:

- no Worker package, model, source, or runtime change;
- no Worker restart or power-mode change;
- no model load or inference request;
- no campaign directory or formal result artifact;
- no RPC process or port;
- no mutation of the matrix, protocol, analysis plan, or scientific locks.

This preserves the existing Workers and the incomplete Phase 09 evidence while
preventing an invalid formal dataset from being created.

## 6. Verification

Read-only admission checks confirmed:

- the pilot manifest is `incomplete` / `user_stopped`;
- the pilot analysis is `freeze_ready=false`;
- all three cooldown candidates are false across the required cohorts;
- pilot failure and instrumentation blockers are present;
- the formal matrix and protocol still carry both independent pilot gates;
- `.run/controller/campaigns/` contains no campaign artifact;
- the existing formal-manifest regression rejects the shipped closed gate.

No full live-hardware or long-running local validation was repeated because the
user explicitly ended Phase 09 validation. GitHub Required CI is the repository
regression checkpoint for this documentation-only Phase 10 stop.

## 7. Stop boundary and valid next actions

Phase 10 stops before campaign creation. Formal execution remains blocked until
all of the following are versioned and frozen together:

1. a complete revised pilot with independent run-level variance evidence;
2. a valid repeat-count decision;
3. a Raspberry Pi cooldown and stabilization rule;
4. Raspberry Pi telemetry overhead within the accepted policy, or a new
   preregistered measurement policy validated by a pilot;
5. updated matrix/protocol/analysis-plan and source/runtime lock fingerprints.

Development that does not claim formal results may continue. Phase 11 can only
develop analysis/export tooling against fixtures and separated pilot data; it
must not label the incomplete pilot as formal evidence.
