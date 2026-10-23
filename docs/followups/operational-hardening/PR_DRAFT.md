# PR draft — 운영 통합 및 실패 관측 보강 (F01–F03)

이 문서는 PR 본문 초안이다. PR 생성/병합 또는 운영 배포를 수행했다는 기록이 아니다.

## Branch / source

- Head: `codex/operational-observability-hardening`
- Target: `main` (직접 push하지 않음)
- F01 integration: `e4ffb2cee254fab14f424914eb73f350cafbfdec`
- F01 report: `0a17c9d7afe5ece53a3faf31180927bcae82f9bd`
- F02: `99fe4e47a8f5eda44c589539193e62bda34683c4`
- F03 구현: `237000f35e1addf54e36fad5138fc6029b52b1ad` (검증 snapshot의 222개 source 파일과 일치).
- 후속 문서 delivery commit의 SHA와 exact-commit CI는 최종 인계에 기록.

## Summary

- 최신 main/backend, 기존 운영 수정, Research Lab UI를 격리 branch에서 통합했다.
  Matrix/Compare/명시적 baseline/theme/fonts/mobile 및 main의 CAD를 보존했다.
- F02: Controller↔Worker physical request identity, 실패해도 로컬에 남는 bounded lifecycle journal,
  메모리/process 관측, 명시적 사후 기록 회수. 기존 inference/telemetry/auth/ownership 구조를 재사용한다.
- F03: 기존 server loop의 process-local 관측과 오류 유지, child/Worker/cache 구분,
  결과 상세의 bounded offline 진단 요약 및 run 선택 race 방지.
- 실제 job 문서의 suite `id`와 실행 `job_id` 차이를 반영해 F02 job binding을 수정했다.
- 조회는 실행을 시작하지 않고 원래 실패·성능 결과를 바꾸지 않는다. 기존 CSV 19 columns,
  privacy, fencing/cleanup/quarantine, default 1 job/disjoint 최대 2, formal gate를 유지한다.

## Changed runtime areas

| 범위 | 변경 |
| --- | --- |
| Controller Python | 기존 benchmark transport/core/instrumentation/persistence에 F02 hooks; F03 Dashboard service/facade/routes, progress/diagnostic projections |
| Worker Python | F02 app/routes/inference/telemetry + shared diagnostic journal; auth/ownership 연동, `request_diagnostics_v1` capability |
| Frontend | 기존 결과/Sweep/Campaign 카드의 관측·진단 패널, bounded details, selection guard, theme CSS와 asset version |
| Packaging / docs | 로컬 font/license 포함 정책, integration/diagnostic/validation 보고서, 수동 인수·배포 안내 |

F03 자체는 Worker 파일을 추가 변경하지 않았다. **새 Worker 진단을 운영에서 받으려면 F02가
포함된 Worker 코드를 배포하고 재시작해야 한다. GitHub push만으로 기능이 운영에 적용되지 않는다.**

## Compatibility

API/metadata는 additive다. 오래된 Worker와 legacy 결과는 기존 실행 경로와 UNKNOWN/진단 없음
fallback을 사용한다. 모델/runtime을 바꾸거나 기존 결과를 migration하지 않는다. 원문 privacy를
완화하지 않는다. 진단 ID는 실행 소유권이 아니며 기존 lease/fencing을 대체하지 않는다.

진단 보존량: host journal 4 MiB, queue 256 × 4096-byte ceiling; explicit recovery 최대
256 × 128 KiB/run. F03 reader는 Controller 그룹 최대 1,024개/32 events, 응답 최대 20개 요약.
메모리/process 정보는 해당 관측 시점의 값이다. Worker API RSS와 별도 모델 process를 구분하며
관측하지 못한 모델 process는 UNKNOWN이다. 새 계측은 향후 formal 재검증 대상이다.

## Validation

실제 명령/count/exit/source manifest와 로그는 [F03 validation](F03-validation.md)에 기록했다.
Python focused/full, npm syntax/fixtures/publication/E2E, compileall, JSON/lock/workflow validator,
shell syntax, wheel import/install 및 변경 assets byte 비교, diff checks를 수행했다.
가짜 Worker·합성 자료·임시 경로·localhost를 사용했다. 실제 하드웨어 검증을 대신하지 않는다.
H01 timeout의 실제 원인이 해결됐다는 주장도 하지 않는다.

## Deployment checklist — 별도 승인 후 사용, 이번에는 NOT RUN

1. **현재 운영 경로부터 확인**: service launcher의 cwd, branch/commit, dirty 파일, 실제 import 경로,
   frontend asset version을 기록한다. 과거 UI 배포 보고서만으로 실행 버전을 확정하지 않는다.
2. **active job과 자원 점유 확인**: 운영자가 승인한 조회로 Dashboard durable active jobs,
   Sweep/Campaign active attempt, child identity, lease/quarantine, Worker ownership/RPC session을
   대조한다. 조회 전에 원본 manifest/job/result 경로를 보존한다. active/cleanup 불확실이면 배포 보류.
3. **진행 경계 확보**: 진행 중이면 기존 Pause로 새 dispatch를 정지하고 child/cleanup/cooldown
   완료를 기다린다. heartbeat만으로 lease를 해제하거나 PID를 강제 종료하지 않는다.
4. **rollback 준비**: 배포 전 실제 source/release와 dirty 파일, launcher/env 설정을 별도 안전한
   위치에 보존한다. runtime/results/inventory/모델을 code checkout과 분리해 유지한다.
   현재 운영 dirty checkout에 Git reset/clean을 수행하지 않는다.
5. **Controller 적용**: 검토된 전체 source 또는 wheel을 일관되게 적용하고 기존 launcher로
   backend를 재시작한다. **정적 파일만 교체하면 부족하다.** Python hooks/services가 바뀌었다.
   wheel의 변경 JS/CSS/templates/fonts 포함 여부를 확인한다.
6. **Worker 적용**: 해당 승인 Worker에 F02 포함 source를 적용하고 기존 Worker API를 재시작한다.
   별도 Dashboard/scheduler/daemon/monitoring agent는 설치하지 않는다. 모델 파일이나 power mode,
   OS/network 설정은 이 변경을 위해 수정할 필요가 없다.
7. **source/capability 확인**: serving instance 변경, Controller source, 각 Worker source와
   `request_diagnostics_v1`, diagnostic mode를 기록한다. static cache version을 갱신한 화면인지
   확인한다. 재시작 후 알려진 완료 Trial의 중복 실행 여부도 확인한다.
8. **수동 인수**: [인수 순서](../../manual/execution-driver-and-sweep-acceptance.md)의 작은 C1 →
   작은 context Sweep → browser closed → Pause/Resume/Cancel → disjoint → 필요 시 RPC → 진단 회수를
   운영자 승인 범위에서 수행한다. 과부하/OOM을 고의로 유발하지 않는다.

Dashboard 서버가 꺼져 있는 동안 후속 Sweep 조건을 계속 제출하는 기능은 이 PR의 범위가 아니다.
실험 동안 서버·네트워크를 유지한다. 다른 server instance는 UNKNOWN이며 중복 Controller를
기동해서 기존 owner를 대신하도록 만들지 않는다.

## Rollback — 데이터 보존

- 동일한 active job/cleanup 확인과 안전 경계를 먼저 확보한다. cleanup 불확실이면 진행을 보류한다.
- 실제 배포 전에 저장한 **알려진 정상 release/source** 및 launcher/env를 복원하고 기존
  Controller/Worker 서비스만 재시작한다. F01/F02 SHA를 무조건 과거 운영 버전으로 가정하지 않는다.
- inventory, 모델, 결과, attempt/manifest, resource/ownership/quarantine, private token과 Worker
  journal을 삭제하거나 되돌리지 않는다. rollback은 실패 기록을 숨기거나 lease를 초기화하는 방법이 아니다.
- 추가 진단 artifacts는 과거 reader가 무시할 수 있다. 과거 코드로 최신 진단 기능이 지원된다고
  표시하지 않는다. source/capability와 UNKNOWN fallback을 재확인한다.

## Remaining acceptance / eligibility

- [ ] 운영 배포와 serving Python/static identity 일치 확인 (NOT RUN)
- [ ] 승인 Worker에서 실제 journal persistence/recovery 및 overhead 측정 (NOT RUN)
- [ ] 수동 hardware acceptance, 필요한 경우 작은 RPC cleanup (NOT RUN)
- [ ] 현재 계측/source 기준의 연구 재검증과 별도 formal 승인 (NOT RUN)
- [ ] exact delivery commit의 hosted CI 결과 확인 (최종 인계의 실제 상태를 사용)

기존 `formal_execution_allowed=false`와 연구 lock을 그대로 존중한다. 이 PR은 승인·lock을
자동 변경하지 않는다. F03 software 완료 후 추가 Phase를 자동 시작하지 않는다.
