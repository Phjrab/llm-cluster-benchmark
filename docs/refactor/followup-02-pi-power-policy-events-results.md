# Follow-up 02 — Pi Power Policy, Events, and Results

Date: 2026-08-21
Phase: FOLLOWUP 02 only
Baseline: 5dd93ac feat(telemetry): add Raspberry Pi power integrity status

## Scope

Connected the Follow-up 01 Raspberry Pi power-integrity observation to Controller preflight warnings, run-local observations, typed events, measurement-quality aggregation, result persistence, and multi-model suite aggregation.

No Dashboard rendering, live hardware acceptance, remote deployment, model operation, benchmark run, RPC run, schema-major migration, or request-metric formula change was performed.

## Preflight policy

Power integrity is an independent non-blocking axis.

- history_warning produces one typed PI_POWER_HISTORY warning per node.
- active_degraded produces deterministic active-condition warning records.
- unavailable produces PI_POWER_STATUS_UNAVAILABLE.
- every power warning has blocking=false.
- warnings are deduplicated by node and code.
- an accepted experiment response includes the additive warnings list.
- Worker offline, stale/not-ready environment, unverified backend, missing model, checksum failure, invalid strategy/config, and RPC readiness continue to use their existing blocking paths.
- power warnings do not participate in uniform-model configuration rejection.

The Controller normalizes Raspberry Pi Worker health into the Follow-up 01 canonical shape. A legacy Pi Worker with no power_integrity field becomes non-blocking unavailable. Jetson and generic Workers remain outside this Pi-specific axis.

## Observation lifecycle

BenchmarkRunner now owns a run-local RunPowerIntegrityTracker and collects bounded snapshots at:

1. preflight, before model/RPC preparation
2. pre_measurement, after warmup and before measured scheduling
3. measurement, at the measurement boundary before the benchmark wall timer starts
4. postflight, immediately after scheduling/RPC cleanup or during failure finalization

The measurement-boundary sample is deliberately outside benchmark timing. It does not change task planning, request concurrency, selected Worker order, warmup exclusion, or wall-time metric formulas.

This phase does not introduce high-frequency polling. A short transient condition occurring strictly between the measurement-boundary and postflight snapshots may be missed. New historical bits at postflight still identify a condition that likely appeared during the observed run interval without claiming causation.

Power-only sampling failures are normalized to unavailable and never fail request execution. Existing Worker/API/model failures remain failures.

## Measurement quality algorithm

Per-node quality is deterministic:

- active condition at pre-measurement, measurement, or immediate postflight: degraded
- history bit absent at valid preflight but present at valid postflight: degraded
- historical bit or unknown firmware bit only: warning
- no valid snapshot: unknown
- valid clean snapshot plus unavailable observation: warning with PI_POWER_OBSERVATION_INCOMPLETE
- all valid observations clean and complete: clean

Overall quality uses the explicit severity order:

degraded > warning > unknown > clean

The selected nodes' worst quality is used. Run status is never computed from measurement quality, so completed + degraded and failed + warning remain valid combinations.

## Events and deduplication

Run events use the existing experiment channel:

- power_integrity_snapshot for preflight, pre_measurement, and postflight durable boundaries
- power_integrity_changed only when availability, status, documented flags, reason codes, or unknown bits change
- measurement_quality_finalized once with the final aggregate

Repeated identical samples do not produce transition events. Recovery from active to warning/clean and available/unavailable transitions do produce new events. Unknown-bit changes are intentionally considered semantic transitions and are retained as evidence.

The Controller StatusMonitor independently emits power_integrity_status on the system channel only for initial state or a semantic transition. Normal metric churn and observed_at changes do not repeat this event.

Existing events.jsonl append/fsync behavior is reused, so a preflight or transition snapshot survives even if no final summary is written.

## Result persistence

Schema version remains 2. requests.csv retains the exact existing 19-column header. responses.jsonl does not duplicate power metadata per request.

New summary fields are additive:

    {
      "measurement_quality": "warning",
      "measurement_quality_reasons": [
        "PI_UNDERVOLTAGE_HISTORY",
        "PI_THROTTLING_HISTORY"
      ],
      "power_integrity": {
        "overall": {
          "quality": "warning",
          "reason_codes": [
            "PI_UNDERVOLTAGE_HISTORY",
            "PI_THROTTLING_HISTORY"
          ]
        },
        "nodes": {
          "pi-01": {
            "quality": "warning",
            "preflight": {"status": "history_warning", "raw_hex": "0x50000"},
            "pre_measurement": {"status": "history_warning", "raw_hex": "0x50000"},
            "measurement": {
              "sample_count": 1,
              "valid_sample_count": 1,
              "unavailable_sample_count": 0,
              "active_warning_samples": 0,
              "worst_status": "history_warning"
            },
            "postflight": {"status": "history_warning", "raw_hex": "0x50000"},
            "reason_codes": [
              "PI_UNDERVOLTAGE_HISTORY",
              "PI_THROTTLING_HISTORY"
            ]
          }
        },
        "warnings": [
          {
            "code": "PI_POWER_HISTORY",
            "stage": "preflight",
            "node": "pi-01",
            "blocking": false
          }
        ]
      }
    }

The existing top-level warnings list remains string-based for compatibility. Typed warning evidence lives in power_integrity.warnings rather than replacing the legacy field.

Failed runs retain their original failed/cancelled status and add the last observed power state to structured failure evidence. The evidence does not state that power caused the failure.

Each model run in a suite has independent quality. Suite artifacts add worst measurement_quality and clean/warning/degraded/unknown counts, while suite status remains controlled only by existing model success, cancellation, and cleanup semantics.

## Legacy compatibility

- Results without measurement_quality or power_integrity remain readable and are not rewritten.
- Runs with no applicable Pi observation omit the new fields.
- Worker health route names and existing fields are unchanged.
- Existing Dashboard and Worker route names are unchanged.
- Existing event fields remain; experiment_id is additive on new durable run events.
- requests.csv and responses.jsonl semantics are unchanged.
- benchmark strategy planning, logical/physical counts, p50/p95, throughput, agreement, speedup, efficiency, cancellation, per-node serialization, suite cleanup, and RPC cleanup semantics are unchanged.
- no package dependency was added.

## Tests

Focused Follow-up 02 tests cover:

- clean, history-only, active, new-history, unavailable, incomplete, and multi-node quality
- postflight active-condition handling
- deterministic warning ordering and deduplication
- transition, recovery, and no-repeat event behavior
- crash-time preflight journal durability
- completed + degraded and failed + degraded run persistence
- structured failure power evidence
- schema version 2 and exact requests.csv compatibility
- no power duplication in responses.jsonl
- legacy/non-Pi field omission
- suite status independence
- Controller normalization for current and legacy Pi Workers
- non-blocking preflight and preservation of Worker/backend/model blockers
- system-channel transition deduplication

Final local gates:

- 274 Python tests passed with actual local loopback permissions.
- Python compileall passed.
- Dashboard JavaScript syntax and export fixtures passed.
- all repository shell syntax checks passed.
- Controller, benchmark runner, and durable job CLI help passed.
- cluster configuration JSON parsing passed.
- git diff check passed.

## Hardware acceptance

NOT RUN — hardware-independent implementation phase.

No SSH, Worker lifecycle, package installation, model transfer, remote vcgencmd, inference benchmark, RPC process, power configuration, or remote filesystem mutation was performed.

## Remaining risks

- No high-frequency measurement sampler was added; transient power conditions can be missed unless they remain active at a boundary or set a new historical bit.
- A legacy auto-platform Worker whose health request fails cannot be identified as Raspberry Pi from that failed request; the existing blocking Worker-offline policy remains authoritative.
- Dashboard visualization of the additive fields belongs to FOLLOWUP 03.

## Definition of Done

- [x] non-blocking power preflight warnings
- [x] existing blocking preflight conditions preserved
- [x] typed warning records
- [x] preflight/pre-measurement/measurement/postflight observations
- [x] deterministic quality and new-history detection
- [x] transition events and duplicate suppression
- [x] additive summary and suite metadata
- [x] legacy result fallback
- [x] run status independent from quality
- [x] schema version and requests.csv unchanged
- [x] benchmark semantics unchanged
- [x] focused/full tests passed
- [x] hardware not run
- [ ] commit and push after final diff review

FOLLOWUP 03 has not been started.
