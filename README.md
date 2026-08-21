# LLM Cluster Benchmark

macOS Controller가 NVIDIA Jetson 및 Raspberry Pi Worker를 관리하고, 재현 가능한
LLM benchmark를 실행·보존·비교하는 프로젝트입니다. Controller는 inference
participant가 아니며 GGUF 모델이나 CUDA/OpenBLAS runtime을 로드하지 않습니다.

## Controller quick start

```bash
git clone https://github.com/Phjrab/llm-cluster-benchmark
cd llm-cluster-benchmark
./scripts/setup-controller
llm-cluster start
llm-cluster status
```

Dashboard 기본 주소는 `http://127.0.0.1:8080/`입니다.

```bash
llm-cluster logs
llm-cluster restart
llm-cluster stop
```

`llm-cluster`는 로컬 Dashboard만 관리합니다. 원격 Worker 제어 및 점검은 다음
호환 CLI에서 수행합니다.

```bash
python -m cluster.clusterctl --help
```

## Worker setup

프로젝트를 각 Jetson 또는 Raspberry Pi에 배포한 뒤 Worker에서 실행합니다.

```bash
./scripts/setup-worker
```

시스템 패키지는 고정 allowlist와 passwordless `sudo -n` 조건에서만 자동 설치하고,
Python/inference 패키지는 프로젝트의 `.venv`에 설치합니다. Jetson은 CUDA,
Raspberry Pi 5는 OpenBLAS backend를 검증하며 pinned native llama.cpp RPC runtime도
준비합니다.

## Documentation

- [Cluster operation guide](cluster/README.md)
- [Refactor and acceptance reports](docs/refactor/)
- [Formal experiment identity lock](docs/research/experiment-identity-lock.md)
- [Locked research configuration](config/research/)

과거 단일 Jetson benchmark, standalone chat server, notebook, plotting script 및
historical output은 현재 제품 트리에서 제거되었습니다. 삭제 전 내용과 커밋은 Git
history에 보존되며 history rewrite 없이 복구할 수 있습니다.
