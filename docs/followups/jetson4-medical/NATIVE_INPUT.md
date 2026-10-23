# WS-MED — Native input bootstrap and observed Sweep evidence

2026-10-23. Follow-up to the prepared medical adapter; no historical results or formal locks are changed.

## Boundary

A concrete exploratory `ExperimentConfig` may explicitly provide `input_binding` with
`preparation_id`, `model_sha256`, `template_sha256`, and `prompt_sha256`. The existing
model load, native chat preparation, streaming, JobService ownership, cleanup, and
result paths are reused. This does not create a Sweep identity. It cannot be combined
with Sweep/formal/pilot identities or native RPC. Legacy config fingerprints and
19-column CSV remain compatible. No controller inference or new scheduler is added.

Preparation and completed responses must contain exact input tokens and a native
token-ID hash (`chat_template_token_ids_v1`). Load fallback, missing native evidence,
wrong binding, or effective budget mismatch cannot silently become the requested
condition. GPU placement itself remains unknown when the backend exposes only its
factory argument. The original GPU30 failure and GPU18 proxy-input run are retained
as separate prior attempts; they are not converted to native-bound successes.

## Read-only preview evidence

`service_layers/sweep_evidence.py` reads at most 128 recent server-owned job files,
4 MiB each and 16 MiB total. Symlinks, failed/incomplete jobs, unverified cleanup,
missing native proof, stale live status, different endpoints/source tree/runtime,
and mismatched model/template/prompt evidence are excluded. Hash-only summaries
contain no prompt/response text or credentials. No Worker contact, model operation,
new claim, retry, file write, or catalog/formal approval happens in this adapter.

Only `MODEL_RUNTIME_NOT_VERIFIED` can gain `MODEL_RUNTIME_OBSERVED_RUN`; blocked
checks stay blocked. Runtime fingerprints are compared as observed strings, never
invented as a 40-character upstream commit. A prompt's token count is reusable only
when every selected Worker has observed that exact model/template/input under the
same current source/runtime identity, with no count/hash disagreement. Native RPC
previews retain their separate gates. Candidate `observed_run_refs` exposes bounded
(job, run, Worker) proof references. Every executed child still prepares its input
again and verifies the effective budget. Evidence can expire from the bounded
window; missing evidence means UNKNOWN, not silent bypass.

A correct medical answer is not required for runtime admission. Parsing failures,
wrong fields, truncation, refusal and timeouts remain model outcomes in the evaluator.
B0 uses the original DOC02 input without adding rubric contents or removing fences
from the model's answer. Clinical review and formal eligibility are separate.

## Validation and delivery

Base source: `df7c5daf7f36e0cc4cdfb2966eb0f68ef731b556`, feature
`codex/jetson4-medical-prepare`. Source under test is that base plus this commit.
Focused native/context/medical/Sweep tests: 64 passed. The added cases cover actual
fake Worker payload → preparation → streaming → persistence → evaluator, strict
binding and legacy identity, missing hashes, response mismatch, read-only evidence,
cleanup/source/runtime/endpoint isolation, conflicting counts, and bounded reads.

Full regression, packaging and browser results are recorded below after completion.
Operational hardware results are stored privately outside Git. Source delivery alone
is not deployment or hardware acceptance. Controller backend restart is required;
Workers receive the same source identity so stale evidence cannot be reused.

|Gate on this working tree|Observed result|Exit|
|---|---|---:|
|Full Python with worktree runtime and isolated inventory/results|796 tests, 164.137s, OK|0|
|`npm test` with isolated localhost:4173|syntax/fixtures/PNG passed; browser 44 passed, 1.1m|0|
|`compileall cluster scripts`|passed|0|
|repository validator|20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts|0|
|tracked shell syntax|7 scripts passed; local ShellCheck unavailable|0|
|wheel build + full-suite isolated packaging tests|passed; new adapter and static assets included|0|
|`git diff --check`|passed|0|

Initial checks were retained in private logs: macOS sandbox denied localhost binding
and Chromium MachPort registration; globally setting `CLUSTER_RUNTIME_DIR` also
conflicted with tests that patch their own root/settings. The corrected command used
`env -u CLUSTER_RUNTIME_DIR` within the isolated checkout, explicit empty inventory
and isolated results. One actual compatibility defect (adding a summary field to
legacy runs) was fixed by emitting native evidence only for opt-in bound jobs.
Generated browser captures were restored to their original tracked bytes. No
operational runtime/inventory was used by tests. Hosted CI must be reported against
the pushed commit separately; these local checks are not a hosted-CI claim.
