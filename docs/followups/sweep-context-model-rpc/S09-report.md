# S09 — 조건별 비교·응답 검토·재현 가능한 Export

작성일: 2026-09-19

단계: S09 only

기준 branch: `codex/current-source-pilot-v6`

기준 commit/upstream: `f24493eaa8f3d235af8d6d921b5f58580ae46f35`

## 범위와 안전 경계

통합 지시서의 Master Spec, Data Contracts, Integration Notes와 S09 본문을 기준으로 구현했다. Product 결과 파일, 연구용 lock, model catalog/inventory, 실제 run은 수정하지 않았다. 실제 Worker 접속, 모델 다운로드, inference, benchmark RPC는 실행하지 않았다. 모든 새 결과 검사는 임시 디렉터리의 합성 manifest/run/response/measurement fixture를 사용했다. 일반 exploratory sweep을 formal 승인 데이터로 표시하거나 formal publication 입력에 자동 포함하는 경로도 추가하지 않았다.

Git 시작 상태는 branch와 upstream이 모두 `f24493e...`였고 working tree는 깨끗했다. 적용 가능한 repository `AGENTS.md`는 없었다. 저장소 밖에서 발견한 `../robot-scope-route-planner/AGENTS.md`는 이 저장소에 적용하지 않았다.

## 조사한 기준선

- S06 manifest는 `sweep → trial(repeat) → attempts → run_id`를 이미 보존하고, 성공한 최신 attempt를 `official_attempt_id`로 지정한다. 실패·취소 attempt와 retry 사유는 manifest에서 제거하지 않는다.
- S07의 `/api/sweeps/{id}/results`는 trial/attempt 상태와 failure만 반환했고 실제 `summary.json`, `responses.jsonl`, `measurements.jsonl`을 연결하지 않았다.
- `FilesystemRunRepository`는 기존 `requests.csv` 19-column 계약과 별개로 summary, privacy-aware response journal, measurement journal을 읽는다.
- 기존 `cluster.research.publication.describe`가 fixed seed percentile-bootstrap, mean, median, sample SD, IQR 계산을 제공한다. 기존 구현은 단일 표본 CI를 같은 값의 구간으로 반환하므로 S09 표시 계층에서는 이를 CI unavailable로 명시해야 했다.
- 일반 Results 화면은 sweep trace가 없는 historical run도 계속 읽는다. S09는 기존 run 목록이나 result schema를 재작성하지 않고 sweep manifest가 명시적으로 가리키는 run만 join한다.

## 구현

### 결과 조립과 통계

`SweepResultService`를 추가해 manifest의 모든 trial/attempt를 보존하면서 실제 run artifact를 읽는다. 기본 대표 선택은 manifest의 official attempt, 없으면 latest completed, 그것도 없으면 latest attempt 순서다. `all attempts` 자료는 항상 API에 남는다.

각 attempt는 다음 증거를 제공한다.

- base cell 조건, independent repeat index, attempt/run ID
- model artifact SHA, template/tokenizer identity, RPC coordinator/split profile
- requested/effective `n_ctx`, `max_tokens`, GPU layers, thread, batch와 condition mismatch
- 실제 input/output token, exact/source 여부, finish reason와 early EOS
- warmup 수, reload-per-cell cache policy, response privacy 상태
- 기존 run-level throughput/latency/success 지표
- 기존 energy availability/quality/reason과 원본 measurement journal
- 실패/취소/미실행/blocked 상태 및 retry history
- disjoint parallel의 `parallel exploratory` 라벨, 관측된 overlapping run IDs, shared controller/network/storage 비격리 설명

통계 표본은 대표 completed run만 사용한다. request record는 중첩 설명 자료일 뿐 반복 표본으로 세지 않는다. 기존 `describe` 계산과 seed `20260919`, bootstrap 10,000회를 사용하며 표본이 하나면 CI를 `[null, null]`과 `unavailable_single_repeat`로 표시한다. 평균 p95나 pooled p95를 새로 계산하지 않는다. energy가 없거나 일부 node만 가능한 경우 `N/A`와 quality/reason을 유지하며 tokens/J를 재계산하지 않는다.

### 비교 UI

Active Sweeps 결과 패널에 context capacity, 실제 prompt tokens, concurrency, model, GPU offload, RPC profile 축과 run-level metric, series 선택을 추가했다. 선택 축 값으로 표를 필터링할 수 있고, 나머지 고정 조건이 섞이면 그래프 위에 경고한다. tokenizer/template identity가 다른 모델 비교와 RPC/replicated/broadcast 처리량 의미 차이도 별도 경고한다.

표는 대표 attempt가 기본이며 사용자가 모든 attempt를 펼칠 수 있다. requested/effective mismatch, model/template, tokens/finish reason, warmup/cache, energy N/A, raw response privacy 상태, node×scenario measurement, parallel overlap과 failure를 함께 보여준다. 그래프는 실제 run-level 값이 없으면 N/A로 남고 값을 만들지 않는다.

### Export와 재실행 초안

`GET /api/sweeps/{id}/export-results?format=json|csv`를 추가했다.

- JSON은 plan, result index, 선택 규칙, 통계 단위, 실제 run IDs와 전체 결과를 담으며 `formal_approved=false`다.
- CSV는 고정 summary/index 열만 생성한다. 기존 run의 `requests.csv` 파일과 19-column header는 변경하지 않았다.
- CSV 문자열이 `=`, `+`, `-`, `@`, tab, carriage return으로 시작하면 apostrophe를 붙여 spreadsheet formula 실행을 막는다.
- response/prompt 원문은 기존 response storage와 prompt persistence 정책이 허용해 실제 journal에 남은 경우에만 포함된다. hash-only/none은 원문을 만들거나 복원하지 않는다.
- baseline 정책은 동일 model artifact와 선택 축 외 조건이 일치하는 실제 run만 허용한다고 명시하며 synthetic 1-node RPC baseline은 제공하지 않는다.

`POST /api/sweeps/{id}/trials/{trial_id}/clone-draft`는 선택한 조건 하나를 새 revision-1 draft로 저장한다. source plan/run/attempt는 수정하지 않고 새 sweep을 시작하지 않는다. privacy 정책에 따라 private prompt가 이미 scrub된 경우에는 실행 불가능한 초안을 만들지 않고 명시적으로 거부한다.

## 검증

### S09 집중 검사

- `.venv/bin/python -m unittest cluster.tests.test_sweep_results cluster.tests.test_sweep_api`
  - 22 tests, PASS
  - multi-repeat run 통계, retry 대표 선택과 실패 이력, early EOS, adjusted config mismatch, tokenizer 차이, node-sweep nested scenarios, RPC coordinator/split, missing energy, single-repeat CI unavailable, JSON/CSV privacy, formula injection, parallel overlap, historical non-sweep run, export/clone route를 검증했다.
- `npm run test:syntax`
  - PASS
- `npm run test:fixtures`
  - PASS (`dashboard export fixtures: OK`)
- `npm run test:e2e`
  - 4 tests, PASS (26.8s)
  - 합성 API fixture로 exploratory 라벨, mismatch, raw response, parallel overlap, chart와 clone-to-draft 동작을 확인했다.
- `npm run test:publication-png`
  - PASS (`phase11 publication PNG fixtures: OK`)

### 전체 Python 회귀

- launcher 제외 전체: 667 tests, PASS (31.655s)
- `cluster.tests.test_launcher`: 6 tests, PASS (1.713s)
- 최초 sandbox 전체 실행은 673개 중 671개가 통과하고 localhost bind 권한이 필요한 launcher 2개만 `PermissionError`였다. 전체 suite 권한 확대는 자동 승인 검토가 실제 장비 관련 테스트 이름을 이유로 거부해 반복하지 않았으며, 외부 접속이 없는 launcher 모듈만 좁은 권한으로 재실행했다.

테스트 로그에 나타난 SSH/RPC/setup 문자열은 mock 기반 기존 unit test의 합성 출력이다. 실제 inventory나 Worker endpoint는 사용하지 않았다.

## 변경 파일과 호환성

- 새 결과 조립기: `cluster/dashboard/service_layers/sweep_result_service.py`
- API/facade/schema 연결: `cluster/dashboard/routes.py`, `schemas.py`, `service_layers/sweep_service.py`, `services.py`
- Dashboard: `templates/index.html`, `static/js/sweep-results.js`, `static/styles.css`
- 회귀: `test_sweep_results.py`, `test_sweep_api.py`, `test_dashboard_exports.js`, `e2e/dashboard.spec.js`

기존 result schema v2, `requests.csv`, `responses.jsonl`, `measurements.jsonl`, sweep manifest, formal lock, inventory의 저장 형식은 변경하지 않았다. S10은 시작하지 않았다.
