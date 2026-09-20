# R02 — Energy gap and coverage reliability report

Date: 2026-09-20

Branch: `codex/current-source-pilot-v6`

Baseline: `46cd9aa031a837912e01c53d8c7b6d20f7e78bf5`

## Scope and baseline

R02만 수행했다. 시작 시 branch는 upstream과 일치했고 working tree는 clean이었다.
repository와 적용 가능한 `AGENTS.md`를 다시 확인했다. repository 내부 또는 상위
workspace에는 이 repository에 적용되는 `AGENTS.md`가 없었다.

기존 schema v3 계측은 scenario 사이의 cooldown을 분리했지만, 같은 scenario 안에서는
유효 watt sample만 골라 임의 길이의 시간 간격을 연결했다. 따라서 중간 sample의 watt가
없거나 sampler가 오래 멈춘 경우에도 부분 적분을 full-run `energy_j`와 tokens/J로 표시할
수 있었다. S09 결과 화면은 이미 unavailable 값을 숨겼지만 coverage 근거 자체를 전달하지
않았다. publication reader도 summary 안의 효율 숫자를 availability와 별개로 읽었다.

연구용 lock, pilot/formal plan, 실제 inventory, runtime 결과와 credential은 수정하지 않았다.
실제 Worker 접속, 모델 다운로드, inference, native RPC와 hardware acceptance는 실행하지
않았다.

## Implemented contract

measurement record를 additive schema v4로 올리고 각 telemetry sample에
`controller_collection_interval_s`를 기록한다. 기존 schema v1–v3 record는 계속 허용한다.

`bounded-power-gap-v1`은 scenario마다 다음 조건을 모두 요구한다.

- timestamp가 있는 sample 두 개 이상
- 모든 timestamped sample에 watt 존재
- timestamp가 엄격히 증가
- 인접 sample 간격이 선언 수집 주기의 2.5배 이하

Controller poll과 Worker cache 주기가 모두 있으면 더 큰 값을 기준으로 사용한다. 선언이
없는 과거/synthetic record는 1초를 사용한다. 조건을 하나라도 만족하지 않으면 해당 node의
`energy_j`, `measurement_energy_j`, 평균 전력과 모든 J 기반 효율은 null이다. 정상 scenario의
부분 적분값을 full-run 값으로 승격하지 않는다.

per-scenario와 per-node `energy_coverage`에는 policy, sample 수, 선언/허용 간격, 최대 관측
gap, gap 수, 관측 시간, 적분 가능 시간, coverage ratio와 reason code를 기록한다. overall은
모든 node가 complete일 때만 complete이며 가장 낮은 node coverage ratio와 unavailable node
reason을 보존한다.

Sweep result service는 coverage를 API/UI에 전달하고 ratio와 unavailable reason을 표시한다.
과거 또는 손상된 summary에 효율 숫자가 남아 있어도 availability가 false이거나 coverage가
incomplete이면 Dashboard와 publication reader는 숫자를 null로 처리한다.

## Compatibility and boundaries

- `requests.csv` 19-column 계약과 기존 request/RPC lifecycle record는 변경하지 않았다.
- scenario 사이 gap 분리, Worker cache sample dedupe, Pi watt 무추정, degraded provider reason을
  유지했다.
- collection interval은 측정 정책 근거이며 짧은 peak를 복원하거나 결측 에너지를 추정하지
  않는다.
- schema v1–v3에는 interval field가 없으므로 1초 기본값을 적용한다. 이 보수적 reader 정책은
  긴 legacy gap을 complete energy로 승격하지 않는다.
- 실제 sensor 정확도와 hardware sampling 안정성은 이 software 단계의 완료 주장에 포함하지
  않는다.

## Verification

최종 명령과 실제 결과는 commit 전 working tree에서 기록한다.

| Check | Result |
|---|---|
| `.venv/bin/python -m unittest cluster.tests.test_measurement_instrumentation cluster.tests.test_sweep_results cluster.tests.test_phase11_publication cluster.tests.test_core` | 82 tests, PASS, 2.163s |
| `npm run test:syntax` and `node cluster/tests/test_dashboard_exports.js` | PASS |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | 683 tests, PASS, 134.981s |
| `PYTHONPYCACHEPREFIX=... .venv/bin/python -m compileall -q cluster scripts/ci` | exit 0 |
| `.venv/bin/python scripts/ci/validate_repository.py` | exit 0; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| all repository `.sh` files with `bash -n` | exit 0 |
| `.venv/bin/python -m unittest cluster.tests.test_packaging -v` | 3 tests, PASS; offline wheel build/install/import fixture |
| `.venv/bin/python -m build --wheel --no-isolation` | NOT RUN; local venv에 `build` frontend가 없음 (`No module named build.__main__`) |
| ShellCheck | NOT RUN; executable 미설치. 변경 shell script는 없고 전체 `bash -n`은 PASS |
| `npm test` | PASS; syntax, exports, publication PNG and 4 Playwright tests, 26.9s E2E; coverage/reason 표시 assertion 포함 |
| `git diff --check` | PASS |
| staged diff scope and credential-pattern scan | PASS; 20 intended files only, no match |

첫 전체 Python 실행은 sandbox가 localhost bind를 거절해 launcher 2개와 S10 loopback test
1개가 `PermissionError`로 끝났다. 동일 명령을 localhost 권한만 허용해 다시 실행했고 683개가
통과했다. 첫 `npm test`도 Chromium Mach port가 거절돼 중단됐으며 동일 local fixture 명령을
허용해 재실행한 결과 전부 통과했다. 출력에 보인 SSH/setup/RPC 문자열은 기존 mock fixture이며
실제 endpoint 접속은 없었다.

## Git checkpoint

보고서와 구현은 관련 검사 후 명시 파일만 stage하여 feature branch에 non-force push한다.
commit SHA, push 결과와 CI 상태는 실제 실행 결과로 최종 응답에 보고한다. R03 이후 단계는
이 R02 commit에 포함하지 않는다.
