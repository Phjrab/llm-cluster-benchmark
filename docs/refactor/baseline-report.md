# Phase 00 — Baseline / Repository Reconciliation

## 0. 조사 범위와 결론

이 문서는 `MASTER_SPEC.md`와 `PHASE_00_BASELINE.md`에 따른 읽기 전용 기준선 조사 결과다. 제품 코드는 변경하지 않았으며, 이 문서만 추가했다.

조사 기준:

| 항목 | 값 |
|---|---|
| Repository | `/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark` |
| Remote | `https://github.com/Phjrab/llm-cluster-benchmark.git` |
| Branch | `main` |
| HEAD | `2bcbbe41906b048f397c17e6e1e16a99b6cebeaa` |
| `origin/main` | `2bcbbe41906b048f397c17e6e1e16a99b6cebeaa` |
| Commit subject | `Initial LLM cluster benchmark framework` |
| 조사 전 working tree | clean |
| Host | macOS 26.3, Darwin arm64 |
| Python | CPython 3.13.2 |
| 조사일 | 2026-08-20 (Asia/Seoul) |

핵심 결론은 다음과 같다.

1. **Target repository** `Phjrab/llm-cluster-benchmark`는 5개의 tracked 파일로 이루어진 초기 수동 benchmark framework다.
2. 사용자가 알려준 Jetson `192.168.0.26`에는 별도 **legacy migration source** `/home/jetson_orin_nano/project/llm/local_llm_bench`가 있다. 이 worktree의 remote는 `Phjrab/Jetson-orin-nano-LLM-benchmark`이며 target remote와 다르다.
3. legacy worktree에는 실제 `cluster` package, Head/Worker Dashboard, Worker API, RPC, suite, readiness 및 launcher가 있지만 30개 tracked 변경과 2개 untracked file이 남아 있다. HEAD `98d88c2583ebb301deb6887bd865eef3de66484e`는 `origin/main`과 같지만 working tree가 clean하지 않다.
4. 따라서 target GitHub repository와 실제 legacy 제품 구현이 분리되어 있다. MASTER SPEC의 Mac/GitHub source-of-truth 전제는 아직 성립하지 않으며, 이것이 Phase 01 진입 전 가장 큰 reconciliation blocker다.
5. target에 현재 구현된 제품 동작은 두 가지다.
   - 외부 OpenAI-compatible endpoint에 부하를 보내고 마지막에 CSV를 쓰는 `benchmark/load_test.py`
   - node-local `llama-bench`를 15회 실행하고 raw command output을 CSV에 추가하는 `benchmark/scripts/run_llama_bench.sh`
6. parent workspace의 `jetson_cluster_stage/`는 두 Git repository 모두의 tracked source가 아닌 별도 staging directory이므로 source of truth로 사용하지 않았다.

두 source의 기준선:

| Scope | Location / remote | State | Role in migration |
|---|---|---|---|
| Target | Mac `/Users/hajoonpark/Documents/자율설계/llm-cluster-benchmark`; `Phjrab/llm-cluster-benchmark` | clean, `main == origin/main`, HEAD `2bcbbe4`, 5 tracked files | 최종 source-of-truth가 되어야 하지만 구현이 없음 |
| Legacy | Jetson `192.168.0.26:/home/jetson_orin_nano/project/llm/local_llm_bench`; `Phjrab/Jetson-orin-nano-LLM-benchmark` | HEAD `98d88c2 == origin/main`; 30 tracked dirty + 2 untracked | 현재 제품 동작과 compatibility surface의 실제 migration source |
| Snapshot | `/tmp/llm_phase00_jetson_snapshot_20260820` | source/docs/tests only; `.git`, `.venv`, `.run`, models, outputs excluded | 읽기 전용 분석용, source-of-truth 아님 |

현재 tracked 구조:

```text
.
├── .gitignore
├── EXPERIMENT_PLAN.md
├── README.md
└── benchmark/
    ├── load_test.py
    └── scripts/
        └── run_llama_bench.sh
```

## A. Target repository baseline

다음 1–32절은 clean target repository 자체의 기준선이다. 이후 B절은 Jetson legacy migration source에서 같은 항목을 구체적으로 보완한다.

---

## 1. 현재 module dependency map

```text
README.md / EXPERIMENT_PLAN.md
  ├─ instruct operator to edit and run run_llama_bench.sh on every node
  ├─ instruct operator to start llama-server manually on every node
  ├─ instruct operator to configure nginx/HAProxy round-robin manually
  └─ instruct measurement PC to run load_test.py

benchmark/load_test.py
  ├─ Python stdlib: argparse, asyncio, csv, json, statistics, time
  ├─ Python stdlib HTTP: urllib.request
  ├─ outbound POST: operator-supplied OpenAI-compatible URL
  ├─ parses a narrow SSE subset inline
  └─ writes one request-result CSV after all requests finish

benchmark/scripts/run_llama_bench.sh
  ├─ Bash builtins and shell tools: mkdir, date, printf, tr, sed, sleep
  ├─ external executable: /opt/llama.cpp/build/bin/llama-bench
  ├─ external model: /opt/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf
  └─ writes timestamped CSV under CWD-relative ./results
```

파일 간 Python import 또는 package dependency는 없다. `benchmark/`에는 `__init__.py`가 없고 packaging metadata(`pyproject.toml`, `setup.py`, requirements/lock file)도 없다.

| Current unit | Responsibilities | Direct dependencies | Boundary status |
|---|---|---|---|
| `benchmark/load_test.py` | CLI parsing, request construction, HTTP, SSE parsing, timing, concurrency, aggregation, CSV persistence, console summary | Python stdlib, remote HTTP endpoint | 모든 책임이 한 파일에 직접 결합 |
| `benchmark/scripts/run_llama_bench.sh` | node configuration, path validation, process invocation, cooldown, CSV escaping/persistence | Bash, llama.cpp binary, GGUF, shell tools | config/process/storage가 한 script에 결합 |
| `README.md` | quick start and current architecture statement | operator actions | executable orchestration 대신 수동 runbook |
| `EXPERIMENT_PLAN.md` | hypotheses, workload matrix, fairness guidance | operator actions and hardware | 문서와 실제 enforcement가 분리됨 |

## 2. 주요 God module과 responsibility

현재 codebase 규모상 전형적인 대형 God module은 없다. 다만 책임 집중 기준으로는 다음 두 파일이 향후 분리 대상이다.

### `benchmark/load_test.py`

- line 13–19: percentile 계산
- line 21–56: payload 생성, HTTP I/O, SSE parsing, TTFT/E2E/tokens/s 계산, broad exception 처리
- line 58–66: public CLI 정의
- line 67–75: concurrency scheduling
- line 76–78: CSV persistence
- line 79–87: aggregation과 stdout rendering

### `benchmark/scripts/run_llama_bench.sh`

- line 5–11: platform/node/model/runtime config
- line 13–23: filesystem 생성과 preflight
- line 25–35: experiment matrix, external process execution, CSV serialization, cooldown

## 3. `head` / `role == "head"` / local-head 가정

Tracked text 전체를 case-insensitive search한 결과 `head`, `role == "head"`, `local-head` occurrence는 **0개**다.

- node/domain type 자체가 없다.
- control plane role 자체가 없다.
- legacy read adapter도 없다.
- 따라서 제거할 구체적인 legacy branch는 없고 Phase 01에서 새 domain을 worker-only inventory로 시작해야 한다.

## 4. control 기능과 inference 기능이 결합된 위치

Controller/daemon 구현이 없으므로 `control + inference` daemon 결합은 없다.

현재 기능 배치는 수동으로 분리되어 있다.

- 측정 PC: `benchmark/load_test.py`가 request submission과 aggregation을 함께 수행한다.
- inference node: `run_llama_bench.sh`가 node-local process 실행과 결과 저장을 함께 수행한다.
- inference serving: operator가 repository 밖의 `llama-server`를 직접 실행한다.
- load balancing: operator가 repository 밖의 nginx/HAProxy를 직접 설정한다.

`load_test.py`는 control-plane 후보 로직과 metrics/storage를 한 파일에 묶지만 inference를 수행하지는 않는다.

## 5. RPC coordinator가 head를 강제하는 위치

RPC code, RPC config, coordinator selection, topology metadata가 모두 없다. 강제 위치는 **0개**다.

## 6. node sweep이 head 참여를 전제로 하는 위치

Executable sweep/planner는 없다. `EXPERIMENT_PLAN.md:53-58`은 operator가 proxy backend를 1, 2, 4대로 수동 변경하도록 설명할 뿐 participant identity/order를 저장하지 않는다. head 자동 포함 가정은 없다.

## 7. macOS에서 동작하지 않는 Linux 전용 path

| Location | Assumption | macOS impact |
|---|---|---|
| `benchmark/scripts/run_llama_bench.sh:5` | model at `/opt/models/...gguf` | 기본 Mac에 없음 |
| `benchmark/scripts/run_llama_bench.sh:6` | binary at `/opt/llama.cpp/build/bin/llama-bench` | 기본 Mac에 없음 |
| `benchmark/scripts/run_llama_bench.sh:8-11` | Raspberry Pi/Jetson-specific platform and flags | Controller lifecycle에 사용 불가 |
| `README.md:19-21` | scripts run on nodes; server/proxy manually managed | Mac Controller setup/lifecycle가 없음 |

`load_test.py` 자체는 Python stdlib만 사용하므로 macOS에서 실행 가능하다. `/proc`, Linux process API 또는 package manager를 사용하지 않는다.

## 8. `/proc`, pidfd, systemctl, apt, dpkg, jtop 관련 코드

다음 tracked occurrence는 모두 **0개**다.

- `/proc`
- `pidfd`
- `systemctl`
- `apt`
- `dpkg`
- `jtop`
- `jetson-stats`

즉 현재는 Linux-only lifecycle 문제뿐 아니라 stale PID protection, telemetry provider, environment provisioning도 구현되어 있지 않다.

## 9. repository 외부 dependency

| Dependency | Where assumed | Managed/pinned? |
|---|---|---|
| CPython 3 | `benchmark/load_test.py` shebang | no version declaration |
| Bash | `run_llama_bench.sh` shebang | no version declaration |
| llama.cpp `llama-bench` | shell line 6 | absolute path, no commit/version pin |
| GGUF model | shell line 5 | absolute path, no checksum/metadata |
| `llama-server` | `README.md:20`, `EXPERIMENT_PLAN.md:53,74` | operator-managed |
| nginx or HAProxy | `EXPERIMENT_PLAN.md:53` | operator-managed |
| OpenAI-compatible streaming endpoint | `load_test.py:60` | operator-supplied URL |
| `mkdir`, `date`, `tr`, `sed`, `sleep` | shell script | host tools |

No dependency manifest, installer, environment checker, build pin, or compatibility probe exists.

## 10. `web.app` dependency

`web.app` import/reference는 **0개**다. FastAPI나 any inbound web application도 없다.

## 11. `scripts/llm-cluster` dependency

`scripts/llm-cluster` 및 `llm-cluster` command는 존재하지 않는다. 현재 executable public files는 Git mode `100755`인 다음 두 개뿐이다.

- `benchmark/load_test.py`
- `benchmark/scripts/run_llama_bench.sh`

## 12. project root / models path 계산 방식

- 명시적인 `ProjectLayout`/root resolver가 없다.
- `run_llama_bench.sh:7`의 output path는 caller CWD 기준 `./results`다. script 위치 또는 repository root를 resolve하지 않는다.
- model과 binary는 shell source를 직접 편집해 `/opt/...` absolute path를 설정한다.
- `load_test.py:65`의 default output `load_results.csv`도 caller CWD 기준이다.
- README quick start는 implicitly repository root에서 실행한다고 가정한다.
- `EXPERIMENT_PLAN.md:73`은 실제 path `benchmark/scripts/run_llama_bench.sh`가 아니라 `scripts/run_llama_bench.sh`라고 적어 문서 내부 path가 불일치한다.

## 13. filesystem state 목록

| State | Writer | Timing/durability | Compatibility surface |
|---|---|---|---|
| arbitrary `--output` CSV, default `load_results.csv` | `load_test.py:76-78` | all requests complete 후 truncate/write; atomic/fsync 없음 | request CSV header |
| `./results/<platform>_<node>_<timestamp>.csv` | shell line 13-33 | header truncate 후 invocation마다 append; atomic/fsync 없음 | microbenchmark CSV header/file naming |
| `.DS_Store` | macOS Finder | repository local, ignored | not product state |
| `.git` | Git | normal Git semantics | source history |

`.gitignore`는 `__pycache__/`, `*.py[cod]`, `results/`, `.DS_Store`만 제외한다.

다음 목표 state는 현재 없다: worker inventory, settings, environment report, experiment definition, job registry, event journal, suite result, model catalog/cache, PID/log/runtime directory, token/credential file.

## 14. global mutable state 목록

- `load_test.py:11`의 `PROMPT` module constant만 있다. immutable string이며 runtime mutation은 없다.
- process-global manager, registry, cache, lock, thread, event bus, loaded model handle은 없다.
- mutable request/result state는 function-local list/dict와 `asyncio.Semaphore`뿐이다.

## 15. external process 목록

Code가 직접 실행하는 external process는 shell line 31의 `llama-bench` 하나다.

문서가 operator에게 수동 실행/구성하도록 요구하는 external service:

- worker별 `llama-server`
- nginx 또는 HAProxy round-robin proxy
- node별 llama.cpp build/toolchain

Repository-managed daemon/process lifecycle, PID file, log file, restart/cleanup은 없다. 조사 host에는 `llama-bench`와 `llama-server` executable이 PATH에 없었다. sandbox가 process table 열람을 차단했지만 repository 자체에는 process registry가 없으므로 실행 중 product daemon을 판정할 source도 없다.

## 16. SSH/rsync/subprocess/HTTP side-effect boundary

| Side effect | Current location | Abstraction |
|---|---|---|
| SSH | absent | none |
| rsync/deploy | absent | none |
| Python subprocess | absent | none |
| shell process invocation | `run_llama_bench.sh:31` | inline Bash command substitution |
| outbound HTTP | `load_test.py:25-46` | inline `urllib.request` |
| SSE parsing | `load_test.py:32-46` | inline, only `data: ` lines |
| filesystem write | Python line 76-78; shell line 13-33 | inline |
| sleep/cooldown | shell line 34 | inline |

Infrastructure result types, protocols, adapters, dependency injection은 없다.

## 17. Dashboard event/log routing

Dashboard와 event channel이 없다.

현재 user-visible logging은 다음 stdout/stderr뿐이다.

- `load_test.py:83-87`: final aggregate와 saved path
- shell line 17/21: missing binary/model stderr
- shell line 29/38: per-case progress와 final path stdout
- shell line 31: `llama-bench` stdout/stderr를 합쳐 CSV raw output에 저장

Node ops와 experiment events를 구분하거나 persist하지 않는다.

## 18. model inventory와 Head filesystem dependency

Head filesystem 개념과 inventory endpoint는 없다.

현재 model handling은 node별 shell source에 하드코딩된 단일 path(`run_llama_bench.sh:5`)와 request body의 logical model string(`load_test.py:61`)뿐이다. 두 값의 동일성, file size, SHA-256, quantization 또는 node별 설치 여부를 검증하지 않는다.

## 19. benchmark raw response가 버려지는 위치

`load_test.py:42-46`은 streaming delta content의 존재만 확인해 `chunks`를 증가시키고 first-token timestamp를 잡는다. content text를 누적하거나 반환하지 않으므로 실제 LLM response는 전부 버려진다.

- prompt는 module constant지만 row/result에 저장되지 않는다.
- output SHA-256가 없다.
- logical/scenario/model/node identity가 row에 없다.
- `responses.jsonl`이 없다.

반면 shell CSV의 `raw_output`은 LLM 답변이 아니라 `llama-bench --csv` command output이다.

## 20. failure가 문자열로만 저장되는 위치

- `load_test.py:54-56`: 모든 exception을 broad-catch하고 `error=str(exc)`로 변환한다.
- error code, stage, node, evidence, solution은 없다.
- shell line 16-23은 missing executable/model을 plain stderr와 exit 1로만 표현한다.
- shell line 31의 `|| true`는 `llama-bench` non-zero exit를 성공한 loop iteration처럼 계속 진행하고 raw text만 CSV에 넣는다.

## 21. jtop/jetson-stats pinning 및 mismatch path

jtop, jetson-stats, psutil, telemetry provider 및 version pinning이 모두 없다. `EXPERIMENT_PLAN.md:39-41,66`은 RAM/power/temperature와 power mode를 기록하라고 요구하지만 현재 code는 이를 수집하지 않는다.

## 22. experiment manager/suite manager lifecycle

Manager/suite abstraction은 없다.

- `load_test.py`는 한 process에서 one-shot request batch만 실행한다.
- shell script는 fixed 5 × 3 = 15 invocation matrix를 순차 실행한다.
- multi-model suite, cancellation, warmup lifecycle, unload, cooldown state machine, partial status가 없다.
- plan의 warmup 3회(`EXPERIMENT_PLAN.md:67`)는 어느 script에도 구현되어 있지 않다.

## 23. Dashboard daemon thread/process와 experiment lifecycle 결합

Dashboard daemon이 없으므로 결합도 없다. `asyncio.to_thread()`는 `load_test.py:70`에서 blocking HTTP request를 concurrency-limited thread로 보내는 one-shot implementation일 뿐 durable scheduler가 아니다.

## 24. result persistence 시점과 crash durability

### Load test

`asyncio.gather()`가 모든 request를 반환한 뒤에만 line 76에서 output file을 연다. Process crash/cancel/kill 시 이미 완료된 request도 모두 유실된다. Write는 truncate mode이며 temp file, atomic replace, fsync, journal이 없다.

### Microbenchmark

Header는 line 25에서 truncate/create되고 각 command 완료 후 line 33에서 append된다. 이미 append된 row는 load test보다 생존 가능성이 높지만 fsync, completion marker, structured status가 없고 line 31이 command failure를 숨긴다.

Run directory, `config.json`, `events.jsonl`, `requests.csv`, `responses.jsonl`, `summary.json`은 없다.

## 25. frontend app.js / chart / experiment picker 구조

Frontend asset, HTML template, `app.js`, chart implementation, experiment picker, results view가 모두 없다. Graph generation도 구현되지 않았고 `EXPERIMENT_PLAN.md:76`이 operator에게 CSV를 합쳐 graph를 만들라고만 안내한다.

## 26. public CLI surface

### `./benchmark/load_test.py`

| Argument | Required/default | Meaning |
|---|---|---|
| `--url` | required | OpenAI-compatible chat completions endpoint |
| `--model` | `local-model` | request model field |
| `--requests` | `20` | total submitted tasks |
| `--concurrency` | `4` | semaphore capacity |
| `--max-tokens` | `128` | request max tokens |
| `--output` | `load_results.csv` | CWD-relative or user path |

Public stdout:

```text
success=<success>/<total> wall_s=<seconds> cluster_tok_s=<rate>
ttft_p50=<s> ttft_p95=<s>
e2e_p50=<s> e2e_p95=<s> mean=<s>
saved=<path>
```

### `./benchmark/scripts/run_llama_bench.sh`

No CLI arguments. Operator edits `MODEL`, `LLAMA_BENCH`, `OUT_DIR`, `PLATFORM`, `NODE_NAME`, `EXTRA_ARGS` in source. Matrix order is five repetitions of `(128,128)`, `(512,128)`, `(512,512)` with 30-second sleep after every invocation.

There is no `llm-cluster`, setup-controller, setup-worker, status/logs command, or `python -m cluster.clusterctl` compatibility surface.

## 27. Dashboard API surface

Dashboard/FastAPI application and inbound API are absent. API route count is **0**.

## 28. Worker API surface

Repository-owned Worker API is absent. The only wire contract is an **outbound dependency** on an operator-managed OpenAI-compatible streaming endpoint:

- method: POST
- URL: arbitrary `--url`
- headers: `Content-Type: application/json`
- request fields: `model`, `messages`, `max_tokens`, `temperature=0`, `stream=true`, `stream_options.include_usage=true`
- timeout: 180 seconds
- accepted stream lines: exact `data: ` prefix
- termination: `[DONE]`
- read fields: `usage.completion_tokens`, `choices[0].delta.content`

No health, model inventory, load/unload, telemetry, auth, RPC, environment or power endpoint exists.

## 29. result JSON/CSV/filesystem compatibility surface

No JSON result schema exists. The following two CSV formats and filenames are the entire persisted compatibility surface.

### Load request CSV

Header/order comes from dict insertion order:

```text
ok,ttft_s,e2e_s,completion_tokens,generation_tok_s,error,request
```

Semantics:

- `ok`: request-level boolean
- `ttft_s`: first non-empty delta content time; if none, E2E
- `e2e_s`: full HTTP stream elapsed time
- `completion_tokens`: usage value or content-chunk count fallback
- `generation_tok_s`: tokens divided by `max(E2E-TTFT, 1e-9)`
- `error`: raw exception string or empty string
- `request`: 1-based submission index

### Microbenchmark CSV

```text
platform,node,run,pp,tg,raw_output
```

Default naming:

```text
./results/<platform>_<node>_<YYYYmmdd_HHMMSS>.csv
```

Compatibility caveats:

- `raw_output` format varies with llama.cpp version by script comment.
- no schema version, config snapshot, commit fingerprint, model checksum or completion marker exists.
- raw result directory is Git-ignored and no reader/migration code exists.

## 30. security-sensitive invariant

Current positive properties:

- shell starts with `set -euo pipefail`.
- binary/model/output paths are quoted at primary use sites.
- Python does not invoke a shell.
- no credential is embedded in tracked files.

Current missing or weak properties:

- no SSH BatchMode/host identity policy because SSH is absent.
- no token/auth/TLS enforcement for HTTP; README example uses plain HTTP.
- no constant-time token compare or protected secret/result permissions.
- arbitrary endpoint and arbitrary output path are accepted.
- broad exception text is persisted without redaction.
- `EXTRA_ARGS` is deliberately unquoted at shell line 31, allowing word splitting/globbing after source edit.
- `|| true` masks `llama-bench` process failure.
- no model/project path safety or checksum validation.
- no sudo/package installation path exists, so package allowlist semantics are not yet implemented.

Future phases must add invariants without weakening the current no-shell Python HTTP path and quoted fixed-path behavior.

## 31. 현재 테스트가 보호하는 behavior

Tracked automated tests, test configuration, CI workflow and fixture directories are all absent. Therefore **no behavior is regression-protected by repository tests**.

The only reproducibility descriptions are documentation and fixed constants:

- replica/round-robin intent: `README.md:12-14`
- fixed microbenchmark matrix: `EXPERIMENT_PLAN.md:31-41` and shell line 26-34
- intended cluster matrix: `EXPERIMENT_PLAN.md:53-60`
- fairness controls: `EXPERIMENT_PLAN.md:62-68`
- current percentile implementation: `load_test.py:13-19`

These are not executable contract tests.

## 32. 테스트가 부족한 behavior

All MASTER SPEC test families are missing. Highest-risk gaps:

1. percentile edge cases and aggregate formulas
2. CLI validation (`requests <= 0`, `concurrency <= 0`, invalid output parent)
3. OpenAI request payload contract
4. SSE token/done/error/malformed/blank/unrelated/truncated cases
5. timeout and partial request handling
6. response text/hash persistence
7. CSV schema and escaping
8. shell process non-zero propagation
9. single-node planning and warmup exclusion
10. deterministic round-robin mapping
11. broadcast logical/physical expansion
12. node sweep selected-order semantics
13. scaling speedup/efficiency
14. RPC topology/coordinator/cleanup
15. result schema compatibility/corruption/crash recovery
16. inventory, model checksum and preflight
17. process identity/stale PID and macOS lifecycle
18. security settings and permissions
19. Jetson/Raspberry Pi integration markers
20. Dashboard/Worker contract tests

---

## B. Jetson legacy migration-source baseline

이 절은 target에 아직 들어오지 않은 실제 제품 구현을 current Jetson worktree 기준으로 조사한 결과다. 원격 파일은 수정하지 않았고 source/docs/tests만 `/tmp` snapshot에서 읽었다.

Legacy source tree의 주요 규모:

```text
cluster/clusterctl.py                  1,491 lines
cluster/benchmark/runner.py           1,237 lines
cluster/dashboard/app.py              2,169 lines
cluster/dashboard/static/app.js       2,109 lines
cluster/worker/app.py                   509 lines
web/app.py                              434 lines
scripts/llm-cluster                     638 lines
cluster/tests/test_core.py            1,107 lines
cluster/tests/test_launcher.py          166 lines
```

### B1. Required analysis reconciliation (1–32)

| # | Required item | Actual legacy location and finding |
|---:|---|---|
| 1 | module dependency map | `cluster/dashboard/app.py` imports `cluster.benchmark.runner` and `cluster.clusterctl`; runner imports `Node`, inventory, HTTP and SSH execution from `clusterctl`; `cluster/worker/app.py:24` imports the FastAPI `app`, request schema, SSE helper and global manager from `web.app`; CLI/dashboard/benchmark all point inward to infrastructure-heavy modules, not a one-way domain/application boundary. |
| 2 | God modules | `dashboard/app.py` owns schemas, auth, inventory/storage, LAN scan, status thread, action subprocesses, suite persistence, experiment service and every route. `clusterctl.py` owns domain `Node`, validation, SSH/HTTP/rsync, apt bootstrap, readiness, tokens, lifecycle and CLI. `runner.py` owns config, strategies, HTTP/SSE, RPC process topology, metrics, persistence and CLI. `app.js` owns all UI state/API/events/nodes/models/experiment/results/charts/export/onboarding in one 2,109-line file. |
| 3 | head assumptions | `clusterctl.py:137-169,175-241`, `dashboard/app.py:175-243,331-370,2007-2050`, config CSV, setup script and tests require exactly one enabled `head`; `Node.is_local` equates local control with role head. UI labels it `HEAD · CONTROL + INFERENCE` at `app.js:323`. Full occurrence inventory is represented by `rg` output and tests such as `InventoryTests.test_rejects_inventory_without_one_enabled_head`. |
| 4 | control + inference coupling | `cluster/setup_head.sh:37-72` installs worker runtime, creates head inventory and starts Worker API on the control host. `cluster/worker/app.py` layers cluster endpoints on the same imported `web.app`/`ModelManager`. Dashboard model catalog reads its own `PROJECT_ROOT/models` and the head participates in benchmark requests. |
| 5 | head RPC coordinator | `runner.validate_strategy():261-265`, `build_strategy_scenarios():285`, `_start_rpc_topology():596`, and tests `test_rpc_requires_head_worker_and_acknowledgement`, `test_rpc_check_identifies_pi_head_for_loopback_device` force exactly one selected head as coordinator. |
| 6 | head in sweep | `build_strategy_scenarios():275-283` preserves selected order and cumulative prefixes; default `node_names=[edge-head]`, UI auto-select/head topology, and tests use `[head,w1,w2]` with expected scenarios `[head]`, `[head,w1]`, `[head,w1,w2]`. Planner itself uses input order, but current inventory/UI convention makes head first and included. |
| 7 | macOS-incompatible paths | `scripts/llm-cluster:28-46,146-220` hardcodes Jetson user/path/URL and requires Linux `/proc` plus pidfd. `setup_head.sh`, `worker_setup.sh`, `clusterctl.DISCOVERY_SCRIPT` use `/proc`, dpkg/apt/systemctl and Linux worker setup. Mac cannot run current setup/controller lifecycle as specified. |
| 8 | `/proc`/pidfd/systemctl/apt/dpkg/jtop | `/proc`: `clusterctl.py:83`, `rpc/runtime.sh:20,74-75`, worker profile, setup scripts and launcher. pidfd: `scripts/llm-cluster:377-449`; tested in `test_launcher.py`. apt/dpkg: `clusterctl.py:83-135,342-400`, `worker_setup.sh:326-390`. systemctl/jtop: `worker_setup.sh:501-513`; worker jtop sampler `worker/app.py:218-395`; standalone benchmark jtop in `bench/benchmark.py` and medical scripts. |
| 9 | repository-external dependency | Required runtime includes Ubuntu tools, SSH/rsync, CUDA/JetPack, OpenBLAS, system jtop service, GGUF files, `.venv`, and llama-cpp-python. RPC clones `ggml-org/llama.cpp` at pinned commit `f49e917...`; ordinary `requirements.txt` is lower-bounded while `cluster/requirements-runtime.txt` pins seven control/worker packages. |
| 10 | `web.app` dependency | Hard dependency at `cluster/worker/app.py:24`; it reuses imported global FastAPI app and `ModelManager`. Worker cluster routes and auth middleware are attached to the parent chat application. |
| 11 | `scripts/llm-cluster` dependency | Dashboard wrappers `cluster/dashboard/start.sh` and `stop.sh` directly `exec` this script. It only manages Dashboard but hardcodes `EXPECTED_USER=jetson_orin_nano`, exact legacy path and URL, and requires Linux pidfd; therefore it is not standalone or Mac-compatible. |
| 12 | project root/models calculation | Multiple independent `Path(__file__).resolve().parents[...]` or shell `dirname` calculations exist in runner, clusterctl, dashboard, worker, web app and scripts. Head model root is repeatedly `PROJECT_ROOT/models`. Legacy launcher additionally rejects any root other than the hardcoded Jetson path. |
| 13 | filesystem state | `.run/cluster/nodes.local.csv`, settings, dashboard/worker tokens, environment reports, experiment definitions, run directories, `_suites`, PID/identity/log/lock files, RPC source/build; `models/`; `outputs/`. Live counts: 15 run dirs, 2 suite JSON, 3 experiment definitions, RPC source 162M and build 522M. Inventory and an RPC log were mode 0664 while tokens/settings were 0600. |
| 14 | global mutable state | Dashboard: `events`, `inventory_lock`, `settings_lock`, `scan_lock/cache`, `status_monitor`, in-memory `actions`, `experiment_catalog_lock`, `experiments`, FastAPI app/templates. Worker: global `manager`, `sampler` daemon thread, cached `NODE_PROFILE`, `RUNTIME_BACKEND`. Web: global `manager` with loaded Llama handle and RLock. Clusterctl: worker-token lock. |
| 15 | external processes | SSH, rsync, apt-get, git, ssh-keyscan/keygen, uvicorn Dashboard/Worker, native llama.cpp rpc-server/llama-server, `ldd`, `nvcc`, `vcgencmd`, jtop service, standalone benchmark subprocesses. Live probe on 2026-08-20 found ports 8000/8080/50052/18080 all closed and a stale `worker_server_8000.pid=49181`. |
| 16 | side-effect boundary | `clusterctl.run_on_node()` and `request_json()` are partial helpers, but SSH command construction, rsync, package install, token copy and lifecycle remain in one module. Runner calls `urllib` and remote runtime commands directly. Dashboard routes/services call filesystem/subprocess directly. `subprocess.CompletedProcess` crosses into orchestration logic. |
| 17 | event/log routing | One in-memory `EventBus` feeds `/api/events`; types include `cluster_status`, `monitor_error`, `environment_changed`, `action_*`, `settings_changed`, `inventory_changed`, and wrapper `experiment_event`. `app.js:1638-1744` sends environment action logs to a dedicated environment log, but all other node/control action logs go to the Run Control console, so node ops and experiment output are not fully separated. |
| 18 | Head model filesystem | `dashboard.list_models():658-670`, `clusterctl.all_model_paths():1174-1177`, model sync and RPC coordinator path all enumerate `PROJECT_ROOT/models` on head. Experiment validation starts from this catalog even though health exposes node-specific `model_ids`. |
| 19 | raw response discarded | `runner._stream_request():331-404` and `_stream_rpc_request():406-490` accumulate response text only to compute character count/hash, then drop text. `requests.csv` and per-request events omit prompt/response; there is no `responses.jsonl`. |
| 20 | string-only failures | Request rows and run/suite summaries use `error: str(exc)`; `_failure_record()` has no typed code/stage/evidence. Suite errors add a stage string but no error-code family. Dashboard unexpected errors return plain detail. RPC cleanup collects formatted strings. |
| 21 | jtop pin/mismatch | Installer attempts `jetson-stats==4.3.2` only when import is missing, but does not verify an already installed client version. It treats inactive system `jtop.service` as telemetry warning. Worker catches sampler errors and falls back to psutil, correctly not failing inference, but version mismatch behavior has no dedicated test. |
| 22 | experiment/suite lifecycle | `ExperimentManager` is a Dashboard-process global. It runs a daemon suite thread; each model gets independent `run_experiment`, then unload, optional cooldown, continue/stop policy, and persisted suite summary. Cancellation is a `threading.Event`. Final and interrupted suite artifacts are persisted/reconciled. |
| 23 | Dashboard/experiment lifecycle coupling | Status/action/experiment work all runs in Dashboard daemon threads. Dashboard restart cannot reattach a live experiment process; `reconcile_interrupted_suites()` marks nonterminal suites failed. Action records are memory-only and disappear. This is reconciliation, not durable job recovery. |
| 24 | persistence/crash durability | Runner creates `config.json` at start and appends `events.jsonl` per meaningful event/request. `requests.csv` and `summary.json` are written only after scenario/whole-run aggregation (failure writes summary). Completed request metrics may be recoverable from events, but no automatic replay/recovery exists; raw responses are unrecoverable. Suite JSON uses temp+replace. |
| 25 | frontend structure | One `app.js` contains searchable multi-model picker, strategy controls, node onboarding, telemetry, suite results, interactive canvas charts, dashboard PNG and publication SVG/PNG. Charts support bar/line internally but most result models are bar/line; there is no module split. Run console is 210px scrollable but lacks general Auto Scroll/Copy controls required by target. |
| 26 | public CLI | `python -m cluster.clusterctl` supports inventory/status/doctor/environment-check/environment-install/discover/setup/sync-code/sync-models/prepare/prepare-rpc/start/stop/restart/select-model. `python -m cluster.benchmark.runner --config --inventory --results-dir`. `scripts/llm-cluster start|stop|restart|status|logs`. Root standalone benchmark/chat scripts remain public. |
| 27 | Dashboard API | 18 routes: `/`, `/dashboard/health`, bootstrap/events/settings/status/status-refresh/network-scan/node-probe/nodes CRUD/actions/environment/experiments/list/groups/cancel/run detail. Most logic is in the same module. |
| 28 | Worker API | Imported legacy routes `/`, `/api/models`, `/api/select-model`, `/api/unload-model`, `/api/chat/stream`, `/health` plus cluster routes `/cluster/health`, `/cluster/models`, `/cluster/chat/stream`. Optional middleware token auth applies to the shared app. |
| 29 | result compatibility | Live schema v2 summary keys and 19-column requests CSV were inspected without values. Run dir has `config.json`, `events.jsonl`, `requests.csv`, `summary.json`; suite artifacts are `_suites/<id>.json`. Existing standalone general/medical benchmark CSV/JSON/plots are a second compatibility family. No `responses.jsonl`. |
| 30 | security invariants | Positive: BatchMode, quoted remote argv, fixed system package allowlist, private-LAN Node validation, safe project/model paths, constant-time token compare, atomic 0600 tokens/settings, RPC private-LAN warning, worker auth default off by explicit policy. Risks: `StrictHostKeyChecking=accept-new` rather than pinned fingerprint, unauthenticated RPC TCP, dashboard auth default off, stale worker stop scripts only check PID existence, inventory/RPC log legacy permissions 0664, token in EventSource query string when auth is on. |
| 31 | tested behavior | 52 Python tests cover inventory/head invariant, environment normalization/install safety, strategies/logical-physical counts, cancellation scheduling, suite persistence/cleanup/restart reconciliation, dashboard auth fail-closed behavior, readiness admission, platform build plan, launcher identity/pidfd safety. JS fixture covers chart exports/topology helpers. |
| 32 | weakly tested behavior | No fake HTTP Worker/SSE contract matrix; no actual Jetson/Pi/RPC integration markers; no worker inference/backend/model-load tests; no jtop mismatch fixture; no raw-response/durable-job recovery; limited route/DOM/browser tests; no model checksum/catalog/install/delete; no SSH host-key pin test; no crash replay from events; no power-mode feature in this remote worktree. |

### B2. Legacy dependency map

```text
Dashboard FastAPI (cluster/dashboard/app.py)
  ├─ benchmark runner (config + strategies + execution + result persistence)
  │    └─ clusterctl (Node + inventory + SSH + HTTP + auth)
  ├─ clusterctl directly (SSH discovery/actions/HTTP)
  ├─ filesystem repositories implemented inline
  ├─ daemon StatusMonitor / ActionManager / ExperimentManager
  └─ one app.js UI via SSE

Worker FastAPI (cluster/worker/app.py)
  └─ imports and mutates web.app global FastAPI app + ModelManager
       └─ llama-cpp-python / GGUF filesystem

clusterctl
  ├─ SSH + shlex remote command
  ├─ rsync code/model/token
  ├─ apt/dpkg bootstrap + worker_setup.sh
  ├─ Worker HTTP API
  └─ lifecycle shell scripts

RPC runner
  └─ SSH invokes cluster/rpc/runtime.sh
       └─ pinned native llama.cpp rpc-server + llama-server
```

This is a cyclic responsibility graph at the conceptual level even where Python imports are acyclic: domain data, infrastructure, application services and presentation all share the same modules and globals.

### B3. Live legacy filesystem/process baseline

No secret content was read. Only names, modes and counts were inspected.

| Item | Observed |
|---|---|
| Dashboard health `127.0.0.1:8080` | offline / connection refused |
| Worker health `127.0.0.1:8000` | offline / connection refused |
| RPC 50052/18080 | no listeners |
| stale worker PID record | `49181`; no listener/process health |
| runtime token/settings modes | 0600 |
| `nodes.local.csv` | 0664, should be reviewed/migrated privately |
| `rpc_worker_50052.log` | 0664 |
| result runs | 15 |
| suite artifacts | 2 |
| experiment definitions | 3 |
| native RPC source/build | 162M / 522M |

### B4. Exact public compatibility snapshot

Dashboard routes currently exposed by `cluster/dashboard/app.py`:

```text
GET    /
GET    /dashboard/health
GET    /api/bootstrap
GET    /api/events
GET    /api/settings
PUT    /api/settings
GET    /api/status
POST   /api/status/refresh
POST   /api/network/scan
POST   /api/nodes/probe
POST   /api/nodes
DELETE /api/nodes/{node_name}
POST   /api/actions
GET    /api/actions
GET    /api/environment
POST   /api/experiments
GET    /api/experiments
GET    /api/experiment-groups
POST   /api/experiments/cancel
GET    /api/runs/{run_id}
```

The count is 20 including page/health, 18 under API/event paths. All `/api/*` routes declare the optional dashboard-token dependency; actual enforcement depends on `settings.json`.

Worker process routes combine inherited `web.app` and cluster additions:

```text
GET  /
GET  /api/models
POST /api/select-model
POST /api/unload-model
POST /api/chat/stream
GET  /health
GET  /cluster/health
GET  /cluster/models
POST /cluster/chat/stream
```

Live latest-run schema inspection returned the following without reading prompt/response values:

```text
summary.json keys:
actual_model_config, all_replicas_success_rate, answer_agreement_rate,
benchmark_parameters, cluster_tokens_per_s, e2e_p50_s, e2e_p95_s,
execution_strategy, experiment_id, failed, finished_at, logical_requests,
model_count, model_id, model_index, model_placement, name, nodes, per_node,
physical_requests, requests, requests_per_s, result_dir, run_id,
scenario_summaries, schema_version, started_at, status, success_rate,
successful, suite_id, topology, total_generated_tokens, ttft_p50_s,
ttft_p95_s, wall_s, warnings

requests.csv header:
request_id,logical_request_id,scenario_id,replica_index,node,assigned_node,
node_host,started_at,ok,ttft_s,e2e_s,server_ttft_s,server_generation_s,
generated_tokens,tokens_per_s,output_chars,output_sha256,error,warmup

observed events.jsonl types:
node_model_loaded, phase, request_completed, run_finished, run_started,
scenario_finished, scenario_started
```

`config.json` stores the full `ExperimentConfig`, including prompt. `benchmark_parameters` hashes/length-tags the prompt, but no response text is persisted. `/api/runs/{run_id}` returns only parsed `summary.json`; it does not expose request rows or responses.

## Baseline test record

### Commands and outcomes

#### Target repository on Mac

| Command | Passed | Failed | Skipped | Exit | Interpretation |
|---|---:|---:|---:|---:|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v` | 0 | 0 | 0 | 5 | pre-existing: no tests discovered |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider` | 0 | 0 | 0 | 1 | not run: `pytest` is not installed and no dependency manifest exists |
| `PYTHONPYCACHEPREFIX=/tmp/llm_cluster_phase00_pycache python3 -m py_compile benchmark/load_test.py` | 1 file | 0 | 0 | 0 | Python syntax passed without repository cache write |
| `bash -n benchmark/scripts/run_llama_bench.sh` | 1 file | 0 | 0 | 0 | Bash syntax passed |
| `PYTHONDONTWRITEBYTECODE=1 python3 benchmark/load_test.py --help` | 1 CLI | 0 | 0 | 0 | argparse surface loads |
| inline current-percentile assertions | 4 | 0 | 0 | 0 | empty/single/p50/p95 linear interpolation smoke passed |

#### Legacy worktree on Jetson / exact source snapshot on Mac

| Command | Passed | Failed | Skipped | Exit | Interpretation |
|---|---:|---:|---:|---:|---|
| Jetson: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s cluster/tests -v` | 52 | 0 | 0 | 0 | full existing Python suite passed on Linux/aarch64 |
| Jetson: `node --check ... && node ...` | 0 | 0 | 1 gate | 127 | Node.js is not installed on Jetson; command stopped before Bash checks |
| Mac snapshot: `node --check cluster/dashboard/static/app.js` | 1 file | 0 | 0 | 0 | current legacy JS syntax passed |
| Mac snapshot: `node cluster/tests/test_dashboard_exports.js` | 1 fixture script | 0 | 0 | 0 | `dashboard export fixtures: OK` |
| Mac snapshot: `bash -n` over 12 public/cluster shell scripts | 12 files | 0 | 0 | 0 | all shell syntax gates passed |

Known pre-existing test gate failure:

- target repository has no test files; `unittest discover` returns exit 5.
- target repository has no dependency manifest and Mac Python has no `pytest`.
- legacy Python tests pass but live in another remote/repository and therefore do not protect changes committed to target until migrated.
- Jetson lacks Node.js, so JS regression is only reproducible on the Mac snapshot at present.

### Tests not run / reason

| Test | Reason |
|---|---|
| live `load_test.py` request | no test/fake OpenAI SSE endpoint configured; network result would not be deterministic |
| target `run_llama_bench.sh` execution | `llama-bench`/`llama-server` absent from Mac and Jetson PATH; target script has unrelated `/opt` defaults |
| live Worker/Dashboard API contract | current legacy services are offline; starting them would change external process state and Phase 00 did not authorize deployment/lifecycle mutation |
| live inference/model-load | no model was selected for this baseline; expensive GPU allocation and result writes are outside read-only Phase 00 |
| Raspberry Pi integration | no Pi connection supplied |
| live RPC | ports are down; starting unauthenticated RPC processes is outside read-only Phase 00 |
| jtop mismatch / telemetry hardware | no existing deterministic fixture and service was not started/reconfigured |
| power-mode test | feature is absent from current Jetson worktree inspected here; no `nvpmodel` mutation was attempted |

### Golden/snapshot fixture plan

Phase 00 does not create or update behavior fixtures. Before semantic refactoring, add the following fixtures without changing current expected values:

| Behavior | Current baseline to freeze | Proposed fixture |
|---|---|---|
| single-node plan | legacy `build_strategy_scenarios()` maps every logical request to the sole node; target shell also has a separate 15-call micro matrix | serialize legacy `StrategyScenario` and retain target micro CSV fixture separately |
| round-robin mapping | `(logical_id-1) % len(selected_nodes)`; test `test_round_robin_strategy_plan_is_balanced` | ordered task golden including request/logical/scenario/replica/target fields |
| broadcast expansion | every logical request expands once per selected node; tests cover expansion, physical/logical aggregate and group-slot concurrency | plan + aggregate golden with success/hash disagreement cases |
| node sweep ordering | cumulative prefixes or individual nodes preserve selected list order; current test includes legacy head first | golden current output, plus a separate migration expected fixture that removes Controller participation without altering worker order |
| metrics aggregation | linear percentile; total tokens/wall, logical/physical counts, per-node metrics, answer agreement | pure golden fixture for empty/single/mixed failures/broadcast/sweep |
| RPC topology metadata | current topology includes head coordinator, participants, endpoints, device order, split, runtime commit and security string | freeze v2 legacy topology, then add explicit migration fixture where coordinator is a Worker |
| representative rich result | live schema-v2 key set, 19-column requests header and event types recorded in B1/B3 | copy sanitized run fixture with prompt/model/host redacted but field semantics intact |
| target initial CSVs | exact target 7-column load CSV and 6-column micro CSV | legacy import fixtures/readers so initial GitHub behavior remains readable |
| raw response | absent in both target and legacy | fixture must first be introduced additively; do not pretend existing output contains response text |

---

## Reconciliation table

| Legacy assumption | Current code location | Target meaning | Migration approach |
|---|---|---|---|
| head = control + inference | legacy `Node.is_local`, exactly-one-head validation, `setup_head.sh`, UI `HEAD · CONTROL + INFERENCE` | Mac Controller + Worker-only inventory | migrate legacy inventory through adapter; remove head as new domain role; do not add Mac as node |
| head RPC coordinator | `runner.py:261-265,285,596`; RPC tests | selected prepared Worker coordinator | introduce `rpc_coordinator_node` worker policy and migration golden; update topology coordinator value |
| head in sweep | `build_strategy_scenarios():275-283`; UI/default config put head first; test expects `[head],[head,w1]...` | workers only, selected order | keep generic prefix algorithm, feed Worker list only; make old head fixture migration-specific |
| head model filesystem | `dashboard.list_models`, `clusterctl.all_model_paths`, RPC `PROJECT_ROOT/models` | per-worker inventory + independent catalog/cache | query each Worker and separate catalog; retain controller-cache→rsync as one adapter, not truth |
| target already contains legacy implementation | target has 5 files; implementation is in different dirty Jetson repository | Mac working tree + target GitHub source-of-truth | first preserve/import legacy history and dirty changes explicitly; never overwrite either worktree implicitly |
| `scripts/llm-cluster` is portable | hardcoded user/path/URL and Linux `/proc`/pidfd | macOS Controller lifecycle only | replace platform-specific process inspection behind adapter while retaining stale-PID invariant |
| Worker is standalone | `cluster/worker/app.py:24` imports `web.app` globals | Worker-owned inference abstraction | wrap legacy ModelManager, then eliminate parent runtime dependency without changing llama semantics |
| results are crash durable | events journal partial metrics; CSV/summary at end; no response text | request/event/response journal + recovery | add append response record at request completion and deterministic replay to summary |
| failures are structured | strings throughout runner/dashboard/suite | typed error code/stage/evidence/solutions | map deterministically and keep raw legacy message as additive field |
| event channels are separated | one EventBus/SSE; non-environment action log falls into Run Control | node_ops / experiment / system channels | type events centrally and route without breaking legacy JSON event readers |
| jtop is required for inference | installer already warns/falls back to psutil | telemetry degradation does not fail inference | retain fallback, add client/service mismatch test and explicit readiness split |
| model presence implies validity | IDs/paths only; no SHA-256 in inventory/preflight | checksum-valid per-worker model | additive metadata and preflight; no timed auto-download |

---

## Phase 01–15 actual file mapping

This is mapping only; no later phase was started.

| Phase | Current files/symbols to protect or migrate | New responsibility required by target |
|---|---|---|
| 01 Domain and roles | legacy `clusterctl.Node`, `ExperimentConfig`, `RequestTask`, `StrategyScenario`, Dashboard Pydantic models; target has none | extract Controller/Worker/experiment/strategy domain; adapters read legacy head CSV |
| 02 Standalone and storage | target CWD CSVs; legacy root constants and inline inventory/settings/environment/experiment/suite/result persistence | `ProjectLayout` and repository interfaces; legacy readers/atomic semantics |
| 03 Infrastructure and platform | `clusterctl` SSH/rsync/HTTP/apt, runner urllib, scripts `/proc`/pidfd, worker subprocess telemetry | typed command/SSH/HTTP/SSE/storage/platform adapters |
| 04 Mac Controller CLI | legacy `scripts/llm-cluster`, dashboard wrappers and launcher tests | portable Mac setup/lifecycle preserving exact-process safety; target wrapper surface |
| 05 Worker runtime | `web.app.ModelManager`, `cluster/worker/app.py`, setup/start/stop scripts | standalone Worker inference and telemetry providers; retain routes through compatibility adapter |
| 06 Benchmark core | legacy `runner.py` and strategy tests; target `load_test.py` metrics/CSV | split config/planner/executor/metrics/persistence; add goldens before edits |
| 07 RPC coordinator | legacy `runner._start/_stop_rpc_topology`, `rpc/runtime.sh`, RPC tests | Worker coordinator policy and cleanup context; Controller never compute |
| 08 Durable jobs | Dashboard `ExperimentManager`, daemon threads, suite reconciliation | separate job process/registry/recovery from Dashboard lifecycle |
| 09 Results and failures | legacy run writer/events/suite; target CSVs; string errors | responses journal, request recovery, structured failure and all legacy readers |
| 10 Event channels | legacy EventBus, `emit`, app.js event routing | central event types/channels and compatible SSE adapter |
| 11 Models and preflight | head `list_models/all_model_paths`, Worker health IDs, sync, readiness | per-worker inventory/checksum, catalog, install/delete/verify and preflight |
| 12 Dashboard backend | 2,169-line `cluster/dashboard/app.py` | routers/schemas/dependencies + injected application services/repositories |
| 13 Dashboard frontend | 2,109-line `app.js`, one template/CSS, current chart/export fixture | split modules, Controller/Worker UI, response viewer, bounded terminals, appropriate charts |
| 14 Regression/quality/security | legacy 52 Python + JS fixture + launcher tests; target no tests; permission/host-key gaps | migrate tests into target, add contract/golden/markers/security and corruption gates |
| 15 Migration/Git/cleanup | two distinct remotes, dirty Jetson worktree, live artifacts/models/outputs | archive/import/commit/push/deploy/acceptance; keep legacy workspace until hardware gate succeeds |

---

## Phase 00 Definition of Done

- [x] repository 전체 구조를 실제 code 기준으로 설명
- [x] legacy `head` coupling 위치 파악: target 0; Jetson legacy concrete symbols/tests mapped
- [x] public compatibility surface 파악
- [x] test baseline 기록
- [x] side effect / global state / filesystem state 파악
- [x] Phase 01–15에 actual file mapping 제시
- [x] product code 변경 없음

## Phase 00 completion report

```text
Implemented:
- baseline report only

Changed behavior:
- none

Backward compatibility:
- unchanged

Tests passed:
- Target Python py_compile: 1 file
- Target Bash syntax: 1 file
- Target load_test.py CLI help and percentile smoke: passed
- Jetson legacy Python unittest: 52 passed, 0 failed, 0 skipped
- Legacy JavaScript syntax/export fixture on Mac snapshot: passed
- Legacy Bash syntax: 12 files passed

Tests not run / reason:
- Target unittest: 0 tests discovered (exit 5); pytest dependency absent
- Legacy live inference, Dashboard/Worker contract, RPC, telemetry and power: services were offline or would require hardware/process/system mutation
- Raspberry Pi integration: no Pi connection supplied

Remaining issues:
- Target GitHub repository and actual legacy implementation are different repositories
- Jetson legacy worktree has 30 tracked dirty files and 2 untracked files
- target has no migrated automated regression suite
- legacy still couples head/control/inference and has no durable job process, raw response persistence, structured failures or per-worker model source-of-truth
- current services are offline and a stale Worker PID file remains; no destructive cleanup was performed

Next phase readiness:
- BLOCKED
```

`BLOCKED` means Phase 01 should not edit either product tree until the legacy working tree is safely preserved and the authoritative Mac target worktree is chosen/populated without overwriting user changes. This report does not start that migration. Once source reconciliation is complete, the first semantic extraction must migrate/golden-protect legacy tests and result schemas before changing behavior.
