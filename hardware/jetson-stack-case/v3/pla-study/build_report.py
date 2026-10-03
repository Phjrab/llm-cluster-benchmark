from pathlib import Path
import json,hashlib
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
R=Path(__file__).resolve().parent;A=json.loads((R/'analysis_assumptions.json').read_text());S=json.loads((R/'results/summary.json').read_text());pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'));styles=getSampleStyleSheet()
for name,size,leading in [('KO',10,15),('KH',13,18),('KT',20,27),('KS',8,11)]:styles.add(ParagraphStyle(name=name,fontName='HYSMyeongJo-Medium',fontSize=size,leading=leading,spaceAfter=8,wordWrap='CJK'))
story=[]
def p(t,style='KO'):story.append(Paragraph(t.replace('·',' 및 '),styles[style]))
def table(rows,widths):
 t=Table([[Paragraph(str(x).replace('·',' 및 '),styles['KS']) for x in row] for row in rows],colWidths=widths,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#e9eef2')),('GRID',(0,0),(-1,-1),.4,HexColor('#d9d9d9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story.append(t);story.append(Spacer(1,8))
frame=[r for r in S['meshes'] if r['kind']=='frame'];pins=[r for r in S['meshes'] if r['kind']=='pin'];f=frame[-1];cf=S['coarse_to_fine_change_percent_relative_fine']['frame']
p(A['report_title_ko'],'KT');p('실제 STEP 중력 해석 + 공기 에너지 수지 민감도 | 2026년 10월 3일','KS')
p(f"선택한 중력 조건에서 하단 프레임의 최대 변위는 {f['max_displacement_mm']:.5f} mm였습니다. 이는 실제 출력물의 안전성 인증이나 PLA 장기 사용 승인값이 아닙니다. 먼저 맞춤 시험편과 1단을 출력하고 온도·잔류변형을 확인하세요.")
p('가정과 실제 하중 경로','KH')
table([['항목','채택 조건'],['형상',A['geometry_description_ko']],['재료·출력','Bambu Lab PLA Basic 임시 기준. 0.4mm 노즐, 0.2mm 층, 4벽, 30% gyroid, 상하5층. Cubicon 동등 물성 가정 없음'],['유효 물성','E=1000MPa, ν=0.35. E=500/2000MPa는 선형1/E 스케일링 탐색, 검증된 하한이나 재료 교정값 아님'],['층별 질량',A['mass_description_ko']+f" 상부 출력부는 실제 CAD의 전체 고체 체적 기준 {1000*A['upper_tier_print_mass_kg']:.2f}g/층. 하단 프레임 전체고체 {f['cad_volume_mm3']*1.24e-3:.2f}g"],['중력 전달',A['loadpath_description_ko']+f" 자기 보드 {f['loads_N']['board']:.4f}N, 위2층 {f['loads_N']['upper']:.4f}N + 프레임 자중"],['지지·솔버','네 모서리 실제 바닥 접촉부 Z구속 + 최소 XY앵커. Gmsh4.13.1, CalculiX2.20, C3D10. 3단 전체 접촉 대신 하단1단 등가 모델']],[88,436])
p('물성과 질량의 한계','KH');p('Bambu TDS V3.0은 XY2580±220MPa, Z2060±170MPa, HDT54/57°C를 제시합니다. 시험편은100% infill, 55°C8시간 처리·건조 조건이므로30% 미열처리 출력물에 그대로 대입하지 않았습니다. 실제 출력 강도 안전율은 계산하지 않았습니다. HDT는 장기 허용온도가 아닙니다. 전체고체 질량은30% 출력 질량을 과대평가할 수 있으나, 이것만으로 해석 전체가 보수적인 상한이 되지는 않습니다.')
story.append(PageBreak());p('하단 프레임: 실제 정적 결과','KT')
table([['메시h','노드 / 요소','최대변위mm','수직처짐mm','최대등가응력MPa']]+[[r['h_mm'],f"{r['nodes']:,} / {r['elements_C3D10']:,}",f"{r['max_displacement_mm']:.6f}",f"{r['max_vertical_displacement_mm']:.6f}",f"{r['stress_von_mises_peak_MPa']:.4f}"] for r in frame],[60,130,106,108,120])
p(f"세분화에서 최대변위 변화 {cf['max_displacement_mm']:.2f}%, 최대응력 변화 {cf['stress_von_mises_peak_MPa']:.2f}% (세밀 메시 기준). 응력 피크는 날카로운 모서리·구속 경계와 절점 평균 방식에 민감합니다. 변위 수렴이 응력이나 파손 수렴을 뜻하지 않습니다.")
story.append(Image(str(R/'frame_static_results.png'),width=524,height=218));p('실제 솔버 해석장으로 표면 삼각형 평균을 표시. 변형 전 형상, 컬러바는 각 해석장 실제 범위입니다.','KS')
table([['유효E MPa','500','1000','2000'],['프레임 최대변위mm']+[f"{f['max_displacement_mm']*1000/e:.6f}" for e in [500,1000,2000]]],[146,126,126,126])
p('검증과 미해석 항목','KH');p('최종 실행은 자원 범위 안에서 직접 솔버로 완료한5→4mm 메시(최대크기20% 감소)로 제한했습니다. 작은 CAD 곡선은 곡률 기반 세분화로 유지하지만, 세밀한 노치 응력이나 파손 수렴을 확정하지 않습니다.','KS');p(f"모든 프레임 메시의 비양의 Jacobian0개. 프레임 CAD대비 체적오차 {100*frame[0]['geometry_volume_relative_error']:.3f}% → {100*f['geometry_volume_relative_error']:.3f}%. 정육면체 압축 패치를 다시 실행해0.001mm, 반력10N 해석해를 확인했습니다. 구속절점의 자체 중력하중을 보정한 최대 반력 오차 {max(r['reaction_balance_relative_error'] for r in S['meshes']):.2e}입니다.")
p('프린트 내부격자, 층방향 파손, 불완전 끼움, 마찰, 접촉 분리, 적층 흔들림·미끄럼·전도, 케이블 당김, 진동, 충격, 낙하, 크리프 및 열팽창은 계산하지 않았습니다. 세 층을 바닥에 고정하고, 위층만 잡아 들지 마세요. 최소 XY앵커는 수치 강체운동 제거용이며 실제 접합 유지력을 검증하지 않습니다.')
story.append(PageBreak());p('하중면과 접합 해석 한계','KT');story.append(Image(str(R/'load_footprints.png'),width=450,height=286));p('상·하 프레임의 실제 평면 교차부를 별도 STEP로 추출해 받침면 총523.335mm²에 하중을 주었습니다. 소켓 입구뿐 아니라 외곽 경사·실제 바닥 형상도 반영한 면입니다. 접촉 압력 분포·마찰·핀 보존력·게이트 유지력·소켓 미끄럼은 푸는 모델이 아닙니다. 보드 하중은 원래 하부 보호대를 유지한 상태의 양쪽 레일 지지부에 주었습니다. 실제 교차면의 곡선 끝점을 직선으로 이은 다각형 근사이며, 면적은 실제325.448mm² 대비323.169mm²(약0.70% 작음)입니다. 모든 하중의 총합은 정확히 유지됩니다. 별도 보드 핀은 없는 설계이며, 적층핀은 위치맞춤 부품으로만 취급하세요.')
p(f"곡선 경계를 직선으로 근사한 세밀 메시의 받침 면적은 {f['areas_mm2']['upper']:.3f}mm²로, 실제 CAD 면적 대비 {100*(f['areas_mm2']['upper']/f['exact_seating_CAD_area_mm2']-1):.2f}% 큽니다. 하중 총합은 유지했으며 경계 근사에 따른 국부 응력 차이가 남습니다.",'KS');p(f"새 뒤쪽 가이드 서비스 홈도 실제 형상에 포함했습니다. 세밀 메시의 홈 주변 절점 최대 등가응력은 {f['rear_service_notch_region_peak_MPa']:.4f}MPa입니다. 이는 매끈한 연속체의 절점 평균 응력으로, 작은 홈의 실제 출력 결함·층 분리·노치 파손을 검증한 값이 아닙니다.");p(A['comparison_description_ko']);p('프레임 원본 SHA256: '+f['source_step_sha256'],'KS')
story.append(PageBreak());p('열 검토: 계산 범위와 실물 시험','KT');p('팬의 실제 설치 풍량·정압곡선, 재순환, 방열판-보드-PLA 접촉 열저항이 없어 열FEM/공기CFD를 실행하지 않았습니다. 아래는 ΔT=P/(ρCpQ) 혼합 공기 에너지 수지입니다. PLA/CPU 온도나 그 상한으로 읽으면 안 됩니다.')
p(A['thermal_description_ko']);story.append(Image(str(R/'airflow_sensitivity.png'),width=432,height=245));p('주변25°C, ρ1.18kg/m³, Cp1005J/(kg K). 주변35°C에서 동일한 에너지 수지 결과는10°C 더 높습니다. 실측 설치 유량이나 PLA 접촉 온도를 대체하지 않습니다.','KS')
p('출력 후 확인 순서','KH');p('1. 같은 재료·방향·설정으로 레일 맞춤/적층 쿠폰을 출력하고 힘을 주지 않아도 동시에 끼워지는지 확인하세요.<br/>2. 먼저1단에서 최대 지속 부하·예상 최고 주변온도 조건으로 레일·기둥 어깨·소켓 부근 PLA 접촉 온도와 변위를 측정하세요.<br/>3. 45°C 접근/초과, 처짐 증가, 층분리나 영구변형이 있으면 중단하고 재료·냉각·보강을 재검토하세요. 45°C는 제조사 허용치가 아닌 임시 재검토 기준입니다.<br/>4. 외부 고정 후3단에서 지속 하중·온도 및 하중 제거 후 잔류변형을 반복 확인하세요. 수일 시험도 장기 수명 인증은 아닙니다.')
p('공식 근거와 재현','KH')
p(' / '.join(f'<link href="{url}" color="blue">{title}</link>' for title,url in A['sources']),'KS')
p('재현 패키지는 원본 STEP, 실제 메시·입력·출력·로그, 분석가정JSON, 검증/후처리/그림/보고서 생성 스크립트와CSV를 포함합니다. E민감도는 선형 스케일링이며 별도 실행이나 교정 물성이 아닙니다.','KS')
def footer(c,d):c.setFont('Helvetica',8);c.drawRightString(568,20,f'{d.page} / 4')
SimpleDocTemplate(str(R/A['report_filename']),pagesize=(612,792),rightMargin=44,leftMargin=44,topMargin=34,bottomMargin=33).build(story,onFirstPage=footer,onLaterPages=footer)
