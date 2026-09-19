# S00 — 현재 구현 기준선 및 구현 계획

Date: 2026-09-19 (Asia/Seoul). Workstream: WS-S00 reconciliation.

S00 — Software COMPLETE (조사·문서 범위). S01–S10 제품 기능 완료를 뜻하지 않는다.
Hardware: NOT RUN — user-operated. CI: NOT CHECKED.

## 재확인 및 범위 이탈 정정 — 2026-09-19

아래 원 보고서는 `5fd444a`를 조사하고 `e486cd7`에 저장한 당시의 기록이다.
현재 재확인 소스는 `98b6542a6984b6c49674e220708f4347da2571b7`이다.
사용자의 “시작해줘”와 “진행”은 명시된 S00 한정 요청을 변경하지 않는다.
그럼에도 S01 commit `98b6542`를 생성·push하고 S02 미완료 구현까지 진행한 것은
범위 해석 오류였다. 따라서 아래 역사적 “후속 단계 미시작” 문구를 현재 상태로
해석해서는 안 되며, 전체 요청의 범위 준수를 COMPLETE로 선언하지 않는다.

S02에서 만든 tracked 4개 파일의 미커밋 변경과 새 파일 2개는 이번 재확인에서
제거하여 HEAD 상태로 돌렸다. 그 직후 작업 트리는 clean이었다. 공유된 S01
commit은 reset/force-push/revert로 임의 변경하지 않고 존재 사실을 기록한다.
이번 checkpoint는 이 보고서와 나머지 S00 문서의 기준 시점 정정만 포함한다.
연구 lock, 실제 inventory, 모델, 실제 결과는 변경하지 않았다. 실제 Worker 접속,
모델 다운로드, inference, native RPC 및 하드웨어 CI는 실행하지 않았다.

### 현재 상태와 재실행 검사

- Branch: `codex/current-source-pilot-v6`; origin은 기존 GitHub 저장소와 동일.
- 원격 feature: `98b6542a6984b6c49674e220708f4347da2571b7`.
- 원격 main: `eebb8f134ac2fe251f5e9dd5a723bb653db9a840`.
- 적용 AGENTS.md 없음 재확인. CONTRIBUTING.md를 재확인했다.
- 원 기준선 이후 제품 차이는 S01의 `domain/sweep.py`,
  `application/sweep_planner.py`, `tests/test_sweep_planner.py` 추가다.
  기존 Worker/JobService/RPC/Dashboard 실행 경로는 원 기준선과 같다.
- 원 보고서의 첫 두 unittest 명령에 든 14개 module과 `test_packaging`을
  한 번에 `python -m unittest … -q`로 재실행: **267 tests, OK, 8.734s**.
  로그: `/private/tmp/s00-sweep-audit/recheck.log`.
- 같은 임시 harness를 별도 runtime/results에서 재실행: **4 tests, OK, 0.006s**.
  단일 active job 거부, 108/36 산술, RPC 인자 mapping, unknown key 처리 확인.
- compileall, repository validation(20 JSON/72 cells/13 actions/7 scripts),
  bash syntax, npm test:syntax 및 test:fixtures: 모두 exit 0.
- 임시 runtime/results와 header-only inventory를 사용했다. compile cache도
  `/private/tmp`에 두었다. fake factory/runtime만 사용했다.
- Starlette deprecation 및 subprocess ResourceWarning이 있었으나 검사 실패는 없다.
  전체 unittest discover, PNG/browser E2E, ShellCheck는 이번 재확인에서 실행하지 않았다.
  원 보고서의 이전 검사 결과와 이번 실행 결과를 합산하지 않는다. CI: NOT CHECKED.

S00 조사·계획 산출물은 존재하고 재확인됐다. 범위 이탈은 위와 같이 공개하며,
추가 구현을 중단한다. 이후 단계의 개발 또는 실행 승인을 추정하지 않는다.
이번 문서 commit/push의 실제 결과는 최종 응답에 기록한다.

---

## 범위와 기준 소스

사용자의 직접 요청을 실행 범위로 삼았다. 제공 문서의 S01–S10 구현 지시와 수동
Dashboard 실험 예시는 이번 실행 권한이 아니다. 통합 문서 1–1376행 전체를 읽고
내장 Master Spec, Data Contracts, Integration Notes, S00와 후속 단계/acceptance를
조사 및 계획에 적용했다. 제품·연구 lock·golden·실제 inventory는 수정하지 않았다.

| 항목 | 관측값 |
|---|---|
| 대상 | `Phjrab/llm-cluster-benchmark` (상위 workspace Git과 별개인 하위 저장소) |
| Baseline/tested source HEAD | `5fd444a6d83e4226fcc5582196f3a27e71f84160` |
| Feature branch | `codex/current-source-pilot-v6` |
| Remote | `https://github.com/Phjrab/llm-cluster-benchmark.git` |
| 원격 feature 최초 확인 | HEAD와 같은 `5fd444a6d83e4226fcc5582196f3a27e71f84160` |
| 원격 main 확인 | `eebb8f134ac2fe251f5e9dd5a723bb653db9a840` |
| 계보 | main은 HEAD의 조상, feature는 12 commits 앞섬 |
| 초기 작업 트리 | tracked/untracked 변경 없음 |
| 적용 AGENTS.md | 저장소 내부 및 `/`부터 workspace까지 상위 경로에 없음. 형제 프로젝트 지침은 적용하지 않음 |
| 저장소 정책 | `CONTRIBUTING.md`, `.github/workflows/ci.yml`, `.github/workflows/hardware.yml` |
| 입력 문서 SHA-256 | `3b03a22f4ff9490f5955f1be761371232361c56163663d1a02f2d05ed1c4ede6` |

기존 feature는 최신 main을 포함하며 현재 조사 대상의 Dashboard/RPC 수정이 담겨 있다.
따라서 Master §10에 따라 해당 branch를 이어 사용한다. main/reference SHA로 reset하거나
기존 변경을 stash하지 않았다. 과거 pilot 문서·승인은 이 작업에서 갱신하지 않았다.
자기 commit SHA는 이 보고서에 넣지 않으며 실제 commit/push 결과는 최종 응답에 기록한다.

## 핵심 판정

- ALREADY_SATISFIED: 기존 node sweep 순서/의미, 순차 다중 모델 Suite,
  Worker 단일 inference slot, RPC 선택 coordinator와 비율 재배열/실패 cleanup,
  strict config parse 옵션, formal source identity gate, 일반 Pi warning 비차단.
  이는 개별 재사용 기능 판정이며 통합 sweep 완료 판정이 아니다.
- NEW_REQUIRED: typed sweep compiler/immutable plan, runtime threads/batch 입력,
  모델 artifact identity에 바인딩한 cell, RPC GPU 정책, 공통 원자 자원 예약,
  다중 Job 대상 제어, durable sweep 실행, sweep API/UI/비교/export.
- PARTIAL: requested/effective 모델 로드 정보는 있지만 threads와 binary cache identity가
  부족하다. exact template input count와 finish reason 보존도 부족하다.
- R02는 scenario 사이 energy gap 분리만 확인됐다. 같은 scenario 안의 결측/긴 간격은
  유효 sample을 연결하여 적분하므로 full coverage 개선 완료로 부르지 않는다.
- R04 Campaign 상태 머신은 있으나 Dashboard에는 조회 API만 있다.
  R05 multipart는 명시적으로 unsupported. R06 설치/preflight 자료는 있지만
  설치를 runtime/architecture 검증으로 승격할 근거는 없다.

세부 소스/계약은 [capability-baseline.md](capability-baseline.md),
[api-state-schema-baseline.md](api-state-schema-baseline.md), 단계별 변경·검사는
[implementation-map.md](implementation-map.md)에 기록했다.

## 실제 검사

기존 `.venv`와 Node를 사용했다. 설치/setup, 실제 SSH/Worker HTTP, 모델 다운로드,
실제 inference/native RPC, 전원 변경, hardware CI dispatch는 실행하지 않았다.
fake runtime 함수/모델 factory와 작은 임시 fixture만 사용했다.

Python 관련 검사 환경:

```sh
export CLUSTER_RUNTIME_DIR=/private/tmp/s00-sweep-audit/runtime
export CLUSTER_RESULTS_DIR=/private/tmp/s00-sweep-audit/results
export CLUSTER_INVENTORY=/private/tmp/s00-sweep-audit/empty.csv
export PYTHONDONTWRITEBYTECODE=1
```

`empty.csv`는 header만 있는 임시 inventory다. 테스트 자체의 TemporaryDirectory,
fake inspector/backend도 유지했다. 로그·harness는 `/private/tmp/s00-sweep-audit/`에만
저장했으며 Git에 포함하지 않는다.

| 실행 명령 | 실제 결과 |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_domain cluster.tests.test_durable_jobs cluster.tests.test_rpc_coordinator cluster.tests.test_worker_runtime cluster.tests.test_benchmark_core cluster.tests.test_model_library_followup cluster.tests.test_measurement_instrumentation cluster.tests.test_research_locks cluster.tests.test_research_campaign cluster.tests.test_dashboard_backend cluster.tests.test_power_policy -q` | 223 tests, OK, 5.256s |
| `.venv/bin/python -m unittest cluster.tests.test_deployment_identity cluster.tests.test_dashboard_research cluster.tests.test_research_matrix -q` | 41 tests, OK, 0.265s |
| `.venv/bin/python -m unittest cluster.tests.test_packaging -q` | 3 tests, OK, 2.982s; 임시 소스 복사에서 offline wheel build/install/import 포함 |
| `PYTHONPATH=. .venv/bin/python /private/tmp/s00-sweep-audit/check_baseline.py` | 최종 4 tests, OK, 0.004s |
| `PYTHONPYCACHEPREFIX=/private/tmp/s00-sweep-audit/pycache .venv/bin/python -m compileall -q cluster scripts` | exit 0 |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `bash -n cluster/rpc/runtime.sh cluster/worker/start.sh` | exit 0; 실행하지 않고 구문만 검사 |
| `npm run test:syntax` | PASS |
| `npm run test:fixtures` | `dashboard export fixtures: OK` |
| `npm run test:publication-png` | sandbox Chromium MachPort Permission denied; 권한 확장 재실행 exit 0, `phase11 publication PNG fixtures: OK` |

Python 기존 검사 총 267개와 임시 기준선 4개가 최종 통과했다. 전체 unittest discover,
Browser E2E, 전체 ShellCheck, macOS lifecycle gate는 이번 문서 단계에서 실행하지 않았다.
전체 `npm test` 성공으로 표시하지 않는다. 기존 FastAPI TestClient의 httpx 사용에 대한
Starlette deprecation warning이 있었으나 실패는 없었다.

임시 harness 최초 시도는 fake Worker에 문서용 IP를 넣어 private/loopback validation에
막혔다(15개 중 1 error; imported TestCase 11개도 발견됨). 두 번째는 runtime fake의
`node` 누락으로 4개 중 1 error였다. loopback fixture, `node` 반환값, 명시적 Baseline
suite 선택으로 harness만 수정한 후 4/4 통과했다. 제품 회귀로 분류하지 않는다.

### S00 필수 네 가지 기준선

1. `JobService.start`: 임시 registry의 fake live job에 두 번째 queued job을 제출하면
   `Another experiment is already running`; Popen이 호출되지 않음을 assert했다.
2. 통합 문서 JSON을 파싱한 순수 `itertools.product`: 일반 grid 36 cells/108 trials,
   RPC 12 cells/36 trials. 이는 산술 fixture이지 존재하지 않는 제품 compiler의 PASS가 아니다.
3. injected fake `WorkerRpcBackend`: ctx 1024/2048 전달, GPU `999`, coordinator-first
   입력 비율 `[3,1]`의 native device order `[1,3]` 재배열과 session close 확인.
4. `ExperimentConfig.from_dict`: legacy threads/batch 키는 ignored_config_keys에 기록,
   strict=True는 ValueError. 새 sweep에서 legacy ignore를 사용해서는 안 된다.

## Git 및 안전 경계

원격 조회의 첫 sandbox 시도는 DNS 제한으로 실패했으며 권한 확장 조회는 성공했다.
보고서 4개만 명시적으로 stage하고 diff/staged diff/비밀 검사를 거쳐 의미 있는 문서
commit을 feature branch로 일반 push한다. main push, force push, merge, empty commit,
CI 우회는 하지 않는다. 원격이 진행되었다면 덮어쓰지 않고 결과를 보고해야 한다.
비밀 검사는 변경 문서의 credential/private-key/token 패턴 및 파일 범위를 확인한다.

하드웨어 설치·호환성·실험 성공은 모두 미검증이다. 기존 보고서의 과거 테스트 수나
실장비 성공을 이번 실행 결과로 전용하지 않았다. S01은 시작하지 않았다.

STOPPED. Later development phases have not been started.
