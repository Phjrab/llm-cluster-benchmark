# 출처와 근거 범위

확인일: 2026-10-23. URL은 출처 탐색용이며 exact revision을 고정한 실행 Identity가 아니다.
개별 모델 파일·라이선스·runtime 호환성·질문 평가 기준은 실행 준비 시 다시 검증한다.

- 모델 원본/양자화 배포 카드는 모델 존재·기능·설정·제약 근거다. Jetson 4대 실측 성능 근거가 아니다.
- 의료 자료는 임상 rubric 초안을 검토하기 위한 참고다. 이 세트의 임상 타당성 인증은 아니다.
- 합성 문서·통계 문제의 숫자와 정답은 이 패키지에서 새로 작성했다.
- 원본 벤치마크와 사용자 정의 한국어 질문, 영어 번역 대응 질문은 서로 다른 cohort로 보고한다.
- KorMedMCQA 카드에는 비상업 라이선스 표시가 확인되었으나, 이용·배포 조건은 실제 선택 revision에서 재확인한다.
- PubMedQA는 질문+허용된 abstract context로 yes/no/maybe를 평가한다. long answer/결론/정답 키 누출을 막는다.

## NIDDK_A1C
https://www.niddk.nih.gov/health-information/diagnostic-tests/a1c-test/

## NIDDK_DIAGNOSIS
https://www.niddk.nih.gov/health-information/professionals/clinical-tools-patient-management/diabetes/diabetes-prediabetes

## CDC_ANTIBIOTICS
https://www.cdc.gov/antibiotic-use/about/

## FDA_ANTIBIOTICS
https://www.fda.gov/consumers/consumer-updates/know-when-and-how-use-antibiotics-and-when-skip-them

## AHA_CHEST
https://www.heart.org/en/health-topics/heart-attack/warning-signs-of-a-heart-attack

## CDC_STROKE
https://www.cdc.gov/stroke/signs-symptoms/

## NHS_ANAPHYLAXIS
https://www.nhs.uk/conditions/anaphylaxis/

## NHS_HEMATEMESIS
https://www.nhs.uk/symptoms/vomiting-blood/

## NEI_RETINA
https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases/retinal-detachment

## NEI_GLAUCOMA
https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases/glaucoma

## NEI_DR
https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases/diabetic-retinopathy

## NEI_CATARACT
https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases/cataracts

## NEI_DRY
https://www.nei.nih.gov/eye-health-information/eye-conditions-and-diseases/dry-eye

## MEDGEMMA_15
https://huggingface.co/google/medgemma-1.5-4b-it

## MEDGEMMA_27
https://huggingface.co/google/medgemma-27b-text-it

## MEDGEMMA_15_GGUF
https://huggingface.co/unsloth/medgemma-1.5-4b-it-GGUF

## MEDGEMMA_27_GGUF
https://huggingface.co/unsloth/medgemma-27b-text-it-GGUF

## GEMMA3_QAT
https://huggingface.co/google/gemma-3-4b-it-qat-q4_0-gguf

## QWEN3_4
https://huggingface.co/Qwen/Qwen3-4B-GGUF

## QWEN3_8
https://huggingface.co/Qwen/Qwen3-8B-GGUF

## QWEN3_14
https://huggingface.co/Qwen/Qwen3-14B-GGUF

## QWEN3_32
https://huggingface.co/Qwen/Qwen3-32B-GGUF

## DEEPSEEK_R1
https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B

## KORMED_PAPER
https://arxiv.org/abs/2403.01469

## KORMED_DATA
https://huggingface.co/datasets/sean0042/KorMedMCQA

## PUBMEDQA
https://pubmedqa.github.io/

## PUBMEDQA_CODE
https://github.com/pubmedqa/pubmedqa

## 프로젝트 확인 근거
https://github.com/Phjrab/llm-cluster-benchmark/blob/186dc69474610f0436d693dd339da155b83e7ae1/cluster/domain/experiment.py
https://github.com/Phjrab/llm-cluster-benchmark/blob/186dc69474610f0436d693dd339da155b83e7ae1/cluster/config/model_catalog.json

해당 domain 기준 n_ctx 128~16384, max_tokens 1~1024, prompt는 단일 문자열이다. 임의 배열/알 수 없는 sampling key를 넣으면 실행할 수 있다고 가정하지 않는다. 파일럿·실측·승인 상태는 현재 source에서 재확인해야 한다.
