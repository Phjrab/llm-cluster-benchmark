# WS-02 Sensitive Data Storage Controls

Status: complete

## Scope

WS-02만 수행한다. 기존 `persist_prompt`와 호환되는 응답 저장 정책, 결과 API의
강제 경계, Dashboard 저장 상태 표시, 종료된 durable job의 prompt 정리를 증분
구현한다. 반복 실험·통계 집계(WS-03), 하드웨어 추론, native RPC 실행, 기존 결과
마이그레이션은 범위 밖이다.

Base commit은 `6b58fac`이며 작업 브랜치는
`workstream/ws-02-sensitive-data-storage`이다. 조사 시 동일 기능의 열린 PR은 없었다.
작업 전 존재하던 이름 뒤에 ` 2`가 붙은 세 개의 미추적 중복 파일은 사용자 데이터로
간주해 읽거나 수정하거나 커밋하지 않았다.

## Baseline and gap analysis

| 요구사항 | 기존 상태 | 부족한 점 | 변경 |
|---|---|---|---|
| prompt 저장 제어 | `persist_prompt`와 private result 권한 존재 | run-start event, 저장된 실험 정의와 종료 job 범위가 불명확 | prompt hash/count, event 제거, terminal scrub |
| 세 응답 모드 | response journal이 항상 원문 저장 | 사용자가 저장 범위를 선택할 수 없음 | `full`, `hash_only`, `none` 추가 |
| 실패 metadata | 구조화된 failure 존재 | error/event에 prompt·response가 포함될 수 있음 | 재귀적 exact-value redaction |
| Dashboard 상태 | raw response 또는 빈 기록 표시 | 비저장과 빈 응답·legacy를 구분하지 못함 | 네 상태 badge와 설명 추가 |
| legacy 읽기 | journal 누락을 빈 배열로 허용 | 명시적 legacy 상태 없음 | API 정규화와 `legacy_missing` |
| CSV 호환 | 19개 metric column | 없음 | header 유지; none 값만 비움 |

기존 run repository, soft-delete/retention, suite recovery, 실행 전략과 Worker API는
재작성하지 않았다.

## Design note

### Public configuration and schema

`ExperimentConfig`와 Dashboard payload에
`response_storage_mode = full | hash_only | none`을 additive하게 추가했다. 기본값은
기존 동작과 호환되는 `full`이다. 알 수 없는 값은 domain validation에서 거부된다.
정규화 config fingerprint에는 저장 모드가 포함되므로 같은 추론 조건이어도 보존
정책이 다른 실행을 구분할 수 있다.

summary와 `benchmark_parameters`에 `response_storage_mode`를 추가한다. 새 response
record는 schema version 2와 `response_storage_status`를 가진다. `requests.csv` header는
변경하지 않는다.

### Storage behavior

- `full`: response 원문, 정확한 문자 수, SHA-256, metric과 failure metadata를 저장한다.
- `hash_only`: 원문을 제외하고 정확한 문자 수, SHA-256, metric과 failure metadata를
  저장한다.
- `none`: 요청 identity, metric, failure metadata만 저장하며 response 원문·문자 수·
  SHA-256을 저장하지 않는다. legacy CSV의 해당 두 cell도 비운다.

`persist_prompt=false`이면 run config와 response record에 prompt SHA-256 및 문자 수를
남기고, formal prompt-set version이 있으면 함께 기록한다. Dashboard가 저장한 실험
정의도 같은 정책을 따른다.

Durable job은 재시작 후 동일 suite를 복원하기 위해 queued/running 동안 private runtime
파일에 prompt 입력이 필요하다. terminal 상태가 원자적으로 기록될 때 prompt를 hash와
문자 수로 치환한다. 완료 summary를 통한 복구 및 orphan recovery에도 같은 정리를
적용한다.

### Event, error, and API boundary

`request_completed` event는 측정·상태 필드는 유지하지만 raw response/output/text/prompt를
제거한다. run-start event도 저장 설정과 무관하게 prompt 원문을 포함하지 않는다.
event, summary, CSV error와 외부로 다시 전달되는 예외는 구성 prompt와 해당 응답의
정확한 문자열을 재귀적으로 `[REDACTED]` 처리한다.

`GET /api/runs/{run_id}/responses`는 record를 반환하기 전에 저장 상태를 정규화하고
`hash_only`/`not_persisted`에 잘못 들어간 raw 필드를 제거한다. 따라서 파일이 수동으로
잘못 작성되어도 API로 정책을 우회하지 않는다. raw text가 있는 legacy record는
`stored`, text와 상태가 모두 없는 legacy record는 `legacy_missing`으로 읽는다.

### Dashboard compatibility

실험 화면에 prompt 원문 저장 toggle과 응답 저장 범위 selector를 추가했다. 결과
검토 화면은 `stored`, `hash_only`, `not_persisted`, `legacy_missing`을 별도 badge와
설명으로 표시하며 비저장 응답을 빈 모델 답변으로 표현하지 않는다. 기존 API client가
새 설정을 보내지 않으면 두 기본값이 기존 full 저장 동작을 유지한다.

## Security and measurement impact

새 원격 호출, Worker 배포, 포트 또는 권한 변경은 없다. token, SSH credential과 환경
변수를 새 artifact에 추가하지 않는다. hash와 count 계산, 작은 dict 정규화는 요청
완료 후 persistence 경계에서 실행되므로 inference wall과 TTFT 측정 구간을 변경하지
않는다.

hash-only는 익명화나 암호화가 아니다. 짧고 예측 가능한 출력은 사전 계산으로 추정할
수 있으며, 실행 중 durable job은 복구를 위해 private prompt를 잠시 보유한다. 이
잔여 위험과 운영 절차는 `docs/security/sensitive-data-storage.md`에 기록했다.

## Tests

추가·확장한 계약은 다음을 포함한다.

- 세 response storage mode의 원문/길이/hash/metric 저장 조합
- `persist_prompt=false`의 config, response, experiment definition hash/count
- 종료 job의 private prompt 제거와 durable recovery 보존
- request event, summary, CSV error와 외부 예외의 민감 문자열 제거
- hash-only/none record의 raw response API 우회 차단
- response journal이 없는 run과 상태 필드가 없는 legacy record 읽기
- Dashboard payload와 저장 상태 UI의 Chromium 흐름
- 기존 19-column CSV schema 보존

검증 결과:

- `.venv/bin/python -m unittest discover -s cluster/tests -q`: 498 tests passed
- `.venv/bin/python -m compileall -q cluster scripts`: passed
- `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/ws02-wheel`:
  wheel built successfully (`llm_cluster_benchmark-1.0.0-py3-none-any.whl`)
- `.venv/bin/python -m build --wheel --no-isolation`: not run successfully;
  저장소의 `build` package가 PyPA build module을 가려 `build.__main__`을 찾을 수
  없었다. 패키지 설치나 제품 코드 변경 없이 동일 setuptools/wheel backend를
  `pip wheel`로 검증했다.
- `npm test`: JavaScript syntax, export fixtures, publication PNG fixtures와
  2 Playwright E2E tests passed
- `python -m json.tool cluster/config/experiment_defaults.json`: passed
- `git diff --check`: passed
- shell syntax/shellcheck: not run; WS-02에서 shell script를 변경하지 않았다.
- hardware: not run; 실제 모델 inference, Jetson/Pi benchmark와 native RPC는
  저장 정책 변경에 필요하지 않으며 성공을 주장하지 않는다.

## Remaining risks and non-goals

- 이미 생성된 full/legacy 결과를 자동 수정하지 않는다. 보존 정책 변경은 이후 실행에만
  적용되며 기존 결과 삭제는 별도 retention 승인 흐름을 사용한다.
- 로컬 관리자와 Controller 계정은 실행 중 private job 입력을 읽을 수 있다. 본 기능은
  저장 최소화이며 저장 시 암호화를 추가하지 않는다.
- 문자열 redaction은 저장하려는 정확한 prompt/response 값의 노출을 막지만 metadata에
  사용자가 별도로 입력한 민감 식별자를 자동 탐지하지 않는다.
- 실제 Jetson/Pi inference 및 native RPC 하드웨어 검증은 WS-02에서 실행하지 않는다.
