# Operations and Research Interpretation Guide

이 문서는 운영자가 실험을 안전하게 실행하고, 결과를 의도보다 강하게 해석하지
않도록 하는 기준 문서다. Dashboard의 일반 benchmark는 기능·성능 탐색 경로이고,
publication-quality 결과는 별도의 lock, pilot, campaign 승인 절차를 통과해야 한다.

## 1. Architecture and trust boundary

```text
Browser ──HTTP──> macOS Controller ──SSH/HTTP──> Jetson/Pi Workers
                    │                              │
                    ├─ inventory/jobs/results     ├─ GGUF + inference runtime
                    └─ scheduling/analysis        └─ temporary llama.cpp RPC
```

- Controller는 Dashboard, 스케줄링, 상태 복구, 결과 저장과 분석만 담당한다. 모델을
  로드하거나 추론에 참여하지 않는다.
- Jetson과 Raspberry Pi만 Worker다. RPC coordinator도 선택된 Worker 중 한 대다.
- 지원 경계는 신뢰된 사용자가 관리하는 격리된 RFC1918 사설 연구실 LAN이다.
  Dashboard는 기본적으로 `127.0.0.1:8080`에만 bind한다. Worker API와 RPC listener는
  인벤토리에 기록된 사설 IP에만 bind한다.
- 신뢰 LAN의 편의를 위해 인증은 기본 비활성일 수 있지만, 이것은 인터넷·공용 LAN
  안전성을 뜻하지 않는다. 공유 LAN 또는 VPN에서는 Dashboard/Worker 인증과 SSH host
  key pinning을 켜고, TLS/VPN 경계 밖으로 포트 8080, 8000, 50052, 18080을 공개하지 않는다.
- SSH key, token, 계정 비밀번호는 결과에 저장하지 않는다. 상세 위협과 보존 정책은
  [remote operation and retention](security/remote-operation-and-retention.md)을 따른다.

## 2. Quick start and role-specific commands

Controller source checkout에서 다음 순서가 지원되는 설치·실행 경로다.

```bash
git clone https://github.com/Phjrab/llm-cluster-benchmark
cd llm-cluster-benchmark
./scripts/setup-controller
llm-cluster start
llm-cluster status
```

Dashboard 기본 주소는 `http://127.0.0.1:8080/`이다. 이후 어느 디렉터리에서든
`llm-cluster start|stop|restart|status|logs`를 쓸 수 있다. 이 명령은 Controller
Dashboard만 관리하며 Worker나 RPC 프로세스를 광범위하게 종료하지 않는다.

각 Worker의 source checkout에서는 `./scripts/setup-worker`를 실행한다. 플랫폼별
Python 환경과 native RPC build는 프로젝트 내부에 준비되며 이미 검증된 동일 build는
재사용한다. Controller에 CUDA/OpenBLAS Worker runtime을 설치하지 않는다.

## 3. Strategy: model placement and request flow

| Strategy | 모델 배치 | 논리 요청 → 물리 호출 | 주된 질문 | 해석 제한 |
|---|---|---|---|---|
| `single_node` | 한 Worker에 전체 모델 | 1 → 1 | 단일 장치 기준선은 얼마인가 | 장치·runtime·power 조건이 다른 기준선은 직접 합치지 않는다 |
| `replicated_round_robin` | 모든 Worker에 전체 모델 복제 | 각 요청 1 → 1, Worker를 순환 | 동시 사용자 처리량이 얼마나 늘어나는가 | 한 모델을 분할한 결과가 아니다 |
| `broadcast_compare` | 모든 Worker에 전체 모델 복제 | 각 요청 1 → Worker 수 | 같은 입력의 지연과 출력이 일치하는가 | 복제 응답 token 합계를 사용자 처리량으로 부풀리지 않는다 |
| `node_sweep` | 선택 prefix 또는 각 Worker에 전체 모델 | scenario마다 round-robin | 노드 수 증가의 speedup/efficiency는 얼마인가 | 동일 모델·prompt·platform·runtime cohort만 scaling 비교한다 |
| `model_parallel_rpc` | 한 GGUF 계산을 coordinator와 RPC 장치에 분할 | 1 → 공동 생성 1 | 한 장치에 안 맞는 모델과 LAN 분할 비용은 어떤가 | 복제형 처리량과 직접 비교할 수 없는 실험 경로다 |

Jetson과 Pi를 섞은 구성은 명시적인 exploratory 실험으로만 취급한다. 공식
platform 비교나 scaling 결과는 같은 platform, model identity, prompt set, runtime
cohort, inference 조건 안에서 만든다. 느린 장치와 LAN이 병목인 혼합 결과를 Jetson
또는 Pi 자체의 공식 성능으로 표시하지 않는다.

## 4. Concurrency and the single Worker inference slot

논리 concurrency는 Controller가 동시에 계획하는 사용자 요청 수다. 현재 Worker는
모델별 llama context를 하나의 lock으로 보호하고 `inference_slots=1`을 보고한다. 따라서
한 Worker에 여러 요청이 도착해도 실제 모델 생성은 직렬화될 수 있다.

`controller_executor_queue_wait_s`는 Controller executor에서 기다린 시간이고,
`worker_inference_lock_wait_s`는 Worker의 단일 추론 slot 앞에서 기다린 시간이다.
E2E 증가를 모델 계산 속도 저하로 단정하기 전에 두 대기 시간을 분리해 본다. Worker
수를 늘린 round-robin은 독립 slot 수를 늘리지만, 한 Worker 안의 병렬 생성 수를 늘리지
않는다.

## 5. Result metrics: formulas and cautions

모든 비율의 분모 `wall_s`는 scenario request 실행 구간의 합이다. 모델 다운로드·load,
warmup, unload, cooldown, telemetry probe 완료와 결과 저장 시간은 포함하지 않는다.
`successful`은 성공한 물리 호출 수이며, `all_success`는 해당 논리 요청의 모든 replica가
성공한 그룹 수다. 결측치는 0이 아니며 `null`과 `availability.reason`으로 보존한다.

### Counts, latency, and throughput

| 결과 필드 | 계산 | 해석 주의 |
|---|---|---|
| `requests`, `physical_requests` | 계획된 물리 호출 수 | broadcast에서는 논리 요청 수 × Worker 수다 |
| `logical_requests` | `(scenario_id, logical_request_id)` 고유 그룹 수 | 실제 Worker 호출 수와 같다고 가정하지 않는다 |
| `successful`, `failed` | 성공 물리 호출 수, `physical_requests - successful` | 실패 요청도 분모와 실패율 증거에 남는다 |
| `success_rate` | `successful / physical_requests` | 물리 호출 성공률이며 broadcast 사용자 완결률이 아니다 |
| `all_replicas_success_rate` | `all_success / logical_requests` | broadcast에서 모든 replica가 성공해야 한 건 성공이다 |
| `wall_s` | 각 scenario의 request executor interval 합 | 준비·냉각 시간이 제외된 측정 경계다 |
| `physical_requests_per_s` | `successful / wall_s` | 실제 Worker/RPC 호출 처리량이다 |
| `logical_requests_per_s` | `all_success / wall_s` | 완결된 사용자 요청 처리량이다 |
| `requests_per_s` | `physical_requests_per_s`의 legacy alias | 신규 표·논문에서는 의미가 분명한 필드를 사용한다 |
| `physical_cluster_tokens_per_s` | 모든 성공 물리 응답의 생성 token 합 / `wall_s` | broadcast replica token이 모두 포함된다 |
| `effective_user_tokens_per_s` | 완전히 성공한 논리 그룹마다 결정적 대표 replica 하나의 token 합 / `wall_s` | broadcast 사용자 관점의 권장 token 처리량이다 |
| `cluster_tokens_per_s` | `physical_cluster_tokens_per_s`의 legacy alias | broadcast에서 사용자 token/s로 표기하면 안 된다 |
| `ttft_p50_s`, `ttft_p95_s` | 성공 물리 호출 TTFT의 선형보간 50/95 백분위 | Worker prefill 자체가 아니라 first-token까지의 관측이다 |
| `e2e_p50_s`, `e2e_p95_s` | 성공 물리 호출 E2E의 선형보간 50/95 백분위 | 실패 latency는 성공 분포에서 제외되지만 실패율에는 남는다 |
| `answer_agreement_rate` | 성공한 다중-replica 그룹 중 output SHA-256이 모두 같은 그룹 비율 | replica가 하나뿐이면 `null`; 의미적 유사도가 아닌 exact byte identity다 |
| per-node `effective_tokens_per_s` | 해당 노드 성공 token 합 / 전체 `wall_s` | node-sweep의 서로 다른 active interval 비교에 단독 사용하지 않는다 |
| per-node `average_generation_tokens_per_s` | 성공 요청별 generation token/s의 산술평균 | cluster throughput이 아니며 긴·짧은 요청 가중이 다르다 |
| `speedup_vs_baseline` | scenario physical token/s / 첫 scenario physical token/s | 동일 조건의 node-sweep에서만 유효하다 |
| `scaling_efficiency` | `speedup_vs_baseline / node_count` | 1.0이 완전 선형이라는 기술 지표이지 통계적 유의성을 뜻하지 않는다 |

Broadcast에서는 `physical_cluster_tokens_per_s`가 Worker 수에 따라 복제 token을
의도적으로 합산한다. 시스템이 실제 수행한 계산량을 보여 주는 값이지, 사용자가 받은
고유 답변 처리량이 아니다. 사용자 관점에는 `logical_requests_per_s`와
`effective_user_tokens_per_s`를 쓰고, 복제 비교에는 `all_replicas_success_rate`,
`answer_agreement_rate`, per-node latency를 함께 제시한다.

### Request-stage, energy, thermal, and network metrics

| 결과 필드 | 계산 | 해석 주의 |
|---|---|---|
| `prefill_time_s` | request 시작부터 first token까지 | first-token decode를 포함하는 proxy이며 native prefill-only timer가 아니다 |
| `prefill_tokens_per_s` | `input_tokens / prefill_time_s` | token count 또는 proxy가 없으면 `null` |
| `decode_time_s` | first streamed token부터 stream 종료까지 | 첫 token interval은 제외된다 |
| `decode_tokens_per_s` | 첫 token 이후 생성 token 수 / `decode_time_s` | 0초 또는 source 미지원은 `null` |
| `tokens_per_s` | 성공 요청의 `generated_tokens / generation_s` | 요청별 생성 속도이며 cluster wall-time 처리량이 아니다 |
| `connection_setup_s` | client 시작부터 HTTP response header까지 | TCP·server queue·초기 처리가 섞인 관측이며 순수 RTT가 아니다 |
| `effective_bandwidth_bytes_s` | `(body bytes sent + received) / E2E` 또는 host counter delta / sampled duration | HTTP body 기준은 protocol header 제외, host 기준은 다른 트래픽 포함 |
| `energy_j` | scenario 내부 연속 전력 sample의 사다리꼴 적분 합 | 각 scenario에 유효 sample 2개 이상이 필요하며 cooldown 간격을 잇지 않는다 |
| `average_power_w` | `energy_j / covered scenario duration` | idle power를 빼지 않은 측정 구간 평균이다 |
| `peak_power_w` | 유효 sample의 최대값 | cluster peak는 node별 peak 합이라 동시 peak라는 보수적 상한이다 |
| `joules_per_request` | `energy_j / successful physical requests` | logical request당 에너지가 아니며 broadcast 복제를 포함한다 |
| `joules_per_generated_token` | `energy_j / successful generated tokens` | physical 생성 token 기준이다 |
| `tokens_per_joule`, `generated_tokens_per_j` | successful generated tokens / `energy_j` | 같은 측정 경계에서만 비교한다 |
| `tokens_per_second_per_watt` | throughput / time-weighted power | 동일 구간에서는 차원상 tokens/J와 같으며 독립 효율 지표가 아니다 |
| `requests_per_j` | successful physical requests / `energy_j` | 전략별 물리 호출 배수가 다르면 직접 비교하지 않는다 |
| temperature start/mean/peak/end | 첫 값, sample 최대 sensor값의 산술평균, 전체 최대, 마지막 값 | 1초 sampling이 짧은 peak를 놓칠 수 있다 |
| `throttling_sample_count` | active Pi fault인 측정 sample 수 | 지원하지 않는 플랫폼은 0이 아니라 `null`이다 |
| `model_load_distribution_s` | RPC device 시작 시간 map + coordinator load 시간 | inference wall time 밖의 준비 비용이며 RPC 실용성에는 별도 보고한다 |
| `cleanup_s` | RPC stop 전체 구간, 실패한 stop 시도 포함 | non-zero 자체가 성공을 뜻하지 않아 cleanup 상태와 같이 본다 |

세부 source·availability 계약은
[measurement instrumentation contract](research/measurement-instrumentation.md)에 있다.

## 6. RPC operational and interpretation boundary

RPC는 선택 Worker 중 coordinator가 GGUF를 열고 다른 Worker의 native llama.cpp RPC
장치를 이용하는 proof-of-concept다. `model_load_distribution_s`를 request wall time과
분리해 보고하되, 대형 모델의 실제 사용성을 논할 때는 load 시간도 빠뜨리지 않는다.
토큰 생성마다 LAN 전송과 동기화가 개입하므로 네트워크 bandwidth/latency와 가장 느린
장치가 병목이 될 수 있다. replicated round-robin의 독립 요청 처리량과 동일 개념이 아니다.

native RPC에는 application 인증이 없다. 실험 동안에만 고정 사설 IP의 포트를 열고 성공,
실패, 취소, Controller 복구 뒤 모두 종료한다. Worker token 인증을 켠 상태에서는 직접 RPC
실험을 차단한다. 종료 후 다음처럼 선택한 정확한 Worker에 대해 점검한다.

```bash
.venv/bin/python -m cluster.clusterctl \
  --node edge-worker-01 --node edge-worker-02 rpc-cleanup-check
```

포트 50052/18080 또는 프로젝트 관리 process identity가 남으면 결과를 clean으로 승인하지
않고 해당 Worker에서 다음 실험을 중단한다. `pkill -f`나 `killall`로 우회하지 않는다.

## 7. Power-condition interpretation

### Jetson

전력 모드 비교는 동일한 Jetson model, L4T/kernel, CUDA/runtime fingerprint, GGUF,
prompt, threads, batch, GPU layers, `jetson_clocks` 상태를 맞추고 `nvpmodel`만 의도한
factor로 바꾼 경우에만 전력 모드 효과로 해석한다. 이름이 `MAXN`인지만 보지 말고
장비가 실제 보고한 mode ID/이름을 lock한다. 서로 다른 물리 Worker에서 얻은 15W와
MAXN 결과는 장치 차이가 섞인 탐색 비교다.

### Raspberry Pi

`vcgencmd get_throttled`는 watt sensor가 아니라 전력 무결성 상태다.

- `ok`: 현재·과거 알려진 fault bit가 없다.
- `history_warning`: 과거 undervoltage/throttling 등이 있었지만 현재 fault는 없다.
  일반 readiness를 막지 않으며 warning 분석군에 보존한다.
- `active_degraded`: 현재 undervoltage, frequency cap, throttling 또는 thermal limit가
  있다. 일반 run은 degraded로 보존하고 formal preflight에서는 차단한다.
- `unavailable` 또는 unknown bits: 0으로 바꾸지 않고 quality unknown으로 취급한다.

Pi의 psutil 및 `get_throttled`에서 watt를 추정하지 않는다. 외부 전력 센서가 없으면
Pi energy metric은 `null`이고 reason은 `raspberry_pi_power_sensor_unavailable`이다.

## 8. Smoke, pilot, and publication-quality experiments

| 등급 | 목적 | 허용되는 주장 | 필요한 조건 |
|---|---|---|---|
| Smoke | 설치·연결·모델 load·짧은 생성 확인 | 기능 경로가 작동했다 | exact 연구 반복·정밀도 보장 없음 |
| Pilot | 변동성, cooldown, thermal rule, 반복 수 산정 | 명시적으로 pilot인 기술 관측 | formal 결과와 분리, non-formal 승인 표시 |
| Formal/publication-quality | 사전 선언한 matrix의 추정과 비교 | lock 범위 안의 연구 주장 | 승인 lock, frozen protocol/order/repeats, 동일 cohort, clean evidence, 완료 campaign |

request는 run 내부의 중첩 관측이고 독립 반복 단위는 run이다. Smoke와 pilot을 formal
cell에 합치지 않는다. publication bundle은 `pilot` 또는 `formal` 한 등급만 입력받고,
prompt/response 원문을 표와 그림에 복사하지 않는다. 결측치는 0으로 대체하지 않으며
실패·제외도 coverage와 exclusions에 남긴다.

## 9. Sensitive prompt and response storage

민감 실험 전에 `persist_prompt=false`를 선택하고 응답 정책을 정한다.

| 응답 정책 | 보존 내용 |
|---|---|
| `full` | 응답 원문, 길이, SHA-256, metrics |
| `hash_only` | 길이, SHA-256, metrics; 원문 없음 |
| `none` | request identity, metrics, failure; 원문·길이·hash 없음 |

기본값 `persist_prompt=true`, `full`은 legacy 호환용이지 민감 데이터 권장값이 아니다.
정책은 암호화가 아니므로 Controller 저장소와 백업도 같은 민감도로 보호한다. 완료된
job은 runtime 문서의 prompt를 hash/count로 치환하지만 실행 중 private job에는 복구를
위해 prompt가 임시로 필요하다. 전체 계약은
[sensitive data storage](security/sensitive-data-storage.md)를 따른다.

## 10. Result files and schema compatibility

```text
<run>/
├── config.json
├── events.jsonl
├── responses.jsonl
├── requests.csv
├── measurements.jsonl
└── summary.json
```

| 파일 | 현재 계약 | 호환 규칙 |
|---|---|---|
| `requests.csv` | 기존 19 columns | 순서·의미를 유지한다; 새 민감/측정 필드는 넣지 않는다 |
| `responses.jsonl` | request record `schema_version=2` | storage mode에 따라 원문·hash·길이를 의도적으로 생략할 수 있다 |
| `measurements.jsonl` | 새 기록은 version 3; schema는 1/2/3 허용 | legacy run에 파일이 없으면 유효하며 API는 빈 목록을 반환한다 |
| `summary.json` | run summary `schema_version=2` + additive objects | 구 reader가 모르는 `measurement_instrumentation`, identity trace를 무시할 수 있다 |
| campaign/statistical/publication manifests | 각 schema version 1 | 각 문서의 validator로 검증하며 run schema와 같은 버전 번호 체계가 아니다 |

`schema_version`은 artifact family별 계약이다. 숫자가 같아도 서로 교환할 수 없고,
프로젝트 전체에 하나의 schema version이 있는 것이 아니다. 결과 디렉터리와 파일은
기본 0700/0600이며 prompt/response는 `responses.jsonl` 정책 경계 밖으로 복제하지 않는다.

## 11. Recovery, cancellation, and result retention

Controller 재시작 뒤 durable job/suite/campaign 문서를 읽어 실행 중 child identity와
terminal result를 다시 연결한다. 외부 상태가 불명확하면 같은 run을 중복 시작하지 않는다.
다중 모델 suite는 완료·실패·취소·미실행 모델과 unload/cooling 상태를 보존한다.

cleanup 실패는 run을 non-clean/failed로 만들고 해당 노드의 후속 schedule을 중단한다.
일반 결과 삭제는 `results/_trash/`로 원자 이동하며 복원할 수 있다. 영구 삭제는 run ID와
현재 content checksum을 다시 확인해야 하고 formal/campaign 결과에는 허용되지 않는다.

## 12. Formal lock review and approval

일반 Dashboard run을 formal이라고 이름 붙이는 것만으로 formal 결과가 되지 않는다.
승인자는 다음 순서로 fail-closed evidence를 확인한다.

1. `config/research/prompt_set.json`, `model_lock.json`, `runtime_lock.json`,
   `experiment_conditions.json`의 version과 canonical lock fingerprint를 검토한다.
2. 선택 모델이 `approved`이고 exact filename, positive byte size, SHA-256, metadata가
   모든 선택 Worker의 실제 파일과 일치하는지 확인한다.
3. source/deployment fingerprint, runtime cohort, backend, NTP, storage, 실제 threads,
   batch, context, GPU layers, Jetson power mode/clock, Pi active fault를 다시 확인한다.
4. protocol, seeded order manifest, pilot에서 고정한 repeat/cooldown/thermal/precision
   조건과 `formal_execution_allowed` gate를 확인한다.
5. admission이 만든 campaign/cell/attempt identity가 run `research_identity`에 연결되고,
   측정·실패·cleanup evidence가 보존되는지 확인한다.

하나라도 누락되거나 drift가 있으면 승인하지 않는다. 자동으로 모델을 다시 받거나 power
mode를 바꾸어 lock을 맞추지 않으며, 수정된 source/runtime은 새 lock 검토와 필요한 pilot을
거친다. 현재 승인 상태와 남은 제한은
[formal experiment identity lock](research/experiment-identity-lock.md)을 기준으로 한다.

## 13. Operator acceptance checklist

- Controller quick start, loopback Dashboard, Worker-only inference 경계를 확인했다.
- 선택 전략의 logical/physical 호출 배수와 graph metric label을 확인했다.
- 동일 platform/cohort가 아닌 결과에 exploratory 표시가 있다.
- prompt/response storage 정책과 private result permission을 확인했다.
- power mode와 Pi power-integrity evidence를 결과와 함께 보존했다.
- RPC run이면 load/LAN 비용을 별도 보고하고 종료 후 cleanup check가 통과했다.
- publication 후보면 smoke/pilot/formal 디렉터리와 분석을 섞지 않았다.
- formal 후보면 모든 lock/admission gate와 campaign identity를 확인했다.
