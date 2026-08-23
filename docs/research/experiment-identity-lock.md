# Formal Experiment Identity Lock

이 문서는 `formal-study-v1` version 3 정식 실험 캠페인의 불변 조건을 사람이 검토할 수 있게 설명한다. 하드웨어·런타임 재관측 시각은 2026-08-23 UTC이고, 검증된 Worker 코드 기준점은 `6dc88f8b2feeb8d7531d966dc11e4e25bc66c093`이다. 모델 승인 상태는 유지하면서 전력·커널·L4T가 같은 Worker만 하나의 formal cohort로 묶었다. 자동 전력 변경, 재부팅, OS 업데이트는 수행하지 않았다.

## 1. Selected formal models

최소 후보 집합은 네 개다. Qwen2.5 1.5B와 Granite 3.3 2B는 exact official binary, GGUF metadata, 6대 Worker checksum, 플랫폼별 생성 스모크를 통과해 `approved`다. 나머지 두 후보는 자동 승격하지 않았다.

| model key | 역할 | 현재 상태 |
|---|---|---|
| `qwen2.5-1.5b-instruct-q4-k-m-official` | Pi/Jetson 공통 소형, Qwen scaling | approved |
| `qwen2.5-3b-instruct-q4-k-m-official` | Jetson 중형, Qwen scaling | source_locked |
| `granite-3.3-2b-instruct-q4-k-m-official` | Pi/Jetson 공통 소형, cross-family | approved |
| `granite-3.3-8b-instruct-q4-k-m-official` | Granite scaling, RPC upper-bound | source_locked |

정식 Matrix는 `approved`만 참조한다. 현재 cross-platform·cross-family 승인 모델은 2개다.

## 2. Rejected/deferred models

- 여섯 Worker에 설치된 Llama 3.2 1B 파일은 크기 `807694464`, SHA-256 `6f85a640…df83`로 서로 같다. 그러나 community GGUF repository/revision, converter revision, chat template hash, tokenizer metadata hash, 라이선스 수락이 비어 있어 formal campaign에서는 `deferred`다.
- Jetson 02의 기존 대문자 경로 Qwen2.5 1.5B 설치본은 크기 `986048768`, SHA-256 `1adf0b11…3370`으로 남아 있으나 사용하지 않는다. 별도 canonical 소문자 경로에 공식 binary(`1117320736`, `6a1a2eb6…407e`)를 설치하고 승인했다.
- catalog의 Qwen2.5 7B 단일 파일 항목은 pinned official repository의 Q4_K_M multipart 배포와 맞지 않아 현 lock에서 `rejected`다.
- 나머지 catalog 후보는 이번 연구 질문에 중복되거나 exact revision/checksum/license/converter 검토가 부족해 선정하지 않았다. 일반 catalog는 삭제하거나 대체하지 않았다.

## 3. Exact model binary provenance

정확한 repository/revision/file/size/SHA/license는 [`model_lock.json`](../../config/research/model_lock.json)과 [provenance 표](model-provenance-table.md)에 있다. Source checksum만 고정된 항목은 `source_locked`이며 Worker 파일을 직접 확인하기 전에는 승격하지 않는다. Official GGUF 제공자가 converter commit을 공개하지 않은 경우 `unpublished_by_provider`로 명시했으며, binary SHA가 identity 기준이다. 승인된 두 모델은 dependency-free GGUF parser로 architecture, chat template, tokenizer metadata를 실제 파일에서 추출해 canonical SHA-256으로 고정했다.

## 4. Prompt set

`prompt_set_version=1`은 한국어 일반 생성, 영어 제약 생성, 산술 reasoning, Python coding의 네 prompt만 포함한다. 각 SHA-256은 추가 newline 없이 정확한 UTF-8 text byte를 기준으로 한다. 텍스트 변경 시 기존 ID를 재사용하지 않고 새 ID 또는 prompt set version을 사용한다.

## 5. Inference parameters

공통 profile은 `deterministic-throughput-v1`이다.

- `n_ctx=4096`, `max_tokens=128`, `temperature=0.0`, `top_p=0.9`, `seed=42`
- streaming 활성, conversation history 없음, timeout 600초
- `top_k`, `min_p`, `repeat_penalty`, `stop_sequences`는 현재 Worker API가 노출하지 않으므로 `unsupported_by_worker_api`로 명시한다.
- chat template는 잠긴 GGUF의 실제 template hash와 일치해야 한다. 두 승인 모델은 lock의 hash와 Worker actual metadata가 일치한다.
- 요청 20개/시나리오, logical concurrency 4, Worker당 warm-up 1개, 모델 사이 unload와 3초 cooling, uniform actual config 요구

숫자는 현재 checked-in experiment default와 Worker load schedule을 기준으로 고정했다. Worker가 memory fallback으로 `n_ctx` 또는 `n_batch`를 낮추면 동일 Matrix cell로 인정하지 않는다.

## 6. Platform-specific parameters

- Jetson: CUDA verified, threads 6, requested/actual batch 256, GPU layers 30, `jetson_clocks=OFF`. 각 run은 lock에 기록된 단일 runtime cohort의 exact power mode를 따라야 하며 cohort 간 결과를 한 cell로 합치지 않는다.
- Raspberry Pi 5: OpenBLAS verified, threads 4, requested/actual batch 256, GPU layers 0.
- RPC: native llama.cpp commit `f49e9178767d557a522618b16ce8694f9ddac628`, layer/auto split, explicit Jetson coordinator, coordinator GPU layers `all`, 최소 2대. 정식 비교는 같은 platform끼리 구성하며 Jetson/Pi mixed RPC는 exploratory로만 분류한다.

Prompt, seed, temperature, max tokens, context, model binary와 chat template는 platform 사이에도 동일해야 한다.

## 7. Runtime versions

Controller는 macOS 26.3 arm64, Python 3.13.2, FastAPI 0.136.0, Uvicorn 0.44.0, Pydantic 2.13.1, psutil 7.2.2다. Controller는 participant가 아니다.

Jetson은 Ubuntu 22.04.5/Python 3.10.12/llama-cpp-python 0.3.20 CUDA이며 runtime fingerprint는 모두 `05e2c27b3bf0eff2`다. Worker 01은 L4T R36.4.7, Worker 02·03은 R36.5.0이다. Pi는 Ubuntu 24.04.4/Python 3.12.3/llama-cpp-python 0.3.20 OpenBLAS, runtime fingerprint `b4387053e655722a`다. 여섯 Worker 모두 pinned RPC build READY를 보고했다.

배포 디렉터리에 `.git` metadata를 복사하지 않고 Phase 01 manifest로 source identity를 검증한다. 6대 모두 commit `6dc88f8…c093`, source tree `9136db62…1a59`, 158 source files, clean/verified 상태를 보고하며 플랫폼 cohort별 runtime fingerprint도 일치한다.

## 8. Worker hardware state

Inventory participant는 Jetson Orin Nano Super 3대와 Raspberry Pi 5 8GB 3대다. 정확한 RAM, lock 시점 disk free, OS/kernel/backend/power 관측은 [`runtime_lock.json`](../../config/research/runtime_lock.json)에 있다. credential, serial number, MAC address는 기록하지 않았다.

모든 Worker는 aarch64이고 environment-check에서 LLM backend와 pinned RPC runtime READY를 보고했다. 이는 기능 준비도이지 model approval을 뜻하지 않는다.

## 9. Jetson power-mode consistency

현재 모드는 다음과 같다.

- `jetson-worker-01`: MAXN_SUPER
- `jetson-worker-02`: MAXN_SUPER
- `jetson-worker-03`: 15W

세 장비 모두 `15W`, `25W`, `MAXN_SUPER`를 지원하고 `jetson_clocks=OFF`다. NVIDIA 문서상 MAXN_SUPER는 고정 watt budget이 없지만 최대 core/clock 구성을 제공하므로 대시보드 추천은 MAXN 계열을 먼저 선택하도록 수정했다. 다만 Worker 03의 변경에는 operator sudo가 필요하여 자동 우회하지 않았다.

정식 실행은 다음 세 Jetson cohort로 분리한다.

| cohort | Worker | L4T / kernel | power | formal 범위 |
|---|---|---|---|---|
| `jetson-orin-r3647-maxn-super-cuda` | 01 | R36.4.7 / 5.15.148 | MAXN_SUPER | single node |
| `jetson-orin-r3650-maxn-super-cuda` | 02 | R36.5.0 / 5.15.185 | MAXN_SUPER | single node |
| `jetson-orin-r3650-15w-cuda` | 03 | R36.5.0 / 5.15.185 | 15W | single node |

서로 다른 cohort를 하나의 homogeneous node-scaling cell로 합치는 것은 `RUNTIME_COHORT_MISMATCH`로 차단한다. Worker 03을 추후 MAXN_SUPER로 정렬하려면 해당 장비에서 `sudo /usr/sbin/nvpmodel -m 2`를 실행하고 재관측·re-lock해야 한다. 자동 재부팅은 허용하지 않는다.

## 10. Raspberry Pi power-quality policy

재부팅 후 기준선에서 `pi-worker-02`는 `get_throttled=0x50000`: 현재 fault bit는 없고 과거 undervoltage/throttling 이력만 있어 `warning`이다. `pi-worker-03`과 `pi-worker-04`는 `0x0`으로 `clean`이다. 세 장비 모두 Ubuntu 24.04.4, kernel 6.8.0-1061, OpenBLAS runtime fingerprint `b4387053e655722a`로 하나의 3-node formal cohort다.

History warning은 Worker ready, inference, experiment 생성 또는 formal eligibility를 차단하지 않는다. 측정 중 active bit가 생기면 해당 run의 measurement quality를 `degraded`로 기록한다. warning/degraded 결과는 삭제하지 않고 분석 cohort에서 구분한다.

## 11. Fixed, controlled, factor, observed variables

| 분류 | 변수 |
|---|---|
| Fixed | prompt text/hash, exact model binary/SHA/quantization/revision, chat template, sampling, max tokens, context, benchmark code commit, prompt order |
| Platform-controlled | runtime cohort, threads, GPU layers, batch, CUDA/OpenBLAS backend, Jetson power mode, RPC runtime/split policy |
| Experimental factor | platform, runtime cohort, node count, strategy, model, RPC/non-RPC |
| Observed covariate | temperature, Pi power quality, available RAM/storage, network state, instantaneous power |

Fixed identity가 달라진 run은 같은 Matrix cell에 합치지 않는다.

## 12. Formal eligibility

모델 승인과 Worker deployment identity blocker는 해소됐다. Formal eligibility는 이제 **하나의 runtime cohort 내부에서만** 성립한다. Pi cohort는 최대 3-node homogeneous 실행이 가능하다. 현재 Jetson cohort는 각각 1대이므로 single-node formal run만 가능하며, 세 Jetson을 묶는 scaling/RPC 결과는 조건 정렬 전까지 exploratory다.

Qwen2.5 3B와 Granite 3.3 8B는 별도 후속 후보로 계속 `source_locked`이며 승인된 두 모델의 실행을 막지 않는다. Pi 02의 power history는 non-blocking warning이다.

## 13. Remaining unresolved identities

- Qwen 3B와 Granite 8B의 controlled installation 및 runtime metadata 검증(후속 후보)
- Qwen Research License의 프로젝트 수락 결정
- Jetson 03을 MAXN_SUPER로 맞추거나 세 Jetson의 L4T/kernel을 정렬한 뒤 더 큰 homogeneous cohort로 재관측
- formal Matrix config에 다섯 additive trace field를 기록하는 작업(FOLLOWUP 09 범위)

## 14. Lock fingerprint

- lock ID: `formal-study-v1`
- lock version: `3`
- prompt set version: `1`
- lock set SHA-256: `3d5c7f2429c42d02db60ab635a263db6edd86fb4527078eb8d5ff80fc30744a5`
- benchmark source baseline: `6dc88f8b2feeb8d7531d966dc11e4e25bc66c093`

Fingerprint는 네 JSON 문서를 filename으로 묶고, key를 정렬한 canonical JSON을 UTF-8로 SHA-256 처리한다. `created_at`, `checked_at`, `observed_at`, `received_at`, `verified_at`, `lock_sha256` 필드는 제외한다. 따라서 key order와 관측 timestamp는 hash를 바꾸지 않지만 연구 조건 값의 변경은 hash를 바꾼다.

현재 result schema/`requests.csv`에는 손대지 않았다. 후속 formal Matrix는 `experiment_lock_id`, `experiment_lock_sha256`, `model_lock_entry`, `prompt_set_version`, `runtime_lock_version`을 additive하게 보존해야 한다.
