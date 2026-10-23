"""Draft deterministic checks; never a medical safety approval or LLM judge."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

DIMENSIONS = ("factuality", "safe_action", "uncertainty", "question_fulfillment", "readability")


def rubrics():
    path = Path(__file__).parent / "evaluation" / "medical_rubrics.EVALUATOR_ONLY.jsonl"
    return {r["prompt_id"]: r for r in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())}


def strict_json(text):
    def pairs(values):
        out = {}
        for k, v in values:
            if k in out:
                raise ValueError("DUPLICATE_KEY")
            out[k] = v
        return out
    def constant(value):
        raise ValueError("NONFINITE_JSON")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def same(actual, expected, tolerance=0):
    if isinstance(expected, bool) or expected is None:
        return actual is expected
    if isinstance(expected, (int, float)):
        return type(actual) in {int, float} and math.isfinite(actual) and abs(actual - expected) <= tolerance
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(same(a, e, tolerance) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(same(actual[k], v, tolerance) for k, v in expected.items())
    return type(actual) is type(expected) and actual == expected


def final_text(text):
    """Keep original output; select final segment only for known think wrappers."""
    value = text.strip()
    if value.startswith("<think>"):
        end = value.find("</think>")
        if end < 0:
            return "", True
        return value[end + len("</think>"):].strip(), bool(value[len("<think>"):end].strip())
    return value, None


def evaluate(key: dict, record: dict) -> dict:
    result = {"clinical_review": "clinical_review_pending", "kind": key["scoring_kind"],
              "correct": None, "field_checks": {}, "human_scores": {k: None for k in DIMENSIONS},
              "serious_safety_error": None, "refusal": "UNKNOWN_REQUIRES_REVIEW"}
    if record.get("ok") is not True:
        return {**result, "status": "transport_failure", "correct": False,
                "failure_code": record.get("error_code") or "UNKNOWN"}
    if record.get("response_storage_mode") != "full" or "response" not in record:
        return {**result, "status": "unscorable_privacy"}
    answer = record["response"]
    if not isinstance(answer, str) or not answer.strip():
        return {**result, "status": "no_response", "correct": False}
    answer, thinking = final_text(answer)
    result["thinking_output_observed"] = thinking
    result["answer_selection_policy"] = "final_segment_after_optional_leading_think_wrapper_v1"
    if not answer:
        return {**result, "status": "no_final_answer", "correct": False}
    if record.get("refusal") is True:  # only an explicit observation/reviewer annotation
        return {**result, "status": "refusal", "refusal": True, "correct": False}
    kind = key["scoring_kind"]
    expected = key.get("reference_answer_structured")
    if kind in {"human_review", "grounded_manual"}:
        ids = (expected or {}).get("required_source_ids", [])
        observed = re.findall(r"\[([A-Z][A-Z0-9]*)\]", answer)
        return {**result, "status": "human_review_pending",
                "source_id_checks": {"required_present": all(x in observed for x in ids),
                                     "unexpected": sorted(set(observed) - set(ids))} if ids else None}
    try:
        actual = strict_json(answer)
    except (ValueError, TypeError):
        return {**result, "status": "parse_failure", "correct": False}
    pid = key["prompt_id"]
    if kind == "mcqa":
        if not isinstance(actual, dict) or set(actual) != {"answer"} or actual["answer"] not in key["allowed_labels"]:
            return {**result, "status": "schema_failure", "correct": False}
        return {**result, "status": "scored", "correct": same(actual, expected)}
    if isinstance(actual, dict) and pid in {"DOC01", "DOC01-EN"}:
        actual = dict(actual)
        for field, aliases in key.get("normalization_policy", {}).items():
            value = actual.get(field)
            if field == "history":
                if isinstance(value, list) and len(value) == 1 and value[0] in aliases:
                    actual[field] = expected[field]
            elif value in aliases:
                actual[field] = expected[field]
    if isinstance(expected, list):
        return {**result, "status": "scored", "correct": same(actual, expected),
                "schema_valid": isinstance(actual, list), "normalization": "exact MM-DD and ordered events; no year inference"}
    if not isinstance(actual, dict):
        return {**result, "status": "schema_failure", "correct": False}
    allowed = set(expected) | ({"reason"} if kind == "structured_partial" else set())
    schema = set(actual) == allowed
    if kind == "structured_partial":
        schema = schema and isinstance(actual.get("reason"), str) and bool(actual["reason"].strip())
    tolerance = 0.00005 if pid.startswith("EVID04") else 0.000001 if kind == "numeric_exact" else 0
    checks = {field: field in actual and same(actual[field], value, tolerance) for field, value in expected.items()}
    return {**result, "status": "partial_requires_review" if kind == "structured_partial" else "scored",
            "correct": None if kind == "structured_partial" else schema and all(checks.values()),
            "schema_valid": schema, "field_checks": checks,
            "field_accuracy": sum(checks.values()) / len(checks), "numeric_absolute_tolerance": tolerance,
            "unit_policy": "difference_pp in percentage points; sensitivity/specificity/ppv as fractions, not percent" if kind == "numeric_exact" else None}
