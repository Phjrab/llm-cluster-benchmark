# F03 — 진행 관측, 실패 진단 UI, 통합 검증 및 인수

## 범위와 소스

사용자의 최종 `f03까지 진행` 승인에 따라 F03을 수행했다. F01/F02는 기존 완료 commit을
이어받았다. P00–P04나 benchmark/Sweep/Campaign 엔진을 다시 구현하지 않았다.
이번 F03 software 패키지 이후 새 Phase는 시작하지 않는다.

- worktree: `/Users/hajoonpark/자율설계/llm-cluster-benchmark-f01`
- branch: `codex/operational-observability-hardening`
- F01 integration: `e4ffb2cee254fab14f424914eb73f350cafbfdec`
- F01 report: `0a17c9d7afe5ece53a3faf31180927bcae82f9bd`
- F02 / F03 base: `99fe4e47a8f5eda44c589539193e62bda34683c4`
- F03 구현 commit: `237000f35e1addf54e36fad5138fc6029b52b1ad`. 테스트 시 HEAD는 base이고 미커밋
  snapshot을 실행했다. commit 후 코드/테스트 등 222개 파일의 Git blob SHA-256이 검증 소스
  manifest와 모두 일치함을 확인했다. 후속 보고서 commit은 문서만 변경한다.
- `.artifacts/f03/source-manifest.json`: code/test/CI/package 파일 222개 SHA-256;
  manifest SHA-256 `dde6291593cc96fc4b396bbb524d7e67f1bf00a93b226870989afa6227655964`.
- 실제 delivery commit과 exact-commit CI 상태는 최종 응답 및 로컬 delivery 기록에 남긴다.
  과거 F01/F02 보고서는 당시 Phase 상태이며 현재 배포 완료의 근거가 아니다.

## 구현

### 진행 loop 관측

`ProgressObservation`은 기존 Sweep/ResearchService `_drive`의 경계에서만 갱신되는
process-local 표다. 새 daemon/scheduler, job 생성 경로 또는 Worker orchestration은 없다.
동일 DashboardFacade가 serving instance/PID/start identity를 공유한다.

- 마지막 tick/정상 tick, 오류 코드 `PROGRESS_LOOP_ERROR`, local loop instance, freshness 제공.
- loop exception 후 thread가 끝나도 오류를 보존한다. raw exception 내용은 이 projection에 없다.
- loop 관측은 최대 512개. 종료 관측부터 제거하며 active 표가 가득 차면 새 관측은 UNKNOWN이다.
  실행 자체를 중단하거나 새 loop를 만들지 않는다. 서버 재시작 시 표는 복원하지 않는다.
- tick age는 같은 process의 monotonic으로 계산한다. 10초보다 오래되면 STALE/확인 불가다.
  이 값은 실행 timeout이나 heartbeat에 따른 lease 정책이 아니다.
- `last_dispatch_at`은 Sweep durable attempt start / Campaign dispatch event의 UTC 근거다.
  Campaign preflight 시작을 dispatch로 해석하지 않는다. 새 dispatch와 heartbeat를 구분한다.
- child는 검증된 job/suite 또는 Campaign/cell/attempt 연결의 **저장된** status/phase를 표시한다.
  현재 process 생존은 UNKNOWN이며 결과가 오래됐다는 이유로 장애로 판정하지 않는다.
- Worker 응답은 기존 StatusMonitor cache의 이름/응답 여부만 사용한다. 기존 5초 수집 주기를
  유지하고 batch 시작부터 15초 이내만 FRESH로 표시한다. 수집 시각이 없거나 stale이면 UNKNOWN.
- 긴 child, resource/model cooldown, cleanup, resource wait, pause, gate 대기, loop error,
  terminal을 표시한다. 여러 disjoint child는 각 ID/phase를 따로 표시한다.
- 다른 serving instance/외부 Study runner의 loop 상태는 UNKNOWN이다. `LOCAL_LOOP_OBSERVED`는
  로컬 loop를 관측했다는 근거이며 전역 실행 소유권이나 Worker liveness의 증명이 아니다.

GET/list/results/SSE의 제어 의미는 그대로다. 새 projection은 파일/cache만 읽고 tick/claim/start,
모델/RPC 조작, cleanup, retry, lease 해제를 호출하지 않는다. UI freshness는 명시된 **조회 시점**
기준이며 기존 event 갱신/새로고침으로 다시 읽는다. 새로운 자동 Worker polling은 추가하지 않았다.

### F02 진단의 로컬 조회와 화면

`diagnostic_views.run_diagnostics`는 고정 파일만 읽어 F02의 identity/owner hash/event 정제와
`summarize_request`를 재사용한다. `responses` 응답에 additive `diagnostics`가 들어간다.
기존 인증을 쓰는 `GET /api/runs/{run_id}/diagnostics?offset=0&limit=20`도 제공한다.
summary/최종 response가 없는 중단 run은 알려진 run ID로 직접 조회할 수 있다.

- Controller 4 × 1 MiB, Worker collection 256 × 128 KiB, config 1 MiB까지 읽는다.
  Controller 요청 그룹은 최대 1,024개, 그룹당 32개 lifecycle event, 응답은 최대 20개 요약이다.
  보존된 최신 요청부터 페이지를 제공한다. 한도/회전/잘못된 연결/읽기 실패는 gap으로 표시한다.
- run/attempt/job/physical replica 오연결과 ownership 불일치를 거부한다. symlink 파일/진단
  디렉터리를 따라 임의 파일을 읽지 않는다. API는 run ID와 bounded page만 받는다.
- 원문 event/config/collection summary를 노출하지 않고 allowlist에서 요약을 다시 계산한다.
  prompt/response/token/header/SSH credential/환경변수/임의 경로/raw exception을 새 진단에 넣지 않는다.
- Worker 기록이 없으면 `WORKER_RECORD_NOT_ACQUIRED`; legacy/off는 `NO_DIAGNOSTIC_RECORD`.
  자료가 없다는 사실을 Worker 미실행 또는 성공으로 바꾸지 않는다.
- Controller timeout 뒤 Worker 완료는 추가 근거다. requests.csv, summary, 성공률·성능 수치,
  원래 실패, retry/attempt 이력은 바꾸지 않는다. 원인 분류는 INSUFFICIENT_EVIDENCE를 유지한다.
- 메모리/Worker process 관측 시각·source·bytes·availability와 같은 호스트 내 duration을 표시한다.
  결측 값은 UNKNOWN이다. RSS와 system memory를 더하거나 OOM을 추정하지 않는다.
- 진단 페이지는 별도 수집을 시작하지 않는다. 종료된 job의 기록 회수는 F02 CLI의 명시적
  one-shot 절차다. raw 응답이 없는 기록도 Controller journal에서 찾는다.
- 결과 선택 중 늦게 도착한 이전 조회 응답은 selection version/run ID로 거부한다.

**통합에서 발견한 수정:** 실제 job document의 `id`는 suite ID이고 `job_id`가 실행 job ID다.
F02 `job_context`의 비교를 `job_id`로 고쳤다. 테스트도 실제 `id != job_id` 형식을 사용한다.
이 변경이 없으면 정상 운영 job의 명시적 진단 회수가 거부될 수 있었다.

UI는 기존 theme/typography/navigation/Matrix/Compare를 유지하며 상태·요약·접는 상세만 추가했다.
변경 static asset cache version도 갱신했다. 별도 frontend framework나 Worker monitoring agent는 없다.

## 실제 자동 검증

2026-10-23 KST, Python 3.13 / Node 22.17.1 / npm 11.18.0 / Playwright 1.62.1.
가짜 Worker, 작은 합성 자료, 임시 filesystem과 localhost만 사용했다. 운영 8080 API는 조회하지
않았다. browser fixture port는 4273, inventory는 비어 있는 `.artifacts/f03/runtime/nodes.local.csv`.
Python 전체 검사는 `CLUSTER_RUNTIME_DIR`를 unset하여 auth test의 설정 주입을 보존했고,
기본 runtime은 격리 worktree 자체의 `.run`이다. 결과 경로는 `.artifacts/f03/results`다.

| 명령 / gate | 실행 결과 | 로컬 evidence |
| --- | --- | --- |
| `.venv/bin/python -m unittest` focused 5 modules `-v` | exit 0, 107 tests / 14.247 s | `focused.log` |
| `.venv/bin/python -m unittest discover -s cluster/tests -v` 최종 | exit 0, **775 tests** / 157.805 s | `python-full-final.log` |
| `npm test` | exit 0, syntax/fixtures/publication PNG + **43 E2E** / 1.1 m | `npm-full.log` |
| 새 F03 Playwright spec | exit 0, 4 tests / 6.5 s (위 43개에도 포함) | `e2e-focused.log` |
| `.venv/bin/python -m compileall -q cluster scripts` | exit 0 | local command completion |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON / 72 formal cells / 13 pinned actions / 7 shell scripts | `repository-validation.log` |
| `bash -n` tracked shell files | exit 0, 7 files | `shell-validation.log` |
| offline `pip wheel . --no-deps --no-build-isolation` | exit 0 | `wheel-build-final.log` |
| wheel static/template/new modules byte comparison | exit 0, 32 files 일치 | `package-static-check.json` |
| `git diff --check`, staged diff | exit 0 (staged check included before commit) | Git inspection |

focused modules: `test_operational_observability`, `test_request_diagnostics`, `test_sweep_api`,
`test_dashboard_research`, `test_phase12_facade_split` (모두 `cluster.tests.` prefix).
추가된 Worker cache 읽기/결측 테스트는 최종 전체 검사에 포함된다.

실제 browser 명령:

```bash
TYPOGRAPHY_OUTPUT_DIR="$PWD/.artifacts/f03/typography" DASHBOARD_TEST_PORT=4273 \
CLUSTER_RUNTIME_DIR="$PWD/.artifacts/f03/runtime-browser" \
CLUSTER_INVENTORY="$PWD/.artifacts/f03/runtime/nodes.local.csv" \
CLUSTER_RESULTS_DIR="$PWD/.artifacts/f03/results-browser" npm test
```

최종 wheel SHA-256: `b7e707af14c8ee61d9426a0af9409a1459977bd84a4e42a02810dd52190ce13e`.
local screenshots `progress-mobile-dark.png`, `diagnostic-result-light.png`를 직접 확인했다.
모든 evidence의 기준 폴더는 위 worktree의 `.artifacts/f03/`이며 Git에 포함하지 않는다.
ShellCheck는 로컬 실행하지 않았다. hosted CI 실행 여부/결과는 exact commit 기준으로 별도 보고한다.

초기 개발 검사는 빠진 import와 테스트 fixture의 잘못된 keyword/cache version을 검출하여 수정했다.
초기 전체 774 tests PASS 이후 cache 시각을 batch 시작 기준으로 보강하고 관련 테스트를 추가해
최종 전체 gate를 실행했다. 초기 로그도 삭제하지 않았다. 실패를 환경 탓으로 분류하지 않았다.

### 요구 시나리오와 검증 근거

| 요구 | 실제 검증 |
| --- | --- |
| 브라우저 polling 없이 후속 Sweep 완료 | 기존 `test_sweep_api` browserless 서비스/HTTP integration |
| 반복 GET이 claim/start/cleanup을 하지 않음 | 기존 read-only Sweep test + 새 offline filesystem byte 불변/loop-error 조회 |
| loop 예외 뒤 정상 running으로 오인하지 않음 | 새 Sweep/ResearchService 예외 주입, LOOP_ERROR 유지, E2E 표시 |
| 긴 child 및 cooldown | fake clock 800초 전진, live tick/자원 대기/cleanup/cooldown 분리 |
| disjoint 진단/취소 분리 | 새 child ID/node별 projection + 기존 resource coordinator/S10 targeted cancel + F02 ownership/correlation |
| restart 중복 완료 Trial 금지 | 기존 P01 restart recovery, 새 serving instance UNKNOWN fallback |
| old Worker/legacy | 기존 S10 old Worker fallback + F02 미수집 + 새 legacy 화면 |
| formal gate 유지 | 기존 Campaign gate-first/recovery/closure tests + 기존 JSON validator |
| privacy와 CSV | 기존/F02 tests + 새 폐기된 원문 collection summary/owner lease 미노출, none-mode UI |
| 최신 UI baseline/단위/theme/mobile | 전체 43 E2E와 JS fixtures, 두 theme 및 390px 포함 |

## 운영 보존 및 한계

운영 checkout은 `/Users/hajoonpark/자율설계/llm-cluster-benchmark`, branch
`codex/pi4-model-benchmark-20261014`, HEAD `e81e2b5ce84d9f9bc338e0da131f115d9ad8166e`로
유지됐다. 사전 dirty/untracked 11개 파일 SHA-256 모두 동일하다. 로컬 process 정보에서
Dashboard PID 49634, 시작 `2026-10-19 12:18:51 KST`, cwd가 운영 checkout임을 확인했다.
소스/정적 파일을 덮어쓰지 않았다. 실행 Python과 disk frontend의 실제 버전 일치는 **UNKNOWN**.
현재 active job/Worker 상태도 운영 API를 조회하지 않았으므로 **UNKNOWN**이다.

| 구분 | F03 종료 판정 |
| --- | --- |
| Software implementation | F03 범위 구현 완료 |
| Automated validation | 위 실제 실행 결과 기준 |
| Git / CI delivery | feature branch non-force delivery; 정확한 commit/CI는 최종 응답 |
| Deployment | NOT RUN; push는 배포가 아님 |
| Hardware acceptance | NOT RUN; 수동 인수 안내만 준비 |
| Formal eligibility | 기존 closed gate/연구 lock 유지; 승인하지 않음 |

하드웨어 overhead를 측정하지 않았고 성능 영향 없음/timeout 근본 원인 해결을 주장하지 않는다.
기존 H01의 600초 timeout, concurrency, 연구 결과·lock은 수정하지 않았다. 실제 Worker SSH/API,
모델 조작, inference/RPC, Sweep/Campaign/pilot, 장비 재부팅/전력 모드/네트워크 변경,
hardware workflow dispatch, 운영 배포와 main 병합은 실행하지 않았다.

인수 순서·중단 기준·기록 회수는 [기존 수동 인수 문서](../../manual/execution-driver-and-sweep-acceptance.md),
배포·rollback과 PR 초안은 [PR_DRAFT.md](PR_DRAFT.md)를 따른다.
**F03 commit/push 후 이번 software 패키지를 종료한다.**
