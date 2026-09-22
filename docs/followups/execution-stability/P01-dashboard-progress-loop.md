# P01 — Dashboard 소유 실행 진행 loop

작성일: 2026-09-22 (Asia/Seoul)

기준 branch: `codex/current-source-pilot-v6`

기준 commit: `91755e818e4ddeb20bfebe57115619255e649d4b`

## 적용 범위

이 구현은 P00 뒤에 사용자가 제공한 “독립 실행기 신설 제외” 지시를 따른다. 새 execution
driver process, 상시 daemon, 별도 scheduler service와 Worker-side orchestration은 만들지 않았다.
Mac Controller의 Dashboard 서버, 전원과 Worker 네트워크가 실험 동안 유지된다는 운용 전제에서
브라우저 polling 없이 이미 승인된 Sweep/Campaign을 진행한다.

제품 변경은 기존 `SweepSupervisor`, `CampaignRunner`, `JobService`, durable manifest와 원자적
claim을 재사용한다. 연구 lock, formal matrix, 실제 inventory, 모델 artifact와 실제 결과는
수정하지 않았다. 실제 Worker 접속, 모델 다운로드/load, inference, native RPC와 실제
Sweep/Campaign은 실행하지 않았다.

## 구현 결과

### Sweep 진행

`SweepService`가 Dashboard 수명에 묶인 Sweep별 background thread를 process 안에서 관리한다. Start는 기존대로
fresh readiness와 저장 plan을 대조하고 첫 durable lifecycle boundary를 진행한 뒤 thread를
확인한다. thread는 설정된 간격으로 `SweepSupervisor.tick()`을 호출하고 다음 상태에서 멈춘다.

- `paused`
- `completed`
- `partial`
- `failed`
- `cancelled`

Dashboard startup은 `SweepRepository.list()`로 기존 durable manifest를 읽고 `ready` 또는
`running` Sweep만 복구한다. Sweep run manifest는 Start 처리에서만 생성되므로 draft 조회나
서버 시작이 새 실행 승인이 되지 않는다. `paused`와 terminal Sweep은 자동 시작하지 않는다.

Dashboard shutdown은 진행 thread에 stop event를 보내고 bounded join을 수행한다. 이 경로는
`JobService.cancel()`을 호출하지 않으며 기존 child process를 일괄 종료하거나 자원을 해제하지
않는다. 서버 중지 중 현재 child는 기존 durable process 정책에 따라 완료될 수 있지만 다음
Trial dispatch는 지원 범위가 아니다. 서버 재시작 후 같은 attempt/job identity를 inspect하여
복구하고 완료 Trial을 다시 시작하지 않는다.

동일 Sweep에 여러 Dashboard service instance의 thread가 존재해도 `SweepRepository`의
per-sweep file lock, atomic claim과 deterministic backend job ID가 중복 child dispatch를 막는다.
진행 thread 자체는 `max_parallel_jobs`의 inference job으로 계산되지 않는다.

### 조회 전용 API

`SweepService.get()`에서 `_tick()` 호출을 제거했다. 다음 경로는 durable 상태를 읽고 표현할
뿐 Trial claim, child 생성, model/RPC lifecycle 또는 실행 승인을 수행하지 않는다.

- Sweep GET/detail
- list
- events와 SSE
- results와 export

terminal privacy prompt scrub은 진행 loop의 `_tick()` terminal 처리에 남아 있으므로 사용자가
화면을 열지 않아도 완료된다.

### Campaign 진행과 formal gate

Campaign은 기존 `ResearchService` daemon thread와 `CampaignRunner`를 그대로 사용한다. 새
Campaign scheduler를 추가하지 않았다. 기존 Start/Resume gate를 유지하고, 진행 loop가 새 cell을
dispatch하기 직전 현재 `formal_execution_allowed`를 다시 읽도록 보강했다.

gate가 active child 실행 중 닫히면 loop는 해당 child의 terminal 상태와 cleanup을 기존 정책대로
reconcile한다. active child가 없으면 후속 cell을 제출하지 않고
`campaign_dispatch_gate_blocked` event를 한 번 기록한 뒤 gate를 계속 확인한다. resource
reservation, fencing, cooldown, quarantine와 formal 직렬 실행 정책은 변경하지 않았다.

## 자동 검증 근거

모든 동작 검사는 임시 저장소, fake backend, fake Worker와 localhost fixture만 사용했다.

### 브라우저 없는 자동 진행

HTTP로 repeat 3의 Sweep을 Start한 뒤 추가 GET/SSE 요청을 전혀 보내지 않았다. fake child가
완료될 때마다 Dashboard 진행 thread가 후속 Trial을 시작했고, plan의 전체 6개 Trial이 각 1회
완료됐다. service-level 검사도 같은 조건으로 수행했다.

결과: **PASS**

### 조회 부작용 제거

draft, running, paused Sweep에 GET을 반복 호출했다. draft에는 run manifest가 생기지 않았고,
running/paused Sweep의 backend start 횟수도 증가하지 않았다.

결과: **PASS**

### 재시작과 중복 방지

첫 Trial 완료와 두 번째 Trial 시작 뒤 첫 `SweepService`를 shutdown하고 같은 repositories와
backend로 새 service를 구성해 startup recovery를 수행했다. 전체 실행이 완료됐고 첫 job의 start
횟수는 1회였다. 두 service가 같은 running Sweep을 동시에 recover한 검사에서도 같은 backend
job은 한 번만 시작됐다.

결과: **PASS**

### Pause, Cancel, cleanup 경계

Pause는 active child 완료 뒤 `paused`에 도달했고 Resume에서만 후속 Trial이 시작됐다. 선택한
Sweep의 active job 취소 뒤 durable cancel reconciliation이 완료됐으며, 기존 S10 disjoint pool
검사는 다른 active job과 resource ownership이 유지됨을 확인했다. cleanup uncertainty와
quarantine 정책은 변경하지 않았다.

결과: **PASS**

### Formal gate

gate가 닫힌 Campaign Start는 기존대로 409로 차단됐다. active child reconciliation 직후 gate를
닫는 합성 검사에서는 active child 결과 처리는 완료됐지만 후속 pending cell dispatch는 발생하지
않았다.

결과: **PASS**

## 실행한 검사

| 검사 | 실제 결과 |
|---|---|
| P01 focused Sweep/Campaign/S10 integration | 72 tests, PASS, 9.736s |
| 전체 Python unittest discovery | 712 tests, PASS, 144.234s |
| `npm test` | PASS; syntax, fixtures, publication PNG, Playwright 8/8, 52.2s |
| `python -m compileall -q cluster scripts/ci` | PASS |
| `scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| repository shell scripts `bash -n` | 7 scripts, PASS |
| packaging regression | 3 tests, PASS |
| offline wheel build | PASS; system Python 3 build environment; SHA-256 `67d4dc166d3ba13915fd1e775f099046f0e59b9c9c02b343736b8b65ae4545a9` |

전체 Python, compile, validator, packaging과 wheel 검사는 현재 변경을 복사한 `/private/tmp`
clean snapshot에서 실행해 실제 `.run`, inventory와 결과를 사용하지 않았다. `npm test`는 repository의
fixture webserver와 Playwright 설정으로 실행했다. test output의 SSH/RPC/model 문자열은 mock 또는
negative fixture이며 실제 endpoint 호출이 아니다. Controller `.venv`에는 `setuptools`가 없어 그 interpreter의 offline wheel
명령은 metadata backend import 단계에서 종료됐다. 동일 snapshot을 repository에서 사용하는
system Python의 설치된 `setuptools 82.0.0`으로 다시 빌드해 성공했다. 이 실패는 제품 test failure로
분류하지 않았으며 두 실행 결과를 모두 보존했다.

## 호환성과 제한

- 요청 concurrency와 `max_parallel_jobs` 의미는 바꾸지 않았다.
- 기본 1 job, 명시적 disjoint parallel 상한 2와 물리 Worker overlap 방지는 유지된다.
- JobService child process, ProcessIdentity, targeted cancellation과 resource fencing은 유지된다.
- formal Campaign의 직렬 실행, fresh preflight, cooldown과 gate 정책은 유지된다.
- 기존 결과/event/measurement/response와 `requests.csv` 계약은 변경하지 않았다.
- 별도 execution driver/process/daemon은 없다.

검증 결과의 구분은 다음과 같다.

- 브라우저 없이 자동 진행: **VERIFIED**, Dashboard 서버가 실행 중인 동안
- Dashboard 재시작 복구: **VERIFIED**, durable state와 같은 attempt identity 사용
- Dashboard 서버 종료 중 전체 자동 진행: **OUT OF SCOPE**
- Mac 종료·네트워크 단절 중 지속 실행: **OUT OF SCOPE**

## 다음 단계

P02의 runtime lock 기대 source tree hash와 실제 관측 hash 직접 비교가 다음 추천 단계다. 이
P01 작업에서는 P02, P03, 선택 작업과 hardware/formal 실행을 시작하지 않았다.
