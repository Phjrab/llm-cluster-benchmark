"""Local, licensed B2 subset import. No dataset/network download or dispatch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .bank import sha

SOURCES = {
    "kormedmcqa": ("sean0042/KorMedMCQA", "79efd6f91edfc8036330d7a4daa88b9f2deb9a82", "cc-by-nc-2.0"),
    "pubmedqa": ("qiaojin/PubMedQA", "9001f2853fb87cab8d220904e0de81ac6973b318", "mit; abstract rights require review"),
}


def import_subset(rows, *, dataset, config, split, revision, answer_base=None,
                  seed=20261023, calibration=20, evaluation=200):
    repo, pinned, license_id = SOURCES[dataset]
    if revision != pinned or not 0 <= calibration <= 500 or not 1 <= evaluation <= 500:
        raise ValueError("SUBSET_PROVENANCE_OR_SIZE_INVALID")
    if dataset == "kormedmcqa":
        if config not in {"dentist", "doctor", "nurse", "pharm"} or split not in {"test", "dev"} or answer_base not in {0, 1}:
            raise ValueError("EXPLICIT_KORMED_CONFIG_SPLIT_ANSWER_BASE_REQUIRED")
    elif config != "pqa_labeled" or split != "train":
        raise ValueError("PUBMEDQA_LABELED_TRAIN_REQUIRED")
    inputs, keys, seen = [], {}, set()
    for row in rows:
        if dataset == "kormedmcqa":
            identity = [row[k] for k in ("subject", "year", "period", "q_number")]
            label = row["answer"]
            if type(label) is not int or not answer_base <= label < answer_base + 5:
                raise ValueError("INVALID_ANSWER_ENCODING")
            answer = "ABCDE"[label - answer_base]
            text = str(row["question"]) + "\n" + "\n".join(f"{k}. {row[k]}" for k in "ABCDE")
            lang, profile = "ko", "public_mcqa"
        else:
            identity = row["pubid"]
            answer = row["final_decision"]
            if answer not in {"yes", "no", "maybe"}:
                raise ValueError("INVALID_PUBMED_LABEL")
            ctx = row["context"]
            if not isinstance(ctx.get("contexts"), list) or len(ctx["contexts"]) != len(ctx.get("labels", [])):
                raise ValueError("LABELED_ABSTRACT_CONTEXT_REQUIRED")
            allowed = []
            for label, paragraph in zip(ctx["labels"], ctx["contexts"]):
                # No answer-derived predictions, long_answer, conclusions or unlabeled sections.
                if str(label).strip().upper() in {"BACKGROUND", "OBJECTIVE", "OBJECTIVES", "METHOD", "METHODS", "RESULT", "RESULTS"}:
                    allowed.append(f"[{str(label).strip().upper()}] {paragraph}")
            if not allowed:
                raise ValueError("NO_PERMITTED_CONTEXT")
            text = str(row["question"]) + "\n\n" + "\n".join(allowed)
            lang, profile = "en", "pubmedqa"
        pid = dataset + "-" + sha(json.dumps([config, split, identity], ensure_ascii=False))[:24]
        if pid in seen or not text.strip() or len(text) > 19000:
            raise ValueError("DUPLICATE_OR_OVERSIZE_DATASET_QUESTION")
        seen.add(pid)
        inputs.append({"prompt_id": pid, "language": lang, "category": dataset,
                       "instruction_profile": profile, "text": text, "text_sha256": sha(text),
                       "max_output_tokens": 128, "clinical_use": False})
        keys[pid] = {"prompt_id": pid, "reference_text_sha256": sha(text), "scoring_kind": "mcqa", "evaluation_only": True,
                     "reference_answer_structured": {"answer": answer},
                     "allowed_labels": list("ABCDE") if dataset == "kormedmcqa" else ["yes", "no", "maybe"]}
    # Immutable IDs + seed, independent of source row ordering. Calibration/evaluation are disjoint.
    inputs.sort(key=lambda q: sha(f"{seed}:{q['prompt_id']}"))
    cal, ev = inputs[:calibration], inputs[calibration:calibration + evaluation]
    used = cal + ev
    for cohort, questions in (("calibration", cal), ("evaluation", ev)):
        for question in questions:
            question["dataset_ref"] = {"repo": repo, "revision": revision, "config": config, "split": split,
                                       "seed": seed, "cohort": cohort, "provenance": "caller_attested_local_source"}
    if not ev:
        raise ValueError("NO_EVALUATION_ROWS_AFTER_CALIBRATION")
    manifest = {"dataset": dataset, "repo": repo, "revision": revision, "config": config, "source_split": split,
                "license": license_id, "seed": seed, "answer_base": answer_base,
                "calibration_ids": [q["prompt_id"] for q in cal], "evaluation_ids": [q["prompt_id"] for q in ev],
                "requested": {"calibration": calibration, "evaluation": evaluation},
                "actual": {"calibration": len(cal), "evaluation": len(ev)},
                "evaluation_label_distribution": {a: sum(keys[q["prompt_id"]]["reference_answer_structured"]["answer"] == a for q in ev)
                                                  for a in sorted({keys[q["prompt_id"]]["reference_answer_structured"]["answer"] for q in ev})},
                "scope": "fixed local subset; not an official full benchmark score"}
    return {"manifest": manifest, "calibration_questions": cal, "evaluation_questions": ev,
            "evaluation_only_keys": {q["prompt_id"]: keys[q["prompt_id"]] for q in used}}


def write_subset(subset, output: Path):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "evaluator-only").mkdir(mode=0o700)
    for name, value in (("manifest.json", subset["manifest"]),
                        ("calibration.questions.json", subset["calibration_questions"]),
                        ("evaluation.questions.json", subset["evaluation_questions"]),
                        ("evaluator-only/keys.json", subset["evaluation_only_keys"])):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
