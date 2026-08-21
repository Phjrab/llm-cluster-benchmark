# Follow-up 01 – Raspberry Pi Power Integrity Domain and Telemetry Report

Date: 2026-08-21
Phase: FOLLOWUP 01 only
Baseline: 9f1dd5c docs(power): map Raspberry Pi power observability boundaries

## Scope and boundary

Implemented the typed, non-blocking Raspberry Pi power-integrity telemetry boundary described by FOLLOWUP 01. No experiment admission, measurement-quality policy, results schema, dashboard UI, hardware control, remote deployment, model operation, benchmark, or RPC work was started.

## Delivered implementation

- Added cluster/domain/power.py as a pure, framework-free decoder and typed model boundary.
- Added immutable RaspberryPiPowerIntegrity and PowerConditionBits models.
- Added PowerIntegrityStatus values: ok, history_warning, active_degraded, and unavailable.
- Added documented current and history flag decoding, deterministic reason-code ordering, raw-mask validation, and an unavailable result constructor.
- Added MeasurementQuality as a type-only Follow-up 02 extension point; no Follow-up 02 policy consumes it.
- Re-exported the domain API from cluster/domain/__init__.py.
- Added a RaspberryPiTelemetry power probe with fixed argv vcgencmd get_throttled, timeout=2, capture_output=True, check=False, and shell=False.
- Added an additive power_integrity object to the Worker health response only when the provider supplies a Raspberry Pi result.
- Kept generic and Jetson Worker health payload shape unchanged and preserved pre-existing inference/telemetry readiness behavior.

## Parser contract and required mask

The pure decoder accepts only the exact get_throttled output form with optional surrounding whitespace, including case variants such as throttled=0x0 and throttled = 0x50000. It rejects empty, malformed, negative, out-of-range, and extra-text values.

The required 0x50000 case decodes as history-only:

- current: under_voltage=false, frequency_capped=false, throttled=false, soft_temp_limit=false
- history: under_voltage=true, frequency_capped=false, throttled=true, soft_temp_limit=false
- status: history_warning
- blocking: false
- message: Historical Raspberry Pi power or thermal condition detected.
- stable reason-code order: PI_POWER_UNDERVOLTAGE_HISTORY, PI_POWER_THROTTLED_HISTORY

Unknown set bits are retained as unknown_bits and cannot produce an OK result.

## Failure and safety behavior

The probe never raises into the health route. Missing vcgencmd, timeout, non-zero command status, malformed output, and provider exceptions return a structured unavailable object. Public output contains no raw command error, stderr, local path, token, or SSH detail. The field is explicitly observational and always non-blocking.

No privileged command, sudo, shell invocation, network call, process lifecycle change, or remote hardware operation was added.

## Worker health compatibility

The new top-level power_integrity key is optional and additive:

- Raspberry Pi providers expose the structured result.
- Generic and Jetson providers omit the key.
- Existing injected telemetry doubles without the new method remain compatible.
- inference_ready and telemetry readiness retain their existing semantics.

Controller normalization was not changed because it already treats Worker health fields additively and does not require a strict fixed schema.

## Test gates

Focused execution completed successfully:

- 52 focused tests passed across Pi-power, Worker runtime, and Phase 14 security/regression coverage.

Full local gate completed successfully with actual loopback permissions:

- 254 Python tests passed.
- Python compileall passed.
- Dashboard JavaScript syntax and export fixtures passed.
- Shell syntax checks passed for repository shell scripts.
- Cluster CLI help surfaces passed.
- Cluster JSON configuration parsing passed.
- Git diff check passed.

The focused test coverage includes clean, history-only, active, each documented bit, malformed output, unknown bits, unavailable probe behavior, exact command invocation, no shell use, Worker health readiness invariance, and additive health response compatibility.

## Hardware acceptance

No Raspberry Pi or Jetson hardware validation was run in this phase. The implementation is intentionally source-level and test-double verified only. Follow-up hardware observation requires a separately authorized phase and a reachable worker.

## Compatibility and migration notes

- Existing Worker health clients continue to work because the new field is optional.
- Existing telemetry readiness and inference admission behavior are unchanged.
- Existing result artifacts, experiment schemas, dashboard UI, Worker configuration, RPC control paths, and benchmark execution are untouched.
- No package dependency was added.

## Completion definition

FOLLOWUP 01 is complete when this report, the typed decoder, Worker telemetry probe, additive health field, tests, and one Git checkpoint are present. FOLLOWUP 02 has not been started.
