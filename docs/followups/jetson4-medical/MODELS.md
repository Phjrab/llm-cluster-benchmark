# WS-MED — 모델 후보 상태

2026-10-23 공개 metadata + 기존 catalog. 아래 identity는 다운로드 승인이나 installed/runtime 검증이 아니다. 기존 8개는 재사용, M05/R02 두 항목만 추가.

|Key|Catalog 표시|양자화|크기(bytes)|공개 artifact metadata|
|---|---|---|---|---|
|M01|Qwen2.5 1.5B Instruct|Q4_K_M|1117320736|MATCH|
|M02|Granite 3.3 2B Instruct|Q4_K_M|1545303328|MATCH|
|M03|Qwen3 4B|Q4_K_M|2497280256|MATCH|
|M04|Gemma 3 4B IT QAT|Q4_0|3155051328|METADATA_UNAVAILABLE|
|M05|MedGemma 1.5 4B IT|Q4_K_M|2489894976|MATCH|
|M06|Qwen3 8B|Q4_K_M|5027783488|MATCH|
|M07|DeepSeek-R1 Distill Qwen 7B|Q4_K_M|4683073504|MATCH|
|R01|Qwen3 14B|Q4_K_M|9001752960|MATCH|
|R02|MedGemma 27B text IT|Q4_K_M|16546405376|MATCH|
|R03|Qwen3 32B|Q4_K_M|19762149024|MATCH|

모든 exact repository/revision/filename/SHA-256은 [model-candidates.json](model-candidates.json)에 있다. 이번 선택 파일은 모두 단일 GGUF이며 shards를 합산하거나 이름으로 checksum을 추정하지 않았다. 각 source checkpoint와 GGUF repository는 구분했다.

## 검증 수준

- M01/M02 원본 checkpoint revision은 기존 catalog에 없음(null). 배포 GGUF revision과 SHA는 고정되어 있다. 공개 source의 최신 revision을 conversion 원본으로 대체하지 않았다.
- M04 QAT는 이번 unauthenticated metadata 조회가 실패했다(curl 22). 기존 Google gated artifact의 identity는 보존했으며 라이선스 동의나 재검증을 수행하지 않았다.
- M05/R02는 Google 원본과 Unsloth community PTQ GGUF가 다르다. 공개 API metadata는 확인했으나 Google raw model card 조회 실패와 exact conversion lineage 미확인을 남겼다. Health AI Developer Foundations 약관은 사용자가 검토해야 한다.
- Template/tokenizer SHA 및 해당 모델의 pinned runtime 실행 적합성은 현재 study에서 UNKNOWN. 기존 모델 파일 검증이나 Dashboard의 설치 표시와 구분한다.
- M04의 QAT Q4_0, 다른 후보의 PTQ Q4_K_M은 같은 quant 조건이 아니다. 동일 base의 추가 exact quant identity가 없으므로 B7-quantization은 0건/보류다. Q8/dynamic quant를 섞어 우열을 주장하지 않는다.
- 70B 기존 catalog 항목은 변경하지 않았고 medical mapping/preset에 포함하지 않았다.

## Sampling

기본은 common_direct_greedy: temperature=0, top_p=0.9, seed=42. 이는 제조사 최적 권장값이라는 뜻이 아니다.

Qwen3 원본 card는 non-thinking temp=0.7/top_p=0.8/top_k=20/min_p=0 및 thinking temp=0.6/top_p=0.95를 안내한다. 현재 config는 enable_thinking hard switch/top_k/min_p를 제공하지 않는다. 모든 Qwen3 문항 끝에는 공식 soft switch `/no_think`만 동일하게 붙인다. 실제 effective non-thinking은 미검증이고 controlled 성능 집계에서 UNKNOWN으로 분리한다. 정답/추가 병력 힌트는 추가하지 않는다.

DeepSeek 선택 cohort는 temp=0.6/top_p=0.95, user-only template, max_tokens=1024다. 제조사의 긴 reasoning 설정(예: 32768 output) 전체를 재현하지 않는다. capped reasoning이며 일반 greedy/non-thinking 비교와 분리한다. leading `<think>…</think>` 이후 final segment만 deterministic parser가 평가하고 원문은 보존한다. 미닫힘/빈 final은 no_final_answer다.

## 출처

- [Qwen3 native modes/card](https://huggingface.co/Qwen/Qwen3-4B/blob/1cfa9a7208912126459214e8b04321603b3df60c/README.md)
- [DeepSeek official card](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/blob/916b56a44061fd5cd7d6a8fb632557ed4f724f60/README.md)
- [Google MedGemma 1.5](https://huggingface.co/google/medgemma-1.5-4b-it)
- [Google MedGemma 27B text](https://huggingface.co/google/medgemma-27b-text-it)
- [Health AI terms](https://developers.google.com/health-ai-developer-foundations/terms)
- 질문 원래 참고 출처: [SOURCES.md](../../../cluster/medical/data/SOURCES.md). 의료 rubric은 초안이며 한국어 우수성·임상 안전성 또는 의료 영상 평가를 보증하지 않는다.
