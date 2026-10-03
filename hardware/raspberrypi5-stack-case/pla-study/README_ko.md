# Raspberry Pi 5 v1 PLA 예비 선별 해석

원본 잠금 STEP를 사용한 실제 CalculiX2.20 선형 중력 FEM입니다. 전체 안전성/수명 인증이 아닙니다. 분석 가정과 재료·질량·접촉 범위는 analysis_assumptions.json, 결과는 results/summary.json, 메시 수렴은 mesh_convergence.csv를 보세요. 한국어 PDF는 수치와 해석장, 미해석 위험, 실물 시험 순서를 설명합니다.

보드+냉각기+소형 케이블 질량은150g/층 가정입니다. 공식 질량이나 실측이 아닙니다. 상부 출력부와 프레임 자중은 실제 CAD의 전체고체 체적과1.24g/cm³에서 계산했습니다. 30% infill 질량은 직접 계산하지 않았고 E=500/1000/2000MPa도 출력물 교정값 또는 검증된 하한이 아닙니다. 500/2000 결과는1000MPa 선형 해석의1/E 스케일링입니다. 출력강도 안전율을 계산하지 않았습니다.

실제 부품의 압력 접촉 고리를 CAD 표면에서 분할한 뒤 C3D10으로 메시했습니다. 원본 STEP는 그대로 유지됩니다. 탈착형 핀은4개에 균등한 중력과 seated shoulder 지지에 대한 별도 국부 모델일 뿐 보존력/인장/삽입/접촉 미끄럼 시험이 아닙니다. 적층 흔들림, 미끄럼, 전도, uplift, PCB 손상, 진동/낙하, 열팽창, 크리프는 계산하지 않았습니다.

열 검토는P/(rho Cp Q) 에너지 수지 민감도만 실행했습니다. 실제 설치팬 풍량, 재순환과 접촉 열저항이 없어 열FEM/CFD, PLA/CPU 온도를 예측하지 않았습니다. 공식1.09CFM은팬1개의명목최대값으로 실제3단유량이나 상한이 아닙니다.

## 재현

이 폴더 옆 기존 jetson-pla-study/tools를 기본 사용하거나 STUDY_DEPS를 압축해제한 공용 의존성 디렉터리로 지정합니다. 의존성은별도dependencies.txt의공식공급처참조. 시스템설정은수정하지않습니다.

STUDY_DEPS=/path/to/tools python run_study.py
STUDY_DEPS=/path/to/tools python verify_solver.py
python postprocess.py
STUDY_DEPS=/path/to/tools python plot_results.py
python build_report.py

NPZ는이 절차에서재생성되는편의중간파일로원본결과는STEP/MSH/INP/FRD/DAT/solver.log에보존됩니다. 프레임 h3.2/2.2mm, 핀 h0.7/0.45mm; 모든최종메시의 Jacobian양수와체적오차/반력균형을 확인합니다. 새로운해석은이전v2의결과나압축패치기록을현재결과로가장하지않고패치를재실행합니다.

GitHub에는하나의정식전체ZIP을8MiB이하원시바이트parts로나누어보존하고ARCHIVE_RECONSTRUCTION.json+reassemble_archive.py로SHA256/ZIP CRC를검증해복원합니다. 그parts는독립ZIP이 아닙니다. 사용자전달delivery폴더의ZIP은각각정상적으로풀수있는독립ZIP이며같은폴더에풀면합쳐집니다. 전체고체질량과유효E를혼합한선별모델이며안전상한을보장하지않습니다.
