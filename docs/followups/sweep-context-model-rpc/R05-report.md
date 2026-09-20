# R05 — Multipart GGUF logical artifact-set report

Date: 2026-09-20

Implementation branch: `codex/r05-multipart-gguf`

Push target: `codex/current-source-pilot-v6`

Baseline: `c41a33f705f017ae3b2fc8d17325eb7792021aa4`

## Scope and baseline

R05만 수행했다. 원래 checkout에 R04와 무관한 Sweep UI working-tree 변경이 남아 있어 이를
건드리거나 stage하지 않고 baseline commit에서 격리 worktree를 만들었다. repository와 적용
가능한 `AGENTS.md`를 확인했으며 이 repository에 적용되는 파일은 없었다.

기준선은 single-file GGUF의 exact revision/size/SHA 설치와 Worker inventory/preflight를
지원했지만 multipart 항목은 loader/manifest 계약 없이 항상 차단했다. Sweep contract에는
`artifact_kind=artifact_set` 표현만 있었고 planner가 이를 무조건 차단했다.

연구용 lock, formal matrix, 실제 model catalog identity, 실제 inventory, model file과 result
artifact는 수정하지 않았다. 실제 Worker 접속, Hugging Face/model 다운로드, inference,
native RPC, formal/exploratory job 실행은 수행하지 않았다. 모든 multipart binary와 network
동작은 임시 디렉터리, fake byte stream, mock transport로 검사했다.

## Implemented artifact-set contract

- multipart catalog identity는 exact 40-character revision, 표준
  `name-00001-of-000NN.gguf` 전체 순서, shard별 positive byte size/lowercase SHA-256,
  loader first shard, total size와 versioned ordered-manifest SHA-256이 모두 일치해야 한다.
- single GGUF는 기존 file SHA-256을 계속 identity로 사용한다. multipart는 file SHA를
  하나로 가장하지 않고 manifest SHA-256을 logical model identity로 사용한다.
- public direct install은 Worker의 격리 staging directory에서 모든 shard의 URL, size, SHA와
  first-shard GGUF metadata를 확인한 뒤에만 기존 파일을 backup하고 세트를 승격한다. 실패 시
  새 shard와 metadata를 제거하고 이전 세트를 복구한다.
- gated install은 Controller의 기존 Hugging Face credential store로 각 exact shard를 private
  cache에 검증하고 Worker에 동기화한 뒤 `/cluster/models/verify-set`이 전체 manifest를 다시
  확인해야 logical model metadata를 게시한다. token은 Dashboard payload, action record,
  subprocess argv 또는 log에 포함하지 않는다.
- Worker inventory는 등록된 shard를 개별 모델로 중복 노출하지 않고 aggregate size,
  `artifact_kind=artifact_set`, artifact count, manifest SHA를 가진 한 logical model로 반환한다.
  missing/tampered shard는 checksum invalid가 되며 load와 preflight가 fail closed한다.
- loader에는 canonical first shard path만 전달한다. cache identity에는 first-file SHA 대신 전체
  manifest SHA를 사용하므로 다른 shard 교체도 reload/blocking identity 변경으로 처리한다.
- logical delete는 등록된 artifact-set member와 metadata를 함께 제거한다.

## Controller, Dashboard, and sweep integration

- catalog-derived install builder가 public `install-model-set`과 gated
  `install-model-set-cache` action을 만든다. 임의 Dashboard action으로 URL/manifest를 주입하는
  기존 차단은 유지하며 14B 이상 one-coordinator 및 storage preflight도 aggregate size로 적용한다.
- Worker install/verify-set route는 기존 owner authentication과 resource/action coordination을
  사용한다. 별도 scheduler, model ID 체계 또는 downloader authority를 만들지 않았다.
- model preflight는 catalog와 inventory의 artifact kind/count/manifest SHA를 비교한다. selected
  coordinator 규칙, license, architecture, quantization, template/runtime evidence 경계는 유지한다.
- Sweep resolver는 검증된 artifact set을 `ModelReference.artifact_kind=artifact_set`과 manifest
  identity로 고정한다. manifest가 없는 multipart candidate만
  `ARTIFACT_SET_MANIFEST_REQUIRED`로 차단하며 다른 model cell은 계속 계획한다.
- Model Library는 multipart badge와 현재 eligibility를 표시한다.

## Current catalog boundary

현재 `llama3.3-70b/catalog.gguf`에는 exact GGUF repository/revision, shard filename/size/SHA,
ordered manifest가 없다. 해당 record와 `multipart_unsupported` policy를 수정하거나 외부 값을
추정하지 않았다. 따라서 이 모델은 계속 catalog-only blocker이고 단일 GGUF 33개 및 다른 sweep
cell은 영향을 받지 않는다. R05 software 지원은 실제 70B 실행 가능성, memory fit, llama.cpp/RPC
compatibility 또는 formal approval을 뜻하지 않는다.

## Verification

| Check | Result |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_multipart_models ... test_sweep_planner -v` | 95 tests, PASS after final hardening; ordered hash, canonical sequence, direct atomic staging/rollback, tamper/missing shard, loader first shard, public/gated catalog actions, fake authenticated cache publication, preflight and sweep identity, single-file regression |
| `.venv/bin/python -m unittest discover -s cluster/tests -v` | 696 tests, PASS, 139.180s with local loopback permission. Restricted sandbox run had 693 pass/3 environment errors only because localhost bind was denied |
| `npm test` | PASS; syntax, Dashboard export fixture, publication PNG and Playwright 4/4, 27.8s. Existing dependencies/venv were linked read-only into the isolated worktree |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `python3 -m json.tool cluster/config/model_catalog.json` | PASS; catalog content unchanged |
| `git diff --check` | PASS |

No verification command contacted an actual Worker, Hugging Face repository, model runtime or RPC
endpoint. Tests that exercise download and loader boundaries use synthetic local GGUF headers, in-memory
responses, mocks and fake factories only.

## Git checkpoint

R05 files and this report are committed as one checkpoint and non-force pushed to the existing feature
branch after confirming its remote head still equals the R04 baseline. The unrelated original-checkout
working tree is excluded. Actual commit, push and required CI results are reported after execution. R06 is
not included in this checkpoint.
