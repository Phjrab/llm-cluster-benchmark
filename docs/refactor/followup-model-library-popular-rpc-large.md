# Follow-up: Popular Model Library and RPC Large

## Scope

`FOLLOWUP_MODEL_LIBRARY_POPULAR_AND_RPC_LARGE`만 수행했다. 실제 GGUF 다운로드,
Worker 변경, 모델 load/generation, benchmark와 native RPC 실행은 수행하지 않았다.

## Baseline and reconciliation

- Baseline commit: `d87e511`
- Existing catalog entries preserved: 17
- Existing direct install primitive reused: `install-model-url` → Worker verified download
- Existing cache sync/delete, preflight and benchmark/result contracts preserved
- Added catalog entries: 17 (total 34)
- Added families/cohorts: DeepSeek-R1 Distill, Gemma 3, Ministral 3, Magistral,
  Llama 3.1/3.3, Qwen3 14B/32B

## Model identity policy

Original model source and GGUF download source are separate. Only an entry with an exact
Hugging Face repository, 40-character commit, single safe GGUF filename, exact byte size,
lowercase SHA-256, quantization, provenance and license can be `direct`. Gated, incomplete
and multipart entries remain visible but fail closed. No SHA, file name or source URL was
guessed for newly added catalog-only entries.

The initial checkpoint enabled Qwen2.5 1.5B and Granite 3.3 2B. The follow-on activation
checkpoint queried Hugging Face metadata without downloading binaries, then pinned exact
source/GGUF repositories, 40-character revisions, filenames, byte sizes and SHA-256 values.
The resulting catalog has 33 identity-locked `direct` records and one intentionally blocked
multipart Llama 3.3 70B record. Community GGUFs retain both original-model and quantizer
provenance instead of presenting the community artifact as the upstream source.

## Download workflow

`POST /api/models/{model_id}/install` accepts only Worker names, `source=direct`, and explicit
confirmation. The server resolves immutable metadata from the static catalog. Public artifacts
use the existing credential-free Worker path. Gated artifacts require a Controller-side
`hf auth login`; the Controller uses `huggingface_hub` to download the exact artifact into a
private cache, checks byte size and SHA-256, then syncs it to the selected Worker and asks the
Worker to persist verified provenance metadata. The frontend cannot provide a URL, path,
checksum, token or command. Known Controller/Worker disk capacity is checked against expected
size, 15% partial reserve and a 512 MiB margin.

License acceptance is separate from Hugging Face account access. The Dashboard exposes the
current terms/source link and an explicit checkbox. Acceptance is stored as a private,
project-local revision-bound fingerprint; changed terms/revisions and explicit revocation fail
closed for both installation and new experiment admission. Hugging Face credentials are never
stored by the Dashboard or included in action options, subprocess argv, API responses or logs.

Models of 14B or larger require exactly one selected coordinator for direct installation;
they are never automatically replicated to all selected Workers. Installing a model does not
create or start an experiment.

## Dashboard

Added Pi/Edge, Jetson, RPC, >8B, Reasoning, Coding, Installed, Downloadable and Gated filters;
source/license/provenance/identity details; direct/cache/delete actions; and RPC LARGE,
RPC EXTREME, CATALOG ONLY, GATED and COMMUNITY badges. A Hugging Face account panel shows and
copies `hf auth login`, verifies the configured account, and clearly separates repository
access from local license consent. The UI explains that Ollama is a runtime, Gemini is not
Gemma, and estimated memory fit is not verified execution.

## Verification

No command in this verification set downloads a model or contacts a Worker.

- `.venv/bin/python -m unittest cluster.tests.test_domain cluster.tests.test_model_library_followup -v`
  — 43 tests passed
- `.venv/bin/python -m unittest discover -s cluster/tests -v`
  — 480 tests passed
- `.venv/bin/python -m compileall -q cluster`
  — passed
- `node --check cluster/dashboard/static/app.js`
  — passed
- `node --check cluster/dashboard/static/js/models.js`
  — passed
- `node cluster/tests/test_dashboard_exports.js`
  — passed (`dashboard export fixtures: OK`)
- `python3 -m json.tool cluster/config/model_catalog.json`
  — passed
- every repository shell file checked with `bash -n`
  — passed
- `git diff --check`
  — passed

Live GGUF download, model load/generation, benchmark and native RPC execution were not run.
Those acceptance checks are intentionally delegated to the manual guide.

## Activation and Hugging Face account checkpoint

After the first checkpoint, the catalog activation and account workflow were completed without
downloading a real GGUF or contacting a Worker. Hugging Face repository APIs were used only to
lock metadata. The Controller dependency now includes pinned `huggingface-hub`; an operator logs
in with `hf auth login`, while the Dashboard exposes only installed/configured/verified state and
the non-secret account name. Gated downloads use the official token store through the library,
never a token accepted or persisted by this application.

Current verification:

- `.venv/bin/python -m unittest discover -s cluster/tests -v`
  — 486 tests passed in the restricted sandbox; two listener lifecycle tests were blocked only
  by the sandbox's local socket policy.
- `.venv/bin/python -m unittest cluster.tests.test_launcher.ControllerLifecycleTests -v`
  — the two socket lifecycle tests passed with local-listener permission (488/488 total).
- Focused Hugging Face, model library, Worker metadata, Dashboard backend, packaging and security
  tests passed.
- Python compileall, catalog JSON parsing, Dashboard JavaScript syntax/export fixtures, all shell
  syntax checks and `git diff --check` passed.

The download implementation was tested only with small temporary mock byte strings. No real GGUF,
inference, benchmark, RPC session, Worker transfer or hardware mutation was performed.
