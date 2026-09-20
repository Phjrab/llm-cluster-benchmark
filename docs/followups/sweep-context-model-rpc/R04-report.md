# R04 — Formal Campaign API/control report

Date: 2026-09-20

Branch: `codex/current-source-pilot-v6`

Baseline: `e70e953384fff6cf51694a67026f1e6bba42dfca`

## Scope and baseline

R04만 수행했다. 시작 시 R03 commit과 upstream은 일치했고 working tree는 clean이었다.
repository와 적용 가능한 `AGENTS.md`를 확인했으며 이 repository에 적용되는 파일은 없었다.
Integration Notes의 경계에 따라 formal Campaign을 exploratory sweep schema로 바꾸지 않고,
기존 `CampaignRunner`, formal manifest, `CampaignJobDocumentFactory`, `JobService`를 재사용했다.

기준선에는 durable campaign claim, restart-safe attempt inspection, fresh formal drift gate,
pause/resume/cancel/manual retry, cooldown, exact job document adapter가 이미 있었다. Dashboard에는
GET list/detail만 있고 production runner factory, mutation API, control UI, application-start recovery가
없었다.

연구용 lock, formal matrix, 실제 inventory, campaign/result artifact는 수정하지 않았다. 실제
Worker 접속, 모델 다운로드, inference, native RPC, formal job dispatch는 실행하지 않았다. shipped
matrix의 `formal_execution_allowed=false`와
`CURRENT_SOURCE_PILOT_REVALIDATION`/`RUNTIME_SOURCE_RELOCK` blocker를 그대로 유지했다.

## Implemented control path

- token-protected Campaign start, pause, resume, cancel, per-cell retry POST API를 추가했다.
- 모든 mutation body는 `confirmed: true`를 요구하고 retry는 1–512자 사유를 요구한다.
- start/resume/retry는 research document의 formal execution gate를 가장 먼저 검사한다. 현재
  gate에서는 구조화된 `FORMAL_EXECUTION_GATE_CLOSED` HTTP 409를 반환하며 runner/JobService를
  호출하지 않는다.
- gate가 열린 미래 실행에서는 manifest의 exact model mapping과 현재 research lock으로
  `CampaignJobDocumentFactory`를 만들고 기존 `experiments._jobs`의 `JobService`에 연결한다.
- 새 cell마다 현재 status snapshot과 research documents를 다시 읽어 source/model/runtime/prompt,
  backend, NTP, storage, Jetson power와 active Pi power gate를 평가한다. frozen cooldown은 기존
  runner가 지속적으로 적용한다.
- Dashboard 재시작은 gate가 열려 있을 때 `running` manifest만 복구한다. `ready` campaign은
  운영자 Start 없이 자동 실행하지 않는다. 종료 시 background driver를 멈춘다.
- 같은 Dashboard의 중복 Start는 하나의 driver만 유지하고, 다중 process 중복 claim은 기존
  atomic manifest lock이 차단한다.
- Campaign 화면에 상태별 Start/Pause/Resume/Cancel과 failed/cancelled cell Retry를 연결했다.
  execution-producing control은 gate가 닫혀 있으면 비활성화되고 이유를 표시한다.

## Compatibility and safety

- exploratory Sweep API/schema, ordinary Experiment API, formal manifest schema와 result identity는
  변경하지 않았다.
- 별도 scheduler, job ID 체계, model downloader 또는 RPC launcher를 만들지 않았다.
- pause/cancel은 gate가 닫혀도 안전한 정지 경로로 남긴다.
- gate를 여는 기능, lock 재승인, campaign 생성 기능은 R04에 추가하지 않았다.
- software control 연결만 완료했다. hardware/formal acceptance는 `NOT RUN`이다.

## Verification

| Check | Result |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_phase12_facade_split cluster.tests.test_dashboard_research cluster.tests.test_research_campaign -v` | 42 tests, PASS; gate-first, single driver, gated running recovery, control route 위임, 409/job 무생성, durable runner/adapter 포함 |
| `.venv/bin/python -m unittest discover -s cluster/tests -v` | 686 tests, PASS, 152.174s. 최초 sandbox 실행은 localhost bind 금지로 683 pass/3 error였고 동일 명령을 localhost 허용으로 재실행해 전부 통과 |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `npm test` | PASS; syntax, export fixture, publication PNG, Playwright 8 tests, E2E 53.8s. 검사 중 병행 중인 별도 Sweep UI working-tree 변경도 보존한 상태로 실행 |
| wheel/package | 전체 Python suite의 packaging/wheel install tests PASS. 별도 `python -m build` 반복은 현재 venv에 build CLI가 없어 NOT RUN |
| `git diff --check` | PASS |
| staged snapshot 재구성 검사 | R04 index만 `/tmp`에 재구성해 42 Python tests, Dashboard export fixture, `research.js` syntax PASS |
| staged scope/credential-pattern scan | PASS; R04 14개 파일만 포함, credential pattern match 없음 |

Python/Browser 검사는 localhost fake server와 fake runtime만 사용했다. 실제 inventory/Worker,
모델 파일, inference endpoint, native RPC endpoint 또는 formal runtime artifact에는 접근하지 않았다.
검사 도중 나타난 별도 Sweep UI working-tree 변경은 R04 staged scope와 commit에서 제외한다.

## Git checkpoint

관련 검사를 마친 뒤 R04 파일만 명시적으로 stage하고 feature branch에 별도 commit으로
non-force push한다. 실제 commit, push와 CI 결과를 최종 응답에 보고한다. R05는 이 commit에
포함하지 않는다.
