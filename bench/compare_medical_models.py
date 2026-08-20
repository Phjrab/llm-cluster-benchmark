#!/usr/bin/env python3
"""의학용 LLM 다중 모델 비교 스크립트.

이 스크립트는 시각 판독 기반 안내문 벤치마크를 여러 모델에 대해 반복 실행하고,
모델별 키워드 충족률과 누락 내역을 별도 출력 폴더에 저장한다.

실제 추론 연결부는 generate_llm_response()에서 교체하면 된다.
현재 기본 구현은 모델 이름과 요청 키워드를 바탕으로 모의 안내문을 생성한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import threading
import time
import statistics

try:
    import psutil
except Exception:
    psutil = None

try:
    from jtop import jtop

    JTOP_IMPORT_ERROR = None
except Exception as exc:
    jtop = None
    JTOP_IMPORT_ERROR = str(exc)

if __package__ is None or __package__ == "":
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from bench.benchmark_medical_llm import (  # noqa: E402
    MEDICAL_SAMPLE_DATA,
    build_cot_prompt,
    evaluate_keyword_coverage,
)

DEFAULT_OUTPUT_DIR = Path("outputs") / "medical_benchmark"


# -----------------------------------------------------------------------------
# 2) 데이터 구조
# -----------------------------------------------------------------------------
@dataclass
class ModelSpec:
    """비교 대상 모델 1개를 나타내는 설정."""

    name: str
    path: str
    n_gpu_layers: Optional[int] = None
    n_ctx: Optional[int] = None
    max_tokens: Optional[int] = None


@dataclass
class EvaluationResult:
    """문항별 채점 결과."""

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


@dataclass
class ModelRunResult:
    """모델별 전체 실행 결과."""
    model_name: str
    model_path: str
    total_questions: int
    correct_answers: int
    reasoning_failure_count: int
    accuracy: float
    average_keyword_coverage: float
    reasoning_failures: List[Dict[str, object]]
    items: List[Dict[str, object]]
    # New hardware/timing metrics
    avg_tps: Optional[float] = None
    avg_ttft_s: Optional[float] = None
    peak_rss_mb: Optional[float] = None
    peak_gpu_temp_c: Optional[float] = None
    avg_power_w: Optional[float] = None
    hw_sample_count: Optional[int] = None


# -----------------------------------------------------------------------------
# 3) 입력 및 파싱
# -----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    """명령행 인자를 파싱한다."""

    parser = argparse.ArgumentParser(description="의학 전용 다중 모델 비교 스크립트")
    parser.add_argument("--models-file", required=True, help="CSV 파일 경로: name,path[,n_gpu_layers,n_ctx,max_tokens]")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR / "compare"), help="결과 저장 폴더")
    parser.add_argument("--output-csv", default=None, help="모델별 요약 CSV 경로")
    parser.add_argument("--details-json", default=None, help="모델별 상세 JSON 경로")
    parser.add_argument("--limit", type=int, default=0, help="데이터셋 일부만 테스트할 때 사용하는 상한값")
    return parser.parse_args()


def to_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return int(value)


def resolve_model_path(raw_path: str, models_file: str) -> str:
    path = Path(raw_path).expanduser()
    if path.is_file():
        return str(path.resolve())

    models_dir = Path(models_file).resolve().parent
    project_root = Path(__file__).resolve().parent.parent

    candidates = [
        models_dir / raw_path,
        Path.cwd() / raw_path,
        project_root / raw_path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    normalized = raw_path.replace("\\", "/")
    if "/models/" in normalized:
        suffix = normalized.split("/models/", 1)[1]
        candidate = project_root / "models" / suffix
        if candidate.is_file():
            return str(candidate.resolve())

    return raw_path


def load_model_specs(models_file: str) -> List[ModelSpec]:
    """모델 CSV를 읽어 비교 대상 목록을 만든다."""

    if not os.path.isfile(models_file):
        raise FileNotFoundError(f"Models CSV not found: {models_file}")

    specs: List[ModelSpec] = []
    with open(models_file, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Models CSV is empty.")

        required = {"name", "path"}
        if not required.issubset({name.strip() for name in reader.fieldnames}):
            raise ValueError("Models CSV must include headers: name,path")

        for row in reader:
            name = (row.get("name") or "").strip()
            path = (row.get("path") or "").strip()
            if not name or not path:
                continue
            specs.append(
                ModelSpec(
                    name=name,
                    path=resolve_model_path(path, models_file),
                    n_gpu_layers=to_optional_int(row.get("n_gpu_layers")),
                    n_ctx=to_optional_int(row.get("n_ctx")),
                    max_tokens=to_optional_int(row.get("max_tokens")),
                )
            )

    if not specs:
        raise ValueError(f"No valid model rows found in {models_file}")

    return specs


# -----------------------------------------------------------------------------
# 4) 프롬프트 및 모의 추론
# -----------------------------------------------------------------------------
def _extract_section(prompt: str, header: str) -> str:
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


def generate_llm_response(prompt: str, model_spec: ModelSpec) -> str:
    """모델별 응답 생성 함수의 자리 표시자.

    실제 환경에서는 여기에서 HuggingFace/vLLM/Ollama 호출로 교체하면 된다.
    현재 구현은 모델 이름과 요청 키워드에 따라 일관된 모의 응답을 만든다.
    """

    analysis = _extract_section(prompt, "시각 분석")
    context = _extract_section(prompt, "환자 맥락")
    request = _extract_section(prompt, "요청")
    keywords_line = _extract_section(prompt, "필수 키워드")
    keywords = [keyword.strip() for keyword in keywords_line.split(",") if keyword.strip()]
    keyword_sentence = _build_keyword_sentence(keywords)

    tone = "따뜻하게" if "친절" in request or "안심" in request else "명확하게"
    if "fail" in model_spec.name.lower() and keywords:
        keywords = keywords[:-1]
        keyword_sentence = _build_keyword_sentence(keywords)

    first_sentence = f"{analysis}라는 결과가 확인되었고, {context}을 고려하면 {tone} 설명이 필요합니다."
    second_sentence = f"지금은 {keyword_sentence}를 중심으로 안내드리며, 필요한 경우 빠르게 안과 진료를 받아보시는 것이 좋습니다."
    third_sentence = "증상이 더 심해지거나 불편이 지속되면 지체하지 말고 의료진과 상담하세요."

    return f"{first_sentence} {second_sentence} {third_sentence}"


# -----------------------------------------------------------------------------
# 5) 평가 및 저장
# -----------------------------------------------------------------------------
def evaluate_dataset(dataset: Sequence[Dict[str, object]], model_spec: ModelSpec) -> Tuple[List[EvaluationResult], Dict[str, object]]:
    results: List[EvaluationResult] = []

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

            request_start = time.perf_counter()
            response = generate_llm_response(prompt, model_spec)
            finished_at = time.perf_counter()

            ttft_s = finished_at - request_start
            generated_tokens = len(response.split()) if response else 0
            tps = generated_tokens / ttft_s if ttft_s > 0 else 0.0

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

    avg_tps = statistics.mean(tps_list) if tps_list else 0.0
    avg_ttft_s = statistics.mean(ttft_list) if ttft_list else 0.0
    if peak_rss_mb is None:
        try:
            import resource

            peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            peak_rss_mb = None

    combined = {
        "avg_tps": round(avg_tps, 3),
        "avg_ttft_s": round(avg_ttft_s, 4),
        "peak_rss_mb": round(peak_rss_mb, 2) if peak_rss_mb is not None else None,
        "peak_gpu_temp_c": hw_metrics.get("peak_gpu_temp_c"),
        "avg_power_w": hw_metrics.get("avg_power_w"),
        "hw_sample_count": int(hw_metrics.get("sample_count") or 0),
    }

    return results, combined


def summarize_results(results: Sequence[EvaluationResult], model_spec: ModelSpec, combined: Optional[Dict[str, object]] = None) -> ModelRunResult:
    total_questions = len(results)
    correct_answers = sum(1 for result in results if result.is_correct)
    reasoning_failure_count = sum(1 for result in results if result.reasoning_failure is not None)
    accuracy = (correct_answers / total_questions * 100.0) if total_questions else 0.0
    average_keyword_coverage = (
        sum(len(result.matched_keywords) / len(result.required_keywords) for result in results if result.required_keywords)
        / total_questions * 100.0
        if total_questions
        else 0.0
    )

    reasoning_failures = [
        {
            "index": result.index,
            "matched_keywords": result.matched_keywords,
            "required_keywords": result.required_keywords,
            "missing_keywords": result.missing_keywords,
            "reasoning_failure": result.reasoning_failure,
        }
        for result in results
        if result.reasoning_failure is not None
    ]

    items = [
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

    return ModelRunResult(
        model_name=model_spec.name,
        model_path=model_spec.path,
        total_questions=total_questions,
        correct_answers=correct_answers,
        reasoning_failure_count=reasoning_failure_count,
        accuracy=accuracy,
        average_keyword_coverage=average_keyword_coverage,
        reasoning_failures=reasoning_failures,
        items=items,
        avg_tps=combined.get("avg_tps") if combined else None,
        avg_ttft_s=combined.get("avg_ttft_s") if combined else None,
        peak_rss_mb=combined.get("peak_rss_mb") if combined else None,
        peak_gpu_temp_c=combined.get("peak_gpu_temp_c") if combined else None,
        avg_power_w=combined.get("avg_power_w") if combined else None,
        hw_sample_count=combined.get("hw_sample_count") if combined else None,
    )


def print_model_summary(summary: ModelRunResult) -> None:
    print(f"\n==================== {summary.model_name} ====================")
    print(f"모델 경로: {summary.model_path}")
    print(f"총 문항 수: {summary.total_questions}")
    print(f"통과 수: {summary.correct_answers}")
    print(f"추론/형식 실패 수: {summary.reasoning_failure_count}")
    print(f"통과율: {summary.accuracy:.2f}%")
    print(f"평균 키워드 충족률: {summary.average_keyword_coverage:.2f}%")

    if summary.reasoning_failures:
        print("파싱/형식 실패 내역: 있음")
        for failure in summary.reasoning_failures:
            print(f"- [{failure['index']}] 누락={', '.join(failure['missing_keywords'])} / 사유={failure['reasoning_failure']}")
    else:
        print("파싱/형식 실패 내역: 없음")


def save_outputs(summaries: Sequence[ModelRunResult], output_dir: str, output_csv: Optional[str], details_json: Optional[str]) -> None:
    """모델별 요약과 상세 결과를 파일로 저장한다."""

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    csv_path = Path(output_csv) if output_csv else target_dir / "medical_compare_summary.csv"
    json_path = Path(details_json) if details_json else target_dir / "medical_compare_details.json"

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
            "name",
            "model_name",
                "model_path",
                "total_questions",
                "correct_answers",
                "reasoning_failure_count",
                "accuracy",
                "average_keyword_coverage",
                "avg_tps",
                "avg_ttft_s",
                "peak_rss_mb",
                "peak_gpu_temp_c",
                "avg_power_w",
            ],
        )
        writer.writeheader()
        for summary in summaries:
            writer.writerow(
                {
                    "name": summary.model_name,
                    "model_name": summary.model_name,
                    "model_path": summary.model_path,
                    "total_questions": summary.total_questions,
                    "correct_answers": summary.correct_answers,
                    "reasoning_failure_count": summary.reasoning_failure_count,
                    "accuracy": f"{summary.accuracy:.2f}",
                    "average_keyword_coverage": f"{summary.average_keyword_coverage:.2f}",
                    "avg_tps": f"{summary.avg_tps:.3f}" if summary.avg_tps is not None else "N/A",
                    "avg_ttft_s": f"{summary.avg_ttft_s:.4f}" if summary.avg_ttft_s is not None else "N/A",
                    "peak_rss_mb": f"{summary.peak_rss_mb:.2f}" if summary.peak_rss_mb is not None else "N/A",
                    "peak_gpu_temp_c": f"{summary.peak_gpu_temp_c:.1f}" if summary.peak_gpu_temp_c is not None else "N/A",
                    "avg_power_w": f"{summary.avg_power_w:.2f}" if summary.avg_power_w is not None else "N/A",
                }
            )

    json_payload = [
        {
            "model_name": summary.model_name,
            "model_path": summary.model_path,
            "total_questions": summary.total_questions,
            "correct_answers": summary.correct_answers,
            "reasoning_failure_count": summary.reasoning_failure_count,
            "accuracy": round(summary.accuracy, 2),
            "average_keyword_coverage": round(summary.average_keyword_coverage, 2),
            "avg_tps": summary.avg_tps,
            "avg_ttft_s": summary.avg_ttft_s,
            "peak_rss_mb": summary.peak_rss_mb,
            "peak_gpu_temp_c": summary.peak_gpu_temp_c,
            "avg_power_w": summary.avg_power_w,
            "reasoning_failures": summary.reasoning_failures,
            "items": summary.items,
        }
        for summary in summaries
    ]

    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[INFO] 요약 CSV 저장: {csv_path}")
    print(f"[INFO] 상세 JSON 저장: {json_path}")


# -----------------------------------------------------------------------------
# 6) 메인
# -----------------------------------------------------------------------------
def main() -> int:
    args = parse_args()
    model_specs = load_model_specs(args.models_file)
    dataset = MEDICAL_SAMPLE_DATA[: args.limit] if args.limit and args.limit > 0 else MEDICAL_SAMPLE_DATA

    summaries: List[ModelRunResult] = []

    for index, model_spec in enumerate(model_specs, start=1):
        print(f"\n=== [{index}/{len(model_specs)}] 의료 벤치마크 실행: {model_spec.name} ===")
        results, combined = evaluate_dataset(dataset, model_spec)
        summary = summarize_results(results, model_spec, combined)
        summaries.append(summary)
        print_model_summary(summary)

    save_outputs(summaries, args.output_dir, args.output_csv, args.details_json)

    print("\n[DONE] 의료 전용 다중 모델 비교가 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())