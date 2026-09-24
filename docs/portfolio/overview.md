# LLM Cluster Benchmark — 포트폴리오 설명 (2026-09-24)

기준: 로컬 `main` `ba5bbce2c60d2d0af80aaa77c77424be704f8a08`.
이 문서 작업에서 Worker를 조회하거나 모델을 실행하지 않았습니다. 과거 실장비
기록과 현재 소스의 기능, Pilot와 Formal 계획량은 각각 분리해 적습니다.

## 문제와 구조

단일 처리량 수치만 보면 어떤 모델·장치·동시성·측정 조건에서 나왔는지,
실패한 시도와 누락된 전력 자료가 무엇인지 알기 어렵습니다. 이 프로젝트는
macOS Controller가 실행 구성과 durable job/결과를 관리하고, Jetson CUDA 또는
Raspberry Pi OpenBLAS Worker가 GGUF 로컬 추론을 수행하도록 분리합니다.
Controller는 추론 참여자가 아닙니다. 플랫폼별 비교가 기본이며 혼합 RPC는
탐색적 별도 실험입니다.

| 기능 | 코드·문서 근거 | 검증 해석 |
|---|---|---|
| Worker 등록·상태 | [운영 가이드](../operations-and-research-guide.md), [cluster 안내](../../cluster/README.md) | 코드 기능과 현재 장비 online은 별개 |
| 복제/분할 전략 | [`strategies.py`](../../cluster/benchmark/strategies.py) | round robin은 전체 모델 복제, RPC만 분할 |
| 결과·측정 | [`metrics.py`](../../cluster/benchmark/metrics.py), [측정 정의](../research/measurement-instrumentation.md) | 물리/논리 TPS·TTFT/prefill proxy 구분 |
| 보존·분석/export | [Phase 11](../roadmap/phase-11-analysis-publication-export.md), [pipeline](../research/publication-analysis-pipeline.md) | export 구현은 formal 결과 완료가 아님 |
| 과거 실장비 | [2026-08-21 6 Worker acceptance](../refactor/hardware-six-worker-acceptance-20260821.md) | 해당 환경의 기능 검증이지 현재 성능 순위 아님 |

## 연구 진행 상태

- **v5 Pilot:** 29 attempts, 28 successful observations, 보존된 실패 1건.
  15회 반복과 최소 180초 cooldown은 당시 source/runtime의 freeze 결정입니다
  ([Phase 09](../roadmap/phase-09-pilot-experiment.md)).
- **v6 current-source Pilot:** 13 completed에서 중단되어 전체 재검증이 아닙니다
  ([reconciliation](../research/formal-gate-reconciliation.md)).
- **v7 Pi-only Pilot:** 22 attempts 중 19 completed. 하드웨어 범위에 Jetson이
  없고 실패율·요청 성공률·일부 precision 기준 때문에 `freeze_ready=false`
  ([H01](../followups/sweep-context-model-rpc/H01-report.md)).
- **Formal:** 현재 matrix의 `formal_execution_allowed=false`, blocker는
  `CURRENT_SOURCE_PILOT_REVALIDATION`과 `RUNTIME_SOURCE_RELOCK`입니다.
  1,080 runs는 완료 결과가 아니라 선택된 계획량입니다
  ([matrix](../../config/research/formal_experiment_matrix.json)).

과거 짧은 smoke의 높은 speedup을 일반 scaling 결론으로 옮기지 않습니다.
`physical_cluster_tokens_per_s`에는 broadcast replica가 모두 포함됩니다.
사용자 관점에는 `effective_user_tokens_per_s`와 `logical_requests_per_s`를
사용합니다. TTFT는 순수 prefill 시간이 아니며 `prefill_time_s`는 first-token
decode를 포함하는 proxy입니다. Pi `get_throttled`는 W 센서가 아니므로 센서
없는 경우 전력·에너지 효율 값은 결측으로 남깁니다
([지표 해석](../operations-and-research-guide.md)).

## AI 활용과 본인 기여

제품 내부 AI는 Worker의 GGUF 추론입니다. 개발 과정의 AI 도구 사용은 별도
기록으로 입증해야 합니다. 코드와 커밋은 기능의 출처를 보여 주지만, 브랜치명이나
작성자 이름만으로 누가 설계·구현·장비 검증을 수행했는지 확정하지 않습니다.

| 사례 후보 | 확인할 개인 역할과 근거 |
|---|---|
| broadcast 물리/논리 처리량 분리 | 지표 정의를 누가 결정·검토했는지와 [`metrics.py`](../../cluster/benchmark/metrics.py) |
| Worker 단일 inference slot과 Controller 대기 | 설계 판단과 [측정 기록](../research/measurement-instrumentation.md) |
| Pilot 실패 보존과 formal gate | 실패를 남기고 relock을 보류한 판단, [H01](../followups/sweep-context-model-rpc/H01-report.md) |

지원서에는 본인이 정한 조건, AI에 맡긴 실제 작업, 직접 수정·검증한 내용,
장비 구성 담당 범위를 대화/PR/실험 기록에 연결한 후 1인칭으로 서술합니다.
AI 사용 모델·시간 절감률·개인 기여율은 추정하지 않습니다.

## 읽을 자료

[증거·화면 계획](evidence.md)에 공개 가능 자료와 추가 촬영 조건을 정리했습니다.
실제 결과 표에는 run ID/날짜/source/runtime/model lock, 장치 수, 전략,
논리·물리 요청, 실패/제외, 네트워크·전력·측정 구간을 함께 붙여야 합니다.

GitHub About 후보: `Controller/Worker benchmark for reproducible local LLM experiments on Jetson and Raspberry Pi`.
Topics 후보: `llm`, `benchmark`, `jetson`, `raspberry-pi`, `gguf`, `distributed-systems`.
원격 메타데이터는 변경하지 않았습니다.
