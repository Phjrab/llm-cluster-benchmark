# Jetson compact v3 PLA 예비 선별 해석

확정된 142 × 100 mm 프레임을 사용하는 실제 CalculiX 2.20 선형 중력 FEM입니다. 사용자는 원래 폭 142 mm를 유지하고 깊이를 줄이기로 했으며, 옛 139 mm 초안이나 142 × 132 mm v2를 새 형상으로 취급하지 않습니다.

분석 가정, CAD 해시, 실제 결과, 메시 검증 및 열 계산 한계는 analysis_assumptions.json, source-cad-manifest.json, results/summary.json, results/mesh_checks.json 및 한국어 PDF에 기록합니다.

장비 300 g/층은 이전 연구와 같은 가정값입니다. 상부 출력부와 프레임 자중은 새 CAD 전체고체 체적과 밀도 1.24 g/cm³로 다시 계산합니다. 30% infill 출력의 실제 질량·강성은 직접 측정하지 않았습니다. E=500/1000/2000 MPa는 교정값이나 검증된 하한이 아니며, E500/2000 결과는 선형 1/E 스케일링입니다. 출력물의 강도 안전율을 계산하지 않습니다.

원래 stock 하부 보호대와 새 레일의 바닥 교차부에서 압력 다각형을 추출합니다. 실제 곡선 접촉 경계의 끝점을 직선으로 연결한 근사이며, 공급자 전체 CAD는 포함하지 않습니다. 계산은 그 유효 지지부에 장비 중력을 분배합니다. 상부 두 층은 받는 소켓 입구 반경 4.4 mm를 제외한 기둥 어깨에서 받습니다. 별도 보드 핀 모델이 없는 설계입니다. 적층핀의 이탈 방지, 게이트 유지력, 접촉 분리·마찰·미끄럼·전도·진동·케이블 당김·충격·크리프는 계산하지 않습니다.

열 검토는 P/(rho Cp Q)의 에너지 수지 민감도만 실행합니다. 실제 설치 팬 풍량·정압곡선, 재순환 및 접촉 열저항이 없어 열 FEM/CFD 또는 PLA/CPU 온도를 예측하지 않습니다. NVIDIA 25 W 모드는 모듈 예산이며 전체 장치 열의 실측값이 아닙니다.

## 재현

공통 의존성 설치는 dependencies.txt를 참조하고, STUDY_DEPS를 해당 폴더로 지정합니다. 원본 frame.step와 분석용 접촉 다각형은 이 폴더에 있습니다. 시스템 설정을 변경하지 않습니다.

STUDY_DEPS=/absolute/path/to/tools python run_study.py
STUDY_DEPS=/absolute/path/to/tools python verify_solver.py
python postprocess.py
python check_mesh.py
STUDY_DEPS=/absolute/path/to/tools python plot_results.py
python build_report.py

NPZ는 재실행으로 생성하는 편의 중간 파일입니다. 원본 메시 MSH, 입력 INP, 실제 출력 FRD/DAT와 솔버 로그는 정식 전체 압축 파일에 보존합니다. GitHub의 원시 byte-parts는 독립 ZIP이 아니며, 동봉한 manifest와 reassemble_archive.py로 SHA256 및 ZIP CRC를 검증해 복원합니다. 사용자 전달 ZIP은 각각 정상 압축 파일로, 같은 폴더에 풀면 합쳐집니다.

이전 v2는 상부 출력부 150 g/층과 다른 접촉 하중 범위를 사용했습니다. 새 형상의 질량·접촉을 갱신했으므로 변위/응력의 단순 증감률을 강성 향상이나 설계 우열로 비교하지 않습니다.

최종 메시 크기는 5.0 / 4.0 mm이며, 두 경우 모두 검증된 SPOOLES 직접 선형 솔버를 사용합니다. 더 큰 2.8 mm 초안 및 반복형 솔버의 부정확한 시험은 보고 결과에서 제외했습니다. 반복형 시험은 별도 압축 패치에서 오차가 확인되어 채택하지 않았습니다. DISCARDED_TRIALS.json에 폐기 이유를 기록하며, 실패/중단 메시의 원시 기록은 로컬에 보존합니다.
