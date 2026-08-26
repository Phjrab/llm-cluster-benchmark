# Model Library 다운로드 및 RPC Large 수동 검증

이 문서는 소프트웨어 구현 후 사용자가 Dashboard에서 실제 GGUF와 하드웨어를 검증하는
순서다. 다운로드, 모델 load, 추론과 RPC는 자동 테스트에 포함되지 않았다.

## 사전 확인

1. Controller와 Worker 코드를 같은 commit으로 동기화한다.
2. Worker 환경 보고가 `READY`이고 backend가 Jetson CUDA 또는 Pi OpenBLAS로 검증됐는지 확인한다.
3. Worker 디스크 여유 공간과 모델 라이선스/접근 조건을 확인한다.
4. 공개 모델은 `DIRECT`, 별도 조건 모델은 `LICENSE REVIEW REQUIRED` 또는 `GATED`로
   표시되는지 확인한다. `COMMUNITY GGUF`는 원본과 실제 GGUF provenance를 함께 검토한다.
5. Gated 모델을 사용할 경우 프로젝트 루트의 Controller 터미널에서 `.venv/bin/hf auth login`을 실행하고, 모델의
   Hugging Face 페이지에서 계정 접근 요청/약관 승인을 완료한 뒤 Dashboard의
   **접근 권한 확인**을 누른다. Hugging Face token을 Dashboard 입력란이나 로그에 붙이지 않는다.

## A. 작은 모델 다운로드 확인

1. Dashboard에서 Model Library를 연다.
2. `다운로드 가능` 필터에서 원하는 exact-locked 공개 모델을 선택한다. 예를 들어
   Qwen2.5 0.5B/1.5B, DeepSeek-R1 Distill Qwen 1.5B, Granite 2B,
   Ministral 3B가 있다. 모델 크기와 대상 Worker의 안전 메모리를 먼저 확인한다.
3. Jetson 또는 Pi Worker 한 대만 선택한다.
4. **Worker 직접 다운로드**를 누르고 대상·모델을 확인한다.
5. node 작업 로그의 queued → downloading → verify → ready 진행 상태를 확인한다.
6. Worker inventory의 SHA-256과 catalog SHA-256이 같은지 확인한다.
7. load/unload smoke를 수행하고 오류가 없음을 확인한다.

## B. 여러 Worker 설치 확인

1. 동일 catalog model을 선택한다.
2. 같은 플랫폼 Worker 여러 대를 선택한다.
3. 각 Worker 직접 다운로드 또는 이미 검증된 Controller cache 동기화를 실행한다.
4. 모든 Worker의 size와 SHA-256 equality를 확인한다.
5. 실험 preflight가 모델 누락·checksum mismatch를 차단하는지 확인한다.

설치 작업은 benchmark 시간이 아니며 실험을 자동 생성하지 않는다.

## C. 별도 라이선스와 gated 모델

1. 모델 카드의 현재 약관/source 링크를 새 창에서 열고 계정에 필요한 접근 승인을 완료한다.
2. Dashboard checkbox를 선택하고 **동의 저장**을 누른다. 이 동의는 현재 프로젝트와 현재
   모델/source revision에만 적용된다.
3. Gated 모델이면 `HUGGING FACE ACCOUNT`가 연결된 계정을 표시하는지 확인한다.
4. **Controller 인증 다운로드**를 누른다. Controller cache 다운로드 → byte/SHA-256 검증 →
   선택 Worker 동기화 → Worker provenance 검증 순서를 작업 로그에서 확인한다.
5. 접근 거부, token 만료, byte/SHA 불일치가 발생하면 ready로 표시되지 않아야 한다.
6. 동의를 철회하면 기존 파일은 자동 삭제되지 않지만 새 설치와 새 실험이 차단되는지 확인한다.

## D. 14B RPC 우선 테스트

1. Qwen3 14B, DeepSeek 14B 또는 Ministral 3 14B의 exact GGUF identity가 catalog에
   고정되어 있는지 다시 확인한다.
2. intended Jetson coordinator 한 대에만 모델을 먼저 설치한다.
3. 두 대 이상의 RPC participant에서 pinned native llama.cpp commit을 확인한다.
4. `model_parallel_rpc`를 선택하고 coordinator를 명시한다.
5. 최소 context와 짧은 prompt로 model load, TTFT, E2E, 결과 저장을 확인한다.
6. 성공·실패·취소 뒤 포트 50052와 18080 및 native process가 정리됐는지 확인한다.

## E. 24B / 27B / 32B 단계

14B의 load·추론·cleanup이 모두 통과한 뒤 Magistral 24B, Gemma 3 27B, Qwen3 또는
DeepSeek 32B 순으로 확장한다. 각 단계에서 실제 aggregate safe memory, coordinator
storage, LAN 병목, context와 cooldown을 다시 계산한다.

## F. 70B RPC Extreme

- catalog의 memory 값은 추정치이며 실행 가능성을 뜻하지 않는다.
- multipart GGUF 설치는 현재 자동 경로에서 지원하지 않는다.
- aggregate safe memory와 shard 지원, coordinator storage를 다시 확인한다.
- 가장 작은 context와 충분한 cooldown을 검토한다.
- 실패 시 모든 RPC process/port cleanup을 확인한다.

70B가 이 클러스터에서 실행 가능하다고 가정하거나 보고하지 않는다.
