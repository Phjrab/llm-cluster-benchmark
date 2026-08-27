# WS-05 Formal Lock Admission

## Scope and gap analysis

WS-05 completes the formal-only admission contract without changing normal Dashboard benchmark admission. Existing model, prompt, runtime, and condition lock documents remain the source of truth; no model was downloaded, promoted, or reclassified by this workstream.

| Requirement | Existing state | Gap found | Resolution |
|---|---|---|---|
| Immutable model source and GGUF identity | Repository, 40-character source commit, exact basename, SHA-256, quantization, and license metadata were validated | `size_bytes` was present but not validated | Require a positive exact byte size for every lock entry |
| Approval evidence | Approved models required template/tokenizer hashes and per-Worker SHA-256 | Per-Worker observed size and the aggregate identity verdict were not enforced | Require matching size and `observed_identity_matches_expected=true` before `approved` validates |
| License decision | Gated licenses remained false until explicitly accepted | No automatic acceptance path was found | Preserve operator-owned values; this workstream never writes acceptance state |
| Worker-specific admission | Fresh model SHA and deployment identity were checked before a campaign cell | A Worker with a matching live file could be selected even if it was absent from the model's approved Worker set | Block with `MODEL_WORKER_NOT_VERIFIED` |
| Fresh run-start identity | Campaign preflight already checks deployment commit, runtime fingerprint, backend, model SHA, power state, and snapshots | The lower-level formal gate did not fail closed when a supplied preflight omitted model SHA | Add `MODEL_SHA_MISSING`; campaign admission remains fail-closed |
| Formal trace | All six required fields are stored in `ExperimentConfig`, run summary, failure summary, and durable campaign job | No gap | Reused unchanged |
| Ordinary benchmark compatibility | Formal assessment is only called from the research campaign gate | No gap | No normal benchmark code or schema changed |
| Deterministic lock fingerprint | Canonical sorted JSON excludes observation timestamps | No gap | Existing behavior and regression tests retained |

## Design note

Public Dashboard and Worker APIs are unchanged. The result schema is unchanged. Formal admission adds two structured reason codes: `MODEL_WORKER_NOT_VERIFIED` and `MODEL_SHA_MISSING`. Existing lock files remain valid because approved entries already contain exact per-Worker size and identity evidence.

Validation and eligibility remain pure functions: they perform no file access, network calls, model downloads, license acceptance, or Worker mutation. The live snapshot is collected outside the gate immediately before a campaign cell starts. A missing or mismatched identity pauses that formal campaign cell; it does not affect smoke, pilot, or ordinary Dashboard benchmarks.

This change adds no work to the inference timing interval. Model size and checksum comparison occur during formal preflight, before the durable backend job starts.

## Failure behavior

- An incomplete `approved` entry fails lock validation before a formal manifest or job can be accepted.
- A selected Worker without frozen model evidence receives `MODEL_WORKER_NOT_VERIFIED`.
- A fresh snapshot without `model_sha256` receives `MODEL_SHA_MISSING`.
- A checksum, source commit, source tree, backend runtime fingerprint, llama-cpp version, or RPC commit mismatch remains blocking.
- Unapproved entries stay `source_locked`, `worker_verified`, or `rejected`; there is no automatic promotion.

## Compatibility and non-goals

- No existing result file, request CSV column, Dashboard endpoint, or CLI command changed.
- No model binary or Worker state was modified.
- No license was accepted automatically.
- This workstream does not begin or claim completion of a hardware formal campaign.

## Verification

- Focused lock and campaign tests cover immutable identity, unapproved models, per-Worker checksum and size, missing live checksum, runtime/source mismatch, trace persistence, and durable start blocking.
- The complete Python, repository validation, packaging, JavaScript, and Chromium gates are run before the workstream checkpoint.
- Hardware validation is reported as unavailable when no Workers are connected; absence is not treated as success.

## Remaining risk

The lock proves identity only for Workers explicitly listed in each approved model entry and only against a freshly collected preflight snapshot. Adding or reinstalling a Worker requires a new observation and an intentional lock update. License ownership remains an operator decision.
