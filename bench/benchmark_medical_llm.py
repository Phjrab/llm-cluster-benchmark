#!/usr/bin/env python3
"""의학용 안내문 생성 벤치마크용 LLM 평가 스크립트.

시각 판독 결과와 환자 맥락을 바탕으로,
LLM이 지정된 조건을 모두 포함하는 한국어 안내문을 생성하도록 유도한 뒤,
필수 키워드 충족 여부를 자동 검증하여 점수를 계산한다.

실제 추론 엔진(HuggingFace, vLLM, Ollama 등)은
generate_llm_response() 내부만 교체하면 연결할 수 있도록 설계했다.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import threading
import time
import statistics

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

try:
    from jtop import jtop

    JTOP_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - optional dependency
    jtop = None
    JTOP_IMPORT_ERROR = str(exc)


# -----------------------------------------------------------------------------
# 1) 벤치마크용 샘플 데이터셋
# -----------------------------------------------------------------------------
MEDICAL_SAMPLE_DATA: List[Dict[str, object]] = [
    {
        "index": 1,
        "vision_analysis": "우안: 백내장(Cataract) 의심 89%, 좌안: 정상",
        "patient_context": "70대 어르신, '요즘 눈에 안개가 낀 것처럼 뿌옇게 보인다'고 호소함.",
        "question": "AI 분석 결과 우안에 백내장이 89% 확률로 의심되며 좌안은 정상입니다. 이 70대 어르신이 불안해하지 않고 이해하기 쉽도록, 친절하고 따뜻한 말투로 3문장 이내로 요약해 주고 안과 방문을 권유하세요.",
        "required_keywords": ["우안", "백내장", "안과", "방문"],
    },
    {
        "index": 2,
        "vision_analysis": "양안: 녹내장(Glaucoma) 고위험군 판독 95% (시신경 유두 함몰 관찰)",
        "patient_context": "40대 직장인, 최근 건강검진에서 안압이 높다는 소견을 받은 적 있음.",
        "question": "분석 결과 양쪽 눈 모두 녹내장 고위험군으로 95% 판독되었습니다. 녹내장은 조기 치료가 중요한 질환임을 강조하면서, 사무적인 톤으로 명확하게 3문장 이내로 상황을 설명하고 정밀 검사를 권고하세요.",
        "required_keywords": ["양쪽", "녹내장", "조기", "정밀"],
    },
    {
        "index": 3,
        "vision_analysis": "우안: 당뇨망막병증(Diabetic Retinopathy) 78%, 점상 출혈 발견",
        "patient_context": "50대 여성, 10년째 당뇨 약 복용 중.",
        "question": "우안에서 당뇨망막병증이 78% 확률로 의심되며 미세 출혈이 발견되었습니다. 당뇨 환자임을 감안하여 합병증 관리의 중요성을 포함해 3문장 이내로 진단 결과를 설명하세요.",
        "required_keywords": ["오른쪽", "당뇨망막병증", "출혈", "합병증"],
    },
    {
        "index": 4,
        "vision_analysis": "양안: 정상(Normal) 99%",
        "patient_context": "20대 대학생, 최근 스마트폰을 많이 봐서 눈이 피로하다고 느낌.",
        "question": "검사 결과 양안 모두 99% 정상입니다. 단순 피로로 인한 증상일 수 있음을 설명하고, 눈 건강을 위한 휴식 방법을 제안하는 안심시키는 멘트를 3문장 이내로 작성하세요.",
        "required_keywords": ["정상", "피로", "휴식"],
    },
    {
        "index": 5,
        "vision_analysis": "좌안: 황반변성(Macular Degeneration) 의심 82%",
        "patient_context": "60대 남성, '최근 선이 구불구불하게 보이고 시야 중심이 까맣게 보인다'고 호소함.",
        "question": "좌안 황반변성이 82% 확률로 의심됩니다. 환자가 말한 '구불구불하게 보이는 증상'이 이 질환과 연관이 있음을 설명하고, 실명 위험을 막기 위해 즉시 병원에 가야 함을 3문장 이내로 작성하세요.",
        "required_keywords": ["왼쪽", "황반변성", "구불구불", "병원"],
    },
    {
        "index": 6,
        "vision_analysis": "분석 실패: 이미지 조도 부족 및 초점 흐림 (Confidence < 30%)",
        "patient_context": "사용자가 기기 앞에서 너무 많이 움직여 사진이 흔들림.",
        "question": "사진이 흔들리고 어두워서 AI가 정확한 분석을 할 수 없습니다. 사용자에게 시스템 오류가 아님을 친절히 안내하고, 눈을 크게 뜨고 다시 촬영해 달라는 안내 멘트를 2문장으로 작성하세요.",
        "required_keywords": ["다시", "촬영", "흔들", "정확"],
    },
    {
        "index": 7,
        "vision_analysis": "양안: 건성안 증후군(Dry Eye Syndrome) 소견",
        "patient_context": "30대 프로그래머, 하루 10시간 이상 모니터를 봄. 눈이 뻑뻑하고 이물감이 심함.",
        "question": "양안 안구건조증 소견이 보입니다. 직업적 특성을 공감해주면서, 인공눈물 사용과 모니터 사용 중 휴식의 필요성을 포함해 3문장 이내로 안내하세요.",
        "required_keywords": ["안구건조증", "인공눈물", "모니터", "휴식"],
    },
    {
        "index": 8,
        "vision_analysis": "우안: 익상편(Pterygium, 군날개) 검출 88%, 동공 침범 전 단계",
        "patient_context": "50대 농부, 야외 활동이 잦음. 눈에 흰 막이 자라나는 것 같다고 함.",
        "question": "우안에 익상편(군날개)이 88% 의심되며 아직 동공을 가리지는 않았습니다. 자외선 노출이 원인일 수 있음을 설명하고, 선글라스 착용 권장과 안과 방문을 3문장 이내로 요약하세요.",
        "required_keywords": ["오른쪽", "군날개", "자외선", "선글라스"],
    },
    {
        "index": 9,
        "vision_analysis": "좌안: 결막염(Conjunctivitis) 의심 91%, 심한 충혈",
        "patient_context": "10대 학생, 눈이 가렵고 눈곱이 많이 끼며 충혈됨.",
        "question": "좌안 결막염이 91% 의심됩니다. 전염성이 있을 수 있으므로 손을 씻고 눈을 비비지 말라는 주의사항을 포함해 3문장 이내로 학생이 이해하기 쉽게 설명하세요.",
        "required_keywords": ["왼쪽", "결막염", "전염", "비비지"],
    },
    {
        "index": 10,
        "vision_analysis": "양안: 정상(Normal) 95% (단, 환자는 비문증 호소)",
        "patient_context": "40대 여성, '사진에는 정상이지만 눈앞에 날파리가 떠다닌다'고 입력함.",
        "question": "카메라 분석 결과 눈의 외관은 정상이나, 환자가 호소하는 날파리증(비문증)은 안저 깊은 곳의 문제이거나 노화 현상일 수 있음을 설명하고 정밀 망막 검사를 권하는 멘트를 3문장 이내로 작성하세요.",
        "required_keywords": ["비문증", "정상", "망막", "검사"],
    },
    {
        "index": 11,
        "vision_analysis": "우안: 백내장 중기 75%, 좌안: 백내장 초기 40%",
        "patient_context": "65세 여성, 양쪽 눈의 시력 차이가 심해져서 두통이 옴.",
        "question": "오른쪽 눈은 백내장이 꽤 진행되었고 왼쪽은 초기 단계입니다. 양쪽 눈의 시력 차이로 인해 두통이 발생했을 가능성을 짚어주고, 수술적 치료 상담을 위해 병원 방문을 3문장 이내로 권유하세요.",
        "required_keywords": ["오른쪽", "초기", "두통", "수술"],
    },
    {
        "index": 12,
        "vision_analysis": "좌안: 포도막염(Uveitis) 의심 65%, 결막염과 혼동 주의",
        "patient_context": "30대 남성, 며칠 전부터 눈이 심하게 붉어지고 통증과 눈부심이 심함.",
        "question": "단순 결막염이 아닌 포도막염이 의심되는 상태입니다. 염증이 눈 내부에 발생하여 시력 저하를 유발할 수 있는 응급 상황일 수 있음을 경고하며, 즉시 안과에 가야 함을 3문장 이내로 강하게 설명하세요.",
        "required_keywords": ["포도막염", "내부", "시력", "응급"],
    },
    {
        "index": 13,
        "vision_analysis": "분석 불가: 동공 가려짐 (머리카락 또는 안경테)",
        "patient_context": "사용자가 뿔테 안경을 벗지 않고 앞머리가 눈을 가린 채로 촬영함.",
        "question": "안경이나 앞머리로 인해 눈이 가려져 AI가 각막과 동공을 정확히 분석할 수 없습니다. 안경을 벗고 앞머리를 넘긴 뒤 다시 촬영해 달라는 키오스크 안내 음성용 멘트를 2문장으로 작성하세요.",
        "required_keywords": ["안경", "앞머리", "가려져", "다시"],
    },
    {
        "index": 14,
        "vision_analysis": "우안: 망막박리(Retinal Detachment) 의심 위험군 (시야 결손 호소 기반)",
        "patient_context": "50대 고도근시 환자, 시야 한쪽에 검은 커튼이 쳐진 것처럼 보인다고 함.",
        "question": "고도근시 환자에게 나타난 시야 결손 증상으로 미루어 응급 질환인 망막박리가 강하게 의심됩니다. 지체할 경우 영구적 실명 위험이 있으므로 응급실이나 대학병원 안과를 즉시 찾으라는 안내를 3문장 이내로 작성하세요.",
        "required_keywords": ["망막박리", "실명", "응급실", "대학병원"],
    },
    {
        "index": 15,
        "vision_analysis": "양안: 정상(Normal) 98% (단, 안구 진탕증 미세 관찰됨)",
        "patient_context": "초등학생, 보호자가 '아이가 책을 볼 때 눈동자가 미세하게 떨린다'고 입력함.",
        "question": "눈의 구조적 이상은 없으나 눈동자가 떨리는 안구 진탕(눈떨림)이 관찰됩니다. 이것은 시력 발달에 영향을 줄 수 있으므로 소아안과 전문의의 검진이 필요하다는 점을 보호자에게 3문장 이내로 친절히 안내하세요.",
        "required_keywords": ["정상", "눈떨림", "시력", "소아안과"],
    },
    {
        "index": 16,
        "vision_analysis": "좌안: 각막 궤양(Corneal Ulcer) 의심 72%",
        "patient_context": "20대 여성, 콘택트렌즈를 낀 채로 자주 자거나 수영장 다녀온 후 극심한 통증 호소.",
        "question": "잘못된 렌즈 착용 습관으로 인한 각막 궤양이 강하게 의심됩니다. 즉시 렌즈 착용을 중지하고 각막 손상 치료를 위해 안과를 방문해야 한다는 내용을 3문장 이내로 경고하세요.",
        "required_keywords": ["렌즈", "각막", "궤양", "중지"],
    },
    {
        "index": 17,
        "vision_analysis": "우안: 망막전막(Epiretinal Membrane) 의심 60%",
        "patient_context": "60대 여성, 글씨가 미세하게 찌그러져 보인다고 하나 황반변성 소견은 약함.",
        "question": "망막 앞쪽에 얇은 막이 생기는 망막전막 질환이 의심됩니다. 당장 실명하는 병은 아니지만 정기적인 추적 관찰이 필요하다는 점을 환자가 안심할 수 있도록 3문장 이내로 설명하세요.",
        "required_keywords": ["막", "망막전막", "안심", "관찰"],
    },
    {
        "index": 18,
        "vision_analysis": "양안: 다래끼(Hordeolum) 의심 94%, 눈꺼풀 부종",
        "patient_context": "30대 직장인, 최근 야근이 잦았고 눈꺼풀 겉면이 부어오르고 아픔.",
        "question": "피로 누적으로 인한 겉다래끼가 의심됩니다. 온찜질이 도움이 될 수 있으며, 임의로 짜내지 말고 안과에서 처방받은 안약을 사용하라는 조언을 3문장 이내로 요약하세요.",
        "required_keywords": ["다래끼", "피로", "온찜질", "짜내지"],
    },
    {
        "index": 19,
        "vision_analysis": "양안: 정상(Normal) 99% (질문: 식염수로 눈을 씻어도 되나요?)",
        "patient_context": "환자가 눈이 가렵다며 인공눈물 대신 식염수나 소금물로 씻어도 되는지 텍스트로 질문함.",
        "question": "AI 분석은 정상이지만, 환자의 소금물/식염수 세척 질문에 대해 의학적으로 위험하다는 점을 명확히 하고 멸균된 무방부제 인공눈물을 사용해야 함을 3문장 이내로 단호하게 답변하세요.",
        "required_keywords": ["소금물", "위험", "멸균", "인공눈물"],
    },
    {
        "index": 20,
        "vision_analysis": "좌안: 녹내장 말기 의심 85%, 우안: 정상",
        "patient_context": "50대 남성, 한쪽 눈이 거의 안 보인다고 호소함. 이전 병원 기록 없음.",
        "question": "왼쪽 눈의 녹내장 진행이 상당히 심각한 상태로 분석되었습니다. 더 이상의 시신경 손상을 막는 것이 최우선이므로, 즉각적인 약물 치료나 레이저 시술 상담이 필요함을 3문장 이내로 심각하게 강조하세요.",
        "required_keywords": ["녹내장", "시신경", "손상", "즉각적"],
    },
    {
        "index": 21,
        "vision_analysis": "우안: 결막하출혈(Subconjunctival Hemorrhage) 98%",
        "patient_context": "40대 남성, 아침에 일어났더니 오른쪽 눈 흰자가 피가 터진 것처럼 새빨개서 매우 당황하며 키오스크를 찾음.",
        "question": "분석 결과 시력에 지장이 없는 단순 결막하출혈입니다. 환자가 겉보기에 심각해 보여 매우 놀란 상태이므로, 피로 기침 등으로 실핏줄이 터진 것이며 시간이 지나면 자연스럽게 흡수된다는 점을 강조하여 3문장 이내로 안심시키세요.",
        "required_keywords": ["결막하출혈", "실핏줄", "자연스럽게", "안심"],
    },
    {
        "index": 22,
        "vision_analysis": "양안: 알레르기성 결막염(Allergic Conjunctivitis) 소견",
        "patient_context": "20대 여성, 봄철 꽃가루 때문에 눈이 너무 가려워 계속 비비고 있음.",
        "question": "양안에 알레르기성 결막염이 의심됩니다. 눈을 비비면 증상이 더 악화되고 각막에 상처가 날 수 있으므로 절대 비비지 말고, 냉찜질과 항히스타민 안약이 필요하다는 점을 3문장 이내로 안내하세요.",
        "required_keywords": ["알레르기", "비비지", "냉찜질", "안약"],
    },
    {
        "index": 23,
        "vision_analysis": "양안: 정상(Normal) 99%, 각막/수정체 특이사항 없음",
        "patient_context": "50대 초반 남성, '가까운 글씨가 잘 안 보이고 눈이 침침해서 큰 병에 걸린 것 같다'고 호소함.",
        "question": "AI 분석상 눈의 구조적 질환은 없습니다. 환자의 연령과 증상을 고려할 때 자연스러운 노안(Presbyopia)이 시작된 것일 확률이 높으므로, 안경원에서 돋보기(근거리 안경) 처방을 받아보라는 권유를 3문장 이내로 친절히 작성하세요.",
        "required_keywords": ["정상", "노안", "자연스러운", "돋보기"],
    },
    {
        "index": 24,
        "vision_analysis": "우안: 중심장액맥락망막병증(CSC) 의심 70%",
        "patient_context": "30대 직장인 남성, 최근 야근과 스트레스가 극심했으며 시야 가운데가 동그랗게 까맣게 보인다고 함.",
        "question": "과로와 스트레스로 인해 망막 중심부에 물이 차는 질환(CSC)이 의심됩니다. 스트레스 관리가 최우선 치료법 중 하나임을 설명하고, 자연 회복이 가능하지만 안과 진료가 꼭 필요함을 3문장 이내로 설명하세요.",
        "required_keywords": ["스트레스", "망막", "물이", "과로"],
    },
    {
        "index": 25,
        "vision_analysis": "좌안: 검열반(Pinguecula) 검출 92%",
        "patient_context": "60대 여성, 눈 흰자에 노란색 볼록한 점이 생겨 황달이나 큰 병인 줄 알고 걱정함.",
        "question": "익상편과 달리 각막을 침범하지 않는 양성 점(검열반)입니다. 황달이나 위험한 병이 아님을 명확히 밝히고, 이물감이 심할 때만 안약을 쓰면 된다는 점을 3문장 이내로 작성해 환자를 안심시키세요.",
        "required_keywords": ["검열반", "황달", "양성", "이물감"],
    },
    {
        "index": 26,
        "vision_analysis": "좌안: 전방출혈(Hyphema) 및 각막 손상 의심",
        "patient_context": "10대 남학생, 테니스공에 눈을 정통으로 맞은 직후 통증과 함께 시야가 붉게 보인다고 호소함.",
        "question": "안구 외상으로 인해 눈 내부 전방에 출혈이 발생한 심각한 응급 상황입니다. 절대 눈을 누르거나 비비지 말고 머리를 높인 상태로 즉시 안과 응급실로 가야 함을 3문장 이내로 강력하게 경고하세요.",
        "required_keywords": ["외상", "출혈", "응급실", "절대"],
    },
    {
        "index": 27,
        "vision_analysis": "양안: 각막 지형 이상 (원추각막, Keratoconus 의심)",
        "patient_context": "20대 남성, 최근 안경을 새로 맞춰도 계속 난시가 심해지고 시력이 안 나온다고 함. 평소 눈을 심하게 비비는 습관이 있음.",
        "question": "눈을 비비는 습관으로 인해 각막이 얇아지고 돌출되는 원추각막이 의심됩니다. 일반 안경으로는 시력 교정이 어려우며 특수 렌즈나 시술이 필요할 수 있으니 대학병원급 안과 방문을 3문장 이내로 권유하세요.",
        "required_keywords": ["비비는", "원추각막", "난시", "대학병원"],
    },
    {
        "index": 28,
        "vision_analysis": "우안: 각막염(Keratitis) 소견, 수포성 병변 관찰 (헤르페스 의심)",
        "patient_context": "40대 여성, 최근 면역력이 떨어져 입술에 포진이 생겼고 오른쪽 눈이 심하게 시리고 눈물이 남.",
        "question": "면역력 저하로 인한 헤르페스 바이러스성 각막염이 의심됩니다. 일반 인공눈물로는 치료되지 않으며 항바이러스제 처방이 필수적임을 설명하고 신속한 병원 방문을 3문장 이내로 권고하세요.",
        "required_keywords": ["면역력", "바이러스", "항바이러스제", "처방"],
    },
    {
        "index": 29,
        "vision_analysis": "양안: 사시(Strabismus) 판독 85%, 우안 외사시 소견",
        "patient_context": "5세 아동의 보호자, 아이가 멍하게 TV를 볼 때 한쪽 눈이 바깥쪽으로 돌아가는 것 같다고 입력함.",
        "question": "사진 분석상 오른쪽 눈의 시선이 바깥으로 향하는 외사시가 의심됩니다. 소아 사시는 시력 발달(약시)에 치명적인 영향을 줄 수 있으므로 조속히 소아안과 전문의를 찾아야 함을 3문장 이내로 보호자에게 안내하세요.",
        "required_keywords": ["사시", "시력", "발달", "소아안과"],
    },
    {
        "index": 30,
        "vision_analysis": "분석 실패: 환자 눈 감음 (Eyes Closed)",
        "patient_context": "촬영 순간에 환자가 눈을 깜빡여 눈이 완전히 감긴 사진이 찍힘.",
        "question": "사진 촬영 시 눈이 감겨 질환 분석을 진행할 수 없습니다. 시스템이나 눈의 이상이 아니니, 화면의 안내선에 맞추어 눈을 크게 뜨고 다시 한번 촬영 버튼을 눌러달라는 안내를 2문장으로 작성하세요.",
        "required_keywords": ["감겨", "크게", "다시", "촬영"],
    },
    {
        "index": 31,
        "vision_analysis": "시신경 이상 의심 (동공 반사 비대칭), 질환 확률: 시신경염 60%",
        "patient_context": "30대 여성, 갑자기 한쪽 눈의 시력이 급격히 떨어지고 눈알을 움직일 때마다 뻐근한 통증이 있음.",
        "question": "시신경염 등 급성 시신경 질환이 강하게 의심됩니다. 색각 이상이나 영구적 시력 손상을 막기 위해 골든타임 내에 신경안과 진료 및 MRI 검사가 필요할 수 있음을 3문장 이내로 심각하게 안내하세요.",
        "required_keywords": ["시신경", "급격히", "통증", "골든타임"],
    },
    {
        "index": 32,
        "vision_analysis": "좌안: 콩다래끼(Chalazion) 소견 90%, 급성 염증 소견 없음",
        "patient_context": "20대 여성, 한 달 전 눈꺼풀에 뭐가 났는데 아프지는 않지만 딱딱한 몽우리가 남아있어 미관상 신경 쓰임.",
        "question": "통증 없는 콩다래끼로 분석됩니다. 급성 염증(겉다래끼)과 달리 약물 치료 효과가 적을 수 있으며, 온찜질을 꾸준히 하거나 안과에서 간단한 절개 시술을 받을 수 있음을 3문장 이내로 설명하세요.",
        "required_keywords": ["콩다래끼", "통증", "온찜질", "절개"],
    },
    {
        "index": 33,
        "vision_analysis": "양안: 극심한 충혈 및 표면 손상 (화학화상 배제 불가)",
        "patient_context": "50대 남성, 청소 중 락스(염기성 세제) 방울이 눈에 튀어 극심한 작열감을 호소하며 뛰어옴.",
        "question": "분석을 떠나 락스와 같은 화학물질이 눈에 들어간 것은 최상위 응급 상황입니다. 즉시 흐르는 물이나 생리식염수로 15분 이상 눈을 충분히 씻어낸 후 119나 응급실로 직행하라는 내용을 3문장 이내로 다급하게 지시하세요.",
        "required_keywords": ["화학물질", "응급", "흐르는 물", "15분"],
    },
    {
        "index": 34,
        "vision_analysis": "양안: 안검염(Blepharitis) 82%, 눈꺼풀 테두리 발적",
        "patient_context": "60대 남성, 속눈썹 쪽에 비듬 같은 것이 생기고 눈이 자주 충혈되며 가려움.",
        "question": "눈꺼풀 테두리의 피지선이 막혀 생기는 안검염 소견이 보입니다. 이는 안구건조증의 주원인이 되므로, 처방 안약과 함께 눈꺼풀 전용 세정제로 아침저녁 닦아주는 관리가 필요함을 3문장 이내로 안내하세요.",
        "required_keywords": ["안검염", "피지선", "안구건조증", "세정제"],
    },
    {
        "index": 35,
        "vision_analysis": "분석 실패: 역광 및 심한 빛 반사 (Extreme Glare)",
        "patient_context": "키오스크 바로 뒤에 밝은 창문이나 조명이 있어 환자의 얼굴이 새까맣게 역광으로 찍힘.",
        "question": "강한 역광과 빛 반사 때문에 얼굴이 어둡게 찍혀 눈동자를 인식할 수 없습니다. 키오스크 방향을 돌리거나 조명을 조절한 뒤 다시 촬영해 달라는 기술적 안내 멘트를 2문장으로 작성하세요.",
        "required_keywords": ["역광", "빛 반사", "어둡게", "조절"],
    },
    {
        "index": 36,
        "vision_analysis": "양안: 안저 색소 변화 의심 (망막색소변성증, RP 특이 소견)",
        "patient_context": "20대 남성, 어두운 곳에 가면 전혀 보이지 않는 야맹증이 갈수록 심해지고 시야가 좁아지는 느낌이 듦.",
        "question": "야맹증과 시야 협착을 동반하는 망막색소변성증(RP) 등 유전성 망막 질환이 의심되는 소견입니다. 매우 전문적인 진료가 필요한 질환이므로 지체 없이 대학병원 망막 전문의를 찾아야 함을 3문장 이내로 진지하게 안내하세요.",
        "required_keywords": ["야맹증", "시야", "유전성", "대학병원"],
    },
    {
        "index": 37,
        "vision_analysis": "우안: 녹내장 의심단계 (Glaucoma Suspect), 시신경 유두비 약간 증가",
        "patient_context": "40대 남성, 아무 증상이 없으나 AI가 녹내장 '의심'이라고 띄워서 매우 불안해함.",
        "question": "시신경 모양이 녹내장과 비슷하게 보이지만, 아직 확진이 아닌 '의심 단계(경계선)'입니다. 당장 실명하는 것이 아니니 절대 불안해하지 말고, 예방 차원에서 1년에 한 번씩 안과 정기 검진만 받으면 된다고 3문장 이내로 안심시키세요.",
        "required_keywords": ["의심 단계", "실명", "정기 검진", "안심"],
    },
    {
        "index": 38,
        "vision_analysis": "좌안: 단안 백내장(외상성 의심) 85%",
        "patient_context": "30대 후반 남성, 노인도 아닌데 한쪽 눈만 눈앞이 뿌옇게 흐려진다고 함. 과거 눈을 부딪친 적 있음.",
        "question": "젊은 나이임에도 한쪽 눈에만 백내장 소견이 발견되었습니다. 이는 과거의 외상이나 염증이 원인일 수 있음을 짚어주고, 시력 회복을 위해 수술적 치료가 가능하니 전문의 상담을 받으라고 3문장 이내로 요약하세요.",
        "required_keywords": ["젊은", "외상", "한쪽", "수술"],
    },
    {
        "index": 39,
        "vision_analysis": "양안: 정상(Normal), 시각적 피로도 높음",
        "patient_context": "20대 대학생, '블루라이트 차단 안경만 쓰면 눈 피로가 완벽히 사라지는지' 키오스크에 질문함.",
        "question": "구조적 질환은 없으나 디지털 기기 사용으로 인한 피로입니다. 블루라이트 안경이 보조적 도움은 주지만 근본적 해결책은 아니며, 20분마다 20피트 먼 곳을 20초간 바라보는 '20-20-20 규칙' 실천이 중요함을 3문장 이내로 답변하세요.",
        "required_keywords": ["블루라이트", "보조적", "20-20-20", "규칙"],
    },
    {
        "index": 40,
        "vision_analysis": "우안: 각막 찰과상(Corneal Abrasion) 강력 의심",
        "patient_context": "30대 여성, 아기랑 놀아주다가 아기 손톱에 눈을 살짝 긁힌 후 눈을 뜰 수 없을 정도로 눈물이 나고 아픔.",
        "question": "아기 손톱에 의한 각막 긁힘(찰과상)으로 극심한 통증이 유발된 상태입니다. 세균 감염으로 각막 궤양까지 번지는 것을 막기 위해 항생제 안약 처방이 필수이니 오늘 당장 안과를 방문하라고 3문장 이내로 작성하세요.",
        "required_keywords": ["손톱", "찰과상", "세균 감염", "항생제"],
    },
]

DEFAULT_OUTPUT_DIR = Path("outputs") / "medical_benchmark"


# -----------------------------------------------------------------------------
# 2) 응답 템플릿
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = """너는 의학 판독 결과를 환자에게 설명하는 한국어 안내문 생성 모델이다.

반드시 3문장 이내의 자연스러운 안내문만 출력해야 한다.
다른 제목, 번호, 머리말, 꼬리말, 코드블록, 목록은 절대 추가하지 마라.

반드시 주어진 분석 결과와 환자 맥락을 반영하고,
지정된 필수 키워드를 자연스럽게 포함해야 한다.
"""


def build_cot_prompt(item: Dict[str, object]) -> str:
    """분석 결과와 요청사항을 포함한 단일 프롬프트 문자열로 변환한다."""

    vision_analysis = str(item["vision_analysis"])
    patient_context = str(item["patient_context"])
    question = str(item["question"])
    required_keywords = item["required_keywords"]

    keyword_line = ", ".join(str(keyword) for keyword in required_keywords)

    user_prompt = "\n".join(
        [
            "[시각 분석]",
            vision_analysis,
            "",
            "[환자 맥락]",
            patient_context,
            "",
            "[요청]",
            question,
            "",
            "[필수 키워드]",
            keyword_line,
            "",
            "[출력 규칙]",
            "1. 3문장 이내로 작성한다.",
            "2. 따뜻하고 친절한 말투 또는 요청된 톤을 유지한다.",
            "3. 필수 키워드를 자연스럽게 모두 포함한다.",
        ]
    )

    return f"[SYSTEM]\n{SYSTEM_PROMPT}\n\n[USER]\n{user_prompt}"


def parse_args() -> argparse.Namespace:
    """명령행 인자를 파싱한다."""

    parser = argparse.ArgumentParser(description="의료용 CoT 벤치마크 스크립트")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="결과 파일을 저장할 출력 폴더 경로",
    )
    return parser.parse_args()


# -----------------------------------------------------------------------------
# 3) 추론 함수(플러그인 교체 가능)
# -----------------------------------------------------------------------------
def _extract_section(prompt: str, header: str) -> str:
    """모의 응답 생성을 위해 프롬프트에서 특정 섹션 본문만 추출한다."""

    pattern = rf"\[{re.escape(header)}\]\s*(.*?)\s*(?:\n\s*\[|$)"
    match = re.search(pattern, prompt, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return prompt.strip()


def _build_keyword_sentence(required_keywords: Sequence[str]) -> str:
    if not required_keywords:
        return ""

    if len(required_keywords) == 1:
        return required_keywords[0]

    return ", ".join(required_keywords[:-1]) + f" 그리고 {required_keywords[-1]}"


def generate_llm_response(prompt: str) -> str:
    """LLM 추론 함수의 자리 표시자.

    실제 연동 시 이 함수 내부를 HuggingFace/vLLM/Ollama 호출로 교체하면 된다.
    현재 구현은 스크립트가 바로 실행되도록 샘플 안내문에 대한 모의 응답을 반환한다.
    """

    analysis = _extract_section(prompt, "시각 분석")
    context = _extract_section(prompt, "환자 맥락")
    request = _extract_section(prompt, "요청")
    keywords_line = _extract_section(prompt, "필수 키워드")
    keywords = [keyword.strip() for keyword in keywords_line.split(",") if keyword.strip()]
    keyword_sentence = _build_keyword_sentence(keywords)

    tone = "따뜻하게" if "친절" in request or "따뜻" in request else "명확하게"
    first_sentence = f"{analysis}라는 결과가 확인되었고, {context}을 고려하면 {tone} 설명이 필요합니다."
    second_sentence = f"지금은 {keyword_sentence}를 중심으로 안내드리며, 필요한 경우 빠르게 안과 진료를 받아보시는 것이 좋습니다."
    third_sentence = "증상이 더 심해지거나 불편이 지속되면 지체하지 말고 의료진과 상담하세요."

    return f"{first_sentence} {second_sentence} {third_sentence}"


# -----------------------------------------------------------------------------
# 4) 정답 파서 및 평가기
# -----------------------------------------------------------------------------
@dataclass
class EvaluationResult:
    """문항별 평가 결과를 저장하는 구조체."""

    index: int
    vision_analysis: str
    patient_context: str
    question: str
    required_keywords: List[str]
    matched_keywords: List[str]
    missing_keywords: List[str]
    is_correct: bool
    reasoning_failure: Optional[str]
    raw_response: str


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def evaluate_keyword_coverage(response: str, required_keywords: Sequence[str]) -> Tuple[List[str], List[str]]:
    """응답이 필수 키워드를 포함하는지 검사한다."""

    normalized_response = _normalize_text(response)
    matched_keywords: List[str] = []
    missing_keywords: List[str] = []

    for keyword in required_keywords:
        normalized_keyword = _normalize_text(str(keyword))
        if normalized_keyword and normalized_keyword in normalized_response:
            matched_keywords.append(str(keyword))
        else:
            missing_keywords.append(str(keyword))

    return matched_keywords, missing_keywords


def evaluate_dataset(dataset: Sequence[Dict[str, object]]) -> List[EvaluationResult]:
    """데이터셋 전체를 순회하며 추론, 파싱, 채점을 수행한다."""

    results: List[EvaluationResult] = []

    for index, item in enumerate(dataset, start=1):
        prompt = build_cot_prompt(item)
        response = generate_llm_response(prompt)
        required_keywords = [str(keyword) for keyword in item["required_keywords"]]
        matched_keywords, missing_keywords = evaluate_keyword_coverage(response, required_keywords)
        is_correct = not missing_keywords
        failure_reason = None if is_correct else f"필수 키워드 누락: {', '.join(missing_keywords)}"

        results.append(
            EvaluationResult(
                index=index,
                vision_analysis=str(item["vision_analysis"]),
                patient_context=str(item["patient_context"]),
                question=str(item["question"]),
                required_keywords=required_keywords,
                matched_keywords=matched_keywords,
                missing_keywords=missing_keywords,
                is_correct=is_correct,
                reasoning_failure=failure_reason,
                raw_response=response,
            )
        )

        # Hardware and timing aggregation
        ttft_list: List[float] = []
        tps_list: List[float] = []
        peak_rss_mb: Optional[float] = None

        class _SimpleHardwareMonitor:
            def __init__(self, poll_interval_s: float = 0.5):
                self.poll_interval_s = poll_interval_s
                self._stop = threading.Event()
                self._thread: Optional[threading.Thread] = None
                self._lock = threading.Lock()
                self._sample_count = 0
                self._power_w: List[float] = []
                self._gpu_temp_c: List[float] = []

            def start(self) -> None:
                if jtop is None:
                    return
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

            def stop(self) -> Dict[str, Optional[float]]:
                if self._thread is not None:
                    self._stop.set()
                    self._thread.join(timeout=max(1.0, self.poll_interval_s * 4.0))
                with self._lock:
                    return {
                        "sample_count": self._sample_count,
                        "avg_power_w": statistics.mean(self._power_w) if self._power_w else None,
                        "peak_gpu_temp_c": max(self._gpu_temp_c) if self._gpu_temp_c else None,
                    }

            def _run(self) -> None:
                try:
                    with jtop() as jetson_obj:
                        while jetson_obj.ok() and not self._stop.is_set():
                            try:
                                stats = jetson_obj.stats if isinstance(jetson_obj.stats, dict) else {}
                                power = None
                                if isinstance(jetson_obj.power, dict):
                                    for k, v in jetson_obj.power.items():
                                        if "tot" in k or "vdd_in" in k or "in" in k:
                                            power = v
                                            break
                                if power is None:
                                    # fallback to stats
                                    for k, v in stats.items():
                                        if "power" in k or "pwr" in k:
                                            power = v
                                            break

                                gpu_temp = None
                                temp_obj = getattr(jetson_obj, "temperature", None) or {}
                                if isinstance(temp_obj, dict):
                                    for k, v in temp_obj.items():
                                        if "gpu" in k and ("temp" in k or "temperature" in k):
                                            gpu_temp = v
                                with self._lock:
                                    self._sample_count += 1
                                    if power is not None:
                                        try:
                                            self._power_w.append(float(power))
                                        except Exception:
                                            pass
                                    if gpu_temp is not None:
                                        try:
                                            self._gpu_temp_c.append(float(gpu_temp))
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            if self._stop.wait(self.poll_interval_s):
                                break
                except Exception:
                    return

        monitor = _SimpleHardwareMonitor(poll_interval_s=0.5)
        monitor.start()

        try:
            for index, item in enumerate(dataset, start=1):
                prompt = build_cot_prompt(item)

                # Timing: measure TTFT and generation duration around the generate call.
                request_start = time.perf_counter()
                response = generate_llm_response(prompt)
                finished_at = time.perf_counter()

                ttft_s = finished_at - request_start
                # Synchronous mock; generation_time is 0. Use ttft as proxy for throughput calc.
                generated_tokens = len(response.split()) if response else 0
                tps = generated_tokens / ttft_s if ttft_s > 0 else 0.0

                # Track process RSS if psutil available
                if psutil is not None:
                    try:
                        proc = psutil.Process()
                        rss_mb = proc.memory_info().rss / (1024.0 * 1024.0)
                        peak_rss_mb = rss_mb if peak_rss_mb is None else max(peak_rss_mb, rss_mb)
                    except Exception:
                        pass

                required_keywords = [str(keyword) for keyword in item["required_keywords"]]
                matched_keywords, missing_keywords = evaluate_keyword_coverage(response, required_keywords)
                is_correct = not missing_keywords
                failure_reason = None if is_correct else f"필수 키워드 누락: {', '.join(missing_keywords)}"

                ttft_list.append(ttft_s)
                tps_list.append(tps)

                results.append(
                    EvaluationResult(
                        index=index,
                        vision_analysis=str(item["vision_analysis"]),
                        patient_context=str(item["patient_context"]),
                        question=str(item["question"]),
                        required_keywords=required_keywords,
                        matched_keywords=matched_keywords,
                        missing_keywords=missing_keywords,
                        is_correct=is_correct,
                        reasoning_failure=failure_reason,
                        raw_response=response,
                    )
                )
        finally:
            hw_metrics = monitor.stop()

        # Aggregate hardware/timing metrics
        avg_tps = statistics.mean(tps_list) if tps_list else 0.0
        avg_ttft_s = statistics.mean(ttft_list) if ttft_list else 0.0
        if peak_rss_mb is None:
            try:
                # fallback to resource maxrss
                import resource

                peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:
                peak_rss_mb = None

        combined_metrics = {
            "avg_tps": round(avg_tps, 3),
            "avg_ttft_s": round(avg_ttft_s, 4),
            "peak_rss_mb": round(peak_rss_mb, 2) if peak_rss_mb is not None else None,
            "peak_gpu_temp_c": hw_metrics.get("peak_gpu_temp_c"),
            "avg_power_w": hw_metrics.get("avg_power_w"),
            "hw_sample_count": int(hw_metrics.get("sample_count") or 0),
        }

        return results, combined_metrics


# -----------------------------------------------------------------------------
# 5) 리포트 출력
# -----------------------------------------------------------------------------
def print_summary(results: Sequence[EvaluationResult]) -> None:
    """채점 결과를 사람이 읽기 쉬운 형태로 출력한다."""

    total_questions = len(results)
    passed_items = sum(1 for result in results if result.is_correct)
    pass_rate = (passed_items / total_questions * 100.0) if total_questions else 0.0
    average_keyword_coverage = (
        sum(len(result.matched_keywords) / len(result.required_keywords) for result in results if result.required_keywords)
        / total_questions * 100.0
        if total_questions
        else 0.0
    )

    reasoning_failures = [
        result for result in results if result.reasoning_failure is not None
    ]

    print("\n==================== 의료 안내문 벤치마크 요약 ====================")
    print(f"총 문항 수: {total_questions}")
    print(f"통과 수: {passed_items}")
    print(f"통과율: {pass_rate:.2f}%")
    print(f"평균 키워드 충족률: {average_keyword_coverage:.2f}%")

    if reasoning_failures:
        print("\n파싱/형식 실패 내역:")
        for result in reasoning_failures:
            reason = result.reasoning_failure or "예측 정답 없음"
            print(f"- [{result.index}] 누락={', '.join(result.missing_keywords)} / 사유={reason}")
    else:
        print("\n파싱/형식 실패 내역: 없음")

    print("\n문항별 결과:")
    for result in results:
        status = "통과" if result.is_correct else "미달"
        matched = ", ".join(result.matched_keywords) if result.matched_keywords else "N/A"
        missing = ", ".join(result.missing_keywords) if result.missing_keywords else "N/A"
        print(f"- [{result.index}] {status} | 충족={matched} | 누락={missing}")


def save_results(results: Sequence[EvaluationResult], output_dir: str, combined_metrics: Optional[Dict[str, object]] = None) -> None:
    """평가 결과를 별도 하위 폴더에 저장한다."""

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    total_questions = len(results)
    passed_items = sum(1 for result in results if result.is_correct)
    pass_rate = (passed_items / total_questions * 100.0) if total_questions else 0.0
    average_keyword_coverage = (
        sum(len(result.matched_keywords) / len(result.required_keywords) for result in results if result.required_keywords)
        / total_questions * 100.0
        if total_questions
        else 0.0
    )

    summary_payload = {
        "total_questions": total_questions,
        "passed_items": passed_items,
        "pass_rate": round(pass_rate, 2),
        "average_keyword_coverage": round(average_keyword_coverage, 2),
        "reasoning_failures": [
            {
                "index": result.index,
                "matched_keywords": result.matched_keywords,
                "required_keywords": result.required_keywords,
                "missing_keywords": result.missing_keywords,
                "reasoning_failure": result.reasoning_failure,
            }
            for result in results
            if result.reasoning_failure is not None
        ],
    }

    # Inject combined hardware/timing metrics into top-level summary when provided
    if combined_metrics:
        summary_payload.update(
            {
                "model_name": combined_metrics.get("model_name", "medical_benchmark"),
                "accuracy_percent": round(pass_rate, 2),
                "avg_tps": combined_metrics.get("avg_tps"),
                "avg_ttft_s": combined_metrics.get("avg_ttft_s"),
                "peak_rss_mb": combined_metrics.get("peak_rss_mb"),
                "peak_gpu_temp_c": combined_metrics.get("peak_gpu_temp_c"),
                "avg_power_w": combined_metrics.get("avg_power_w"),
            }
        )

    detail_payload = [
        {
            "index": result.index,
            "vision_analysis": result.vision_analysis,
            "patient_context": result.patient_context,
            "question": result.question,
            "required_keywords": result.required_keywords,
            "matched_keywords": result.matched_keywords,
            "missing_keywords": result.missing_keywords,
            "is_correct": result.is_correct,
            "reasoning_failure": result.reasoning_failure,
            "raw_response": result.raw_response,
        }
        for result in results
    ]

    summary_path = target_dir / "medical_benchmark_summary.json"
    detail_path = target_dir / "medical_benchmark_details.json"

    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_path.write_text(json.dumps(detail_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[INFO] 요약 저장: {summary_path}")
    print(f"[INFO] 상세 저장: {detail_path}")


def main() -> None:
    """스크립트 진입점."""

    args = parse_args()
    results, combined_metrics = evaluate_dataset(MEDICAL_SAMPLE_DATA)
    # Attach a model name to combined metrics for output clarity
    if combined_metrics is None:
        combined_metrics = {}
    combined_metrics.setdefault("model_name", "medical_benchmark")

    print_summary(results)
    save_results(results, args.output_dir, combined_metrics)


if __name__ == "__main__":
    main()