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

The two existing fully locked records enabled for direct installation are Qwen2.5 1.5B
and Granite 3.3 2B. Community Phi and Llama records preserve the original model repository
but do not pretend it is a verified GGUF download source.

## Download workflow

`POST /api/models/{model_id}/install` accepts only Worker names, `source=direct`, and explicit
confirmation. The server resolves immutable metadata from the static catalog and constructs
the credential-free `https://huggingface.co/.../resolve/<commit>/<file>` URL. The frontend
cannot provide a URL, path, checksum, token or command. Known Worker disk capacity is checked
against expected size, 15% partial reserve and a 512 MiB margin. Existing node action events,
Worker `.part`, `fsync`, checksum verification and atomic replace remain the implementation.

Models of 14B or larger require exactly one selected coordinator for direct installation;
they are never automatically replicated to all selected Workers. Installing a model does not
create or start an experiment.

## Dashboard

Added Pi/Edge, Jetson, RPC, >8B, Reasoning, Coding, Installed, Downloadable and Gated filters;
source/license/provenance/identity details; direct/cache/delete actions; and RPC LARGE,
RPC EXTREME, CATALOG ONLY, GATED and COMMUNITY badges. The UI explains that Ollama is a
runtime, Gemini is not Gemma, and estimated memory fit is not verified execution.

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
