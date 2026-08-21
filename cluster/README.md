# Edge LLM Controller/Worker Cluster

이 디렉터리는 macOS Controller 한 대가 여러 대의 Jetson Orin 또는
Raspberry Pi 5 Worker를 관리하고, 동일 GGUF 워크로드를 재현 가능하게 비교하는
클러스터 벤치마크 런타임이다.

Controller는 대시보드·스케줄링·결과 저장만 담당한다. 모델을 로드하거나 추론에
참여하지 않는다. Jetson과 Raspberry Pi만 Worker이며, 모델 분할 RPC의 coordinator도
선택된 Worker 중 한 대다. 과거 CSV의 `head` 행은 읽기·마이그레이션 호환용일 뿐 새
실험의 참여 노드가 아니다.

## 지원 구성

| 역할/플랫폼 | 런타임 | 필수 조건 | 추론 참여 |
|---|---|---|---|
| macOS Controller | FastAPI/Uvicorn Dashboard | Python 3.10+ | 안 함 |
| Jetson Orin Worker | CUDA `llama-cpp-python` | JetPack/CUDA가 설치된 64-bit L4T | 함 |
| Raspberry Pi 5 Worker | OpenBLAS `llama-cpp-python` | 64-bit Raspberry Pi OS/Ubuntu, `aarch64` | 함 |

Worker 등록 수에는 고정 상한이 없다. 대시보드의 전체·Jetson·Raspberry Pi 탭에서
플랫폼별로 관리하며, Pi의 복제형 실험은 `n_gpu_layers=0`을 사용한다.
Jetson과 Pi 혼합 실험은 탐색 목적으로 허용하지만, 동일 가속기 성능 비교는 플랫폼별
실험으로 분리하는 편이 해석하기 쉽다.

## macOS Controller 설치

```bash
git clone https://github.com/Phjrab/llm-cluster-benchmark
cd llm-cluster-benchmark
./scripts/setup-controller
llm-cluster start
llm-cluster status
```

`setup-controller`는 프로젝트 `.venv`에 Controller 의존성만 설치하고, 비어 있는
Worker 인벤토리를 생성하며, `~/.local/bin/llm-cluster` 심볼릭 링크를 설치한다.
CUDA, OpenBLAS inference, llama-cpp-python, jtop, JetPack 또는 Worker 서비스를
Controller에 설치하지 않는다. 기본 대시보드 주소는 `http://127.0.0.1:8080/`이다.

```bash
llm-cluster start
llm-cluster stop
llm-cluster restart
llm-cluster status
llm-cluster logs
```

자동 시작용 systemd/launchd unit은 만들지 않는다. 위 명령은 Controller Dashboard만
관리하며 원격 Worker 프로세스를 임의로 종료하지 않는다.

## Worker 연결과 환경 구성

대시보드의 `+ 워커 연결`은 Controller가 붙은 RFC1918 사설 LAN에서 SSH 포트가 열린
후보를 찾는다. 사용자가 선택한 장치만 등록되며 미리 만든 1대/2대/4대 토폴로지는 없다.

1. Jetson 또는 Pi에서 SSH를 켠다.
2. 대시보드가 보여 주는 Controller 공개 키를 Worker의
   `~/.ssh/authorized_keys`에 한 번 등록한다.
3. 사용자명과 Worker 프로젝트 절대경로를 확인한다.
4. `SSH 환경 확인`으로 보드, OS, `aarch64`, 디스크, NTP, 누락 패키지를 점검한다.
5. `저장 후 자동 준비`를 실행한다.

자동 준비 순서는 시스템 의존성 확인 → 코드 동기화 → 프로젝트 `.venv` 생성 →
플랫폼별 llama-cpp-python 빌드 → 선택 모델 동기화 → Worker API 시작이다. 대시보드는
SSH/sudo 비밀번호를 받거나 저장하지 않는다. apt는 고정 allowlist와 `sudo -n`에서만
자동 실행하며, 비밀번호가 필요하면 Worker에서 실행할 명령만 안내한다. JetPack,
CUDA, OS 이미지는 자동 설치하지 않는다.

직접 배포한 Worker에서는 다음 명령을 사용할 수 있다.

```bash
./scripts/setup-worker
./cluster/worker_setup.sh --plan-only --platform jetson
./cluster/worker_setup.sh --plan-only --platform raspberry-pi
```

환경 보고서는 `.run/cluster/environment/<worker>.json`에 저장된다. `READY`는 패키지와
플랫폼별 backend 준비 상태를 뜻하며, 선택 모델의 실제 로드 가능 여부는 실험 시작 전
model preflight에서 다시 확인한다. Jetson 고급 GPU/전력 지표는 jtop 서비스가 없으면
psutil 기본 지표로 안전하게 저하된다.

## 등록 데이터와 결과

커밋하지 않는 런타임 데이터:

- `.run/cluster/nodes.local.csv`: 등록된 Worker 인벤토리
- `.run/cluster/settings.json`: 선택형 Dashboard/Worker 인증 설정
- `.run/cluster/dashboard.token`, `worker.token`: `0600` 토큰 파일
- `.run/cluster/environment/`: Worker 환경 보고서
- `.run/cluster/experiments/`: 저장된 실험 정의
- `.run/cluster/results/`: run/suite 원시 결과와 요약
- `.run/controller/jobs/`: 재시작 가능한 durable job 상태와 이벤트

각 run은 `config.json`, `events.jsonl`, `responses.jsonl`, `requests.csv`,
`summary.json`을 갖는다. 새 파일은 사용자 전용 권한으로 생성한다. `requests.csv`의 기존
19개 metric 컬럼은 유지하고, prompt/응답/구조화 실패는 `responses.jsonl`에 별도로
보존한다.

## 실험 방식

| 방식 | 모델 배치 | 요청 흐름 | 검증 목적 |
|---|---|---|---|
| `single_node` | Worker 1대에 전체 모델 | 모든 요청을 한 Worker가 처리 | 단일 장치 기준선 |
| `replicated_round_robin` | 각 Worker에 전체 모델 복제 | 논리 요청을 round-robin 분배 | 다중 사용자 처리량 |
| `broadcast_compare` | 각 Worker에 전체 모델 복제 | 같은 요청을 모든 Worker에 전송 | 지연·정확 응답 일치 |
| `node_sweep` | 각 Worker에 전체 모델 복제 | 1대→N대 시나리오 반복 | speedup·scaling efficiency |
| `model_parallel_rpc` | 모델 하나를 여러 Worker 장치에 분할 | 각 토큰 계산에 여러 Worker 참여 | 메모리 확장·LAN 비용 |

기본은 기존 의미와 호환되는 `replicated_round_robin`이다. 이 방식은 모델을 나누지
않는다. `broadcast_compare`의 물리 호출 수는 논리 요청 수 × Worker 수이며,
`model_parallel_rpc`의 처리량은 여러 Worker가 함께 만든 단일 응답 처리량이다.

다중 모델을 선택하면 선택 순서대로 독립 run을 실행한다. 모델 사이에는 unload와 냉각
대기를 적용하고, suite 상태·미실행 모델·정리 실패를 영속화한다. 취소와 Dashboard 재시작
후에도 durable job/suite 기록으로 상태를 복구한다.

## 모델 분할 RPC

RPC는 고정 llama.cpp 커밋으로 별도 native runtime을 준비한다. 선택된 Worker 중 명시된
`rpc_coordinator_node`가 GGUF를 열고 나머지 Worker의 RPC 장치를 사용한다. Controller는
coordinator나 계산 장치가 아니다.

```bash
python -m cluster.clusterctl \
  --node edge-worker-01 --node edge-worker-02 prepare-rpc
```

llama.cpp RPC는 인증되지 않은 실험용 프로토콜이다. 신뢰하는 사설 LAN에서 실험 동안만
열고 모든 성공·실패·취소 경로에서 종료한다. Worker API 인증을 켠 상태에서는 RPC 실험을
차단한다. Jetson+Pi 혼합 RPC는 허용하지만 네트워크와 가장 느린 장치가 병목이 될 수 있다.

## CLI 실험

```bash
python -m cluster.clusterctl inventory
python -m cluster.clusterctl status
python -m cluster.clusterctl discover
python -m cluster.clusterctl environment-check
python -m cluster.clusterctl --node edge-worker-01 environment-install --confirmed

cp cluster/config/experiment_defaults.json .run/cluster/experiment.json
python -m cluster.benchmark.runner \
  --config .run/cluster/experiment.json \
  --inventory .run/cluster/nodes.local.csv
```

예제의 `node_names`는 실제 등록 이름으로 바꾼다. Worker-only 인벤토리가 정상 형식이며,
legacy head 행을 새로 만들 필요가 없다.

## 보안과 재현성

- SSH는 `BatchMode`와 사용자 소유 `0600` identity 파일만 사용한다.
- 공인 IP, hostname, CGNAT, link-local 주소는 Worker 인벤토리에서 거부한다.
- 원격 명령은 고정 argv와 shell escaping을 사용하며 `shell=True`를 사용하지 않는다.
- apt 자동 설치는 고정 allowlist와 `sudo -n`만 허용한다.
- 모델 경로와 프로젝트 경로는 traversal/광범위 루트를 거부한다.
- Dashboard와 Worker 인증은 신뢰 LAN 편의를 위해 기본 꺼짐이며 설정에서 켤 수 있다.
- Dashboard 토큰은 URL query로 받지 않고 `X-Cluster-Token` 헤더로만 전송한다.
- Worker 토큰은 브라우저에 보내지 않고 Controller와 Worker 사이에서만 사용한다.
- 모델은 사용자가 명시적으로 설치/동기화하며 실험 시작이 자동 다운로드하지 않는다.
- 코드 동기화는 `.git`, `.venv`, `models`, `outputs`, `.run`을 제외하고 `--delete`를 쓰지 않는다.

## Legacy 호환 경계

`cluster/setup_head.sh`, `cluster/config/nodes.example.csv`, 루트의 단일 Jetson 벤치 및
standalone chat 스크립트는 기존 설치를 읽고 이전 워크플로를 재현하기 위한 compatibility
surface다. 새 Mac Controller 설치에는 `scripts/setup-controller`와 Worker-only 인벤토리를
사용한다. Legacy `head` 행은 변환 시 제외되며 Controller 또는 추론 Worker로 자동 승격하지
않는다.
