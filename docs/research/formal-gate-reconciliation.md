# Formal Gate Reconciliation — 2026-08-28

## Outcome

Phase 09 v5 completed after the earlier Phase 10 stop report. Its historical
decision is now represented exactly in the checked-in matrix, protocol, and
analysis plan: **15 independent repeats** and a **180-second minimum cooldown**.
This reconciliation does not start or authorize the formal campaign.

The gate remains fail-closed because every observed v5 Worker record identifies
source commit `7aeb9fedf7ade0ecefa5fe799cfaef31e1c5401f`, while measurement-affecting
changes were committed afterward. A formal result must not combine the old
pilot decision with an unvalidated current measurement implementation.

## Preserved evidence

| Item | Frozen value |
|---|---|
| Pilot ID | `formal-study-v1-phase09-pilot-v5` |
| Declared / successful observations | 28 / 28 |
| Attempts | 29 |
| Freeze ready | `true` |
| Selected repeats | 15 |
| Minimum cooldown | 180 seconds |
| Maximum Worker collection overhead | 0.047984985256483446 |
| Allowed collection overhead | 0.05 |
| Pilot blockers | none |
| Pilot plan file SHA-256 | `9fdd5de46b425d55905295af4d9e1d5faa042ef07da46663b86c13da190e7ad2` |
| Manifest SHA-256 | `796aa9fb5963d9c7f18039ffcb6549b6a3402a5b97fbfb5ac66c3bcff12922b0` |
| Analysis SHA-256 | `cf7d914fba1afc58031166fb43544cccf122b7aebcc1d1c3e82cd8fe81523e1a` |

The machine-readable record is
[`pilot_freeze_decision.json`](../../config/research/pilot_freeze_decision.json).
It distinguishes a frozen historical decision from present execution
compatibility instead of rewriting or deleting the runtime evidence.

## Current-source gap

The current line includes post-pilot benchmark tracing, energy provenance,
formal model admission, and private-LAN safety changes. These changes are useful
and tested, but the protocol requires another bounded pilot whenever a repair or
behavior change can affect formal measurement or admission.

The formal execution gate therefore contains no stale phase-number blocker. It
contains two concrete requirements:

```text
CURRENT_SOURCE_PILOT_REVALIDATION
RUNTIME_SOURCE_RELOCK
```

The Dashboard and campaign builder both treat `formal_execution_allowed=false`
as authoritative even when `blocking_phases` is empty.

## Predeclared current-source pilot

[`pilot_plan.v6.json`](../../config/research/pilot_plan.v6.json) is the
separated current-source revalidation plan. It preserves the v5 workload and
selection policy, but has its own durable pilot ID and result directory. It
contains 28 serialized observations: eight thermal calibration observations and
twenty independent variance observations. It is not a formal campaign and does
not pool its records with v5 or formal results.

## Raspberry Pi-only follow-up

At the operator's request, v6 was stopped after 13 completed observations,
during the cooldown before its next Raspberry Pi run. Its manifest and every
completed result remain immutable evidence. It is not a completed
current-source revalidation.

[`pilot_plan.v7_pi_only.json`](../../config/research/pilot_plan.v7_pi_only.json)
is a separately preregistered 19-observation Raspberry Pi-only follow-up: four
Pi thermal calibration observations and fifteen independent variance
observations across Pi 02, Pi 02–03 round-robin, and Pi 02–03–04 broadcast
cells. Jetson Worker 02 is not a participant. The `HARDWARE_SCOPE_CHANGE`
reason makes this scope reduction explicit; v7 cannot replace Jetson evidence,
does not pool with v6, and leaves the formal execution gate closed.

## Work intentionally not performed

- no model load or inference request;
- no RPC process or port;
- no Worker mutation, deployment, restart, or power-mode change;
- no formal campaign directory, manifest, or result;
- no bounded current-source hardware pilot.

After the bounded pilot passes, the Controller/Worker source and runtime
identities must be re-locked and the execution gate reviewed in a separate
checkpoint. Only then may the selected 1,080-run formal campaign be created.
