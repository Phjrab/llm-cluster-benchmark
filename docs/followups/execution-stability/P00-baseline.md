# P00 — 최신 코드·배포 근거·미해결 항목 재확인

작성일: 2026-09-22 (Asia/Seoul)

단계: P00 only

## 범위와 안전 경계

현재 개발 계보와 기존 S/R/H 보고서를 실제 코드·로컬 artifact·원격 GitHub 상태와 대조했다.
제품 코드, 연구 lock, formal matrix, 실제 inventory, model 및 runtime/result artifact는 수정하지
않았다. 실제 Worker SSH/API, 모델 다운로드·load, inference, native RPC, Sweep/Campaign/pilot,
hardware workflow는 실행하지 않았다. 동작 재현은 임시 디렉터리, fake backend 및 localhost
fixture만 사용했다.

적용 가능한 repository 지침은 `CONTRIBUTING.md`이며 repository 또는 상위 workspace에 이
repository에 적용되는 `AGENTS.md`는 없다. 통합 Sweep prompt의 Master Spec, Data Contracts,
Integration Notes와 요청된 S06/S10/R02/R04/R05/R06/R07/H01 보고서, capability/implementation
map, formal gate 문서와 두 운영 가이드를 읽었다. 기존 완료 주장은 아래 직접 확인 결과와
분리했다.

## 실제 Git 기준선

| 항목 | 확인값 |
|---|---|
| checkout 경로 | `/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark` → `/Users/hajoonpark/자율설계/llm-cluster-benchmark` symlink |
| branch | `codex/current-source-pilot-v6` |
| HEAD | `0ef550b5873355a3252d9e3b2ee4409835c03be3` |
| upstream | `origin/codex/current-source-pilot-v6`, ahead 0 / behind 0 |
| fetched `origin/main` | `eebb8f134ac2fe251f5e9dd5a723bb653db9a840` |
| main 대비 | feature branch가 35 commits ahead, 0 behind |
| 시작 working tree/index | clean / staged file 없음 |
| 기존 PR | 현재 feature head를 대상으로 한 open/closed PR 없음 |
| 최신 Required CI | run `35499855820`, HEAD `0ef550b`, completed/success |

main에는 Sweep/Campaign 후속 구현이 없다. 현재 feature 계보에는 S01–S10, R02–R07와 H01
문서까지 117개 파일, 17,408 insertions/324 deletions의 누적 차이가 있다. 따라서 P00은
`origin/main`만 보고 기능 부재를 판단하지 않고 현재 feature source를 기준으로 했다.

## 직접 확인한 호출 관계

### Exploratory Sweep

```text
POST /api/sweeps/{id}/start
  → DashboardFacade.start_sweep
  → SweepService.start
  → SweepSupervisor.create + tick
  → DurableSweepJobBackend.start
  → JobService.start
  → detached cluster.application.job_process child

GET /api/sweeps/{id}
  → DashboardFacade.sweep
  → SweepService.get
  → SweepService._tick
  → SweepSupervisor.tick
  → terminal child reconcile 또는 다음 Trial claim/start
```

`SweepSupervisor`는 스스로를 “tick-driven”이라고 정의하며 daemon scheduler를 소유하지 않는다.
Start는 최초 실행 경계만 진행한다. child가 끝난 뒤 manifest를 읽기만 하면 상태와 시작 횟수는
변하지 않았고, 첫 GET이 완료 상태를 반영한 뒤 두 번째 GET이 다음 Trial을 시작했다. 재현값은
`running / starts=1` → 첫 GET `completed / starts=1` → 두 번째 GET `running / starts=2`였다.

`GET /api/sweeps/{id}`는 조회 전용이 아니다. 반면 list, events/SSE, results와 export는 현재
durable 자료를 읽을 뿐 다음 Trial을 claim하지 않는다. frontend의 Active Sweeps 조회는 list 뒤
각 detail GET을 호출하므로 화면 갱신이 실행 진행을 우연히 담당한다. 브라우저 polling이 없으면
이미 시작한 child는 끝까지 실행되지만 후속 Trial은 자동 시작되지 않는다.

### Formal Campaign

```text
POST /api/campaigns/{id}/start
  → ResearchService.start_campaign
  → ResearchService._ensure_worker
  → Dashboard 내부 daemon Thread(_drive)
  → CampaignRunner.tick 반복
  → DurableJobRunBackend / JobService child
```

Campaign driver는 `ResearchService`가 보유한 daemon thread다. FastAPI lifespan shutdown은
`ResearchService.shutdown()`의 stop event를 설정하고 driver loop를 종료한다. synthetic gate-open
fixture에서 종료 전 tick 4회, 종료 대기 후에도 4회였고 driver thread는 사라졌다. Dashboard가
다시 시작되면 gate가 열린 경우 `running` campaign만 복구하지만, 서버가 꺼진 동안 child 종료
후 다음 cell을 dispatch하는 독립 실행 주체는 없다. 현재 shipped formal gate가 닫혀 있어 실제
Campaign은 시작할 수 없으며 이 재현은 synthetic fixture에서만 수행했다.

### Child process와 다음 dispatch의 분리

`JobService.start()`는 stdin을 `/dev/null`, stdout/stderr를 private job log에 연결하고
`start_new_session=True`, `close_fds=True`로 child를 시작한다. `JobService.shutdown()`은
Dashboard instance의 registry watcher만 멈추며 child를 signal하지 않는다. child는 자체 cancel/
pause monitor와 resource heartbeat를 사용하고, `ProcessIdentity`로 정확한 process를 확인한다.

따라서 “현재 child 생존”은 구현됐지만 “다음 Trial/cell 자동 dispatch”와는 별도다. durable
manifest/recovery도 존재하지만 진행 loop가 없으면 자동 복구 후 다음 조건을 계속 실행하지 않는다.

## P00 분류

| 질문 | 분류 | 직접 확인 결과 |
|---|---|---|
| A. 다음 Sweep Trial은 누가 진행시키는가 | `REPRODUCED_GAP` | Start와 detail GET이 `SweepSupervisor.tick()`을 호출한다. 독립 driver는 없다. |
| B. GET 상태 조회가 다음 job 시작을 유발하는가 | `REPRODUCED_GAP` | `SweepService.get()` → `_tick()` → `tick()`이 terminal reconcile 및 후속 claim/start를 수행한다. |
| C. 브라우저 polling이 사라져도 전체 Sweep이 진행되는가 | `REPRODUCED_GAP` | active child만 계속된다. child 종료 뒤 다음 Trial은 GET/tick 전까지 시작되지 않는다. |
| D. Dashboard가 종료되면 Campaign driver도 사라지는가 | `REPRODUCED_GAP` | driver는 Dashboard 내부 daemon thread이며 lifespan shutdown stop event로 종료된다. |
| E. child 생존과 다음 cell dispatch가 분리됐는가 | `ALREADY_RESOLVED` | detached `job_process`와 durable JobService는 Dashboard watcher 종료와 독립적으로 실행된다. 다음 dispatch는 별도 supervisor tick 책임이다. |
| F. 기대 source tree hash를 실제 배포값과 직접 비교하는가 | `REPRODUCED_GAP` | lock에 기대 tree가 있으나 `deployment_identity_issues()`가 관측 tree와 직접 비교하지 않는다. |
| G. H01 원시 결과가 로컬 workspace에 존재하는가 | `VERIFIED_IMPLEMENTED` | ignored private runtime에 manifest, analysis와 21개 run directory가 존재한다. 아래 누락은 보존된 중단 경계다. |
| H. 현재 장비 배포 상태의 실제 근거는 무엇인가 | `HARDWARE_NOT_VERIFIED` | 2026-09-20 H01 및 cached snapshot만 있다. 2026-09-22 fresh Worker 확인은 금지 조건에 따라 수행하지 않았다. |

## P01 재현 조건과 재사용 경계

P01 재현의 최소 조건은 2개 이상 Trial을 가진 fake Sweep이다. Start로 첫 child를 만들고 fake
backend에서 terminal로 바꾼 뒤 HTTP/GET/SSE를 모두 중단하면 다음 Trial은 시작되지 않는다.
detail GET 한 번은 terminal reconcile만 하고, 다음 GET이 후속 Trial을 claim한다. Campaign은
gate-open synthetic fixture로 `ResearchService`를 시작한 뒤 Dashboard service를 shutdown하면
driver tick이 멈춘다.

재사용할 구현:

- `SweepRepository`, `SweepSupervisor`, `CampaignRepository`, `CampaignRunner`의 durable state
  machine과 atomic claim
- `JobService`, `job_process`, `DurableSweepJobBackend`, `DurableJobRunBackend`의 child 실행·복구
- `FilesystemResourceCoordinator`, Worker ownership/fencing, quarantine 및 targeted cancel
- `ProcessIdentity`와 기존 process guard, private log/runtime layout
- 기존 fresh preflight, Campaign formal gate, model/RPC cleanup와 cooldown 정책

P01 변경 후보는 application-level 독립 execution driver/process, 그 lifecycle launcher와
provider factory, `SweepService`의 read-only projection, `ResearchService`의 in-process driver 제거,
Dashboard startup/recovery wiring과 통합 테스트다. 예상 접점은
`cluster/application/sweep_runner.py`, `cluster/research/campaign.py`,
`cluster/dashboard/service_layers/sweep_service.py`,
`cluster/dashboard/service_layers/research_service.py`, `cluster/dashboard/services.py`, Controller
lifecycle/process guard 및 관련 test modules다. 새 benchmark engine이나 두 번째 상태 머신을 만들
이유는 없다.

P01은 다음 세 결과를 별도 검사해야 한다.

1. Dashboard가 종료돼도 이미 시작된 child가 계속 실행됨.
2. child 종료 후 독립 driver가 다음 Trial/cell을 시작함.
3. driver/Dashboard 재시작 후 같은 attempt/job identity를 복구하며 불확실한 상태를 자동 재시도하지 않음.

GET/list/results/SSE는 durable 상태 조회와 표현만 수행해야 한다. prompt scrub 및 terminal 정리도
화면 조회에 의존하지 않게 driver 책임으로 이동해야 한다. driver는 parent를 inference child로
세지 않으며 Mac Controller에서 제어만 한다. Dashboard와 독립적 실행은 Mac driver, 네트워크와
필요 Worker가 살아 있어야 한다는 한계를 유지한다.

## P02 재현 조건과 source identity 정의

현재 `config/research/runtime_lock.json`은 Worker별
`deployment.source_tree_sha256=9136db62128920190c13fc11e9a10c72b33a1fbabc91823c528fdc494c691a59`
를 포함한다. Worker 배포 manifest 생성기와 verifier는 모두
`canonical_source_tree_sha256(collect_source_files(...))`를 사용하며, rsync와 정렬된 같은 제외
범위(`.git`, `.venv`, `.run`, models/results/build/dist/node_modules 등)를 공유한다. 따라서 lock의
이 필드와 live deployment의 `source_tree_sha256`은 직접 비교 가능한 source content identity다.
timestamp/runtime을 포함하는 `deployment_manifest_sha256`과 동일한 값으로 취급해서는 안 된다.

현재 `deployment_identity_issues()`는 다음만 검사한다.

- live deployment 존재, `verified=true`, commit/tree/manifest SHA 형식
- lock의 기대 commit과 live commit
- runtime fingerprint, llama-cpp-python version, pinned RPC commit
- 선택 Worker 사이의 `(commit, source tree)` 상호 일치

lock의 기대 `source_tree_sha256`와 관측 tree를 비교하지 않으며 runtime lock validation도 그
기대 필드의 존재·형식을 요구하지 않는다. 실제 shipped lock에서 commit/runtime/RPC를 맞추고
두 Worker 모두 같은 잘못된 `ffff...ffff` tree를 보고하게 한 synthetic 재현과 단일 Worker
재현은 모두 `issues=[]`였다. formal 호출 경로는
`CampaignRunner.tick` → services의 fresh preflight → `assess_campaign_cell` →
`assess_formal_eligibility` → `deployment_identity_issues`다. 현재 gate가 닫혀 있어 production
Campaign 시작은 별도로 차단되지만, gate가 열린 뒤 이 누락이 우회가 될 수 있다.

P02는 기존 `SOURCE_FINGERPRINT_MISMATCH`를 기대/관측 tree mismatch에도 재사용할 수 있다.
missing/invalid expected tree와 missing/invalid/unverified observation은 fail closed해야 한다.
변경 후보는 `cluster/research/locks.py`, deployment identity와 research lock/Campaign tests이며,
새 issue code가 필요할 때만 `cluster/research/eligibility.py`, Dashboard readiness/export fixture를
확장한다. exploratory 경로는 이 formal helper를 호출하지 않는 현재 정책을 유지한다.

## H01 로컬 증거와 누락

tracked plan:

- `config/research/pilot_plan.v7_pi_only.json`

Git ignored private evidence:

- `.run/controller/pilots/formal-study-v1-pi-only-revalidation-v7/manifest.json`
- `.run/controller/pilots/formal-study-v1-pi-only-revalidation-v7/analysis.json`
- `.run/controller/pilots/formal-study-v1-pi-only-revalidation-v7/results/<run_id>/`

manifest에는 observation/attempt 22개가 있고 21개가 run ID와 결과 directory를 갖는다. 19개
completed와 cleanup-failed run 1개는 `config.json`, `summary.json`, `responses.jsonl`,
`requests.csv`, `events.jsonl`, `measurements.jsonl`을 모두 보존한다. 중단 run
`20260920_140922_82992e`는 `config.json`과 `events.jsonl`만 있고 summary/response/request/
measurement가 없다. 다른 operator interruption 한 건은 run ID가 없어 결과 directory가 없다.
pilot directory 아래 별도 Controller/Worker log 파일은 없다. repository의 일반
`.run/controller/dashboard.log`는 존재하지만 v7 전용 보존 자료라고 입증할 연결 metadata가 없다.

H01 보고서와 저장 자료가 마지막으로 입증하는 것은 2026-09-20 Pi 02–04의 source commit
`3f6b8431587f6231171270a4f25c11ffdff6852b`, tree
`f0d4ac73e4a3d569586c2c812b11937df159c17315c799ef9947cc852b6b04d8`, runtime fingerprint
`b4387053e655722a`, pinned RPC commit과 종료 시 power/cleanup 상태다. 현재 Controller HEAD는
`0ef550b`이며 2026-09-22 실제 Worker 배포 상태는 fresh 확인하지 않았다. cached environment
reports도 Pi는 2026-09-20, Jetson은 2026-09-19 자료이므로 현재 상태 증거로 승격하지 않는다.

## 기준선 검사

모든 성공 검사는 `0ef550b` clean archive를 `/private/tmp`에 풀어 실제 `.run`, inventory와
결과를 격리했다. 원 checkout의 `.venv`와 `node_modules`만 test runtime으로 연결했다.

| 검사 | 실제 결과 |
|---|---|
| focused Sweep/Campaign/deployment/formal tests | 100 tests, PASS, 2.407s; isolated runtime |
| full Python discovery | 702 tests, PASS, 137.432s; clean archive, localhost fixture permission |
| `npm test` | PASS; syntax, Dashboard fixtures, publication PNG, Playwright 8/8 (51.7s) |
| `python -m compileall -q cluster scripts/ci` | PASS |
| `scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| repository shell scripts `bash -n` | 7 scripts, PASS |
| packaging unit/wheel isolated import | 3 tests, PASS |
| offline `pip wheel --no-deps --no-build-isolation` | PASS; wheel SHA-256 `0b1fabd8e48bcadf813e7133656651f44532c10e9d636b5070bc6f9105caa93e` |
| `git diff --check` before report | PASS |

첫 focused 실행은 symlink 바깥 실제 runtime의 chmod를 sandbox가 차단해 90개 중 setup error
3개가 발생했고 isolated runtime 재실행에서 100개가 통과했다. 첫 full 실행은 전역
`CLUSTER_RUNTIME_DIR` 주입으로 보안 의미가 달라진 3개 assertion과 localhost 금지 3개 error가
발생했다. 실제 runtime을 사용하지 않는 clean archive, 기본 path semantics와 localhost fixture
권한으로 재실행한 702개는 전부 통과했다. 첫 clean `npm test`는 `.venv`가 없어 browser
webserver만 시작하지 못했으며 runtime link를 추가한 전체 재실행은 8개 E2E까지 통과했다.
출력의 SSH/RPC/model 문자열은 mock/negative fixture이며 실제 endpoint에 접속하지 않았다.

## 다음 단계 범위

P01에서만 기존 state machine을 주기적으로 구동하는 Dashboard 외부 execution driver와
read-only 조회 경계를 구현하는 것이 다음 권장 작업이다. P02는 P01 이후 별도 단계로 기대
source tree 직접 비교를 추가한다. P03/H02/H03, O01/O02와 실제 hardware/formal 실행은 이
checkpoint에서 시작하지 않았다.

Formal gate는 계속 닫혀 있으며 `CURRENT_SOURCE_PILOT_REVALIDATION`과
`RUNTIME_SOURCE_RELOCK` 요구를 변경하지 않았다.
