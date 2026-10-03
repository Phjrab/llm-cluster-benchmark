from pathlib import Path
import json
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.enums import TA_LEFT
R=Path(__file__).resolve().parent
pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='KO',fontName='HYSMyeongJo-Medium',fontSize=10,leading=15,spaceAfter=8,wordWrap='CJK'));styles.add(ParagraphStyle(name='KH',fontName='HYSMyeongJo-Medium',fontSize=13,leading=18,spaceBefore=11,spaceAfter=7));styles.add(ParagraphStyle(name='KT',fontName='HYSMyeongJo-Medium',fontSize=20,leading=27,spaceAfter=12));styles.add(ParagraphStyle(name='KS',fontName='HYSMyeongJo-Medium',fontSize=8,leading=11,spaceAfter=5,wordWrap='CJK'))
story=[]
def p(t,style='KO'):story.append(Paragraph(t,styles[style]))
def table(rows,widths):
 data=[[Paragraph(str(x),styles['KS']) for x in row] for row in rows];t=Table(data,colWidths=widths,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#e9eef2')),('GRID',(0,0),(-1,-1),.4,HexColor('#d9d9d9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(t);story.append(Spacer(1,9))
p('Jetsonstack v2 PLA 예비 검토','KT');p('실제 프레임 정적해석 및 열 민감도 | 2026년 10월 3일','KS')
p('선택한 자중 조건에서 프레임 처짐은 작았습니다. 먼저 PLA 시험편과 1단을 출력해 온도를 확인하는 것을 권합니다. 3단 PLA의 장기 사용 가능 여부는 아직 확정할 수 없습니다. 열 및 크리프, 중력 접합의 이탈 및 전도는 별도 검증이 필요합니다.')
p('해석 가정','KH')
table([['항목','채택 조건'],['재료 및 출력','Bambu Lab PLA Basic 임시 기준. 0.4mm 노즐, 0.42mm 선폭, 0.2mm 층높이, 4벽, 30% gyroid, 상하 각5층. Cubicon과 동등 물성 가정 없음'],['형상','rounded-v2 실제 STEP. 142×132mm, 바닥6mm, 기둥16mm, 피치60mm, 3단185.5mm. 두1mm 가이드 필수'],['질량','키트300g/층 가정(공식174g보다 여유). 상부 출력부150g/층. 하단 프레임 CAD전체 자중134.9g'],['지지와 하중','네 모서리 Z구속 및 최소 XY구속. 두 레일에 자기 키트2.943N, 네 기둥 어깨에 상부 두 층8.829N, 프레임 자체 중력'],['솔버','Gmsh4.13.1 + CalculiX2.20. C3D10, 선형 정적. 가장 아래1단에 위 두 층 하중을 전달하는 등가 모델'],['유효 재료','E=500/1000/2000MPa 민감도, ν=0.35 가정. 실제 출력물에 대해 교정한 물성 또는 검증된 하한은 아님']],[88,436])
p('공식 물성을 그대로 사용하지 않은 이유','KH')
p('Bambu TDS V3.0의 탄성계수는 XY2580±220MPa, Z2060±170MPa입니다. HDT는54°C(1.8MPa)/57°C(0.45MPa), 유리전이는60°C입니다. 시험편은100% infill이고55°C에서8시간 열처리 및 건조했습니다. 이번30% 미열처리 출력물에 공식 강도를 대입한 안전율은 산출하지 않았습니다.')
p('4벽의 단순16mm 기둥은 재료 단면율 약56%, 상하1mm 표면층을 둔6mm 바닥의 이상적 굽힘 단면강성은 약79%입니다. 같은30% infill도 부위별 강성 기여가 다릅니다. 실제 층 방향 및 벽 및 소켓 및 내부 격자는 이번 연속체 모델에서 직접 해석하지 않았습니다. HDT는 장기 허용온도가 아니며, 맞춤 변형 위험 때문에 임의 열처리를 권하지 않습니다.')
story.append(PageBreak())
p('정적 결과와 검증','KT')
table([['메시','노드 / 요소','레일 수직처짐','최대 벡터변위','최대 등가응력'],['4.0mm','72,232 / 38,998','0.011221mm','0.012174mm','0.2776MPa'],['2.8mm','154,234 / 88,507','0.011392mm','0.012373mm','0.2887MPa']],[65,126,104,112,117])
p('위 값은 E=1000MPa 연속체의 결과입니다. 4→2.8mm 세분화에서 처짐 변화1.53%, 최대응력 변화3.99%였습니다. 응력 집중은 레일 끝/가이드 홈 부근(x126,y92.5,z6mm)에 나타났습니다. 날카로운 모서리의 절점 평균 응력에는 메시 의존성이 남습니다.')
story.append(Image(str(R/'static_results.png'),width=524,height=234));p('실제 해석장 표면 보간. 변형 전 형상. 30% 격자와 적층 파손을 직접 표시하는 그림이 아닙니다.','KS')
table([['가정 유효E','500MPa','1000MPa','2000MPa'],['레일 처짐','0.022785mm','0.011392mm','0.005696mm']],[140,128,128,128])
p('E500/2000 결과는 선형 문제의 변위1/E 비례를 이용한 스케일링입니다. 힘과 포아송비가 같으면 응력은 변하지 않습니다. 실제 재료 결함이나 열에 의한 강성 저하가 이 탐색 범위 안에 있다고 보장하지 않습니다.')
p('검증 및 계산 한계','KH')
p('두 최종 메시의 비양의 Jacobian은0개입니다. CAD 대비 메시 체적 오차는0.417%→0.200%입니다. 정육면체 압축 패치 시험은 해석해와 일치했고, 구속절점의 중력하중을 보정한 반력 균형 오차는1.4 x 10^-7 이하였습니다. 최초 곡면2차 메시의 음의 Jacobian 결과는 폐기하고, 최종 양의 Jacobian 직선 경계2차 요소만 사용했습니다.')
p('지지는 접촉/이탈을 푸는 방식이 아니라 운동학적 대체입니다. 3단 전체 접촉, 마찰, 핀 굽힘, 조립력, 후크 강도, 층분리, 크리프, 진동 및 낙하 및 전도는 계산하지 않았습니다. 핀은 잠금 부품이 아니며 위층만 잡고 들면 안 됩니다. 외부 브레이싱/고정을 유지하세요.')
story.append(PageBreak())
p('열 검토와 출력 후 확인','KT')
p('실제 팬 풍량 및 정압과 접촉 열저항이 없어 열 FEM/공기 CFD는 실행하지 않았습니다. 아래는 ΔT=P/(ρCpQ)의 혼합 공기 에너지 수지 민감도입니다. 칩 온도나 이 배출공기 값이 PLA 온도를 뜻하지 않습니다.')
p('가정: 주변25°C, 공기ρ1.18kg/m³ 및 Cp1005J/(kg K), 세 층 총45/75/90W. NVIDIA25W 모드는 모듈 예산이며 전체 장치 발열의 확정값은 아닙니다. 90W는 주변부 여유를 더한 가정입니다. 풍량은 명목 팬 사양의 합이 아닌, 세 층의 열을 실제로 운반하는 공유 유효풍량입니다.','KS')
story.append(Image(str(R/'airflow_sensitivity.png'),width=480,height=285))
p('총90W와 유효5/10CFM 가정에서 혼합 배출공기는57.2/41.1°C입니다. 주변35°C이면67.2/51.1°C입니다. 국부 전도 및 재순환 및 핫스폿 때문에 실제 PLA 온도의 예측이나 상한으로 사용할 수 없습니다.')
p('실물 시험 순서','KH')
p('1. 시험편 맞춤을 확인하고, 두1mm 가이드와 후크의 균열 및 층분리 및 서포트 잔여물을 점검하세요.<br/>2. 1단에서 레일 및 기둥 어깨 및 소켓 부근 접촉식 온도를 측정하세요. 최대 지속 부하와 예상 최고 주변온도에서 온도 안정 후에도 유지 관찰하세요.<br/>3. 45°C 접근/초과, 처짐 증가, 층분리, 영구변형 시 중단하고 재료 및 냉각 및 보강을 재검토하세요. 45°C는 제조사 허용치가 아닌 임시 재검토 기준입니다.<br/>4. 3단은 별도 외부 고정 후 같은 온도 시험과 전원 꺼진 지속 하중 시험을 반복하세요. 하중 제거 후 잔류변형도 확인하세요. 수일 시험도 장기 수명 인증은 아닙니다.')
p('근거와 재현 파일','KH')
p('<link href="https://store.bblcdn.com/s7/default/b189de92249a4b9ebed28b8ea1f080f0/Bambu_PLA_Basic_Technical_Data_Sheet.pdf" color="blue">Bambu PLA Basic TDS V3.0 pp2-5</link>  및  <link href="https://forums.developer.nvidia.com/t/jetson-orin-nano-hw-faq/249118" color="blue">NVIDIA HW FAQ Q10</link>  및  <link href="https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonOrinNanoSeriesJetsonOrinNxSeriesAndJetsonAgxOrinSeries.html" color="blue">NVIDIA 전력 관리</link>  및  <link href="https://www.dhondt.de/ccx_2.20.pdf" color="blue">CalculiX 매뉴얼</link>  및  <link href="https://gmsh.info/doc/texinfo/" color="blue">Gmsh 문서</link><br/>재현 ZIP: 실제 STEP, 메시/솔버 입력 및 출력 및 로그, 가정과 상세 한계, 검증 및 그림 생성 스크립트. 원본 CAD는 수정하지 않았습니다.','KS')
doc=SimpleDocTemplate(str(R/'Jetsonstack_v2_PLA_preliminary_review_ko.pdf'),pagesize=(612,792),rightMargin=44,leftMargin=44,topMargin=38,bottomMargin=34)
def footer(c,d):c.setFont('Helvetica',8);c.drawRightString(568,20,f'{d.page} / 3')
doc.build(story,onFirstPage=footer,onLaterPages=footer)
