# WS-01 Measurement Semantics and Execution Traceability

Status: complete

## Scope

WS-01만 수행한다. 설정 identity, 요청별 추론 경로, 토큰 산정 출처,
논리·물리 처리량 의미, Controller/Worker 대기 시간을 기존 결과에 additive하게
추가한다. Dashboard 재설계, 실행 전략 변경, 실제 하드웨어 추론, native RPC 실행,
후속 Workstream은 범위 밖이다.

Base commit은 `786ab9d`이며 작업 브랜치는
`workstream/ws-01-measurement-trace`이다. 조사 시 열린 PR #1은
`codex/roadmap-phase-08`에서 `main`으로 향하는 기존 Roadmap PR이었고 WS-01
변경과 직접 중복되지 않았다. 구현 전 완료된 Phase 및 후속 작업 계보는 사용자의
요청에 따라 fast-forward로 `main`에 반영했으며 기록을 재작성하지 않았다.

## Baseline and gap analysis

기존 구현은 summary schema v2, 19-column `requests.csv`, private
`responses.jsonl`/`measurements.jsonl`, 논리·물리 요청 수, prompt SHA-256,
TTFT/decode proxy, RPC usage 일부, Worker llama context 단일 lock을 이미 제공했다.
다음 간극만 보완했다.

- `ExperimentConfig.from_dict`가 모든 미지 키를 조용히 버렸다.
- 동일 실험 설정을 연결하는 정규화 지문이 없었다.
- Worker가 모든 chat completion 예외를 completion 호출로 fallback하여 메모리와
  모델/runtime 오류도 성공처럼 보일 수 있었다.
- Worker 출력 토큰 산정 출처와 실제 inference path가 결과까지 전달되지 않았다.
- legacy 처리량은 물리 호출량을 나타내지만 broadcast의 사용자 유효 작업량과
  명시적으로 구분되지 않았다.
- Controller executor queue와 Worker 단일 llama-context lock 대기가 요청 시간에서
  별도 필드로 관찰되지 않았다.

기존 persistence, strategy planner, RPC cleanup, power-quality policy, durable suite
recovery는 재작성하지 않았다.

## Design note

### Public API and configuration

`ExperimentConfig.from_dict(raw, strict=False)`는 기존 호출의 permissive 동작을
유지한다. `strict=True`인 CLI 및 formal child-job 경로는 미지 키 이름을 포함한
`DomainValidationError`로 즉시 실패한다. Dashboard wire model은 forward-compatible
extra key를 받아들이되 실행 config에 반영하지 않으며, 값은 저장하지 않고 정렬된
`ignored_config_keys` 이름만 run event/summary warning에 남긴다. Dashboard 자체의
suite 제어 필드 세 개는 알려진 orchestration 입력이므로 경고 대상에서 제외한다.

### Config fingerprint

`config_fingerprint_sha256`은 dataclass의 실행 필드를 키 순서대로 canonical JSON으로
직렬화한 SHA-256이다. Enum은 wire value로 정규화한다. 원문 prompt와 무시된 키는
identity에서 제외하고 `prompt_sha256`만 포함한다. 지문은 `config.json`, run-start
event, 성공·실패 summary와 `benchmark_parameters`에 동일하게 기록된다.
`persist_prompt=false`이면 run-start event에도 원문 prompt를 쓰지 않는다.

### Inference path and failure behavior

Worker request는 다음 additive trace를 남긴다.

- `inference_path`: `chat_completion` 또는 `completion_fallback`
- `fallback_reason_code`: 현재 `chat_template_unavailable`, 아니면 null
- `chat_template_hash`: GGUF provenance에 존재하는 hash
- `template_hash`: 실제 chat-template hash 또는 deterministic fallback format hash
- `inference_slots`: 현재 Worker llama context 계약에 따라 `1`

fallback classifier는 chat template/chat format과 unavailable·missing·unsupported
등의 compatibility 신호가 함께 있는 예외만 허용한다. 일부 토큰을 이미 전송한 뒤의
오류와 allocation, CUDA, corruption, 일반 runtime 오류는 원래 예외를 유지한다.
원문 prompt, response 또는 인증 token은 trace와 로그에 추가하지 않는다.

### Token count source

모든 durable request record는 다음 taxonomy 중 하나의 `token_count_source`를 가진다.

- `server_usage`
- `llama_cpp_tokenize`
- `retokenized_output`
- `stream_chunk_estimate`
- `unavailable`

Worker는 생성 결과를 llama-cpp tokenizer로 셌으면 `llama_cpp_tokenize`, tokenizer가
수를 주지 못해 stream chunk 수를 사용했으면 `stream_chunk_estimate`를 기록한다.
RPC는 llama-server completion usage 수신 여부 자체를 추적해 `server_usage`를
선택한다. 실패·구형 Worker 누락값은 `unavailable`이며 0으로 위장하지 않는다.

### Throughput semantics

| Field | Meaning |
|---|---|
| `logical_requests_per_s` | 모든 replica가 성공한 논리 사용자 요청 수 / measured scenario wall |
| `physical_requests_per_s` | 성공한 실제 Worker/RPC 호출 수 / measured scenario wall |
| `effective_user_tokens_per_s` | 완전히 성공한 논리 요청별 deterministic 대표 응답 하나의 토큰 합 / wall |
| `physical_cluster_tokens_per_s` | 모든 성공 물리 응답의 토큰 합 / wall |
| `requests_per_s` | legacy alias; `physical_requests_per_s`와 동일 |
| `cluster_tokens_per_s` | legacy alias; `physical_cluster_tokens_per_s`와 동일 |

대표 broadcast 응답은 `replica_index`, `request_id` 순 첫 항목이다. replica의 출력
길이가 달라도 계산이 재현 가능하며, 부분 성공 broadcast는 유효 사용자 응답으로
과장하지 않는다.

### Queue and timing boundaries

`controller_executor_queue_wait_s`는 executor submit 직전부터 worker thread가 실제
transport 호출을 시작하기 직전까지다. `worker_inference_lock_wait_s`는 요청 generator가
llama context lock 획득을 기다린 시간이다. `prompt_eval_s`는 lock 획득 후 첫 유효
생성 token까지의 backend interval이다. 기존 client/server TTFT, prefill proxy,
decode time과 legacy measured wall은 바꾸지 않았다. 계측은 monotonic clock 조회와
작은 dict update뿐이며 telemetry, persistence, cooldown은 계속 request wall 밖이다.

## Result schema and compatibility

`measurements.jsonl`은 additive schema version 2를 기록한다. JSON Schema는 version
1과 2를 모두 읽으며 새 필드를 optional로 둔다. `responses.jsonl`, `config.json`,
summary에 trace/fingerprint를 추가한다. `requests.csv`의 19개 열, legacy summary
필드와 Dashboard API endpoint는 변경하지 않는다. 구형 결과는 누락 필드 없이 기존
reader에서 계속 읽히고, 신형 Controller가 구형 Worker 응답을 받으면 새 source를
`unavailable`로 기록한다.

## Security and measurement impact

미지 Dashboard 입력의 값은 저장하지 않고 키 이름만 저장한다. fingerprint와 template
identity는 SHA-256이며 원문 prompt/chat template을 추가 저장하지 않는다. 인증 token,
SSH credential, 환경변수는 새 상태·event·summary에 포함하지 않는다. 새 원격 명령,
포트, 권한, Worker 배포 또는 cleanup 동작은 없다.

Queue/lock/prompt timing은 기존 request 실행을 감싸는 `perf_counter` 관찰이다. 별도
네트워크 호출, tokenizer 재호출 또는 telemetry probe를 추가하지 않아 기존 benchmark
wall 정의를 보존한다.

## Tests

추가/확장한 회귀 계약은 다음을 포함한다.

- permissive Dashboard/legacy config와 strict CLI/formal config
- canonical privacy-safe fingerprint와 prompt 변경 감지
- 알려진 chat-template fallback success trace
- CUDA allocation failure가 fallback으로 숨겨지지 않는 failure path
- Worker health의 single inference slot과 request token source
- Controller executor queue wait
- broadcast 논리/물리 request 및 token throughput 분리
- legacy throughput alias, 19-column CSV와 legacy result reader 보존

검증 결과:

- `.venv/bin/python -m unittest discover -s cluster/tests -v`: 493 tests passed
- `.venv/bin/python -m compileall -q cluster scripts`: passed
- `python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/ws01-wheel`:
  wheel built successfully
- `.venv/bin/python -m build --wheel --no-isolation`: not run; the existing
  project venv has no `build` module, so the installed setuptools/wheel backend
  was exercised through `pip wheel` without installing another package
- `npm test`: JavaScript syntax, export fixtures, publication PNG fixtures, and
  2 Playwright E2E tests passed
- `git diff --check` and measurement JSON parsing: passed
- shell syntax/shellcheck: not run because WS-01 changed no shell script
- hardware: not run; no real model inference, Jetson/Pi benchmark, or native RPC
  session is required or claimed by this Workstream

## Remaining risks and non-goals

- llama-cpp-python이 native prompt-eval counter를 공통 제공하지 않아 `prompt_eval_s`는
  lock 획득부터 첫 token까지의 관측 interval이다.
- 구형 Worker는 inference path와 template hash를 보내지 않으므로 해당 필드는 null이다.
- native llama.cpp RPC 내부 coordinator queue는 계속 노출되지 않아
  `coordinator_wait_s`는 null이다.
- 실제 Jetson/Pi 모델 inference와 RPC 하드웨어 검증은 이번 WS-01에서 실행하지 않는다.
