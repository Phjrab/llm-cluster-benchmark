# Formal Campaign Model Provenance

기준 lock은 `formal-study-v1`, SHA-256 `4a03235dd7ae27a0a915e918c583df99aad2011e99f0baf1a9f61a3dcb5c3ab4`다. 이 표의 checksum은 pinned Hugging Face revision의 LFS metadata에서 확인한 exact official file 값이다. Worker 실제 파일과 일치하기 전 상태는 `source_locked`다.

| model key | repository @ commit | exact GGUF | bytes | SHA-256 | quant | license | status |
|---|---|---:|---:|---|---|---|---|
| `qwen2.5-1.5b-instruct-q4-k-m-official` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF@91cad51170dc346986eccefdc2dd33a9da36ead9` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | 1117320736 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` | Q4_K_M | Apache-2.0 | source_locked |
| `qwen2.5-3b-instruct-q4-k-m-official` | `Qwen/Qwen2.5-3B-Instruct-GGUF@7dabda4d13d513e3e842b20f0d435c732f172cbe` | `qwen2.5-3b-instruct-q4_k_m.gguf` | 2104932768 | `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d` | Q4_K_M | Qwen Research License; acceptance pending | source_locked |
| `granite-3.3-2b-instruct-q4-k-m-official` | `ibm-granite/granite-3.3-2b-instruct-GGUF@7cdf86ccd1f1bb3491c9b7017b033f2e51367397` | `granite-3.3-2b-instruct-Q4_K_M.gguf` | 1545303328 | `ac71e9e32c0bea919b409c5918f69ca74339854b0319c5065e4e9fb6d95c4852` | Q4_K_M | Apache-2.0 | source_locked |
| `granite-3.3-8b-instruct-q4-k-m-official` | `ibm-granite/granite-3.3-8b-instruct-GGUF@e40e9dd739c7be00fa965c16ce167088190ce114` | `granite-3.3-8b-instruct-Q4_K_M.gguf` | 4942873344 | `77bcee066a76dcdd10d0d123c87e32c8ec2c74e31b6ffd87ebee49c9ac215dca` | Q4_K_M | Apache-2.0 | source_locked |

## Observed installed binaries

| installed catalog ID | Workers | bytes | SHA-256 | decision |
|---|---|---:|---|---|
| `llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf` | all 6 | 807694464 | `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83` | deferred: community provenance/runtime metadata/license unresolved |
| `qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` | jetson-worker-02 | 986048768 | `1adf0b11065d8ad2e8123ea110d1ec956dab4ab038eab665614adba04b6c3370` | rejected for formal use: official lock mismatch |

## Catalog reconciliation

- 그대로 일반 catalog로 유지: catalog는 추천/발견 용도이고 lock은 캠페인 불변 집합이다.
- Metadata 보강 필요: 네 선택 후보 모두 exact revision/checksum/license를 lock에서 보강했다. 향후 catalog 반영은 별도 변경이다.
- Community provenance 검토 필요: Llama 3.2 1B/3B, Phi-4 community GGUF.
- 라이선스 검토 필요: Qwen2.5 3B, LFM, Gemma, Meta Llama 계열.
- 정식 실험 제외: 현 catalog의 단일-file Qwen2.5 7B는 pinned official Q4_K_M multipart artifact와 identity가 다르다.

`approved` 승격 조건은 expected binary를 필요한 Worker에 설치한 뒤 실제 SHA-256 일치, license acceptance, GGUF chat-template/tokenizer metadata hash, tested runtime identity를 모두 기록하는 것이다.
