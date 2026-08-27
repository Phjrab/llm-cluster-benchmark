# WS-04 Energy and Power Instrumentation

Status: complete

## Scope

WS-04만 수행한다. 기존 Worker background telemetry와 run measurement journal을
보존하면서 provider provenance, Jetson 전력 상태와 명시적인 에너지 효율 지표를
additive하게 완성한다. Raspberry Pi 전력 추정, 새 외부 센서, 실제 하드웨어 inference,
후속 Workstream은 범위 밖이다.

Base commit은 `0b3c9e9`이며 작업 브랜치는 `workstream/ws-04-energy-power`이다.
조사 시 열린 PR은 없었다. 기존 미추적 중복 파일 세 개는 수정·커밋하지 않았다.

## Baseline and gap analysis

| 요구사항 | 기존 상태 | 부족한 점 | 변경 |
|---|---|---|---|
| 비동기 Jetson sampling | Worker cache와 Controller background sampler 존재 | provider가 sample에 없음 | provider/degraded/error 추가 |
| timestamp/provider | UTC와 monotonic timestamp 존재 | watt 출처 불명확 | `power_provider=jtop` 명시 |
| trapezoidal energy | scenario별 적분 및 gap 분리 존재 | 요구 지표 이름 일부 없음 | 명시적 alias와 효율식 추가 |
| 센서 부족 처리 | 두 sample 미만이면 null | jtop degraded/Pi 이유 구분 부족 | 구조화된 reason 추가 |
| Pi 무추정 | `power_w=None`, get_throttled 별도 | 없음 | 회귀 테스트 강화 |
| 전력 모드 비교 | participant snapshot과 formal gate 존재 | measurement summary 직접 필드 없음 | per-node mode/clocks 추가 |

기존 power-integrity quality 정책, telemetry thread, formal live gate, CSV schema와
inference timing을 재작성하지 않았다.

## Design note

### Provider provenance

Worker `TelemetryService.snapshot()`은 provider 상태를 cache snapshot에 붙인다.
Controller measurement sample schema v3는 다음을 저장한다.

- `telemetry_provider`: `jtop+psutil`, `psutil` 등 실제 provider
- `power_provider`: 유효 watt가 jtop에서 왔으면 `jtop`; watt가 없으면 null
- `telemetry_degraded`와 안전한 provider error
- `power_mode`와 `jetson_clocks`

probe 자체가 실패해도 inference를 실패시키지 않고 degraded sample로 기록한다.

### Energy calculation

각 scenario 내부에서 연속된 monotonic `(time, watts)` sample을 trapezoidal rule로
적분한다. scenario 사이 cooldown·model 작업의 unsampled interval은 연결하지 않는다.
두 개 이상의 유효 sample이 없는 scenario가 하나라도 있으면 해당 node의 전체
measurement energy는 null이다.

기존 `energy_j`, `generated_tokens_per_j`, `requests_per_j`는 유지한다. 다음 explicit
필드를 per-node와 cluster overall에 추가한다.

- `measurement_energy_j`
- `joules_per_request`
- `joules_per_generated_token`
- `tokens_per_joule`
- `tokens_per_second_per_watt`

마지막 값은 동일 측정 구간의 token throughput을 time-weighted average watts로 나눈
것으로, 단위상 tokens/J와 같다. 0 또는 missing denominator를 0으로 위장하지 않고
null로 둔다.

### Platform semantics

Raspberry Pi의 psutil 및 `vcgencmd get_throttled`는 watt sensor가 아니다. Pi에서는
실제 watt가 없으면 energy와 효율을 모두 null로 두고
`raspberry_pi_power_sensor_unavailable`을 기록한다. get_throttled는 기존
power-integrity/measurement-quality 정보로만 유지한다.

jtop가 설치되지 않았거나 service/client가 맞지 않으면 Jetson inference는 계속할 수
있지만 energy는 null이고 `telemetry_provider_degraded` 이유를 남긴다. Formal
eligibility의 nvpmodel/jetson_clocks lock mismatch 차단은 기존대로 유지한다.

### Schema and compatibility

`measurements.jsonl`은 schema version 3을 기록한다. JSON Schema는 1, 2, 3을 모두
허용한다. `requests.csv`, 단발 summary schema, Worker health 기존 필드는 제거하지
않는다. 신규 Worker snapshot 필드는 additive이며 구형 Controller/reader가 무시할 수
있다.

## Security and measurement impact

새 remote command, 권한, 포트 또는 package 설치는 없다. provider error에 prompt,
response, token과 credential을 넣지 않는다. Worker sensor 접근은 기존 background
cache thread에서 실행되고 Controller는 cache를 별도 sampler thread로 읽는다.
scenario 종료의 boundary sample도 request executor wall 계산 뒤 수행되므로 inference
wall과 처리량 분모에 telemetry 지연이 포함되지 않는다.

## Tests

추가·확장한 계약은 다음을 포함한다.

- jtop provider와 power-mode/jetson-clocks provenance
- synthetic series의 30 J trapezoidal integration
- average/peak watts 및 다섯 explicit 효율 지표
- scenario gap을 energy로 적분하지 않는 동작
- 센서 sample 부족 시 null
- degraded jtop reason
- Raspberry Pi watt·energy 무추정
- background Worker cache와 Controller collection overhead
- formal power-mode gate와 기존 power-integrity 회귀

검증 결과:

- `.venv/bin/python -m unittest discover -s cluster/tests -q`: 508 tests passed
- `.venv/bin/python -m compileall -q cluster scripts`: passed
- `.venv/bin/python scripts/ci/validate_repository.py`: 17 JSON documents,
  72 formal cells, 13 pinned actions, 7 shell scripts validated
- `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/ws04-wheel`:
  wheel built successfully
- `npm test`: JavaScript syntax, export/publication fixtures와 2 Playwright E2E
  tests passed
- `git diff --check`: passed
- shell syntax/shellcheck: changed shell scripts 없음
- hardware: not run; hardware inventory가 없어 해당 gate는 `hardware unavailable`이며
  실제 Jetson watt/inference 또는 Pi 외부 sensor 성공을 주장하지 않는다.

## Remaining risks and non-goals

- 1초 sampling은 매우 짧은 전력 peak를 놓칠 수 있다.
- cluster `peak_power_w`는 node별 peak의 보수적 합이며 동시 peak 주장값이 아니다.
- Pi의 실제 에너지 비교에는 향후 검증된 외부 power sensor provider가 필요하다.
- 실제 Jetson/Pi inference 및 native RPC hardware 검증은 실행하지 않는다.
