# S01 sweep compiler contract (schema version 1)

This is an offline planning interface, not an API endpoint or execution engine.
`cluster.domain.sweep` owns immutable records and strict parsing;
`cluster.application.sweep_planner` owns pure compilation and import verification.
No existing ExperimentConfig, Worker, JobService, Dashboard or research lock is changed.

## Boundary and usage

```python
from cluster.domain.sweep import SweepSpec, ResolutionContext
from cluster.application.sweep_planner import compile_plan, verify_plan

spec = SweepSpec.from_json(spec_json)
context = ResolutionContext.from_dict(cached_server_evidence)
preview = compile_plan(spec, context)
assert preview.executable is False
restored = verify_plan(preview.to_json())
```

The caller supplies cached facts; none are fetched. No raw prompt text, credential,
SSH path, runtime command, approval or arbitrary URL field exists in these records.
The resolution context must eventually be built from server-owned evidence in S03/S07.
A self-consistent imported hash is **not** authentication or operator approval.

The prompt package's `sweep_spec_template` examples are explanatory, unresolved
templates and intentionally fail this executable-intent schema's strict parser.
They use different field shapes, including an axes object and operator selectors.
An eventual UI/template adapter must bind them explicitly. The S01 wire format
uses an **ordered array** of AxisSpec objects so JSON mapping key ordering cannot
accidentally change the declared axis order.

## Input shape

```json
{
  "schema_version": 1,
  "artifact_type": "sweep_spec",
  "mode": "exploratory",
  "revision": 1,
  "combination": "grid",
  "base": {
    "model_ref": "model-a",
    "prompt_ref": "same-text",
    "worker_ids": ["worker-a"],
    "execution_strategy": "replicated_round_robin"
  },
  "axes": [
    {"name": "model_ref", "values": ["model-a", "model-b"]},
    {"name": "n_ctx", "values": [1024, 2048, 4096]},
    {"name": "concurrency", "values": [1, 3, 6]},
    {"name": "max_tokens", "values": [64, 128]}
  ],
  "repeat_count": 3,
  "execution": {"mode": "sequential", "max_parallel_jobs": 1},
  "budget": {"max_trials": 500, "max_physical_requests": 1000000}
}
```

All names above are illustrative references, not an actual inventory or installed
model. `cluster/tests/test_sweep_planner.py:context_data` supplies synthetic pinned
identities for executable tests of the compiler, without opening GGUF files.

- Scalar axes: concurrency, n_ctx, max_tokens, n_gpu_layers, n_threads, n_batch,
  temperature, top_p, seed. Reference axes: model_ref, prompt_ref, rpc_profile_ref.
  Empty axes, unsupported names, booleans used as numbers, nonfinite numbers,
  duplicate axis names and unknown fields fail closed.
- RunCondition keeps worker order, strategy, cumulative/individual mode, request
  count, warmup, positive timeout and privacy settings. Defaults match ordinary
  ExperimentConfig scalars (threads/batch remain omitted). Formal/pilot trace
  fields and ignored_config_keys are not accepted.
- RPC uses ordered worker_ids including coordinator_id, layer/row, auto/equal/custom,
  exact Worker-keyed positive finite weights for custom only, and all/integer GPU
  policy. A profile is one axis value. A changing topology may omit base.worker_ids;
  if supplied, it must match that profile exactly. No ratio/device-order rewrite
  or session startup occurs in S01.
- ModelReference binds catalog ID, model path identifier, source revision,
  quantization, artifact SHA or ordered artifact-set manifest SHA, template SHA,
  installed Workers and cached availability/runtime-compatibility verdicts.
  Artifact sets remain blocked until a loader exists. RPC installation is checked
  on the coordinator; ordinary runs require the model on every participant.
- PromptVariant is keyed by (prompt ref, model ref) and keeps text/template hashes,
  same_text/token_length_profile, optional cached exact input count/source and target.
  same_text requires matching text hashes across model variants. Without exact
  evidence the budget is unknown; exact cached input+output reserve overflow blocks.
  S02 must still implement fresh template-aware checks after operator Start.
- WorkerReference binds a stable ID to a caller-resolved opaque physical endpoint
  identity and platform/capability evidence. Aliases of one endpoint block a cell.
  This is not a reservation implementation.

## Combination, budgets and identities

Grid preserves declared axis/value order. One-at-a-time emits the baseline then
one change at a time. Explicit mode accepts complete RunCondition objects (schema
defaults apply), without axes or implicit merging with base. Duplicate candidates
remain as rows with duplicate_of pointing at the first candidate, but create no
extra Trial. OAT therefore executes its baseline once even if every axis repeats it.

The compiler checks candidate count × repeat count before Cartesian materialization,
deduplication or exclusions. Defaults and hard ceilings are 500 candidate trials,
64 values per axis, 64 Workers/profiles, 128 models and 512 prompt variants. Spec
JSON is limited to 1 MiB; expanded plan import to 32 MiB. The physical-call budget
includes warmup and is capped at 1,000,000. Raising ceilings is a future reviewed
policy change, not a client bypass. Threads 1–1024/batch 1–16384 are syntax limits,
not claims of supported backend values; both axes remain blocked.

An exclusion needs a known semantic cell ID, nonempty reason and spec revision ≥2.
All duplicates of that condition are excluded consistently. Counts expose candidate,
unique, duplicate, excluded, included and valid/blocked/unknown rows separately.
Exclusion cannot evade the pre-materialization candidate budget.

Canonical JSON v1 sorts object keys but preserves arrays. Integral floats normalize
to integers; negative zero becomes zero; other finite floats use shortest JSON
round-trip spelling. RPC custom ratios use exact rational ratios relative to the
first ordered Worker for cell deduplication (1:1 and 50:50). Original input weights
remain in the spec/profile; auto is never converted to equal.

Cell IDs include condition, exact model/prompt identities, ordered Workers and RPC
semantics, and exclude repeat index/readiness observations. Plan SHA includes the
normalized source spec (including original axis order, ratios, policies, revisions
and exclusions) and ordered cell IDs. Changes to cached status/token-count evidence
change readiness without masquerading as a new measured condition. Evidence remains
in the exported plan and is rechecked during import. Timestamps/progress are not
part of this planning schema.

Trial IDs combine plan SHA, cell ID and one-based sweep_repeat_index. Generation and
execution indices are zero-based. Seeded randomized order sorts deterministic SHA-256
keys over seed/cell/repeat, rather than depending on a Python PRNG version. This
is a planned order only; no dispatch takes place. Future actual dispatch must record
its own order/overlap and obey resource constraints.

## Readiness and compatibility

Candidate `status` is valid, blocked or unknown; duplicate/exclusion flags are
independent so no cause is hidden. Trials are pending or blocked; uncertainty is
retained in the cell's capability reasons. `resolution_state` is unresolved when
model/prompt/Worker identities are missing, otherwise resolved. Every S01 plan is
`executable=false` with SWEEP_EXECUTION_NOT_IMPLEMENTED, including fully bound ones.
Parallel intent adds RESOURCE_RESERVATIONS_NOT_IMPLEMENTED. Numeric RPC GPU settings
and threads/batch add their own blockers. Layer/row is preserved with capability
evidence. No fake hardware-verification flag is produced.

Existing strategy validation and definitions/work_units provide counts without
materializing per-request tasks. Logical requests, physical requests, scenarios,
warmup calls and model loads are separate. Ordinary warmup runs once per selected
Worker before all nested node-sweep scenarios; RPC warms and loads at one coordinator.
Counts describe included planned trials, including blocked/unknown ones, not work
that has run. Duration and storage estimates stay null without supporting evidence.

All new axis values remain in RunCondition. The compiler constructs a strict
ExperimentConfig **only locally for legacy scalar/strategy validation and counts**,
with a non-user placeholder prompt; it does not export or execute that object.
Unsupported fields never enter the legacy ignored_config_keys path. S02–S04 must
connect the remaining parameters; S06 must create concrete execution inputs and
perform condition-match checks. Existing API/CLI defaults and CSV headers are untouched.
