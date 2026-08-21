# FOLLOWUP 08 — Experiment Condition and Model Identity Lock

## Status

`PARTIAL`: lock artifacts와 formal-only validator는 완료했지만, 승인 모델이 없고 hardware/runtime blockers가 남아 정식 Matrix 실행 자격은 아직 없다.

## Scope and evidence

- MASTER/FOLLOWUP spec 및 Phase 05/06/07/09/11/14/15 report를 재검토했다.
- checked-in catalog/default config, six-Worker inventory, Worker health/model API, SSH environment-check, 실제 GGUF SHA-256, Pi `get_throttled`, Controller Git/runtime을 읽기 전용으로 확인했다.
- 장시간 benchmark, 모델 다운로드/삭제, power mode 변경, reboot, Worker source 직접 편집은 수행하지 않았다.

## Result

- 공식 revision과 binary checksum이 명확한 최소 4개 후보를 `source_locked`로 확정했다.
- 네 prompt와 정확한 UTF-8 hash, 공통/Jetson/Pi/RPC condition, Controller/Worker/runtime/hardware/power snapshot을 고정했다.
- timestamp/key-order 독립 canonical lock fingerprint와 side-effect-free formal validator를 추가했다.
- 일반 experiment admission, planner, metric 공식, strategy mapping, result schema v2, requests.csv 19 columns는 변경하지 않았다.

## Blocking facts

- `approved` model 0개.
- 기존 Qwen2.5 1.5B 설치본은 official lock과 size/SHA가 다름.
- Worker deployment Git commit은 전부 unverified.
- Jetson power: MAXN_SUPER, MAXN_SUPER, 15W로 불일치.
- Jetson L4T: R36.4.7과 R36.5.0 혼재.
- Pi 02/03의 과거 power warning은 요구대로 non-blocking.

## Lock

- ID: `formal-study-v1`
- SHA-256: `4a03235dd7ae27a0a915e918c583df99aad2011e99f0baf1a9f61a3dcb5c3ab4`
- source baseline: `e70f63e797b9952ad1073ce16f556f0ff874b43b`

## Validation

- `.venv/bin/python -m unittest cluster.tests.test_research_locks -v`: 27/27 PASS
- `.venv/bin/python -m unittest discover -s cluster/tests`: 338/338 PASS
- `node --check cluster/dashboard/static/app.js`: PASS
- `node cluster/tests/test_dashboard_exports.js`: PASS
- `.venv/bin/python -m compileall -q cluster`: PASS
- all `cluster/` and `scripts/` shell files `bash -n`: PASS
- `git diff --check`: PASS

검증 중 장시간 benchmark나 model generation은 실행하지 않았다. 기존 result schema version 2 및 `requests.csv` 19-column 계약은 회귀 테스트로 유지됨을 확인했다.

FOLLOWUP 09는 시작하지 않았다.
