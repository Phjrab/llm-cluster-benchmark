# LLM Cluster Benchmark — 공개 증거·캡처 목록

기준 소스는 `main` `ba5bbce2c60d2d0af80aaa77c77424be704f8a08`입니다.
현재 저장소의 `.run/` 결과와 publication bundle은 비공개 런타임 자료입니다.
이 문서 작업에서 신규 benchmark, 모델 추론, pilot/formal export를 수행하지
않았고 결과 PNG를 Git에 추가하지 않았습니다. 값이 필요한 도표는 원자료의
공개 가능성 검토와 조건표를 먼저 거쳐야 합니다.

| 자료 | 현재 공개 근거 | 그림에서 반드시 밝힐 점 |
|---|---|---|
| Worker 상태 | [6 Worker acceptance](../refactor/hardware-six-worker-acceptance-20260821.md) | 당시 날짜·SHA, Jetson 3/Pi 3 기능 확인; 현재 online 주장 아님 |
| v5 Pilot 결과 | [Phase 09](../roadmap/phase-09-pilot-experiment.md), [freeze decision](../../config/research/pilot_freeze_decision.json) | 29 attempts/28 successful, 당시 source/runtime, pilot 전용 |
| v7 Pi-only 결과 | [H01](../followups/sweep-context-model-rpc/H01-report.md) | 22 attempts/19 completed, 실패·제외, Pi만, `freeze_ready=false` |
| Formal readiness | [reconciliation](../research/formal-gate-reconciliation.md), [matrix](../../config/research/formal_experiment_matrix.json) | 1,080 planned runs, 실행 금지 gate |
| 결과/UI 데모 | 기존 [E2E fixture](../../cluster/tests/e2e/dashboard.spec.js) | `UI 데모 / fixture 데이터 / 성능 증거 아님` |

## 촬영할 화면

1. Worker Dashboard: Jetson/Pi 그룹과 Controller 비추론 역할. 실제 장비 화면이면
   캡처 날짜·source·runtime을, fixture면 fixture 배지를 캡션에도 적습니다.
2. 실험 설정+결과: 동일 run ID의 전략, 모델/quantization, 장치 수, 요청과
   TTFT/TPS를 함께 보여 줍니다. 개인 prompt/response는 제외합니다.
3. Pilot 차트: run-level 단위와 n·분산·실패/제외를 함께 표기합니다. v5와 v7,
   다른 source/runtime을 합쳐 하나의 결과로 그리지 않습니다.
4. 품질/실패 화면: Pi `get_throttled` 경고는 watt가 아니며 센서 없는 W/J/TPS/W를
   0으로 채우지 않습니다. cleanup과 재시도 이력을 유지합니다.
5. readiness: v5 freeze와 현재 formal blocker를 나란히 표시하고, planned
   1,080 runs를 완료 건수로 쓰지 않습니다.

## 차트 공개 전 조건표

각 그림의 캡션 또는 연결된 표에 run ID, 날짜, 소스 SHA, runtime fingerprint,
실험 종류, 모델 계열/quantization/hash, 플랫폼·장치 수, 전략, inference slot,
logical concurrency, prompt set·길이, max tokens, warmup/cooldown, 네트워크,
Jetson 전력 모드, Pi 품질 경고, 전력 센서와 측정 구간, 실패·제외 수,
TPS/TTFT/E2E/prefill/decode/power의 정의와 단위를 적습니다. 미기록 값은
추정하지 않고 `미기록`으로 표시합니다.

기존 [publication pipeline](../research/publication-analysis-pipeline.md)은 pilot
export와 formal export를 구분하고, formal에는 완료된 campaign manifest를
요구합니다. 분석 그림을 만들 때에는 실제 검증된 pilot result root와 새로운
output 경로만 쓰고 `--experiment-type pilot --acknowledge-non-formal`을
지정해야 합니다. 8-token smoke를 pilot으로 바꾸지 않습니다. 원본 `.run/`이나
private publication ZIP을 통째로 Git에 추가하지 않습니다.
