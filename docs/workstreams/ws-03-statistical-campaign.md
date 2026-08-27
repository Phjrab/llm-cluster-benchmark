# WS-03 Repeated Experiments and Statistical Aggregation

Status: complete

## Scope

WS-03만 수행한다. 반복 실행의 순서를 결정론적으로 생성하고, 모든 성공·실패·취소·
제외 시도를 보존한 상태에서 조건별 run-level 통계를 생성하는 additive artifact를
추가한다. 기존 formal campaign, 단발 run schema, publication exporter와 durable job
실행 경로는 유지한다. 전력 적분(WS-04)과 후속 Workstream은 범위 밖이다.

Base commit은 `190df33`이며 작업 브랜치는
`workstream/ws-03-statistical-campaign`이다. 조사 시 열린 PR은 없었다. 기존 미추적
중복 파일 세 개는 사용자 파일로 간주해 수정하거나 커밋하지 않았다.

## Baseline and gap analysis

| 요구사항 | 기존 상태 | 부족한 점 | 변경 |
|---|---|---|---|
| repetitions >= 1 | formal manifest의 10~30회 반복과 pilot 결정 규칙 존재 | 일반 조건 scheduler 계약 없음 | 1회 이상 공용 scheduler 추가 |
| unload/cooldown/recheck | suite unload, campaign cooldown, preflight·thermal gate 존재 | 문서상 WS-03 연결이 불명확 | 기존 경로 재사용 및 회귀 보존 |
| 실행 순서 | seeded randomized blocks 존재 | fixed와 Latin-square 호환 순서 없음 | 세 정책을 순수 함수로 제공 |
| 통계 | publication exporter에 mean/median/SD/IQR/bootstrap CI 존재 | 실패 run이 조건별 출력에서 쉽게 보이지 않음 | 모든 attempt와 제외 이유를 포함한 aggregate 추가 |
| 플랫폼 구분 | formal matrix가 cohort를 분리 | 일반 aggregate의 mixed 표시 없음 | mixed를 `exploratory`로 강제 표시 |
| 단발 run 호환 | summary schema v2 | 없음 | 단발 schema 변경 없음 |

기존 formal scheduler의 deterministic hash ranking, CampaignRepository의 private atomic
manifest/event 저장, durable retry history, model unload와 quality gate를 재작성하지
않았다.

## Design note

### Ordering API

`schedule_condition_order(base_cells, repeat_count, policy, seed)`는 외부 상태를 읽지 않는
순수 함수다. `repeat_count`는 1 이상의 정수이며 condition ID는 유일해야 한다.

- `fixed`: 입력 condition 순서를 매 반복마다 그대로 보존한다.
- `randomized`: 기존 SHA-256 seeded randomized-block 구현을 사용한다. 같은 seed와
  condition이면 Python 버전과 무관하게 같은 순서다.
- `latin_square`: 동일 platform cohort와 order block 내부에서 반복마다 한 칸씩 cyclic
  shift한다. 반복 수가 최대 block 크기의 배수가 아니면
  `latin_square_cycle_complete=false`를 기록해 완전 균형처럼 표현하지 않는다.

각 scheduled cell은 `repeat_index`, 전역 `order_index`, `order_policy`, 적용된 seed를
가진다. 기존 formal campaign은 계속 frozen randomized-block protocol을 사용하며 새
manifest에는 `order_policy=randomized`를 additive하게 기록한다.

### Statistical artifact

`aggregate_campaign_attempts`는 조건별로 다음을 생성한다.

- attempt, 성공 반복, 실패 반복, 제외 반복 수
- 제외된 attempt ID, 원래 run ID, 상태, 이유, measurement quality
- metric별 count, mean, median, sample standard deviation, IQR
- 고정 seed의 percentile-bootstrap 95% mean confidence interval
- 값이 없는 metric의 run ID와 `METRIC_UNAVAILABLE:<metric>` 이유
- platform family와 `homogeneous` 또는 `exploratory` 비교 등급

실패·취소·명시적 제외는 통계 sample에 포함하지 않지만 artifact에서 제거되지 않는다.
completed run의 특정 metric이 없을 때도 해당 metric의 exclusion 목록에 남는다.
`aggregate_run_summaries`는 기존 run summary와 `research_identity`를 이 입력으로
정규화한다. 독립 추론 단위는 request가 아니라 run이다.

Artifact는 schema version 1과
`artifact_type=statistical_campaign_aggregate`를 사용하며 JSON Schema를
`config/research/statistical_campaign_aggregate.schema.json`에 추가했다. 단발 run과
`requests.csv`는 변경하지 않았다.

### Existing lifecycle reuse

Formal CampaignRunner는 각 cell 직전에 live preflight와 thermal/power gate를 다시
실행하고, 완료 뒤 durable cooldown deadline을 기록한다. 각 formal cell은 독립적인
한-model suite이므로 기존 suite runner가 성공·실패·취소 후 model unload를 수행한다.
실패 attempt와 manual retry는 새 attempt ID로 모두 manifest에 남는다. WS-03은 이
검증된 lifecycle을 우회하는 새 실행기를 만들지 않는다.

## Security and measurement impact

통계·순서 계산은 파일, 네트워크, Worker 또는 clock을 읽지 않는다. 원문 prompt,
response, token과 node credential을 aggregate에 넣지 않는다. 실행 순서 생성과 통계
집계는 inference 완료 후 수행되므로 model load, warmup, inference wall, unload와
cooldown 시간 경계를 변경하지 않는다.

Jetson과 Raspberry Pi가 한 condition에 함께 나타나면 결과를 자동으로
`exploratory` 및 `formal_comparison_eligible=false`로 표시한다. 서로 다른 플랫폼을
공식 동일 가속기 scaling 비교로 합치지 않는다.

## Tests

추가·확장한 계약은 다음을 포함한다.

- fixed 순서와 반복 index
- 동일 randomized seed의 완전 재현 및 seed 변경 감지
- Latin-square cyclic rotation과 불완전 cycle 표시
- 반복 수, 정책, condition ID validation
- deterministic bootstrap 통계와 median/SD/IQR
- 실패 run ID·이유·measurement quality 보존
- completed run의 metric 누락 이유 보존
- 기존 run summary와 research identity 정규화
- Jetson/Pi mixed condition의 exploratory 강제
- 기존 formal randomized manifest와 campaign lifecycle 회귀

검증 결과:

- `.venv/bin/python -m unittest discover -s cluster/tests -q`: 507 tests passed
- `.venv/bin/python -m compileall -q cluster scripts`: passed
- `.venv/bin/python scripts/ci/validate_repository.py`: 17 JSON documents,
  72 formal cells, 13 pinned actions, 7 shell scripts validated
- `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/ws03-wheel`:
  wheel built successfully
- `npm test`: JavaScript syntax, export/publication fixtures와 2 Playwright E2E
  tests passed
- `git diff --check`: passed
- shell syntax/shellcheck: changed shell scripts 없음
- hardware: not run; inventory가 제공되지 않은 hardware test는
  `hardware unavailable`로 명시됐고 실제 inference/RPC 성공을 주장하지 않는다.

## Remaining risks and non-goals

- Latin-square는 block 내부 순서만 교차 균형화한다. 서로 다른 platform cohort를 한
  square로 섞지 않는다.
- bootstrap CI는 반복 run이 독립 추론 단위라는 전제이며 request-level 관측치를
  독립 표본으로 승격하지 않는다.
- 기존 formal campaign의 10~30회 반복은 pilot-frozen gate를 유지한다. WS-03이 이
  승인 절차를 자동 해제하지 않는다.
- 실제 Jetson/Pi 반복 inference와 native RPC는 이번 변경에서 실행하지 않는다.
