# LLM Cluster Benchmark

Raspberry Pi와 NVIDIA Jetson Orin Nano를 각각 최대 4개 노드로 구성해, 로컬 LLM의
단일 노드 성능과 요청 분산 클러스터 성능을 재현 가능하게 비교하는 프로젝트다.

## 무엇을 측정하나

- **단일 노드:** prompt 처리 속도, 생성 속도, 메모리·온도·전력
- **클러스터:** 1·2·4 노드 및 동시성 1·2·4·8에서 처리량, 성공률, p50/p95 지연
- **공정성:** 같은 모델, GGUF 양자화, 프롬프트, 출력 길이 및 네트워크 조건

클러스터는 모델을 노드에 분할하는 방식이 아니라 모델 사본을 노드별로 실행한 뒤
요청을 round-robin으로 배분한다. 따라서 핵심 비교값은 한 요청의 속도보다 동시
사용자 처리량이다.

## 빠른 시작

1. 세부 실험 조건은 [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)를 확인한다.
2. 각 노드에서 `benchmark/scripts/run_llama_bench.sh`의 모델 경로와 실행 옵션을 조정해 실행한다.
3. 각 노드에서 `llama-server`를 실행하고, 1·2·4 노드를 프록시의 백엔드에 등록한다.
4. 측정 PC에서 다음과 같이 부하 테스트를 실행한다.

```bash
./benchmark/load_test.py \
  --url http://BENCHMARK_PROXY:8080/v1/chat/completions \
  --concurrency 4 --requests 20 --output results/pi_2nodes_c4.csv
```

## 구조

```text
.
├── EXPERIMENT_PLAN.md       # 가설, 변수, 측정 절차, 결과 표
└── benchmark/
    ├── load_test.py         # OpenAI 호환 API 클러스터 부하 테스트
    └── scripts/
        └── run_llama_bench.sh # 노드별 llama.cpp microbenchmark
```

## 상태

초기 벤치마크 프레임워크 단계다. 실제 결과 CSV와 하드웨어 메타데이터는 `results/`에
저장하되, 원본 결과는 버전 관리에 포함하지 않는다.
