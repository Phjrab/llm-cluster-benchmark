# Dashboard 진행 loop와 Sweep 운영 인수 절차

## 목적과 적용 범위

이 문서는 실제 Mac Controller와 승인된 Worker에서 작은 exploratory Sweep, 병렬 실행,
Campaign 제어와 RPC profile을 수동 인수할 때 사용하는 절차다. 자동 소프트웨어 회귀 통과와
실제 하드웨어 동작, formal 연구 승인을 서로 다른 판정으로 유지한다.

현재 실행 구조는 별도 daemon이나 독립 scheduler가 아니다. 사용자가 Start를 승인하면 Mac
Controller의 Dashboard 프로세스 내부 진행 loop가 기존 `SweepSupervisor` 또는
`CampaignRunner`를 주기적으로 구동하고, 실제 inference는 기존 `JobService` child가 수행한다.
브라우저를 닫아도 Dashboard 서버가 살아 있으면 후속 Trial/cell이 진행된다. Dashboard 서버가
꺼져 있는 동안 이미 시작한 child는 기존 정책에 따라 살아 있을 수 있지만, 후속 조건의 자동
시작은 지원 범위가 아니다. Mac 전원 종료와 Worker 네트워크 단절 중 지속 실행도 지원하지
않는다.

이 절차 자체는 실행 승인이 아니다. 실제 장비, 모델, 조건과 허용 시간을 먼저 고정하고 작은
설치 모델부터 수행한다. 70B 모델이나 formal Campaign을 첫 인수 대상으로 사용하지 않는다.

## 상태를 구분해서 읽는 법

| 상태 | 확인 위치 | 의미 | 의미하지 않는 것 |
| --- | --- | --- | --- |
| Dashboard 접속 | `llm-cluster status`, `/dashboard/health`, 브라우저 연결 상태 | 현재 Controller HTTP 프로세스가 응답함 | Worker online, 진행 loop 또는 child 생존 보장 |
| Dashboard 진행 loop | 새 Sweep/Campaign event와 다음 Trial/cell의 durable 전이 | 이 Dashboard가 저장된 상태 머신을 실제로 진행 중임 | 기존 결과가 보인다는 사실만으로 생존을 추정할 수 없음 |
| child job | Sweep Trial/Campaign cell의 active attempt, job event와 private job log | 특정 실행 child의 상태 | Dashboard 연결 상태 또는 다른 job 상태 |
| Worker 상태 | Worker 카드와 fresh health/capability snapshot | 해당 시점 API 및 배포/runtime 관측 | 진행 loop나 formal 승인 |
| 자원 점유/quarantine | attempt event, resource blocker, reconciliation 상태 | 물리 Worker lease와 cleanup 신뢰 상태 | heartbeat 누락만으로 해제되었다는 뜻이 아님 |
| 일반 실험 실행 가능 | Sweep preview/readiness와 ordinary preflight | exploratory 실행 조건을 만족함 | formal Campaign 승인 |
| formal execution gate | Research Readiness의 execution gate | formal cell을 새로 제출할 수 있는지 | 모델 설치 또는 runtime smoke만으로 열리지 않음 |
| model installed | Model Library의 Worker installation | exact artifact가 Worker filesystem에 검증되어 있음 | 그 context/runtime에서 실행 검증 또는 formal 승인 |
| runtime execution verified | Model compatibility 또는 결과의 verified execution evidence | exact artifact가 관측된 runtime/backend에서 실행됨 | 다른 Worker/context의 보장 또는 formal 승인 |
| formal approved | lock, fresh identity, protocol, gate가 모두 충족된 formal 상태 | 승인된 Campaign 범위에서만 사용 가능 | 일반 Sweep 결과의 자동 승격 |

현재 UI는 process-local 진행 loop를 독립적인 전역 liveness 배지로 제공하지 않는다. 따라서
오래된 결과나 `running` 문자열만 보고 loop가 살아 있다고 판정하지 않는다. 인수 중에는
시작 시각 이후의 새 event와 다음 attempt claim을 함께 증거로 보존한다. 상태가 움직이지 않으면
Dashboard log, child identity와 durable manifest를 확인할 때까지 `UNKNOWN`으로 둔다.

## 인수 전 증거 봉투

실행 전에 다음 값을 한 기록에 고정한다.

- Controller branch, commit, clean/dirty 상태
- 승인된 Worker stable ID와 물리 host identity
- Worker별 source commit/tree, runtime fingerprint, backend와 power 관측
- exact model ID, revision, size, SHA-256와 설치 Worker
- Sweep plan revision/SHA-256 또는 Campaign identity
- context, concurrency, timeout, 요청 수, warmup과 repeat
- 허용한 일반/RPC 실행 범위와 최대 시간
- 결과, event, job log와 cleanup evidence 경로

중간에 product code, 모델 artifact, runtime 또는 조건을 바꾸면 같은 인수 실행으로 계속하지
않는다. 변경 이유를 기록하고 새 plan/revision으로 다시 시작한다.

## 공통 정지 기준

다음 중 하나가 발생하면 새 dispatch를 멈추고 현재 attempt의 안전 경계와 cleanup 상태를
확인한다.

- 승인 범위를 벗어난 Worker, 모델 또는 조건이 선택됨
- source/model/runtime identity가 계획과 다름
- 같은 물리 Worker가 겹치는 두 child가 동시에 실행됨
- targeted cancel이 다른 disjoint job에 영향을 줌
- cleanup 상태가 불명확한데 자원이 free로 표시됨
- 반복되는 Worker offline, model load/OOM, request timeout 또는 native RPC cleanup 실패
- 결과/event/measurement 파일이 기존 경로를 덮어씀
- privacy 설정이 금지한 prompt/response 원문이 노출됨
- formal gate가 닫혔는데 새 Campaign cell이 제출됨

## 1. Mac/Worker 코드와 capability 확인

**작업**

1. Controller에서 `git status --short --branch`와 `git rev-parse HEAD`를 기록한다.
2. `llm-cluster status`와 Dashboard health를 확인한다.
3. Dashboard Worker 화면에서 승인된 Worker만 선택해 fresh health, deployment identity,
   inference backend, `inference_slots`, RPC capability와 power 관측을 기록한다.
4. Model Library에서 사용할 exact artifact의 설치 위치와 checksum을 확인한다.

**예상 결과**

Controller는 제어 역할만 가지며 Worker 목록에 inference participant로 나타나지 않는다. RPC
coordinator 후보는 선택 Worker 중 하나다. 설치, runtime verified와 formal approved 표시는
서로 독립적이어야 한다.

**확보할 자료**

Controller/Dashboard log, health/capability JSON, Worker deployment identity, model inventory와
화면의 blocker code를 저장한다. credential과 token은 저장하지 않는다.

**정지 기준**

commit/tree drift, 잘못된 physical host 매핑, backend 미검증, checksum 불일치 또는 예상하지
않은 Worker가 있으면 실행하지 않는다.

## 2. 모델 1개·Worker 1대의 작은 Sweep

**작업**

설치되고 검증 가능한 작은 모델 하나와 Worker 하나를 선택한다. 요청 수와 output token을 작게
고정하고 `single_node`, repeat 3 이상의 preview를 만든다. plan hash를 검토하고 draft를 저장한
뒤 명시적으로 Start한다.

**예상 결과**

Start 한 번만 첫 attempt를 만든다. 각 child가 끝나면 Dashboard 진행 loop가 브라우저 GET과
무관하게 다음 Trial을 시작한다. 각 Trial은 고유 attempt/job/run ID를 갖고 한 번만 완료된다.

**확보할 자료**

preview, plan revision/hash, start response, 전체 event journal, child job IDs, run IDs와 cleanup
상태를 저장한다.

**정지 기준**

동일 Trial의 중복 child, 계획에 없는 Worker/model, fresh readiness drift 또는 cleanup
불확실성이 발생하면 중단한다.

## 3. 두 context의 요청값과 실제값 비교

**작업**

같은 모델·Worker·prompt에서 안전한 context 두 값을 축으로 사용한다. 나머지 조건은 고정한다.

**예상 결과**

각 cell의 requested `n_ctx`가 plan에 남고, 결과의 requested/resolved/factory/effective config가
구분된다. 명시 context가 자동으로 다른 값으로 바뀌면 해당 cell은 성공으로 처리되지 않는다.

**확보할 자료**

plan cell, preparation identity, `actual_model_config`, requested/effective context, warning/failure
code와 response metadata를 저장한다.

**정지 기준**

context mismatch, preparation identity 불일치, 예상 밖 자동 조정 또는 OOM/load 실패가 발생하면
큰 context로 확대하지 않는다.

## 4. 모델 두 개의 identity/template/응답 확인

**작업**

같은 작은 workload에서 설치된 모델 두 개를 독립 cell로 선택한다. 각 모델의 artifact identity,
chat template/tokenizer identity와 response storage policy를 확인한다.

**예상 결과**

모델 축의 각 값이 별도 cell/run으로 남고 model SHA, source revision, template hash와 실제 적용
config가 뒤섞이지 않는다. response 원문은 설정이 허용한 경우에만 표시된다.

**확보할 자료**

모델별 plan reference, load/preparation event, participant snapshot, template hash, output hash와
privacy status를 저장한다.

**정지 기준**

모델 cache가 다른 모델을 재사용하거나 identity/template이 누락·불일치하면 비교를 중단한다.

## 5. 겹치지 않는 Worker 그룹 두 개의 병렬 실행

**작업**

서로 다른 물리 Worker 집합 A와 B를 만든다. `disjoint_parallel`과 기존 상한 2를 사용하고,
두 child의 실행 구간이 실제로 겹치는지 확인한다. 비교용으로 A와 겹치는 세 번째 조건 C도
준비하되 C가 대기하는지만 확인한다.

**예상 결과**

A와 B는 최대 두 job까지 함께 실행할 수 있다. C는 공유 Worker lease 때문에 기다린다.
요청 `concurrency`는 각 child 내부 요청 수이고 동시 job 수로 계산되지 않는다.

**확보할 자료**

각 job의 Worker set, resource lease/owner, 시작·종료 monotonic/UTC 시간, overlap label과 event를
저장한다.

**정지 기준**

동일 physical host가 두 active lease에 들어가거나 parent 진행 loop가 job 한도를 소비하면
중단한다.

## 6. 한 작업만 취소하고 다른 작업 유지

**작업**

A와 B가 실행 중일 때 A의 Sweep/attempt만 Cancel한다. 광범위한 Worker stop 명령을 사용하지
않는다.

**예상 결과**

A의 exact child만 cancel/cancelling/terminal로 전이하고 B의 PID, lease, 모델과 결과 진행은
유지된다. A cleanup이 불확실하면 해당 자원만 quarantine된다.

**확보할 자료**

Cancel 요청의 idempotency key, targeted job/process identity, A/B 전후 event, lease와 cleanup
결과를 저장한다.

**정지 기준**

B가 중단되거나 공유되지 않은 Worker의 모델/RPC가 unload되면 전체 병렬 인수를 실패로 둔다.

## 7. 브라우저를 닫은 상태의 후속 Trial 진행

**작업**

repeat 3 이상의 작은 Sweep 첫 child가 실행 중일 때 모든 Dashboard 탭을 닫고 GET polling과
SSE 연결을 끊는다. Controller Dashboard 서버는 계속 실행한다.

**예상 결과**

첫 child 종료 후 두 번째와 세 번째 Trial이 자동으로 claim·완료된다. 브라우저를 다시 열면
같은 durable Sweep과 attempt history를 읽으며 새 Start를 만들지 않는다.

**확보할 자료**

브라우저 종료 전 마지막 cursor, 서버 event journal, 후속 child job IDs와 재접속 후 detail을
저장한다.

**정지 기준**

새 브라우저 요청 전까지 다음 Trial이 시작되지 않거나 재접속이 중복 attempt를 만들면 실패다.

## 8. Dashboard 서버 중지 경계 확인

**작업**

작은 Sweep의 child가 실행 중일 때 지원되는 `llm-cluster stop`으로 Dashboard만 중지한다.
Worker나 child에 별도 stop을 보내지 않는다. 관찰 시간을 짧게 제한한 뒤 Controller를 다시
시작한다.

**예상 결과**

Dashboard 중지 자체가 Worker를 offline으로 재분류하거나 child를 무조건 cancel하지 않는다.
이미 시작된 child는 기존 독립 child 정책에 따라 완료할 수 있다. 서버가 꺼진 동안 후속
Trial/cell 자동 dispatch는 **지원하지 않으므로 기대하지 않는다**. 재시작 후 durable state를
reconcile하고 그때 후속 조건을 계속한다.

**확보할 자료**

stop/start 시각, Dashboard PID/log, child ProcessIdentity, 중지 전후 manifest와 event, 재시작
recovery event를 저장한다.

**정지 기준**

Dashboard stop이 unrelated child에 signal을 보내거나, 서버 중지 중 cleanup 불확실성을 free로
바꾸거나, 재시작이 완료 Trial을 다시 실행하면 실패다.

## 9. Dashboard 재시작 후 중복 없는 복구

**작업**

서버를 다시 시작해 동일 Sweep을 연다. Start를 다시 누르지 않고 recovery를 기다린다. 필요하면
동일 idempotency key의 Start 응답 유실 상황도 작은 fixture/승인 범위에서 확인한다.

**예상 결과**

이미 완료된 Trial은 그대로 유지되고 active attempt는 같은 job identity로 inspect된다. 다음
pending Trial만 한 번 claim된다. draft, paused, completed Sweep은 자동 시작하지 않는다.

**확보할 자료**

재시작 전후 attempt/job/run ID, start count, recovery/reconciliation event와 blocker를 저장한다.

**정지 기준**

새 attempt ID로 불확실한 실행을 대체하거나 동일 child가 두 번 dispatch되면 중단한다.

## 10. 작은 RPC profile과 cleanup

**작업**

RPC가 실제 검증된 작은 모델과 승인 Worker 두 대 이상을 사용한다. coordinator를 선택 Worker
중 한 대로 지정하고 작은 context/output/요청 수로 preview한다. exact pinned RPC capability와
profile hash를 확인한 뒤 실행한다.

**예상 결과**

coordinator와 device는 plan/profile에 고정되고 논리 요청 하나가 공동 생성 하나로 기록된다.
일반 replicated/broadcast 처리량으로 표시되지 않는다. 종료 후 session ID에 속한 coordinator와
RPC device만 정리된다.

**확보할 자료**

RPC commit/capability, coordinator, ordered Worker/weight profile, session ID, native process/port
전후 상태, result와 cleanup event를 저장한다.

**정지 기준**

coordinator가 Controller로 선택되거나, profile 밖 Worker가 참여하거나, cleanup 뒤 port/process가
남으면 확대 실행을 하지 않고 quarantine/reconciliation으로 전환한다.

## 11. 실패·quarantine·수동 reconciliation

**작업**

실제 장애를 만들기 위해 전원이나 네트워크를 끊지 않는다. 승인된 작은 fixture 또는 자연 발생한
실패에서 failure code, cleanup 상태와 resource state를 확인한다. 불확실 상태는 기존 수동
reconciliation 절차를 따른다.

**예상 결과**

TTL 또는 heartbeat 누락만으로 lease를 free 처리하지 않는다. process/port/model ownership 증거가
충분할 때만 동일 resource를 다시 사용한다. 다른 disjoint 작업은 유지된다.

**확보할 자료**

failure stage/code, exact attempt owner, ProcessIdentity, port/process/model 확인, quarantine reason과
operator reconciliation event를 저장한다.

**정지 기준**

증거 없이 quarantine을 우회하거나 cleanup 실패를 성공으로 바꾸면 인수를 실패로 둔다.

## 12. 결과 비교와 privacy-aware export

**작업**

Sweep Results에서 cell/repeat/attempt/run 연결, requested/effective config, logical/physical 요청,
성공률, TTFT/E2E, energy availability와 retry history를 확인한다. JSON/CSV export와 한 조건의
새 draft clone을 확인한다.

**예상 결과**

기존 `requests.csv` 19-column 파일은 변경되지 않는다. export는 실제 run ID와 선택 규칙을
포함하고 `formal_approved=false`를 유지한다. hash-only/none privacy에서는 prompt/response 원문을
복원하지 않는다. clone은 draft만 만들고 실행하지 않는다.

**확보할 자료**

export SHA-256, 포함 run IDs, response storage mode, 누락 reason과 clone된 plan hash를 저장한다.
원문 response, 개인정보와 credential은 인수 보고서나 Git에 넣지 않는다.

**정지 기준**

실패 attempt가 사라지거나 성공 retry만 선택되거나, 결측치를 0으로 바꾸거나, privacy 밖 원문이
노출되면 결과 인수를 중단한다.

## 판정 기록

최종 기록은 다음 네 줄을 독립적으로 판정한다.

- **Software contract:** 자동 회귀와 API/data contract 결과
- **Hardware functionality:** 승인 장비에서 위 1–12 단계 중 실제 실행한 결과
- **Measurement quality:** 전력·온도·네트워크·시간·요청 성공률의 실제 품질
- **Formal eligibility:** lock, fresh identity, preregistered evidence와 gate 상태

Software PASS나 exploratory hardware PASS만으로 formal gate를 열지 않는다. 실패 또는 미실행
항목은 `NOT RUN`, `FAIL`, `NEEDS RECONCILIATION` 중 하나로 그대로 기록한다.
