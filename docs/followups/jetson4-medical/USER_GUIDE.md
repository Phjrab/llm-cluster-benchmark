# WS-MED — 사용자가 Dashboard에서 시작하는 순서

이번 작업은 아래 운영 절차를 **실행하지 않았다**. 실험/설치 NOT RUN.
새 UI·render hash는 이 feature의 별도 승인 배포 후 사용할 수 있다.
Controller backend와 필요한 Worker 코드를 반영하려면 idle/cleanup 확인 후 재시작이 필요하다.
현재 8080 Dashboard는 그대로 유지한다. 이후 실험 중에는 Dashboard 서버와 네트워크를 유지한다.
브라우저를 닫는 것은 허용되며 기존 서버 진행 루프가 후속 조건을 담당한다.

## 0. 다른 서버 안전 종료 — 실제 실험 직전, 해당 소유자의 승인 범위만

전제: 현재 job/Sweep/Campaign/모델 설치가 없고 lease/cleanup가 확인돼야 한다.
과거 snapshot에는 Jetson 2의 다른 프로젝트 llama-server가 있었다.
이는 현재 실행 중이라는 증거가 아니며 저장된 PID를 그대로 종료하면 안 된다.

1. 사용자가 장비의 현재 프로세스/service/container 목록을 읽어 서버의 소유자,
   실행 파일·시작 시각·포트·작업 디렉터리·서비스 관리자를 확인한다. credential/환경변수 전체를 저장하지 않는다.
2. 다른 서버에 active request, 저장 중인 작업, 외부 사용자 또는 auto-restart 관리자가 있는지 확인한다.
   해당 소유자가 종료를 허용한 서비스만 대상 목록에 기록하고 원래 재시작 방법을 남긴다.
3. 서비스 관리자의 정상 stop을 먼저 사용한다. 컨테이너라면 해당 컨테이너의 graceful stop을 사용한다.
   unmanaged 프로세스라면 방금 확인한 소유자·시작 identity의 정확한 PID에 정상 종료를 요청한다.
   광범위한 `pkill`, `killall`, stale PID, 강제 kill/재부팅은 사용하지 않는다.
4. 제한된 대기 후 해당 PID/포트의 종료와 auto-restart 부재를 확인한다. 로그와 저장 중 데이터는 보존한다.
5. benchmark Worker API, Controller Dashboard, SSH/network/OS 필수 서비스는 유지한다.
   기존 JobService lease를 heartbeat만 보고 풀거나 cleanup을 생략하지 않는다.
6. 확인할 수 없거나 작업이 진행 중이면 **실험 시작을 보류**한다. 자원이 부족하다고 임의 서비스까지 중단하지 않는다.
   실험 종료·cleanup 후 승인된 방법으로 원래 서버를 재개한다.

남길 근거: 대상 service/container 식별자, 소유자 승인 범위, 관측 시각, 시작 identity,
정상 stop 결과, 재시작 방법, 잔여 resource/lease 상태. 이번 준비 단계에서 원격 조사·종료는 없었다.

## 1. Source·설치·capability 확인

- 전제: idle, 전 단계 서비스 점유 확인, 실험용 전원/냉각, 외부 전력 센서 유무 파악.
- 조작: Dashboard의 Worker 4대 stable ID와 현재 source/runtime/capability 및 설치 모델을 확인한다.
  원본/변환 repository·exact revision/SHA·라이선스는 [MODELS.md](MODELS.md)와 대조한다.
  부족한 모델은 사용자 직접 모델 화면에서 선택해 라이선스를 검토한 뒤 설치한다.
- 기대: 다운로드와 설치가 끝난 후 exact artifact·template·runtime readiness가 확인됨.
  모델 하나가 미지원이면 해당 후보를 보류하고 다른 모델 준비는 유지한다.
- 기록: Controller/Worker source, runtime commit/fingerprint, artifact SHA, native template/tokenizer hash,
  RAM/available memory, 실제 power mode/clock/cooling. 기록이 없으면 UNKNOWN.
- 중단: checksum drift, source 불일치, 전원 경고, OOM/crash 증거, cleanup 불확실, 다른 작업 점유.

## 2. B0 preset 작성과 가져오기

Mac의 **새 feature checkout**에서 준비 파일만 만든다. `--output`은 새 디렉터리여야 한다.
이미 생성된 `.artifacts/medical-presets/B0/`도 그대로 사용할 수 있다.

```bash
.venv/bin/python -m cluster.medical prepare B0 \
  --inventory ../llm-cluster-benchmark/.run/cluster/nodes.local.csv \
  --workers jetson-worker-01 jetson-worker-02 jetson-worker-03 jetson-worker-04 \
  --models M01 --output .artifacts/medical-user/B0
```

이 명령은 inventory를 읽고 JSON만 쓴다. Worker 접속/설치/실행은 없다.
새 inventory를 만들지 말고 현재 Controller의 기존 경로를 지정한다.

- 조작: Dashboard **05 스윕 → 준비된 Sweep preset JSON**에서 해당 `.request.json` 선택 → **Preset 파일 미리보기**.
  `manifest.json`이나 `evaluator-only` 파일을 선택하지 않는다.
- 기대: cached preview만 요청된다. 실제 conditions JSON을 펼쳐 Worker, 문항, output/context, 모델,
  job수·physical/warmup 수·blocker·hash를 검토한다. 일반 form 값은 imported JSON을 대신하지 않는다.
- 사용자가 Readiness 새로 확인을 누르면 실제 장비 조회가 일어날 수 있다. 이번 준비 작업에서는 누르지 않았다.
- hash 확인 체크 → Draft 저장. 저장도 Start가 아니다. 원하는 draft의 기존 **Start**를 사용자가 별도로 누른다.
- 기록: bundle SHA, request SHA, Dashboard plan export, sweep/plan/cell/trial/attempt/job/run ID.
  파일을 수정하면 bundle을 새로 만들고 새 preview/hash를 저장한다. importer 제외 수정은 원본 preset 재생성으로 한다.
- 중단: preview의 unknown/blocked 근거가 해소되지 않음, 적용 설정 차이, input+output reserve 초과.

B0은 설치된 작은 모델/각 Worker/C1/한 요청이다. 먼저 한 Worker만 선택한 파일로 시작해도 된다.
응답 정상, exact input evidence, 종료·cleanup를 확인한 뒤 나머지 Worker로 확장한다.

## 3. 작은 Sweep·브라우저 비의존 진행·제어 확인

- 전제: B0 완료, active job 없음, runtime/template 동일.
- 조작: B4a를 한 모델·한 Worker로 준비해 3개 context 조건 preview 후 시작한다.
  브라우저를 닫은 동안 다음 조건이 진행되는지 서버 기록으로 확인한다.
  기존 Pause/Resume/Cancel을 작은 조건에서 확인한다.
- 기대: 긴 추론/cooldown은 정상 상태로 구분되고, 중단 시 cleanup 근거가 남는다.
- 기록: serving instance, loop tick/dispatch, child ID, cancellation/cleanup, 원본 실패.
- 중단: duplicate trial, stale/unknown owner, quarantine, 실제 전원/OOM/연결 불안정.

## 4. B1~B7는 블록별 선택

- **B1**: 먼저 우선 4모델, 필요하면 M02/M06 추가. 40KO가 모델별 동일하며 노드 복제는 반복 관측이다.
  `--models M01 M02 M03 M04 M05 M06`은 960jobs/6packages다. default는640/4.
- **B2**: 라이선스가 허용된 local 데이터만 import. source revision/config/split을 확인해 정답과 입력을 분리한다.
  아래는 파일을 이미 확보했을 때의 예시이며 이번 작업에서 다운로드하지 않았다.

```bash
.venv/bin/python -m cluster.medical import-subset \
  --dataset kormedmcqa --input local-doctor-test.jsonl --config doctor --split test \
  --revision 79efd6f91edfc8036330d7a4daa88b9f2deb9a82 --answer-base 1 \
  --output .artifacts/medical-user/kormed-subset
```

  `--answer-base 1`은 예시다. 실제 label encoding을 확인하지 못하면 실행하지 않는다.
  subset manifest의 ID와 분포를 고정하고 calibration/evaluation을 섞지 않는다.
  B2 준비 시 `--public-questions .../evaluation.questions.json`만 전달한다.
  evaluator-only/keys.json은 index 시에만 사용한다. 실제 데이터 미확보이면 B2는 보류다.
- **B3**: 같은 모델의 prefix1/2/3/4노드, C=N, repeat3. stable 후에만 `--stress-2n` 별도 cohort.
  다른 모델 답변 비교와 같은 모델의 처리량 scaling을 합치지 않는다.
- **B4a**: 같은 텍스트, 다른 context capacity. **B4b**: ctx8192 고정, 실제 입력 길이 비교.
  B4b token 라벨은 아직 uncalibrated다. actual input tokens를 보고 fit 확인 후 분석한다.
- **B5**: max_tokens128/256/512는 상한. EOS/length/unknown 및 final answer 존재를 따로 확인한다.
- **B6**: R01→R02→R03를 순서대로 선택. 2노드 smoke 후 동일 모델의2/4노드 비교.
  coordinator/device per-node 메모리·runtime·cleanup가 준비돼야 한다. 싱글 baseline 없으면 speedup N/A.
- **B7**: KO/EN8쌍은 별도 언어 cohort, M07은 capped reasoning.
  같은 base의 exact 두 번째 quant artifact가 준비되지 않았으면 quant 비교는 보류한다.

각 단계의 전제는 이전 단계 정상 완료와 자원·cleanup 확인이다.
기록은 동일한 source/model/template·requested/effective 설정, 독립 run ID, 토큰/실패/전력 coverage다.
중단 기준은 1번과 동일하며 실패를 지우기 위한 자동 retry는 하지 않는다.
새 병렬 품질 수집이 필요하면 별도 표시 조건으로 만들고 disjoint 최대2를 유지한다.
이번 preset은 sequential1이다. 고의 OOM/과부하로 진단을 시험하지 않는다.

## 5. 결과 index·맹검 검토

Dashboard에서 해당 Sweep의 **plan export**를 저장한다. 결과 디렉터리는 기존 Controller 저장소다.
다음은 로컬 파일 읽기만 수행한다.

```bash
.venv/bin/python -m cluster.medical index \
  --manifest .artifacts/medical-presets/B0/manifest.json \
  --package '<manifest에 있는 package ID>' --plan saved-plan.json \
  --runs '<실제 result run 디렉터리>' --output .artifacts/medical-user/B0-index
```

B2는 `--keys .../evaluator-only/keys.json`을 추가한다. 두 모델/블록 결과를 비교하려면:

```bash
.venv/bin/python -m cluster.medical compare \
  --indexes '<첫 index.json>' '<두 번째 index.json>' \
  --output .artifacts/medical-user/comparison
```

출력: `index.json`, `REPORT.md`, `blinded-review.json`, `evaluator-only/review-map.json`.
맹검 reviewer에게 mapping을 보내지 않는다. 원문 저장이 꺼진 항목은 임의 복구하지 않는다.
자유 답변 평가는 임상 검토 전 pending이고 자동 keyword/LLM judge 안전 승인은 없다.
같은 질문의 노드/반복/번역을 독립 문항으로 bootstrap하지 않는다.

Index의 run별 TTFT/E2E/처리량/성공률은 기존 summary 공식 그대로다.
메모리·온도·전력·network·F02 실패 단계는 관측된 자료만 포함한다.
0값은 missing 대체가 아니며 에너지 coverage가 없으면 N/A다.
원래 timeout과 나중 확보된 Worker 완료 근거는 별개다.
실패 시 Dashboard 진단 요약과 기존 F02 수집 절차로 제한된 evidence를 회수한다.
수집 자체도 새 실험/retry/cleanup을 시작해서는 안 된다.
