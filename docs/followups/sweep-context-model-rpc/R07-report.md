# R07 — Reliability regression and documentation closeout

Date: 2026-09-20

Branch: `codex/current-source-pilot-v6`

Baseline: `daa6b60894ee9d22c972e471e205f2d61dcec542`

## Scope

R07만 수행했다. 시작 시 local/remote HEAD는 R06 commit과 일치했고 staging 및 working tree는
clean이었다. R07는 S10과 중복되는 scheduler, API, Dashboard 기능 또는 test harness를 만들지
않고 R01–R06 후속 변경이 현재 통합 Sweep 제품과 함께 회귀하는지 확인하고 문서를 현재
source에 맞추는 단계다.

제품 코드, 연구용 lock, formal matrix, 실제 inventory, model catalog identity, runtime/model/result
artifact와 credential은 수정하지 않았다. 실제 Worker 접속, 모델 다운로드, inference, native
RPC, Campaign/Sweep/pilot 실행과 hardware workflow dispatch는 수행하지 않았다.

## Reconciled package status

| Package | Current software evidence | Remaining boundary |
|---|---|---|
| R01 source identity | existing formal source/deployment gate and regression suite | formal relock is H01/operator scope |
| R02 energy coverage | schema v4 `bounded-power-gap-v1`, fail-closed result/publication readers | actual sensor/hardware sampling acceptance |
| R03 Pi policy | ordinary warning and formal active-fault blocking share one documented policy | actual Pi power acceptance |
| R04 Campaign control | gate-first Dashboard API/control, existing CampaignRunner/JobService, restart recovery | shipped formal gate remains closed; no formal execution |
| R05 multipart GGUF | ordered artifact-set install/inventory/preflight/load contract | current 70B record still lacks an exact manifest; no actual download/load/RPC |
| R06 compatibility | selector/preflight/result evidence separates installation, identity, architecture, backend, memory, execution and formal approval | actual model/runtime/hardware smoke evidence |
| R07 regression/docs | current full local quality gates and hosted Required CI checkpoint | H01 remains separate and unexecuted |

The S10 overlap table, capability baseline, implementation map and manual now point to these current
software states. Historical phase reports and their original test counts remain historical records and
were not rewritten.

## Regression result

| Check | Result |
|---|---|
| `.venv/bin/python -m unittest discover -s cluster/tests -q` | 702 tests, PASS, 141.727s |
| `npm test` | PASS; syntax, Dashboard fixtures, publication PNG, Playwright 8/8 |
| `.venv/bin/python -m compileall -q cluster scripts/ci` | PASS |
| `.venv/bin/python scripts/ci/validate_repository.py` | PASS; 20 JSON documents, 72 formal cells, 13 pinned actions, 7 shell scripts |
| `bash -n` on all 7 repository shell scripts | PASS |
| offline wheel build and package asset inspection | PASS with system Python; SHA-256 `54c3b39d750c138004959f6c2177205326c498ecebcddffa2a2ff361e0d69f8c` |
| `git diff --check` | PASS |
| R06 baseline Required CI (`daa6b60`) | PASS; Linux quality, macOS controller, Browser E2E |

Local ShellCheck is `NOT RUN` because the executable is not installed. The hosted Linux Required CI
runs ShellCheck after push. Test output containing SSH, download, RPC and hardware-unavailable strings is
from existing mocks and negative fixtures; no actual endpoint or model repository was contacted.

## Git checkpoint

Only R07 documentation is staged. It is committed as a separate checkpoint and non-force pushed to the
existing feature branch after verifying the remote head is still the R06 baseline. H01 is not started.
