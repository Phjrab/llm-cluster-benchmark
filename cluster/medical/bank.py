"""Immutable input-only question bank. This module never opens evaluator keys."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).parent / "data"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def question_bank() -> dict[str, dict]:
    rows = {}
    for filename in ("medical_prompts.ko.jsonl", "medical_prompts.en_paired.jsonl"):
        for line in (DATA / filename).read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            key = item["prompt_id"]
            if key in rows or sha(item["text"]) != item["text_sha256"]:
                raise ValueError("QUESTION_ID_OR_HASH_MISMATCH")
            if not 1 <= item["max_output_tokens"] <= 1024 or item["clinical_use"] is not False:
                raise ValueError("INVALID_QUESTION_POLICY")
            rows[key] = item
    ko = [v for v in rows.values() if v["language"] == "ko"]
    en = [v for v in rows.values() if v["language"] == "en"]
    if len(ko) != 40 or len(en) != 8:
        raise ValueError("QUESTION_BANK_COUNT_MISMATCH")
    for row in en:
        pair = rows.get(row.get("paired_prompt_id"))
        if not pair or pair["language"] != "ko" or pair["category"] != row["category"]:
            raise ValueError("INVALID_TRANSLATION_PAIR")
    return rows


def model_mapping() -> dict[str, str]:
    return json.loads((DATA / "models.json").read_text(encoding="utf-8"))


def compose(question: dict, *, qwen3_soft_switch: bool = False) -> str:
    profiles = json.loads((DATA / "prompt_profiles.json").read_text(encoding="utf-8"))
    # Explicit allowlist: no dict serialization, scoring_key, rubric or label.
    text = profiles[question["instruction_profile"]] + "\n\n" + question["text"]
    return text + ("\n/no_think" if qwen3_soft_switch else "")


def long_document_variants() -> dict[str, dict]:
    """Same-text workloads, NOT tokenizer-calibrated lengths or padded targets."""
    base = question_bank()["DOC02"]
    output = {}
    fact = '대상 기록: "기침은 있다. 발열과 흉통은 부인한다. 호흡곤란 여부는 묻지 않았다."'
    instruction = "대상 기록만 추출하세요. 다른 합성 문서 번호는 대상자의 기록이 아닙니다. JSON 키 cough, fever, chest_pain, dyspnea의 값은 true/false/null입니다."
    for count, target in ((12, 512), (48, 2048), (96, 4096)):
        distractors = [f"[별도 합성 문서 {i:03d}] 문서 보관 순서는 접수 순서와 다르며, 이 줄에는 대상자의 증상이나 검사 결과가 없습니다." for i in range(count)]
        for position, offset in (("start", 0), ("middle", count // 2), ("end", count)):
            lines = list(distractors)
            lines.insert(offset, fact)
            text = instruction + "\n" + "\n".join(lines)
            key = f"DOC02-L{target}-{position}"
            output[key] = {**base, "prompt_id": key, "base_prompt_id": "DOC02",
                           "paired_prompt_id": "DOC02", "text": text, "text_sha256": sha(text),
                           "nominal_target_tokens": target, "input_tokens": None,
                           "target_status": "UNCALIBRATED_SAME_TEXT", "evidence_position": position}
    return output
