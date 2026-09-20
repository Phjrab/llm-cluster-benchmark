# R03 — Ordinary/formal Raspberry Pi policy report

Date: 2026-09-20

Branch: `codex/current-source-pilot-v6`

Baseline: `57a2f4257234af3c29ccebdbfe026d9dfdeeb37a`

## Scope and baseline

R03만 수행했다. 시작 시 R02 commit과 upstream은 일치했고 working tree는 clean이었다.
repository와 적용 가능한 `AGENTS.md`를 다시 확인했으며 이 repository에 적용되는 파일은
없었다. Integration Notes의 R03 경계는 같은 Pi 정책과 설명을 사용하되 ordinary Pi active
warning은 비차단으로 유지하는 것이다.

기존 서버 정책은 이미 그 경계를 구현하고 있었다.

- `domain/power.py`의 Pi snapshot과 warning record는 항상 `blocking=false`다.
- ordinary experiment preflight는 history, active, unavailable 상태를 warning으로 반환하고
  ready Worker의 실행을 막지 않는다.
- active 상태가 측정 중 나타나면 run status를 바꾸지 않고 measurement quality를
  `degraded`로 보존한다.
- formal Campaign은 fresh preflight의 active bit만 `PI_POWER_ACTIVE` blocking issue로 만들고,
  history-only 상태는 `PI_POWER_HISTORY` warning으로 허용한다.

Dashboard Worker 상세와 ordinary 시작 banner는 기존에 모든 Pi 상태를 “실험 실행 비차단”으로
설명했다. ordinary 화면에서는 동작상 맞지만 formal Campaign의 active preflight 경계를 함께
설명하지 않아 같은 상태를 서로 다른 정책처럼 해석할 여지가 있었다.

연구용 lock, formal/pilot plan, 실제 inventory, runtime 결과와 credential은 수정하지 않았다.
실제 Worker 접속, Pi 상태 변경, inference, model download와 RPC는 실행하지 않았다.

## Implemented alignment

서버 admission 또는 run-state 코드는 바꾸지 않았다. Dashboard의 Pi power detail과 ordinary
experiment banner에 다음 경계를 명시했다.

- history-only: 일반 실험과 정식 Campaign 모두 이력만으로 차단하지 않는다.
- active/degraded: 일반 실험은 비차단 warning으로 계속하고 결과를 degraded로 기록한다.
- formal active: 정식 Campaign의 실행 전 active fault는 `PI_POWER_ACTIVE`로 차단한다.
- unavailable: 일반 실험은 비차단이며 정식 분석에서는 unknown 품질로 구분한다.

`power.js` cache version을 갱신했고 static fixture와 browser E2E가 history banner 및 active
detail의 정책 문구를 확인한다. 기존 서버 tests는 ordinary nonblocking, formal active blocking,
history warning 허용, run 결과 보존을 다시 검증한다.

## Compatibility and boundaries

- ordinary experiment의 start/admission semantics와 warning payload는 그대로다.
- formal Campaign의 source/model/runtime/power gate와 lock은 완화하거나 재승인하지 않았다.
- active ordinary 결과는 성공으로 미화하지 않고 measurement quality `degraded`로 남는다.
- 실제 Raspberry Pi firmware bit, 전원 안정성 또는 hardware acceptance를 주장하지 않는다.

## Verification

| Check | Result |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_power_policy cluster.tests.test_research_campaign -v` | 37 tests, PASS, 0.162s |
| `npm run test:syntax` and `node cluster/tests/test_dashboard_exports.js` | PASS |
| parent R02 Required CI | PASS: 683-test Linux quality, Browser E2E, macOS controller gates on `57a2f42` immediately before R03 |
| full Python suite / compile / packaging repeat | NOT RUN: R03 changes no Python or package content; targeted 37 tests cover the reused server policy |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| shell syntax | NOT RUN: no shell file changed; parent Required CI shell validation PASS |
| `npm test` | PASS; syntax, exports, publication PNG and 4 Playwright tests, 27.3s E2E |
| `git diff --check` | PASS |
| staged scope and credential-pattern scan | PASS; 9 intended files only, no match |

## Git checkpoint

관련 검사를 마친 뒤 R03 파일만 명시적으로 stage하고 feature branch에 별도 commit으로
non-force push한다. 실제 commit, push와 CI 결과를 최종 응답에 보고한다. R04는 이 commit에
포함하지 않는다.
