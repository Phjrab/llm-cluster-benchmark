# Implementation map — S01–S10 구현·인수 상태

## S10 통합 인수 — 2026-09-19

S01–S09 product 경로는 S10에서 loopback fake Worker, injected fake native runtime,
durable supervisor/JobService fixture와 browser E2E로 함께 검증했다. 108-trial 일반 grid와
36-trial RPC grid가 concrete runner/native argv까지 연결되며, disjoint cap 2, overlap 대기,
targeted cancel, 재시작, duplicate Start, crash-after-claim, result-before-manifest, partial RPC
start, cleanup quarantine를 통과했다. 전체 A01–A65 매핑과 실제 명령은
[S10-report.md](S10-report.md), 사용자 인수 절차는
[sweep-context-model-rpc.md](../../manual/sweep-context-model-rpc.md)에 있다.

R01 gate를 재사용한다. R02의 scenario 내부 계측 gap은 schema v4
`bounded-power-gap-v1`과 fail-closed reader로 완료했다. R03은 기존 ordinary/formal Pi
정책을 유지하면서 Dashboard 설명을 같은 경계로 정렬했다. R04는 기존 CampaignRunner와
JobService를 Dashboard API/control 및 재시작 복구에 연결했다. shipped formal gate가 닫혀
있으므로 실제 Campaign 실행은 하지 않았다. R05 multipart artifact set과 R06 실제 hardware
compatibility evidence는 남아 있다.
이 항목은 exploratory sweep software 완료를 뒤집지 않지만 hardware/formal 완료로 승격할 수
없다. R/H hardware 단계는 자동으로 시작하지 않았다.

## 현재 소스에서의 계획 보정 — f22f246

아래 표는 최초 S00의 단계별 설계이며 지금 실행할 목록이 아니다. S01의
`domain/sweep.py`, `application/sweep_planner.py`, `test_sweep_planner.py`는 현재
존재한다. S02에는 추가로 `domain/runtime_profile.py`, `worker/prompt_preparation.py`,
`test_context_runtime.py`가 존재한다. 해당 기존 helper와 이번에 재실행한 검사를
재사용하며 새로 같은 기능을 만들지 않는다. 완성된 전체 제품으로 판정하지 않는다.

S03는 기존 model_service/catalog/inventory와 planner 연결, S04는 pinned RPC
profile→argv 및 coordinator/session/memory 검증, S05는 모든 mutation 진입점의
atomic reservation, S06는 기존 JobService 기반 durable supervisor, S07는 승인·보안
API, S08는 실제 API를 쓰는 Dashboard, S09는 Trial 기반 비교·privacy export,
S10은 fake 통합 장애검사와 사용자 수동 인수로 이어진다. 아래 exact module/test
매핑을 유지한다. S03 미완료 변경은 제거했으며 새 scheduler/downloader는 추가하지 않았다.

각 단계는 사용자가 단계 번호를 명시해 요청했을 때만 별도 작업으로 수행한다.
이번 작업은 S00 문서 재확인 후 STOP이다. S01/S02 범위 이탈 이력이 있으므로
아래 역사적 “구현을 시작하지 않았다”는 문장을 현재 전체 세션의 사실로 읽지 않는다.

> 아래 본문은 `5fd444a` 당시의 역사적 기준선이다. 최신 보정은 위 절과
> [S00-report.md](S00-report.md)의 최종 재확인 절을 따른다.

Source baseline: `5fd444a6d83e4226fcc5582196f3a27e71f84160`.
S00에서 아래 구현이나 테스트 신설을 시작하지 않았다. `proposed` 파일은 현재 없다.
기존 엔진·model service·JobService를 확장하고 경쟁 scheduler를 만들지 않는다.

| Phase | exact module / 계획된 변경 | 기존 검사 재사용 및 추가할 증거 | 종료 기준 |
|---|---|---|---|
| S01 | proposed `cluster/domain/sweep.py`, `cluster/application/sweep_planner.py`; `domain/experiment.py`, `benchmark/strategies.py`의 순수 validation/workload 사용 | `cluster/tests/test_domain.py`, `test_benchmark_core.py`; proposed `test_sweep_planner.py`: 108/36, atomic profiles, OAT baseline dedupe, explicit, finite/bool/unknown rejection, budget-before-materialization, semantic hash | 모든 축 표현 및 valid/blocked/unknown/excluded 구분. runtime 미구현 축은 executable 아님 |
| S02 | `cluster/dashboard/schemas.py`, `domain/experiment.py`, `benchmark/runner.py`, `transport.py`, `core.py`, `worker/schemas.py`, `routes.py`, `inference.py`; optional load profile 및 effective trace | `test_worker_runtime.py`, `test_domain.py`, `test_benchmark_core.py`, `test_measurement_instrumentation.py`; fake factory ctx/threads/batch/GPU 변경, cache binary identity, clamp/overflow, special-token count, early EOS, privacy | 실제 caller→factory→metadata 연결. unknown exactness 정직하게 표시; adjusted 정상 표본 제외 |
| S03 | `cluster/domain/model.py`, `application/model_service.py`, `benchmark/models.py`, proposed planner의 model resolver | `test_models.py`, `test_model_library_followup.py`, `test_gguf_identity.py`; same name/different hash, quantization/template, missing installation, multipart blocker, model별 독립 cell | catalog 후보/설치/runtime evidence/formal approval 분리; 자동 다운로드 없음 |
| S04 | `cluster/benchmark/rpc.py`, `rpc_selection.py`, `transport.py`, `cluster/rpc/runtime.sh`, proposed RpcProfile compiler | `test_rpc_coordinator.py`, `test_rpc_process_guard.py`; 2/3 nodes, coordinator 이동, ratio reorder, ctx/model/GPU argv 변동, 부분 시작·cleanup failure | 새 profile마다 새 session. pinned row/architecture unknown 보존. coordinator 모델만 요구 |
| S05 | `cluster/application/jobs.py`, `job_process.py`, `suite_runner.py`; proposed `cluster/application/resource_service.py`와 `cluster/infrastructure/resource_repository.py`; `worker/routes.py`, `clusterctl.py`, Dashboard actions, runner와 pilot/Campaign admission | `test_durable_jobs.py`, `test_process_guard.py`, `test_rpc_process_guard.py`, `test_dashboard_backend.py`; proposed process-based resource tests: overlap/disjoint, alias, cap, stale owner, corruption, formal race | atomic whole-set reservation, 기본 1/opt-in 2, targeted cancel, quarantine; 구형 Worker는 exclusive만 |
| S06 | proposed `cluster/application/sweep_runner.py`; 기존 jobs/job_process/storage/ExperimentRunner 재사용 | `test_durable_jobs.py`, `test_research_campaign.py`의 durable 패턴; proposed `test_sweep_runner.py`: claim/spawn/summary/manifest crash windows, pause/cancel/cleanup/restart, retry reason/new attempt | 완료 Trial 중복 실행 없음. 동일 pool 직렬. parent/child cap 이중 계산 없음 |
| S07 | proposed `cluster/dashboard/service_layers/sweep_service.py`; `routes.py`, `schemas.py`, auth/dependencies/events 경계 | `test_dashboard_backend.py`, `test_phase14_dashboard_security.py`, `test_events.py`; fake TestClient preview/start/idempotency/hash tamper/body limit/owner cancel/SSE/privacy | preview side-effect free; 서버 identity/approval/resource 검증; 기존 API 보존 |
| S08 | proposed `cluster/dashboard/static/js/sweep-builder.js`, `sweep-control.js`; `templates/index.html`, `static/app.js`, `static/js/api.js`, `state.js`, `events.js`에 연결 | `cluster/tests/e2e/dashboard.spec.js`, `test_dashboard_exports.js`, `package.json` syntax list 확장; fake 108 preview, model/RPC editor, disjoint cancel, reload/pause/keyboard labels | 모든 필수 축의 실제 API 연결. scalar placeholder 완료 금지 |
| S09 | proposed `cluster/dashboard/static/js/sweep-results.js`; `service_layers/result_service.py`, `benchmark/instrumentation.py`, `research/publication.py`, 별도 sweep index/export | `test_measurement_instrumentation.py`, `test_phase11_publication.py`, `test_results_failures.py`, `test_dashboard_exports.js`; Trial CI, missing energy, retry/adjusted, formula injection, hash-only | independent run 통계, RPC/broadcast 의미·tokenizer 차이 표시; historical readers/CSV 보존 |
| S10 | 전체 integration + proposed `docs/manual/sweep-context-model-rpc.md`, `S10-report.md`; `.github/workflows/ci.yml` 기존 gate 사용 | full unittest, compile, repository validation, offline wheel, shell syntax/ShellCheck, npm/Playwright; fake HTTP/native argv/durable child 연결로 108/36 및 restart failures | A01–A65를 실제 test evidence로 매핑. software/Git/CI/hardware 판정 분리. 실제 실험은 사용자 |

## 선행 의존성과 구현 결정

1. S01 hash에서 model/ctx/topology/protocol은 포함, timestamp/progress는 제외한다.
   node/axis 선택 순서를 무조건 정렬하지 않는다. 500 trials 기본 budget, 기본 순차,
   opt-in max_parallel_jobs=2 선언을 포함하되 S05 전 실행 허용하지 않는다.
2. S02–S04는 독립 fake backend/helper 증거를 먼저 만든다. 전체 runtime API compatibility가
   확보되기 전 Dashboard에 미완성 설정을 활성화하지 않는다. Controller tokenizer 설치 금지.
3. S03의 multipart 부재는 그 모델만 차단하며 단일 GGUF sweep을 막지 않는다.
   R06 설치 상태는 architecture/memory verification과 구별한다. 70B 성공을 약속하지 않는다.
4. S05의 중요 의존은 JobService guard 제거가 아니라 모든 mutation 진입점의 공통 소유권이다.
   model prepare→warmup→measure→cleanup→cooldown 전체를 예약하고 cleanup 불확실 시
   quarantine한다. formal readiness가 단순 미완료인 것은 ordinary blocker가 아니다.
5. S06는 SuiteRunner와 모델 반복을 이중으로 곱하지 않는다. 각 concrete cell은 모델 하나,
   반복은 Trial, 재시도는 새 Attempt이다. 자동 retry 기본 off; 이유 없이 실패 기록 삭제 금지.
6. R01 검증은 그대로 사용한다. R02 내부 energy gap debt는 후속
   `bounded-power-gap-v1`에서 해소됐으며 S09 결과 UI는 coverage와 reason을 표시한다.
   R04의 미연결 API를 이미 완성됐다고 가정하지 않으며 필요한 공통 adapter만 해당 단계에서
   설계한다. R00–R07/H01 전체 실행으로 범위를 확대하지 않는다.
7. S10에서 global resource/worker protocol 경로가 미완성이면 unit test만으로 완료 처리하지
   않는다. hardware 미실행과 software 미구현은 별도 상태다.

## 단계별 Git checkpoint

각 단계는 시작 시 HEAD/branch/remote/사용자 변경을 다시 확인하고 해당 단계만 수행한다.
관련 검사 → report 저장 → diff/staged diff/secret 검사 → 명시 파일 stage → commit →
확인된 feature branch에 non-force push → 실제 결과 보고 → STOP 순서다.
기존 formal/pilot lock, 실제 inventory, runtime/model/results/credential은 작업·stage 대상이 아니다.
