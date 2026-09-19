# Context·모델·RPC exploratory sweep 사용 가이드

이 기능은 설치된 모델과 현재 Worker 증거로 일반 탐색 실험을 계획하고 실행한다. 결과는
formal Campaign 승인 자료가 아니며, preview·draft 저장은 모델 설치나 실행을 시작하지 않는다.
실제 장비의 메모리 여유, runtime 호환성, 네트워크 상태가 성공 여부를 결정하므로 어떤 모델
크기나 조합도 성공을 보장하지 않는다.

## 시작 전 준비

1. **Models** 화면에서 사용할 모델을 먼저 설치하고 checksum, 단일 GGUF identity,
   template/tokenizer metadata, Worker별 설치 상태를 확인한다. Sweep 화면은 모델을 자동으로
   다운로드하거나 다른 Worker로 복제하지 않는다.
2. Worker status에서 source/runtime compatibility와 RPC capability를 확인한다. row split이나
   정수 GPU layer가 `unknown` 또는 `unsupported`이면 그 기능을 검증된 것으로 취급하지 않는다.
3. 이미 검증한 8B/12B/14B급 모델이 있다면 작은 context와 짧은 prompt로 시작한다. 더 큰
   모델은 설치·loader·artifact set·메모리·RPC 조건을 별도로 충족해야 한다.
4. 실제 inventory, formal lock, 전력 모드, OS/runtime pin은 sweep 생성 과정에서 바뀌지 않는다.
   재부팅이나 전력 모드 변경은 별도의 운영 판단과 승인 후 수행한다.

## 가장 작은 UI 실행

Dashboard의 **05 / Exploratory Sweeps**로 이동한다.

1. 영숫자로 시작하는 Sweep ID를 입력한다.
2. 설치된 모델이 있는 Worker 한 대와 모델 한 개를 선택한다.
3. Strategy는 `replicated_round_robin`, 조합은 `grid`로 둔다.
4. `n_ctx=1024`, `concurrency=1,2`, `max_tokens=64`, repeat 1을 입력한다.
5. Input variant에 짧은 동일 문장을 입력한다. Context capacity 비교에서는 문장 자체를
   바꾸지 않는다. 입력 길이 비교가 목적이면 별도의 token-length variant를 만든다.
6. **Plan preview**를 누른다. candidate/valid/blocked/unknown/excluded cell, 총 trial,
   logical/physical request, warmup, model load, plan SHA-256을 확인한다.
7. 설치 누락이나 capability blocker가 있으면 Models/Worker 준비로 돌아간다. 임의로 blocker를
   무시하지 않는다. 필요 없는 valid cell은 사유를 입력해 제외하고 revision이 바뀐 preview를
   다시 확인한다.
8. 현재 hash 확인 체크박스를 선택하고 **Draft 저장**을 누른다. Active Sweeps에서 저장된
   revision/hash를 다시 확인한 뒤 **Start**를 누른다. Start는 fresh readiness와 저장한 plan을
   다시 비교하며, 바뀌었으면 실행하지 않는다.

`concurrency`는 한 child run 안의 request 동시성이다. `max_parallel_jobs`는 동시에 실행할
child 수다. 기본은 순차 실행과 1 job이다. `disjoint_parallel`과 2 jobs를 명시적으로 선택해도
같은 물리 Worker를 공유하는 child는 앞 작업의 cleanup이 끝날 때까지 기다린다.

## Context와 모델 확장

작은 실행이 끝난 후 모델 두 개, `n_ctx=1024,2048,4096`, `concurrency=1,3,6`,
`max_tokens=64,128`처럼 한 축씩 늘린다. 2×3×3×2×3은 108 trial이므로 preview에서
요청 수, model reload, cooldown과 저장량을 먼저 검토한다. 108개 전체를 실행할 의무는 없다.

결과 화면에서 다음을 함께 본다.

- requested/effective context와 실제 template 적용 input token 수
- model artifact SHA, quantization, template/tokenizer identity
- output 상한과 실제 output token, finish reason, early EOS
- 처리량 의미: replicated, broadcast, node sweep, RPC는 같은 숫자라도 의미가 다르다
- 독립 repeat를 표본으로 계산한 통계와 CI; request 여러 개를 repeat로 세지 않는다
- energy `N/A`, partial, degraded 이유와 parallel exploratory 공유 자원 경고

## RPC profile 실행

Strategy를 `model_parallel_rpc`로 선택하고 2대 이상의 Worker로 profile을 만든다.
coordinator, Worker 순서, `layer`/`row`, `auto`/`equal`/`custom`, custom Worker별 weight,
RPC GPU layers를 각각 확인한다. 모델 파일은 coordinator에 설치돼 있어야 하며 참여 Worker의
pinned RPC runtime capability와 메모리 증거가 필요하다.

작은 context, request 1, repeat 1로 먼저 실행한다. 각 cell은 새 RPC session을 만들고
종료 시 시작을 시도한 모든 device와 coordinator를 정리한다. cleanup 실패나 포트 잔존은
해당 자원을 quarantine하며, 운영자가 프로세스와 포트가 없다는 증거로 reconcile하기 전에는
겹치는 작업을 시작하지 않는다. llama.cpp RPC 경로는 인증 없는 사설 LAN용 실험 기능이다.

## 일시정지, 재개, 취소, 재시도

- **Pause**는 현재 child를 강제 중단하지 않고 완료·cleanup 안전 경계에서 멈춘다.
- **Resume**은 fresh readiness를 다시 확인하고 완료 trial을 다시 실행하지 않는다.
- **Cancel**은 선택한 sweep의 active child만 대상으로 한다. 다른 disjoint job의 process,
  모델, 로그와 lease는 유지된다.
- **Retry**는 실패하거나 취소된 trial에 사유를 남기고 새 attempt를 만든다. 이전 attempt와
  failure 기록은 유지되며 자동 retry는 없다.

Dashboard를 새로 열거나 Controller가 재시작돼도 Active Sweeps에서 같은 sweep을 선택한다.
manifest, event journal, JobService document와 result artifact를 대조해 상태를 복원한다. Start
응답이 끊겼다면 같은 idempotency key와 plan hash를 사용한다. 새 ID로 반복 Start하지 않는다.

## 장애 대응

| 표시 | 조치 |
|---|---|
| `blocked` | 설치, checksum, template, runtime 또는 capability 이유를 해결한 후 readiness를 새로 확인한다. |
| `unknown` | 실제 증거가 없는 상태다. 지원됨으로 가정하지 말고 Worker/runtime 확인을 완료한다. |
| `needs_reconciliation` / quarantine | 자동 해제하지 않는다. 대상 process·port·모델 상태를 확인하고 운영자 reconcile 증거를 남긴다. |
| requested/effective mismatch | 해당 run을 원 조건의 정상 표본으로 사용하지 않는다. 적용된 값과 adjustment reason을 확인한다. |
| Pi power warning/degraded | 일반 exploratory 실행의 경고로 기록한다. offline, crash, cleanup 실패와 구분한다. formal Campaign의 기존 power gate는 그대로 적용된다. |
| SSE 연결 종료 | 새 run을 만들기 전에 화면을 새로고침한다. run별 journal에서 진행 상태와 로그를 복원한다. |
| RPC partial start/timeout | cleanup evidence를 확인한다. 불확실하면 겹치는 profile을 시작하지 않는다. |

## 결과와 export

기본 표는 official attempt를 표시하고 **모든 attempt**를 선택하면 실패·취소·retry 이력까지
펼친다. 그래프 축과 series를 바꿀 때 나머지 조건이 섞인다는 경고, tokenizer 차이, strategy
처리량 의미를 확인한다. JSON export는 plan과 전체 attempt index를 보존한다. CSV는 요약용이며
기존 run별 `requests.csv`의 19-column 계약을 바꾸지 않는다. prompt/response 원문은 기존
privacy 설정이 허용해 저장된 경우에만 export에 포함된다.

재현할 조건은 **조건을 새 Draft로 복제**해 다시 preview한다. 기존 plan/run/attempt는 수정되지
않으며 복제만으로 실행되지 않는다. 일반 sweep을 formal-approved로 표시하거나 formal 입력에
자동 포함하지 않는다.

## API 예제 템플릿

- [작은 context/model grid](examples/context-model-small.template.json)
- [작은 RPC profile grid](examples/rpc-small.template.json)

두 파일의 `request_template`은 현재 `SweepSaveDraftPayload`의 실제 필드명을 사용한다. 바깥
wrapper는 문서용이며 API payload가 아니다. `unresolved` 경로를 실제 inventory의 Worker ID와
설치·검증된 catalog model ID로 바꾸고 `request_template`만 `/api/sweeps/preview`에 보낸다.
저장할 때는 같은 body를 `/api/sweeps/drafts`에 보내며, preview가 반환한 revision/hash와 새
idempotency key를 `/api/sweeps/{id}/start`에 보내야 한다. 이 템플릿은 자동 import나 Start를
수행하지 않는다.

실제 장비 확인 절차는 작은 일반 sweep, 분리된 두 Worker pool, 작은 RPC profile 순서로
사용자가 직접 수행한다. Hardware acceptance workflow는 수동 또는 정해진 schedule/tag에서만
실행하며 이 가이드의 문서·preview 단계가 자동 dispatch하지 않는다.
