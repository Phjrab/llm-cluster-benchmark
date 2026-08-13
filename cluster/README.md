# Edge LLM Head/Worker Cluster

NVIDIA Jetson Orin Nano 또는 Raspberry Pi 5를 최대 4대까지 묶어 동일한 GGUF
워크로드를 비교하는 head/worker 벤치마크 환경이다. 현재 Jetson Orin Nano가 **head**이며
대시보드, 노드 준비, 모델 배포, 실험 스케줄링, 결과 집계를 담당한다. Head도 추론 노드로
참여할 수 있다.

## 지원 플랫폼

| 플랫폼 | 런타임 | 필수 조건 | 권장 실험 설정 |
|---|---|---|---|
| Jetson Orin Nano | CUDA `llama-cpp-python` | JetPack/CUDA가 미리 설치된 64-bit Ubuntu/L4T | `n_gpu_layers` 30 또는 모델에 맞게 조정 |
| Raspberry Pi 5 | CPU/OpenBLAS `llama-cpp-python` | Raspberry Pi OS 64-bit 또는 Ubuntu 64-bit, `aarch64` | `n_gpu_layers=0`, 1B~3B Q4 GGUF |

Raspberry Pi는 장시간 부하에서 thermal throttling이 발생할 수 있으므로 Active Cooler 또는
팬을 권장한다. 메모리 여유가 작은 장치에서는 큰 swap이 실행을 가능하게 할 수 있지만
스토리지 I/O 때문에 벤치마크 값이 왜곡될 수 있어 1B/3B 모델부터 검증한다.

한 실험에 Jetson과 Pi를 함께 넣을 수는 있지만, Pi가 포함되면 대시보드는 모든 노드의
GPU 레이어를 0으로 맞춘다. 가속기 성능 비교는 Jetson 클러스터와 Pi 클러스터를 별도
실험으로 생성하는 것이 해석하기 쉽다.

## 최초 head 설정

```bash
cd <project-dir>
./cluster/setup_head.sh
./cluster/dashboard/start.sh
```

예: Jetson은 `/home/jetson_orin_nano/project/llm/local_llm_bench`, Raspberry Pi는
`/home/pi/local_llm_bench`처럼 실제 clone 경로를 사용한다.

생성되는 로컬 런타임 파일은 커밋하지 않는다.

- `.run/cluster/nodes.local.csv`: 실제 노드 인벤토리
- `~/.ssh/id_ed25519_llm_cluster`: head 전용 SSH 키
- `.run/cluster/dashboard.token`: 대시보드 접근 토큰
- `.run/cluster/settings.json`: 선택형 보안 설정(기본 worker API 인증 꺼짐)
- `.run/cluster/worker.token`: 인증을 켤 때 생성되는 비공개 head/worker 공유 토큰
- `.run/cluster/experiments/`: 실험 정의 카탈로그
- `.run/cluster/results/`: 실행별 원시 결과와 요약

대시보드는 기본적으로 `http://HEAD_IP:8080`에서 실행한다. 내부 LAN 전용이며 인터넷에
직접 공개하지 않는다.

## 대시보드에서 워커 찾기와 자동 준비

`+ 워커 연결`을 누르면 head가 연결된 사설 LAN의 최대 `/24` 범위에서 SSH 포트가 열린
기기만 제한적으로 찾는다. Docker·가상 브리지 인터페이스는 제외한다.

1. 검색된 기기 카드에서 워커를 선택한다.
2. SSH 사용자와 프로젝트 경로를 확인한다. Raspberry Pi OS 기본 사용자 구성이라면
   사용자명을 `pi`, 경로를 `/home/pi/local_llm_bench`처럼 해당 홈 아래로 바꾼다.
3. Head 공개 키를 워커의 `~/.ssh/authorized_keys`에 최초 한 번 등록한다.
4. `SSH 환경 확인`으로 키 인증, 보드, OS, `aarch64`, 디스크, NTP, 누락 패키지와
   passwordless sudo 가능 여부를 확인한다.
5. `저장 후 자동 준비`를 실행한다.
6. 시스템 의존성 확인 → 코드 동기화 → 플랫폼별 venv/llama 빌드 → 선택 모델 동기화 →
   worker API 시작 순서로 진행된다.

대시보드는 SSH 또는 sudo 비밀번호를 입력받거나 저장하지 않는다. 누락된 apt 패키지가
있고 `sudo -n`이 불가능하면 워커 콘솔에서 실행할 정확한 allowlist 명령을 표시한다.
명령을 한 번 실행한 뒤 환경 확인을 재시도한다. JetPack/CUDA 자체는 자동 설치하지 않고
NVIDIA 이미지에 정상 설치되어 있는지만 검증한다.

공통 apt 의존성:

```text
ca-certificates curl git rsync build-essential cmake ninja-build pkg-config
python3 python3-dev python3-venv
```

Raspberry Pi에는 `libopenblas-dev`가 추가된다. 설치 스크립트는 재실행 가능한 형태이며,
검증된 백엔드가 이미 있으면 다시 빌드하지 않는다. 실제 설치 없이 계획만 확인할 수도 있다.

```bash
./cluster/worker_setup.sh --plan-only --platform jetson
./cluster/worker_setup.sh --plan-only --platform raspberry-pi
```

## jtop형 노드 상태

노드 카드의 `상세 상태`를 누르면 다음 정보를 실시간으로 확인한다.

- 보드, OS/L4T, kernel, Python, 검증된 llama 백엔드, uptime
- 전체/코어별 CPU 사용률과 주파수, load average
- RAM, swap, 디스크 사용량
- 네트워크 송수신 속도
- 온도 센서, 팬, 총전력과 전력 레일
- Jetson GPU/EMC 및 하드웨어 엔진 상태
- 브라우저 세션의 최근 CPU/GPU/RAM 이력 그래프

Jetson은 jtop 서비스가 사용 가능할 때 `jtop + psutil`, 없으면 `psutil`로 안전하게
fallback한다. 고급 GPU/전력/팬 지표에는 운영체제 수준 `jetson-stats` 서비스가 필요하며
`systemctl is-active jtop.service`와 사용자 `jtop` 그룹 권한을 확인한다. Raspberry Pi는
`psutil + vcgencmd/sysfs`를 사용한다. Pi에서
신뢰할 수 없는 GPU 사용률과 보드 전체 전력 값은 0으로 꾸미지 않고 `N/A`로 표시한다.

## 실험과 결과 관리

대시보드의 `실험 묶음`은 영속적인 `experiment_id`를 가진다. 새 실험을 만들거나 기존
실험을 선택해 반복 실행하면 각 run의 `summary.json`이 동일한 실험에 연결된다. 기존
결과처럼 `experiment_id`가 없는 파일은 이름 기준의 `legacy-*` 묶음으로 읽으며 원본을
변경하거나 삭제하지 않는다.

결과 화면에서 실험을 선택하면 해당 실험의 실행만 필터링해 다음을 표시한다.

- 반복 실행별 cluster tokens/s 추세
- TTFT p50과 E2E p95 비교
- 최근 실행의 노드별 effective tokens/s 기여도
- 성공률, 요청 수, 전체 실행 표

각 실행은 `.run/cluster/results/<run-id>/`에 다음 파일을 보존한다.

- `config.json`: experiment ID를 포함한 요청 설정
- `events.jsonl`: 모델 로딩, 워밍업, 요청 완료, 경고의 시간순 기록
- `requests.csv`: 요청별 노드, TTFT, E2E, 토큰, 처리량과 오류
- `summary.json`: 클러스터 및 노드별 p50/p95, 성공률, 유효 처리량

노드가 설정을 자동 하향 조정하면 실제 `n_ctx`, `n_gpu_layers`, `n_batch`를 기록한다.
`동일 구성 강제`가 켜져 있고 노드별 실제 설정이 다르면 측정을 중단한다.

## CLI

```bash
python -m cluster.clusterctl inventory
python -m cluster.clusterctl status
python -m cluster.clusterctl discover
python -m cluster.clusterctl doctor
python -m cluster.clusterctl prepare \
  --model qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
python -m cluster.clusterctl start
python -m cluster.clusterctl select-model \
  --model-id qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
  --n-ctx 1024 --n-gpu-layers 30
```

Pi 노드만 선택한 CLI 실험은 반드시 `--n-gpu-layers 0`에 해당하는 설정을 사용한다.
실험 설정 예시는 `cluster/config/experiment_defaults.json`을 복사한다. 기존 head 이름을
사용하는 설치에서는 `node_names`를 실제 인벤토리 이름으로 바꾼다.

```bash
cp cluster/config/experiment_defaults.json .run/cluster/experiment.json
python -m cluster.benchmark.runner \
  --config .run/cluster/experiment.json \
  --inventory .run/cluster/nodes.local.csv
```

## 안전 및 재현성 원칙

- SSH는 전용 키와 `BatchMode`로만 실행한다.
- LAN 검색 범위는 head가 연결된 RFC1918 네트워크의 최대 `/24`로 제한한다.
- 브라우저에서 임의 SSH identity 파일이나 공인 IP를 등록할 수 없다.
- Worker API 인증은 기본적으로 꺼져 있어 신뢰 LAN에서 간단히 사용할 수 있다. 대시보드
  `설정 → 워커 API 토큰 인증`을 켜면 브라우저에 노출하지 않는 head/worker 공유 토큰으로
  상태, 모델 변경과 추론 요청을 보호하고 모든 활성 노드 API를 자동 재시작한다.
- apt 자동 설치는 고정된 패키지 allowlist와 `sudo -n`에서만 허용한다.
- 코드 동기화에서 `.git`, `.venv`, `models`, `outputs`, `.run`을 제외한다.
- 모델은 `rsync --partial --append-verify`로 선택 파일만 보내며 `--delete`를 사용하지 않는다.
- 실제 인벤토리, 토큰, 작업 로그, 실험 카탈로그와 원시 결과는 `.run/`에 둔다.
- 공정한 시간축 비교를 위해 모든 노드의 NTP 동기화를 권장한다.
