# API / state / schema baseline — S00

> 기준 시점 정정: 본문은 `5fd444a` 당시 S00 조사/계획이다. 현재 `98b6542`에는
> S01의 순수 SweepSpec/ResolvedPlan/compiler 및 tests가 추가되어 있다.
> 따라서 본문의 “신규/없음/proposed” 중 S01 항목은 현재 부재 판정이 아니다.
> 기존 Worker/JobService/RPC/Dashboard 실행 경로에는 변화가 없다.
> 범위 이탈 및 재검사 결과는 [S00-report.md](S00-report.md)의 재확인 절을 따른다.


Source: `5fd444a6d83e4226fcc5582196f3a27e71f84160`.

## 현재 경로 및 우회 가능 경계

| 진입점 | 현재 실행/제어 | 공통 자원 예약 관점 |
|---|---|---|
| Dashboard POST /api/experiments | facade → ExperimentManager → JobService → child job_process → SuiteRunner → runner | JobService 내부 RLock와 queued/running global guard. 프로세스 간 전체 Worker atomic lease 아님 |
| GET /api/experiments | active/jobs/runs 반환 | active는 단일 객체이며 실행 중이 없으면 최신 terminal도 반환 가능 |
| POST /api/experiments/cancel | ID 없는 active 취소, durable cancel_requested, identity-checked signal fallback | 다중 활성 대상 선택 계약 없음 |
| CLI benchmark runner main | config parse → run_experiment 직접 호출 | JobService admission을 거치지 않는 경로; S05 hook 필요 |
| scripts/research/phase09_pilot.py | run_experiment 직접 호출 | controlled pilot 전역 exclusive reservation 접점 필요; 이번에 실행하지 않음 |
| CampaignRunner | injected CampaignRunBackend, preflight/thermal gate, 한 cell씩 tick | concrete JobService adapter가 연결됐다고 가정 금지. 생산 코드에서 CampaignRunner 호출 연결 미발견 |
| Dashboard Campaign | GET /api/campaigns, /api/campaigns/{id}, /api/research/compare, /api/research/readiness | 읽기 기능. start/pause/resume/retry 제어 route 없음 |
| model install/delete/load/unload/sync 및 power/environment action | Dashboard action 경로와 clusterctl/Worker mutation | 일부 active-job checks는 있으나 모든 호출자가 공유하는 owner/fencing reservation 없음 |
| direct Worker routes | backend lock으로 model/load/unload/generation 직렬화 | caller owner/attempt 인증/epoch 없음; 다른 Controller의 mutation 격리 안 됨 |

소스: `cluster/dashboard/routes.py`, `services.py:ExperimentManager`,
`cluster/application/jobs.py`, `job_process.py`, `cluster/benchmark/runner.py`,
`cluster/research/campaign.py`, `protocol.py`, `cluster/worker/routes.py`.
기존 worker 인증과 프로세스 identity 검사는 reservation과 다른 책임이며 둘 다 보존한다.

## 동시성, 취소, background 작업

- `JobService.start`는 queued/running이 하나라도 있으면 거절한다. `cancel()`도 첫 해당
  job만 대상으로 한다. child는 durable cancel flag를 0.2초 주기로 관측한다.
- job service recover는 PID/argv/start-time identity와 suite terminal evidence를 사용하며
  확인되지 않는 상태를 orphaned로 보존한다. 이 기능은 remote cleanup 완료 증거를 대신하지 않는다.
- `_watch`는 단일 active의 변화만 전달한다. Dashboard snapshot과 SSE도 active 단일 객체 중심이다.
- Worker `LlamaCppInferenceBackend.lock`은 load/unload/tokenize/generation을 보호하는
  non-reentrant threading.Lock이다. streaming lifetime 동안 inference_slots=1을 유지한다.
- Dashboard `StatusMonitor`는 5초마다 probe_node를 호출하여 snapshot을 갱신한다.
  automatic SSH discovery는 현재 개선됐지만 live health probing까지 순수 preview는 아니다.
  S07 preview는 저장된 cache를 읽어야 하며 서비스 import/startup의 monitor side effect도 분리한다.
- Worker telemetry cache와 Controller measurement sampler는 별도 background 경로다.
  S05에서는 passive health와 무거운 hash/diagnostics 간 자원 정책을 구분해야 한다.
- RPC runtime의 host별 lifecycle lock/정확한 PID·listener 검증은 재사용한다.
  alias endpoint 중복을 금지하는 inventory validation은 있지만 durable whole-set reservation,
  cap=2, stale-owner fencing, cleanup quarantine은 신규 요구다.

## 현행 schema와 새 계약의 경계

| 객체 | 현재 의미 | 후속 확장 원칙 |
|---|---|---|
| ExperimentConfig | scalar run, suite/model index, formal/pilot identity; from_dict strict=False 기본 | 신규 sweep strict typed boundary. formal repeat_index/campaign_id 빌려 쓰지 않음 |
| ExperimentPayload | extra=allow → non-strict domain parser로 unknown key 기록 | 기존 API 호환 유지, 신규 sweep extra forbid와 size/budget 제한 |
| Worker SelectModelRequest | model_id/n_ctx/n_gpu_layers만 선언 | optional threads/batch 및 owner context, 구형 Worker capability gate |
| experiment_job | schema_version 1; queued/running/completed/failed/cancelled/orphaned | single-job default 보존; owner/job/attempt targeted 제어 |
| suite document | 모델별 ordered summaries/errors/cleanup_status, partial 상태 | 모델 loop는 한 계층이 소유; sweep와 중복 확장 금지 |
| Campaign manifest | config/research/campaign_manifest.schema.json; formal gate/retry/pause | 별도 namespace/승인 경계 유지 |
| measurement | instrumentation schema version 3, v1–3 reader contract | sweep additive trace 별도 version, legacy 데이터 rewrite 금지 |
| requests.csv | 기존 19 columns, test_measurement_instrumentation의 exact-header test | header 유지. sweep index/manifest 또는 additive JSON으로 연결 |
| results/privacy | config prompt hash, raw response full/hash_only/none, private store | plan/events/export도 같은 privacy; terminal recovery input scrub 재사용 |

새 객체 계획: SweepSpec → ResolvedPlan → BaseCell → Trial → Attempt → 기존 Job/Run.
별도의 ResourceReservation이 held/releasing/released/quarantined를 표현한다. 이는 현재
API가 받는 schema가 아니다. Sweep draft/ready/running/paused/terminal과 기존 Job 상태를
한 enum으로 합치지 않는다. requested/effective config, model/prompt identity, rpc profile,
condition_match, overlap IDs, measurement quality를 additive index로 연결한다.

## API/UI 신규 계약 계획

`/api/sweeps` 계열은 S07에서 확정할 제안 namespace다(현재 endpoint 아님).
capabilities/preview/save/list/get/start/pause/resume/cancel/retry/events/results/export가 필요하다.
서버가 catalog와 plan revision/hash를 resolve하고 Start/Resume/Retry는 idempotency 및
resource admission을 거쳐야 한다. GET/preview/import로 모델 준비/다운로드/실행하지 않는다.

현 Dashboard `static/app.js`는 scalar ctx/concurrency/output/GPU와 model_ids/RPC controls,
`static/js/models.js`, `results.js`, `research.js`를 제공한다. 통합 축 목록/조합 preview/
다중 active sweep 카드/targeted cancel은 없다. S08은 기존 framework 안에 builder/control
모듈을 추가하고 S09에서 comparison/export를 연결한다. 공정한 비교는 independent Trial
단위이며 tokenization 차이, early EOS, 실패·adjusted·unrun과 unavailable energy를 표시한다.
