# Formal Experiment Identity Lock

이 문서는 `formal-study-v1` 정식 실험 캠페인의 불변 조건을 사람이 검토할 수 있게 설명한다. 관측 기준 시각은 2026-08-21 11:57~11:59 UTC이고, benchmark 코드 기준점은 `e70f63e797b9952ad1073ce16f556f0ff874b43b`이다. 이 Phase에서는 모델 다운로드, 추론, 전력 모드 변경, 재부팅을 하지 않았다.

## 1. Selected formal models

최소 후보 집합은 네 개다. 모두 source repository, commit, 단일 GGUF filename, 크기, SHA-256, quantization, license가 고정됐지만 실제 Worker와 runtime metadata 검증이 끝나지 않아 `source_locked` 상태다. `approved` 모델은 아직 없다.

| model key | 역할 | 현재 상태 |
|---|---|---|
| `qwen2.5-1.5b-instruct-q4-k-m-official` | Pi/Jetson 공통 소형, Qwen scaling | source_locked |
| `qwen2.5-3b-instruct-q4-k-m-official` | Jetson 중형, Qwen scaling | source_locked |
| `granite-3.3-2b-instruct-q4-k-m-official` | Pi/Jetson 공통 소형, cross-family | source_locked |
| `granite-3.3-8b-instruct-q4-k-m-official` | Granite scaling, RPC upper-bound | source_locked |

정식 Matrix는 `approved`만 참조한다. 따라서 현재 formal eligible model 수는 0이다.

## 2. Rejected/deferred models

- 여섯 Worker에 설치된 Llama 3.2 1B 파일은 크기 `807694464`, SHA-256 `6f85a640…df83`로 서로 같다. 그러나 community GGUF repository/revision, converter revision, chat template hash, tokenizer metadata hash, 라이선스 수락이 비어 있어 formal campaign에서는 `deferred`다.
- Jetson 02의 Qwen2.5 1.5B 설치본은 크기 `986048768`, SHA-256 `1adf0b11…3370`이다. 잠근 공식 binary(`1117320736`, `6a1a2eb6…407e`)와 일치하지 않아 사용하지 않는다.
- catalog의 Qwen2.5 7B 단일 파일 항목은 pinned official repository의 Q4_K_M multipart 배포와 맞지 않아 현 lock에서 `rejected`다.
- 나머지 catalog 후보는 이번 연구 질문에 중복되거나 exact revision/checksum/license/converter 검토가 부족해 선정하지 않았다. 일반 catalog는 삭제하거나 대체하지 않았다.

## 3. Exact model binary provenance

정확한 repository/revision/file/size/SHA/license는 [`model_lock.json`](../../config/research/model_lock.json)과 [provenance 표](model-provenance-table.md)에 있다. Source checksum만 고정된 항목은 `source_locked`이며 Worker 파일을 직접 확인하기 전에는 승격하지 않는다. Official GGUF 제공자가 converter commit을 공개하지 않은 경우 `unpublished_by_provider`로 명시했으며, binary SHA가 identity 기준이다. 그래도 chat template/tokenizer metadata를 추출하고 고정하기 전에는 `approved`가 아니다.

## 4. Prompt set

`prompt_set_version=1`은 한국어 일반 생성, 영어 제약 생성, 산술 reasoning, Python coding의 네 prompt만 포함한다. 각 SHA-256은 추가 newline 없이 정확한 UTF-8 text byte를 기준으로 한다. 텍스트 변경 시 기존 ID를 재사용하지 않고 새 ID 또는 prompt set version을 사용한다.

## 5. Inference parameters

공통 profile은 `deterministic-throughput-v1`이다.

- `n_ctx=4096`, `max_tokens=128`, `temperature=0.0`, `top_p=0.9`, `seed=42`
- streaming 활성, conversation history 없음, timeout 600초
- `top_k`, `min_p`, `repeat_penalty`, `stop_sequences`는 현재 Worker API가 노출하지 않으므로 `unsupported_by_worker_api`로 명시한다.
- chat template는 잠긴 GGUF의 실제 template hash와 일치해야 한다. 아직 hash가 없으므로 이것이 model approval blocker다.
- 요청 20개/시나리오, logical concurrency 4, Worker당 warm-up 1개, 모델 사이 unload와 3초 cooling, uniform actual config 요구

숫자는 현재 checked-in experiment default와 Worker load schedule을 기준으로 고정했다. Worker가 memory fallback으로 `n_ctx` 또는 `n_batch`를 낮추면 동일 Matrix cell로 인정하지 않는다.

## 6. Platform-specific parameters

- Jetson: CUDA verified, threads 6, requested/actual batch 256, GPU layers 30, `jetson_clocks=OFF`, formal power target `MAXN_SUPER`.
- Raspberry Pi 5: OpenBLAS verified, threads 4, requested/actual batch 256, GPU layers 0.
- RPC: native llama.cpp commit `f49e9178767d557a522618b16ce8694f9ddac628`, layer/auto split, explicit Jetson coordinator, coordinator GPU layers `all`, 최소 2대. 정식 비교는 같은 platform끼리 구성하며 Jetson/Pi mixed RPC는 exploratory로만 분류한다.

Prompt, seed, temperature, max tokens, context, model binary와 chat template는 platform 사이에도 동일해야 한다.

## 7. Runtime versions

Controller는 macOS 26.3 arm64, Python 3.13.2, FastAPI 0.136.0, Uvicorn 0.44.0, Pydantic 2.13.1, psutil 7.2.2다. Controller는 participant가 아니다.

Jetson은 Ubuntu 22.04.5/Python 3.10.12/llama-cpp-python 0.3.20 CUDA이며 runtime fingerprint는 모두 `05e2c27b3bf0eff2`다. Worker 01은 L4T R36.4.7, Worker 02·03은 R36.5.0이다. Pi는 Ubuntu 24.04.4/Python 3.12.3/llama-cpp-python 0.3.20 OpenBLAS, runtime fingerprint `b4387053e655722a`다. 여섯 Worker 모두 pinned RPC build READY를 보고했다.

배포 디렉터리에 `.git` metadata가 없어 모든 Worker deployment Git commit과 clean state는 `unverified`다. 코드 동기화를 다시 수행하고 별도 배포 fingerprint를 기록하기 전에는 formal runtime identity가 완성되지 않는다.

## 8. Worker hardware state

Inventory participant는 Jetson Orin Nano Super 3대와 Raspberry Pi 5 8GB 3대다. 정확한 RAM, lock 시점 disk free, OS/kernel/backend/power 관측은 [`runtime_lock.json`](../../config/research/runtime_lock.json)에 있다. credential, serial number, MAC address는 기록하지 않았다.

모든 Worker는 aarch64이고 environment-check에서 LLM backend와 pinned RPC runtime READY를 보고했다. 이는 기능 준비도이지 model approval을 뜻하지 않는다.

## 9. Jetson power-mode consistency

현재 모드는 다음과 같다.

- `jetson-worker-01`: MAXN_SUPER
- `jetson-worker-02`: MAXN_SUPER
- `jetson-worker-03`: 15W

따라서 세 대를 같은 publication comparison에 넣는 조건은 `formal_eligible=false`, `reason=JETSON_POWER_MODE_MISMATCH`다. 또한 Worker 01과 02/03의 L4T가 다르다. operator는 정식 실험 전에 지원되는 동일 power mode를 직접 선택하고, 필요하다면 runtime version을 정렬한 뒤 다시 lock해야 한다. 이 Phase는 `nvpmodel`을 바꾸거나 재부팅하지 않았다.

## 10. Raspberry Pi power-quality policy

`pi-worker-02`와 `pi-worker-03`은 `get_throttled=0x50000`: 현재 fault bit는 없고 과거 undervoltage/throttling 이력만 있어 `warning`이다. `pi-worker-04`는 `0x0`으로 `clean`이다.

History warning은 Worker ready, inference, experiment 생성 또는 formal eligibility를 차단하지 않는다. 측정 중 active bit가 생기면 해당 run의 measurement quality를 `degraded`로 기록한다. warning/degraded 결과는 삭제하지 않고 분석 cohort에서 구분한다.

## 11. Fixed, controlled, factor, observed variables

| 분류 | 변수 |
|---|---|
| Fixed | prompt text/hash, exact model binary/SHA/quantization/revision, chat template, sampling, max tokens, context, benchmark code commit, prompt order |
| Platform-controlled | threads, GPU layers, batch, CUDA/OpenBLAS backend, Jetson power mode, RPC runtime/split policy |
| Experimental factor | platform, node count, strategy, model, RPC/non-RPC |
| Observed covariate | temperature, Pi power quality, available RAM/storage, network state, instantaneous power |

Fixed identity가 달라진 run은 같은 Matrix cell에 합치지 않는다.

## 12. Formal eligibility blockers

현재 blockers는 다음과 같다.

1. 모든 후보 모델이 `source_locked`이며 `approved`가 아니다.
2. Qwen2.5 1.5B 설치본이 locked official binary와 불일치한다.
3. 후보 binary 설치/Worker checksum verification이 없다.
4. chat template와 tokenizer metadata hash가 없다.
5. Qwen2.5 3B 연구 라이선스 수락이 기록되지 않았다.
6. Worker deployment commit/clean state가 `unverified`다.
7. Jetson power mode와 L4T version이 통일되지 않았다.

Pi 02/03의 power history는 non-blocking warning이다.

## 13. Remaining unresolved identities

- 공식 GGUF 내부 architecture/chat template/tokenizer metadata 추출 및 hash
- 네 binary의 controlled installation과 각 Worker SHA 검증
- Qwen Research License의 프로젝트 수락 결정
- Worker 배포 source commit/fingerprint
- Jetson power/runtime 정렬 후 재관측
- formal Matrix config에 다섯 additive trace field를 기록하는 작업(FOLLOWUP 09 범위)

## 14. Lock fingerprint

- lock ID: `formal-study-v1`
- lock version: `1`
- prompt set version: `1`
- lock set SHA-256: `4a03235dd7ae27a0a915e918c583df99aad2011e99f0baf1a9f61a3dcb5c3ab4`
- benchmark source baseline: `e70f63e797b9952ad1073ce16f556f0ff874b43b`

Fingerprint는 네 JSON 문서를 filename으로 묶고, key를 정렬한 canonical JSON을 UTF-8로 SHA-256 처리한다. `created_at`, `checked_at`, `observed_at`, `received_at`, `verified_at`, `lock_sha256` 필드는 제외한다. 따라서 key order와 관측 timestamp는 hash를 바꾸지 않지만 연구 조건 값의 변경은 hash를 바꾼다.

현재 result schema/`requests.csv`에는 손대지 않았다. 후속 formal Matrix는 `experiment_lock_id`, `experiment_lock_sha256`, `model_lock_entry`, `prompt_set_version`, `runtime_lock_version`을 additive하게 보존해야 한다.
