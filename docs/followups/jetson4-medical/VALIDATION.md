# WS-MED — 준비·검증 보고

2026-10-23, PREPARE_ONLY. 실장비 inference 성공 보고가 아니다.

## Source와 실행 환경

- 기준 commit: `186dc69474610f0436d693dd339da155b83e7ae1`
- feature branch: `codex/jetson4-medical-prepare`
- 격리 checkout: `llm-cluster-benchmark-medical`
- 검사 source: 기준 commit + 이 변경의 working tree.
  최종 관련 파일 바이트는 [source-files-sha256.json](source-files-sha256.json)에 고정했다.
- Python 3.13, Node 22.17.1, repository의 기존 pinned dependency 환경 재사용.
- 실제 inventory는 preset 작성 시 로컬 파일로만 읽었다. 테스트의 Worker는 전부 fake/TestClient,
  브라우저 서버는 전용 localhost:4173 및 `.artifacts/playwright` 경로였다.
- 전체 Python의 runtime은 이 격리 worktree의 `.run/cluster`다. 운영 checkout의 `.run`은 사용하지 않았다.
  `CLUSTER_INVENTORY`/`CLUSTER_RESULTS_DIR`는 `.artifacts/medical-validation`로 지정했다.
- 전체 suite 이후 데이터셋 provenance 및 결과 token-source 필드 보강은 해당 focused tests로 추가 확인했다.
  exact pushed commit의 hosted CI 결과는 GitHub에서 별도 확인한다.

## 실행한 검사

|명령|실제 결과|Exit|
|---|---|---:|
|`env -u CLUSTER_RUNTIME_DIR CLUSTER_RESULTS_DIR=… CLUSTER_INVENTORY=… .venv/bin/python -m unittest discover -s cluster/tests -v`|791 tests, 166.594s, OK|0|
|`.venv/bin/python -m unittest cluster.tests.test_medical cluster.tests.test_context_runtime cluster.tests.test_packaging cluster.tests.test_model_library_followup -v`|59 tests, 9.337s, OK|0|
|최종 `.venv/bin/python -m unittest cluster.tests.test_medical -v`|16 tests, 1.046s, OK|0|
|`npm test`|syntax, fixtures, publication PNG, browser **44 passed**, 1.1m|0|
|`.venv/bin/python -m compileall -q cluster scripts`|통과|0|
|`.venv/bin/python scripts/ci/validate_repository.py`|20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts|0|
|추적된 shell 7개 각각 `bash -n`|통과|0|
|`.venv/bin/python -m pip wheel . --no-deps --no-build-isolation -w .artifacts/medical-validation/wheel`|wheel 생성|0|
|packaging test + wheel ZIP 내용 검사|medical 입력/평가 파일, importer 정적 파일 포함; source bytes 대조 통과|0|
|`git diff --check`|통과|0|

ShellCheck는 로컬 설치 없음. hosted CI의 기존 gate에서 확인한다.
실제 runtime overhead는 측정하지 않았으며 “성능 영향 없음”을 주장하지 않는다.

로컬 원본 로그: `.artifacts/medical-validation/` 아래 `python-final.log`,
`focused-verified.log`, `medical-verified.log`, `npm-verified.log`,
`repository-verified.log`, `wheel.log`. 로그/생성 screenshot/wheel/실제 inventory는 Git에 넣지 않았다.

## 발견하고 수정한 사항

1. count-only preview의 duration/storage는 null이었다. 알려진 workload count 필드만 합산해
   실제 runtime을 추정하지 않도록 수정했다.
2. 기존 catalog count fixture를 34→36, direct35로 갱신했다. 새 항목은 MedGemma 두 개뿐이다.
3. static asset cache version 변경을 기존 syntax/fixture에 반영했다.
4. 전체 테스트에 강제로 설정한 `CLUSTER_RUNTIME_DIR`가 보안 테스트의 patchable DEFAULT 경로보다
   우선하여 3개 fixture가 실패했다. 실제 `_settings_path`/`_worker_token_path` 분기를 확인하고,
   이 worktree의 격리 기본 runtime으로 재실행해 통과했다. 제품 보안 검사를 약화하지 않았다.
5. importer 구현 중 Sweep ID 편집이 기존 hash 승인 해제를 건너뛰는 UI 회귀가 E2E에서 잡혔다.
   기존 dirty 처리를 유지하도록 수정했고 전체44 E2E가 통과했다.
6. 늦은 preview 응답이 편집 후 승인을 되살리지 않도록 generation 검사를 추가했다.

초기 실패 로그 `python-full.log`, `npm-full.log`, `npm-final.log`도 로컬에 남겼다.
첫 browser 실행의 localhost bind는 sandbox에서 거부됐고, 승인된 격리 localhost 검사로 재실행했다.
이는 Worker 네트워크 문제나 H01 timeout 해결의 근거가 아니다.

## 검증된 경계

- 40KO/8EN, 원문 hash, 8개 pair, 문항별 concrete payload, <=1024 output, <=500 trials.
- 실제 catalog 선택 ID·Jetson mapping·요청/노드수·warmup/model-load 수.
- 기존 `build_cell_config` → fake FastAPI Worker → native chat 준비 → transport → persistence
  → sealed plan/run/attempt/request join → evaluator.
- rendered token-ID SHA와 input count, privacy, timeout 보존, old evidence null,
  duplicate physical request, 잘못된 run/attempt/node 거부.
- JSON schema/숫자·단위·미기록/부정 구분, 명시 alias, label 기반 MCQA,
  cot/long_answer/conclusion 누출 방지, calibration/evaluation 분리.
- 답변/모델 mapping 분리한 맹검 export, 중복 run 비교 거부.
- UI import/preview/draft save는 Start/install/readiness refresh를 자동 호출하지 않는다.
- 전체 suite에서 기존 P01/F01~F03, ownership/cancel/quarantine/formal gate/CSV 계약 회귀 확인.

## Git·운영 인계

관련 source/test/docs만 feature branch에 non-force push한다.
main merge, PR 병합, 운영 배포·서버 재시작, Worker 접속·코드 배포,
모델 설치·load, 실제 Sweep/pilot/RPC, 전력 변경은 NOT RUN이다.

실행 준비 보류 항목: B2 실제 허용 subset, B7 추가 exact quant,
모델별 native template/runtime readiness, Qwen3 effective non-thinking,
B4b 실제 tokenizer 길이, 실장비 power/clock/cooling, 임상 rubric 검토, formal 승인.
미지원 후보가 다른 후보의 준비를 막지 않는다.

준비 결과는 [README.md](README.md), 수동 시작 및 다른 서버 안전 종료는
[USER_GUIDE.md](USER_GUIDE.md), 설치/모델 상태는 [MODELS.md](MODELS.md)를 따른다.
이 소프트웨어 준비 패키지 이후 자동 다운로드나 benchmark를 시작하지 않는다.
