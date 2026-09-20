# Capability baseline — S00

## R07 종료 기준선 — daa6b60

S01–S10 exploratory Sweep software와 R02–R06 reliability 후속 구현은 현재 feature source에
통합됐다. typed plan, runtime profile/input preparation, catalog model identity, RPC profile,
durable resource reservation/supervisor, lifecycle API, Dashboard, result comparison과 R06
compatibility evidence가 모두 존재한다. 전체 현재 증거는 [R07-report.md](R07-report.md)에 있다.

이 완료 판정은 software와 fake/loopback regression 범위다. 실제 Worker/model/RPC/hardware
acceptance, 현재 manifest가 없는 70B 실행, formal gate 개방과 H01 relock은 포함하지 않는다.
아래 `f22f246` 및 `5fd444a` 절은 구현 전 상태를 보존한 역사적 기준선이다.

## S02 시점 기준선 보정 — f22f246 (역사적 기록)

| 항목 | `f22f246` source / 당시 판정 |
|---|---|
| typed sweep | `domain/sweep.py`, `application/sweep_planner.py`; immutable spec/plan과 예산 검증 존재. execution blocker 유지 |
| threads/batch | `dashboard/schemas.py:166`, `domain/experiment.py:84`, `benchmark/runner.py:65`, `worker/schemas.py:21`, `worker/routes.py:78`, `worker/inference.py:545`; optional strict 입력→factory→effective 기록 존재 |
| cache | `worker/inference.py:555`; binary SHA/template/context/GPU/threads/batch 기반, path만 비교하지 않음 |
| input 준비 | `worker/prompt_preparation.py`, `worker/inference.py:656`, `worker/routes.py:98`; explicit POST 및 제한된 pinned formatter 지원. offline exactness는 unknown |
| 조건 불일치 | `domain/runtime_profile.py`, `benchmark/core.py:295`; sweep 조정/identity/input budget 실패는 warmup 전 거부 |
| 종료 이유 | Worker stream→routes→transport→persistence/instrumentation으로 finish_reason 보존. actual GPU placement/output exactness는 보장하지 않음 |
| model/RPC/Job | S03 catalog resolver 미완료; RPC GPU 999 고정(`benchmark/rpc.py:259`); JobService 단일 active(`application/jobs.py:392`) 유지 |

당시 근거 검사는 S00-report 최종 절의 332+4개다. 이전 표의 threads/batch 부재,
cache path-only, finish reason 미보존 진술은 원 소스에만 해당한다. 기존 model
library·SuiteRunner·node_sweep·formal gate와 R 접점은 유지되며 새 sweep 실행
연결 및 공통 자원 예약은 후속 구현 계획이다.

> 아래 본문은 `5fd444a` 당시의 역사적 기준선이다. 최신 보정은 위 절과
> [S00-report.md](S00-report.md)의 최종 재확인 절을 따른다.

Source: `5fd444a6d83e4226fcc5582196f3a27e71f84160`. 모든 경로는 repository-relative.
ALREADY_SATISFIED는 해당 기존 primitive의 보존·재사용을 뜻한다.

## Parameter → API → domain → backend → metadata

공통 Dashboard 경로는 `dashboard/schemas.py:ExperimentPayload` →
`dashboard/routes.py:/api/experiments` → `dashboard/services.py:ExperimentManager.start`
→ `application/jobs.py` → `application/job_process.py` → `application/suite_runner.py`
→ `benchmark/runner.py`/`benchmark/core.py`이다(모두 `cluster/` 아래).

| 축 | 입력/기본/검증 | 실제 전달·관측 경로 | 판정/부족분 |
|---|---|---|---|
| concurrency | ExperimentPayload/ExperimentConfig: 4, 1–256 | executor thread pool; strategies의 broadcast logical_group | scalar ALREADY_SATISFIED. job parallel과 별개; sweep list/compiler 필요 |
| n_ctx | 4096, 128–16384 | runner load payload → Worker SelectModelRequest → load_model → factory n_ctx; RPC start argv ctx → runtime --ctx-size | 값은 전달됨. Worker fallback 축소 및 metadata 기록. exact input 예산 필요 |
| max_tokens | 128, 1–1024 | transport → ClusterChatRequest/stream_chat → create_chat_completion/create_completion; RPC HTTP payload | warmup은 min(value,16). 생성 상한과 실제 길이 구분; finish reason 보존 추가 |
| n_gpu_layers | 30, 0–120 | runner → Worker load → factory; Pi 일반 경로는 0 요구 | Worker 감소 retry 있음. RPC에서는 이 scalar를 쓰지 않음 |
| n_threads | domain/API 필드 없음 | backend DEFAULT_N_THREADS = LLM_N_THREADS 또는 min(6,cpu_count) | NEW_REQUIRED. caller 값은 legacy config에서 ignored; backend kwargs/default만 있음 |
| n_batch | domain/API 필드 없음 | LLM_N_BATCH 기본 256; context에 맞춘 후보 128/96/64/48/32 retry; current_model_info.n_batch | NEW_REQUIRED. 요청 필드/캐시 키/effective evidence 확장 |
| temperature/top_p/seed | 0.0/0.9/42; 각각 0–2/0–1/-1–2147483647 | transport, Worker stream_chat/set_seed, RPC HTTP payload | scalar 재사용. 모델 간 동일 출력/분포 보장하지 않음 |
| model_ref | 현재 model_id 및 Dashboard model_ids(max 32) | SuiteRunner가 모델 순서대로 별도 ExperimentConfig 생성; model_service/catalog/inventory preflight | pinned single GGUF primitive 재사용, immutable sweep ModelReference 신규 |
| prompt_ref | 현재 prompt bytes, persist_prompt, response_storage_mode | config_fingerprint의 prompt SHA; Worker count_input_tokens/trace | saved model-specific variant와 exact template budget 신규 |
| RPC profile | node_names, rpc_coordinator_node, rpc_split_mode layer/row, policy auto/equal/custom, positional tensor list | rpc_selection → rpc.py resolved_device_order → runtime.sh --rpc/--split-mode/--tensor-split | 현재 scalar topology 지원. node-keyed atomic profiles/새 cell session 계획 신규 |
| rpc_gpu_layers | 필드 없음 | rpc.py:259에서 문자열 999 고정 → runtime.sh:459 --gpu-layers | NEW_REQUIRED all/integer. core의 effective all은 정책 표기이며 실측 layer placement 아님 |

Worker select 기본값은 n_ctx=4096/GPU=8, backend 모듈 기본 context는 LLM_N_CTX/1024로
Dashboard 기본과 다르다. 생략 호환성을 유지하되 각 boundary 기본값을 혼동하지 않는다.

## Context / cache / tokens / 종료 원인

`worker/inference.py:536` cache hit는 모델 path + requested n_ctx + requested GPU만 검사한다.
binary SHA, threads, batch, template identity를 cache key로 쓰지 않는다. ctx 변경 시 재로드는
이미 수행하지만 같은 path의 내용 교체까지 검증하는 sweep identity cache는 아니다.

같은 파일 `load_model`은 ctx → GPU layers → batch 후보로 factory 실패를 재시도한다.
metadata는 선택된 factory 인자와 requested 값/adjustment를 알려주며 runtime 독립 조회의
증거와 동일하지 않다. `benchmark/runner.py:_validate_uniform`은 요청 불일치를 warning으로
돌려주고 `core.py:293`은 기본 require_uniform_config=true일 때 이를 실패로 처리한다.
따라서 “모든 축소가 현재도 성공 처리된다”는 진술은 틀리다. false일 때 조정 실행 가능하므로
sweep 전용 condition key/사유와 정상 표본 제외 규칙을 추가한다.

`count_input_tokens`는 fallback role-lines prompt를 tokenize(add_bos=False)하고 exact=False를
명시한다. 실제 chat-template/special token budget으로 사용할 수 없다. Worker stream은
chunk text만 꺼내며 finish_reason을 저장하지 않는다. `benchmark/transport.py:209`의 RPC는
finish_reason 존재를 success로 인식하지만 reason 값 자체를 결과에 보존하지 않는다.
Worker output은 재토큰화 또는 stream chunk fallback이므로 source/exactness를 함께 유지한다.

## Sweep / model / RPC primitive

| 기존 기능 | 근거 | 재사용 또는 gap |
|---|---|---|
| node_sweep cumulative/individual | `benchmark/strategies.py:NodeSweepStrategy`; `test_benchmark_core.py` | ALREADY_SATISFIED; prefix와 선택 순서, 내부 scenario 수 유지 |
| broadcast | 같은 파일 BroadcastCompareStrategy, work_units=requests×nodes | logical request와 physical replica 구분; cell 안 모델 하나 |
| model suite | `application/suite_runner.py:SuiteRunner.run`; `test_durable_jobs.py:SuiteRunnerTests` | ALREADY_SATISFIED; 순차, 매 모델 cleanup, cleanup 실패 후 중단, cooldown/cancel |
| immutable single-file catalog | `domain/model.py`, `application/model_service.py`, `benchmark/models.py`; `test_model_library_followup.py` | single GGUF SHA/revision/license/preflight 재사용. 설치 자동화는 sweep에 넣지 않음 |
| RPC ordering/cleanup | `benchmark/rpc.py:WorkerRpcBackend.start`, `RpcSession.close`, `rpc_selection.py`; `test_rpc_coordinator.py` | ALREADY_SATISFIED for existing topology primitive; attempted start timeout cleanup 포함 |
| pinned runtime | `rpc/runtime.sh:12`, pin f49e9178767d557a522618b16ce8694f9ddac628; --parallel 1 | row/hybrid/MoE 실행 검증 근거 아님. native 실행 안 함 |
| RPC ownership | runtime lifecycle flock, exact identity/port process guard | host process lifecycle 보호는 있음. Controller 전역 job/attempt lease/quarantine는 없음 |

## R package 접점

`CODEX_FOLLOWUP_RELIABILITY_DASHBOARD_RPC.md` 원본은 로컬 prompt 트리에서 발견하지 못했다.
통합 문서의 Integration Notes로 접점 범위를 정하고 현재 코드로 확인했다. 과거 보고서는
`docs/refactor/source-reconciliation.md`, `followup-model-library-popular-rpc-large.md`,
`docs/workstreams/ws-04-energy-power.md`, `ws-05-formal-lock-admission.md`를 읽었다.
R package 전체 적용 여부를 파일명/과거 보고서만으로 선언하지 않는다.

| 항목 | 현재 판정 | source / 실행된 test 근거 | 후속 취급 |
|---|---|---|---|
| R01 source gate | ALREADY_SATISFIED primitive | `research/locks.py:assess_formal_eligibility` 및 source 검사 helper, `research/eligibility.py`; `test_deployment_identity.py` missing/tampered/cross-worker gate | S10 회귀, lock 재승인 금지 |
| R02 energy coverage | SOFTWARE_COMPLETE | `benchmark/instrumentation.py:_energy_coverage/_scenario_energy_coverage`; 내부 long-gap·missing-power·slow-cache tests, result/publication reader tests | `bounded-power-gap-v1`로 scenario 간 및 내부 gap을 fail closed 처리. 실제 sensor/hardware acceptance는 별도 |
| R03 Pi 정책 | SOFTWARE_COMPLETE | `benchmark/power.py`, `research/eligibility.py`; power-policy/Campaign tests와 Dashboard fixture/E2E | ordinary active warning 비차단, formal fresh-preflight active gate 보존; UI에 동일 경계 명시. 실제 Pi acceptance는 별도 |
| R04 Campaign control | SOFTWARE_COMPLETE | `research/campaign.py:CampaignRunner`, `integrations/campaign_jobs.py`, `dashboard/service_layers/research_service.py`, Campaign POST routes/UI; campaign/service/route/static tests | 기존 JobService와 formal manifest를 재사용하고 gate-first start/resume/retry, pause/cancel, 수동 사유 retry, running 재시작 복구를 제공. shipped gate는 닫힌 상태이며 실제 hardware 실행은 미검증 |
| R05 multipart | SOFTWARE_COMPLETE / catalog identity pending | `domain/model.py` ordered manifest, Worker atomic set install/inventory/load, model preflight/sweep resolver, `test_multipart_models.py` | exact manifest가 있는 direct artifact set 지원. 현재 70B는 manifest가 없어 해당 모델만 차단; 단일 GGUF 계속 가능. 실제 download/load/RPC 미검증 |
| R06 compatibility | SOFTWARE_COMPLETE / hardware evidence pending | `domain/model.py:assess_model_compatibility`, `dashboard/services.py:validate_catalog_execution_preflight`, `benchmark/core.py:model_compatibility_evidence`, Model Library/Sweep/Results UI, `test_model_compatibility.py` | 설치/artifact/architecture/backend/memory/runtime 실행/formal approval을 분리. catalog 또는 설치만으로 runtime verified 승격 금지; 실제 hardware smoke와 formal approval은 별도 |
| R07 regression/docs | SOFTWARE_COMPLETE | current full Python/JS/browser/package/repository gates, hosted Required CI, `R07-report.md` | S10 중복 기능 없이 R01–R06와 Sweep 통합 범위를 검증. H01 current-source pilot/relock과 hardware acceptance는 별도이며 미실행 |

최초 S00 source에는 SweepSpec/ResolvedPlan/ResourceReservation/rpc_gpu_layers/sweep_attempt_id가
없었다. 현재 source에는 해당 계약과 실행 경로가 구현되어 있으며 R07 종료 기준선과 각
S01–S10 report가 현재 상태를 정의한다.
