# WS-MED — Jetson 4대 의료 텍스트 준비

## 범위와 상태

PREPARE_ONLY. 설치·하드웨어 실험·임상 검토·formal 승인을 대신하지 않는다.
사용자가 연결한 inventory를 읽었으며 다시 등록하거나 덮어쓰지 않았다.
다른 서버는 **실제 실험 전에 안전히 종료하는 수동 절차만** 준비했다.

|상태|판정|
|---|---|
|Preparation|preset / 입력·정답 분리 / 평가·결과 index / 사용자 안내 구현|
|Software validation|[VALIDATION.md](VALIDATION.md) 실제 실행 결과 참고|
|Model installation|NOT RUN|
|Hardware execution|NOT RUN|
|Clinical review|PENDING (`clinical_review_pending`)|
|Formal approval|PENDING; 기존 lock/gate 변경 없음|

코드 기준은 F01~F03의 `186dc69474610f0436d693dd339da155b83e7ae1`이다.
`codex/jetson4-medical-prepare` 격리 worktree에서 작업했다.
운영 checkout `codex/pi4-model-benchmark-20261014`는 같은 HEAD, clean,
upstream 없음으로 확인했다. 운영 Dashboard API/Worker에 접속하지 않았다.
F03의 진행 루프/조회 분리/진단 journal과 P01/P02의 소유권·source gate를 재사용한다.
기존 UI나 엔진/scheduler를 재작성하지 않았다.

## 산출물

- [모델 상태](MODELS.md), [exact identity JSON](model-candidates.json)
- `cluster/medical/data/`: 40 한국어 + 8 영어, 입력 profile, 모델 mapping
- `cluster/medical/evaluation/`: evaluator-only rubric. 입력 모듈은 이 디렉터리를 읽지 않는다.
- `python -m cluster.medical`: prepare / import-subset / index / compare만 제공. 실행 명령 없음.
- Dashboard 기존 05 Sweep의 `.request.json` 파일 preview와 draft 저장
- [실행량 preview](execution-preview.json), [사용자 시작 안내](USER_GUIDE.md)
- [아직 실행하지 않은 결과 index](RESULTS_NOT_RUN.json). 측정 수치/품질 순위 없음.
- [원본 팩 8개 SHA](pack-provenance.json). 설계 템플릿 자체를 실행 schema로 취급하지 않는다.

## 저장된 장비 근거

2026-10-23 F03 deployment/preflight 로컬 snapshot을 읽었다. 현재 live 상태는 UNKNOWN.
기록 경로는 운영 checkout의 `.artifacts/deployment-20261023-f03/`이며 Git에 복사하지 않았다.

|Worker|저장된 RAM MiB|저장된 L4T|저장된 설치 목록|
|---|---:|---|---|
|jetson-worker-01|7608.35|R36.4.7|M01, M02|
|jetson-worker-02|7607.31|R36.5.2|M01, M02|
|jetson-worker-03|7607.19|R36.5.2|빈 목록|
|jetson-worker-04|7607.33|R36.5.0|빈 목록|

공통 기록: NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super,
6 cores, threads 6, Ubuntu 22.04.5, Python 3.10.12, CUDA 12.6,
llama-cpp-python 0.3.20. 전력 모드/clock 정책은 UNKNOWN.
RPC runtime pin `f49e9178767d557a522618b16ce8694f9ddac628`.
과거 16자리 runtime fingerprint `05e2c27b3bf0eff2`를 40자리 runtime commit으로 변조하지 않는다.
해당 차이로 기존 readiness가 UNKNOWN이면 실제 호환 근거가 필요하다.
저장된 available memory는 현재 여유 용량/실행 가능성의 근거로 쓰지 않았다.
네 대 RAM 합계로 14/27/32B RPC fit을 보증하지 않는다.

## 설계 선택과 실제 실행량

각 실제 요청은 한 문항의 문자열이다. `requests=40`에 40개 문항을 숨기지 않는다.
동일 문항 반복·노드 복제·번역은 독립 의료 문항 수로 더하지 않는다.
모델별 native template를 유지하고 Qwen3 soft switch 외에 다른 정답 힌트를 넣지 않는다.

|Block|기본 선택|Cells|독립 jobs|측정 logical/physical|Warmup|Model loads|Packages|
|---|---|---:|---:|---:|---:|---:|---:|
|B0|M01, DOC02, 각 노드 C1|4|4|4 / 4|4|4|1|
|B1|우선 4모델 × 40KO × 4노드|640|640|640 / 640|640|640|4|
|B1 선택 확장|6모델 × 40KO × 4노드|960|960|960 / 960|960|960|6|
|B2|허용된 local subset 필요|0|0|0 / 0|0|0|0|
|B3|M03/M05, 4문항, N=1/2/3/4, C=N, repeat3|32|96|1920 / 1920|240|240|2|
|B3 stress 포함|C=N 및 2N|64|192|3840 / 3840|480|480|2|
|B4a|M03/M05, DOC01, ctx 2048/4096/8192, repeat3|6|18|18 / 18|18|18|2|
|B4b|M03/M05, DOC02 3길이 × 3근거위치, repeat3|18|54|54 / 54|54|54|2|
|B5|M03/M05, 4문항 × output128/256/512, repeat3|24|72|72 / 72|72|72|2|
|B6-smoke|R01, 2노드, DOC02|1|1|1 / 1|1|1|1|
|B6|R01, 2/4노드 × 3문항 × repeat2|6|12|12 / 12|12|12|1|
|B7-language|M03/M05, 8KO+8EN|32|32|32 / 32|32|32|2|
|B7-reasoning|M07, EVID04, capped1024|1|1|1 / 1|1|1|1|
|B7-quantization|같은 base의 두 번째 exact quant identity 필요|0|0|0 / 0|0|0|0|

이 표는 예약된 전체 캠페인이 아니다. 한 블록·한 package씩 사용자가 선택한다.
500-trial 상한, 요청+warmup 예산, 최대 disjoint 2-job 제품 상한을 유지한다.
준비 preset은 기본 sequential 1 job이다. 모델당 분할 후에도 500을 넘으면 추가 분할한다.
B3 stress는 N 안정성 확인 후 별도로 선택하며 B3 두 preset을 중복 실행하지 않는다.
B6 R02/R03는 `--models R02` / `--models R03`를 각각 선택할 때만 같은 제한된 설계를 만든다.
3노드/custom split/확장 context는 기본 preset에 자동 추가하지 않는다.

기본 requested: threads6, batch256, GPU layers30, seed42, timeout600s,
warmup1/Worker, cooldown30s, reload_per_cell, new_session_per_cell, manual retry.
RPC는 기존 auto/layer, coordinator 첫 Worker, gpu layers all이며 threads/batch를 억지 적용하지 않는다.
KV cache type은 현재 runtime 기본값/UNKNOWN으로 기록한다.
실제 적용값과 다르면 정상 controlled 표본으로 합치지 않는다.

B4b는 고정 합성 문서의 **same-text 길이 cohort**다. 512/2048/4096은 nominal 라벨이며
실제 exact token 수는 미측정이다. 텍스트를 모델마다 달리 padding해서 같은 입력이라고 하지 않는다.
실제 native template 준비가 input+output reserve를 만족해야 실행할 수 있다.
정확한 token-length profile 비교는 사용자 calibration 후 기존 editor의 별도 cohort로 저장한다.

## 평가와 증거

- B2 label은 evaluator-only 디렉터리로 분리. KorMedMCQA의 doctor/dentist/nurse/pharm과
  test/dev schema를 확인했다. answer 0/1 base는 source 검증 후 사용자가 명시해야 한다.
- PubMedQA `pqa_labeled`는 HF train 1000개다. 존재하지 않는 test split을 만들지 않는다.
  허용 abstract section+question만 입력하며 conclusions/long_answer/예측 field는 제외한다.
- 고정 subset은 revision/config/split/ID/seed20261023을 기록한다. calibration20과 evaluation200은
  겹치지 않는다. 실제 dataset 파일은 이번에 받지 않았으므로 B2는 0건이다.
  [KorMedMCQA](https://huggingface.co/datasets/sean0042/KorMedMCQA) CC-BY-NC-2.0과
  [PubMedQA](https://huggingface.co/datasets/qiaojin/PubMedQA) metadata MIT 및 abstract 권리는 사용자가 검토한다.
- strict JSON/중복 키/NaN/extra key/boolean-vs-number/null-vs-missing, 날짜순서,
  단위/수치 허용오차를 검사한다. DOC01만 사전 정의 alias를 인정한다.
  EVID01 ratio 허용오차1e-6, EVID04 fraction5e-5. 임의 percent 변환은 하지 않는다.
- timeout 뒤 늦은 정답을 성공으로 덮어쓰지 않는다. parse/no response/no final answer/refusal을 분리한다.
  refusal은 명시 관측/검토가 없으면 UNKNOWN이며 키워드 추정으로 확정하지 않는다.
- 자유 답변은 사실성/안전 행동/불확실성/질문 충족/가독성의 0..2 맹검 양식과
  심각 오류 별도 항목을 제공한다. 자동 임상 통과 점수는 없다.
- 요청에 raw text hash, composed prompt hash, actual rendered **token-ID sequence** SHA,
  template hash, exact input/output tokens, finish_reason, output budget를 연결한다.
  렌더 hash의 source는 `chat_template_token_ids_v1`이며 UTF-8 rendered text hash라고 부르지 않는다.
  old Worker와 RPC에 근거가 없으면 null. token 배열은 저장하지 않는다.
- index는 immutable plan 검증 후 model/prompt/trial/attempt/run/replica를 검사한다.
  trial 미실행·원본 실패·missing rows를 보존한다. CLI는 Worker 기록 수집이나 retry를 시작하지 않는다.
- index의 기존 F02 진단 요약은 별도 job ownership 파일이 없으면 cleanup UNKNOWN일 수 있다.
  운영 시 Dashboard run detail/기존 F02 수집 절차로 근거를 확보한다.
- 메모리·process는 기존 측정 sample에서 최대4096개, 원래 시각/availability를 보존한다.
  energy는 기존 summary coverage만 사용한다. OOM/power를 수치만 보고 추정하지 않는다.
- `full`에서만 답변을 export한다. `hash_only`/`none`에서 정답/원문을 복원하지 않는다.
  맹검 mapping은 evaluator-only에 따로 두며 결과 파일은 Git에 추가하지 않는다.

## 배포 경계

현재 운영 버전은 변경하지 않았다. 새 importer는 Controller 코드 배포·backend 재시작 후 제공된다.
새 rendered-token hash에는 Worker의 `inference.py`/`routes.py` 변경도 필요하다.
push는 배포가 아니다. 모델 파일·inventory·기존 결과·연구 lock을 보존하고,
active job이 없는 승인된 창에서 기존 배포 절차를 따른다.
롤백은 이전 source `186dc69`로 서비스 코드를 복귀하고 데이터 경로를 유지한다.
새 계측 source는 이후 formal 재검증 대상이며 gate를 자동 승인하지 않았다.
