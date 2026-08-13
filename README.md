# Jetson Head/Worker LLM Cluster

현재 Jetson Orin Nano를 **head**로 사용하고, 추후 연결할 최대 3대의 Jetson을
**worker**로 등록해 동일한 GGUF 모델을 분산 부하 방식으로 비교하는 환경이다.

## 역할

- `jetson-head`: 대시보드, 노드 제어, 모델 배포, 실험 스케줄링, 결과 집계 및 추론
- `jetson-worker-01`~`03`: 동일 모델 API 실행 및 추론 요청 처리
- 요청 분배: 선택 노드에 round-robin으로 배분
- 노드당 동시 추론: 1개를 권장한다. 현재 모델 관리자는 생성 중 노드 내부 잠금을 사용한다.

## 현재 head 기준 환경

- NVIDIA Jetson Orin Nano 8GB Developer Kit Super
- Ubuntu 22.04 / L4T 36.4.7
- CUDA 12.6, cuDNN 9.3, TensorRT 10.3
- Python 3.10 / `llama-cpp-python 0.3.20` CUDA 빌드
- 전원 모드 `MAXN_SUPER`

## 초기 설정

head에서 한 번 실행한다.

```bash
cd /home/jetson_orin_nano/project/llm/local_llm_bench
./cluster/setup_head.sh
```

생성되는 파일:

- `.run/cluster/nodes.local.csv`: 실제 노드 인벤토리(커밋하지 않음)
- `~/.ssh/id_ed25519_llm_cluster`: head → worker 전용 SSH 키
- `.run/cluster/dashboard.token`: 대시보드 시작 시 자동 생성되는 접근 토큰

SSH 공개 키를 각 worker의 `~/.ssh/authorized_keys`에 추가한다. 최초 한 번은 worker
콘솔에서 직접 추가하거나, 암호 로그인이 허용되어 있으면 다음 명령을 사용할 수 있다.

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_llm_cluster.pub USER@WORKER_IP
```

## 대시보드

```bash
./cluster/dashboard/start.sh
```

출력되는 URL에 접근 토큰이 포함된다. 기본 주소는 `http://HEAD_IP:8080`이다.
이 대시보드는 인증 토큰을 요구하지만 외부 인터넷에 공개하지 말고 신뢰할 수 있는
내부 LAN에서만 사용한다.

화면에서 다음을 수행할 수 있다.

- worker 추가·활성화와 SSH/API 연결 상태 실시간 확인
- 실험에 사용할 1·2·4개 노드 선택
- GPU, RAM, 전력, CPU/GPU 온도 실시간 확인
- 코드 및 선택 모델 동기화
- worker Python/CUDA 환경 점검과 설치
- API 서버 시작·중지 및 모델 로딩
- 모델, 요청 수, 동시성, 생성 길이, context, GPU 레이어, temperature,
  top-p, seed, 워밍업, 프롬프트 변경
- 진행 로그, 성공률, TTFT p50/p95, E2E p50/p95, cluster tokens/s 확인

중지:

```bash
./cluster/dashboard/stop.sh
```

## worker 연결 절차

1. 모든 Jetson에 같은 Ubuntu/L4T/CUDA 계열을 설치한다.
2. worker를 유선 Gigabit Ethernet에 연결하고 고정 IP 또는 DHCP 예약을 적용한다.
3. head SSH 공개 키를 worker에 등록한다.
4. 대시보드에서 worker 이름, IP, SSH 사용자, 프로젝트 경로를 등록한다.
5. 대상 worker를 선택하고 `코드 동기화`를 실행한다.
6. `선택 워커 준비`로 Python 패키지와 CUDA llama.cpp 환경을 점검/설치한다.
7. 실험 모델을 선택하고 `선택 모델 동기화`를 실행한다.
8. `서버 시작` 후 노드 카드가 ONLINE으로 바뀌는지 확인한다.
9. 실험 참여 노드와 파라미터를 선택해 벤치마크를 시작한다.

모델 동기화는 `rsync --partial --append-verify`를 사용하며 원격 모델을 삭제하지 않는다.
20GB 전체를 매번 복사하지 않고 실험에 사용할 모델만 선택해 전송하는 방식을 권장한다.

## CLI 제어

웹 UI와 같은 동작을 head 터미널에서도 수행할 수 있다.

```bash
python -m cluster.clusterctl inventory
python -m cluster.clusterctl status
python -m cluster.clusterctl doctor
python -m cluster.clusterctl sync-code
python -m cluster.clusterctl sync-models \
  --model qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
python -m cluster.clusterctl start
python -m cluster.clusterctl select-model \
  --model-id qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
  --n-ctx 1024 --n-gpu-layers 30
```

CLI 실험 설정 예시는 `cluster/config/experiment_defaults.json`을 복사해 사용한다.

```bash
cp cluster/config/experiment_defaults.json .run/cluster/experiment.json
# node_names 배열을 활성 노드 이름으로 채운 뒤 실행
python -m cluster.benchmark.runner --config .run/cluster/experiment.json
```

## 결과 및 재현성

각 실행은 `.run/cluster/results/<run-id>/`에 저장된다.

- `config.json`: 사용자가 요청한 모든 실험 파라미터
- `events.jsonl`: 모델 로딩, 경고, 요청 완료 등 시간순 이벤트
- `requests.csv`: 요청별 배정 노드, TTFT, E2E, 생성 토큰, 처리량, 오류
- `summary.json`: 클러스터 전체 및 노드별 요약

노드가 메모리 부족으로 `n_ctx` 또는 `n_gpu_layers`를 자동 하향하면 이를 기록한다.
기본값인 `동일 구성 강제`가 켜져 있으면 노드 간 실제 설정이 다를 때 실험을 중단해
공정성을 보장한다.

## 안전 원칙

- SSH 명령은 키 기반 `BatchMode`로만 실행한다.
- 코드 동기화 시 `.git`, `.venv`, `models`, `outputs`, `.run`은 제외한다.
- 모델 동기화와 코드 동기화에 `--delete`를 사용하지 않는다.
- 시스템 및 CUDA 패키지는 자동 설치하지 않는다.
- 실제 인벤토리, 접근 토큰, 로그와 원시 결과는 `.run/`에 보관한다.
