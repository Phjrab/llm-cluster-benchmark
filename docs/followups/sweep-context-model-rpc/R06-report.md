# R06 — Model compatibility evidence report

Date: 2026-09-20

Implementation branch: `codex/current-source-pilot-v6`

Baseline: `c110e6511ef0bdb28de081510ab291a4c7b36b91`

## Scope and safety boundary

R06만 수행했다. 현재 branch, HEAD, staging과 working tree를 먼저 확인했고 R04 Campaign,
R05 multipart GGUF, Sweep Dashboard 변경이 모두 baseline commit에 포함된 clean 상태에서
시작했다. 연구용 lock, formal matrix, model catalog verification record, 실제 inventory,
runtime/model/result artifact는 수정하지 않았다.

실제 Worker 접속, 모델 다운로드, inference, native RPC, Campaign 또는 Sweep 실행은 하지
않았다. 검사는 synthetic catalog/inventory/result fixture와 fake backend만 사용한다. 따라서 이
단계는 특정 모델, 특히 현재 manifest가 없는 70B 후보가 실제 hardware에서 실행 가능하다는
주장이나 formal 승인을 만들지 않는다.

## Implemented evidence contract

- 모델 추천에는 설치, exact artifact identity, architecture, backend, memory, runtime smoke를
  독립적인 `valid`/`blocked`/`unknown` 상태로 직렬화하는 compatibility evidence를 추가했다.
- catalog의 과거 smoke 표기만으로는 `RUNTIME VERIFIED`가 되지 않는다. 현재 Worker에 설치된
  exact revision/SHA, 관측된 architecture, verified backend와 pinned runtime/platform smoke가
  모두 일치할 때만 추천 상태를 `recommended`로 승격한다.
- artifact 또는 architecture drift는 `blocked`이며 추천 UI에서 compatible로 보이지 않는다.
  설치 성공은 memory fit, backend compatibility 또는 실행 성공을 대신하지 않는다.
- experiment preflight는 기존 checksum/revision/license/artifact-set 차단을 보존하면서 현재
  Worker의 compatibility evidence를 계산한다. memory 차단 failure에도 해당 판정을 포함한다.
- Worker model runtime 응답에는 이미 저장된 non-secret source revision, architecture,
  quantization, metadata contract와 artifact kind/count를 포함해 실행 결과가 설치 identity와
  연결될 수 있게 했다.
- benchmark 결과는 Worker별 exact artifact, architecture, backend와 실제 성공 record를
  결합한 `model_compatibility`를 남긴다. 모든 조건이 관측된 완료 실행만
  `verified`; legacy identity나 일부 근거가 없으면 `observed_unverified`이다.
- 모든 compatibility record의 `formal_approval`은 `not_assessed`다. R01 formal gate와 lock을
  변경하거나 자동 승인하지 않는다.

## Dashboard integration

- Model Library는 memory fit, compatibility 상태와 formal 승인 경계를 별도 문구로 표시한다.
- Sweep builder는 예전처럼 `compatible` 추천을 runtime verified로 간주하지 않는다. runtime
  smoke, artifact identity와 architecture가 모두 valid일 때만 runtime evidence가 충족된다.
- Results participant card는 verified execution, observed but unverified, legacy/unavailable을
  구분한다. cache version과 정적 export fixture를 함께 갱신했다.

## Verification

| Check | Result |
|---|---|
| focused Python compatibility/model/core tests | 74 tests, PASS |
| expanded Python model/sweep/dashboard/runtime tests | 193 tests, PASS |
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | 702 tests, PASS, 142.356s |
| `npm test` | PASS; syntax, Dashboard fixtures, publication PNG, Playwright 8/8 |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `bash -n` on 7 repository shell scripts | PASS |
| offline wheel build and static asset inspection | PASS with system Python; SHA-256 `36d8c0267958e415c46c05317da096c8f0fd0aa02d95e214d4e811e6e4afc485` |
| `git diff --check` | PASS |

처음 확장 검사 명령에 JavaScript 파일인 `test_dashboard_exports.js`를 Python module로 잘못
지정해 import error 1개가 발생했다. 제품 실패가 아니며 올바른 Python 모듈 목록으로 재실행한
193개 검사는 통과했다. 일부 Dashboard test가 `Thread.start`를 monkeypatch하여 Python 3.13
종료 시 `cannot join thread before it is started` 경고를 출력하지만 unittest exit code는 0이다.
첫 wheel 명령은 의도적으로 setuptools가 없는 repository `.venv`를 사용해
`Cannot import 'setuptools.build_meta'`로 중단됐다. 같은 `--no-build-isolation` 빌드를
setuptools 82.0.0이 있는 system Python으로 재실행해 성공했고 변경된 Dashboard 정적 자산이
wheel에 포함됐음을 검사했다.

## Remaining boundary

실제 Jetson/Raspberry Pi의 architecture, 가용 memory, pinned llama.cpp runtime과 모델별 smoke
evidence는 아직 수집하지 않았다. 현재 model catalog의 candidate/verified 필드를 그대로
유지한다. hardware acceptance 또는 formal lock 변경은 별도 사용자 승인 단계다.

R07은 시작하지 않는다.
