"""Deterministic Phase 11 analysis and publication bundle generation.

The independent inferential unit is always a run.  Request observations are
retained only for nested descriptive distributions such as latency ECDFs.
Smoke, pilot, and formal result roots are never pooled implicitly.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape
import zipfile


ANALYSIS_ENGINE = "phase11-publication-v1"
SUPPORTED_EXPERIMENT_TYPES = {"formal", "pilot"}
RUN_METRICS = (
    "cluster_tokens_per_s",
    "requests_per_s",
    "ttft_p50_s",
    "e2e_p50_s",
    "success_rate",
    "generated_tokens_per_j",
    "peak_temperature_c",
)
OKABE_ITO = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")
_REPEAT_SUFFIX = re.compile(r"--repeat-\d+$")


class PublicationError(ValueError):
    """The input artifacts cannot produce an honest publication bundle."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def describe(
    values: Sequence[float], *, seed: int, bootstrap_resamples: int = 10_000
) -> dict[str, Any]:
    """Return the preregistered summaries with a run-level bootstrap mean CI."""
    clean = [float(item) for item in values if math.isfinite(float(item))]
    if not clean:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "sd": None,
            "iqr": None,
            "min": None,
            "max": None,
            "p50": None,
            "p95": None,
            "ci95": [None, None],
            "coefficient_of_variation": None,
        }
    mean = statistics.fmean(clean)
    sd = statistics.stdev(clean) if len(clean) > 1 else None
    q25 = percentile(clean, 0.25)
    q75 = percentile(clean, 0.75)
    ci: list[float | None]
    if len(clean) == 1:
        ci = [clean[0], clean[0]]
    else:
        rng = random.Random(seed)
        means = [statistics.fmean(rng.choices(clean, k=len(clean))) for _ in range(bootstrap_resamples)]
        ci = [percentile(means, 0.025), percentile(means, 0.975)]
    return {
        "count": len(clean),
        "mean": mean,
        "median": statistics.median(clean),
        "sd": sd,
        "iqr": (q75 - q25) if q25 is not None and q75 is not None else None,
        "min": min(clean),
        "max": max(clean),
        "p50": percentile(clean, 0.50),
        "p95": percentile(clean, 0.95),
        "ci95": ci,
        "coefficient_of_variation": (sd / abs(mean)) if sd is not None and mean != 0 else None,
    }


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"JSON artifact must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PublicationError(f"cannot read JSONL artifact: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicationError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise PublicationError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(value)
    return rows


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _quality(summary: Mapping[str, Any], experiment_type: str) -> str:
    if experiment_type == "pilot":
        return "pilot"
    value = summary.get("measurement_quality")
    if isinstance(value, str) and value in {"clean", "warning", "degraded", "unknown"}:
        return value
    return "unknown"


def _research_identity(config: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    value = summary.get("research_identity")
    if isinstance(value, Mapping):
        return dict(value)
    return {
        key: config.get(key)
        for key in (
            "experiment_type",
            "campaign_id",
            "campaign_cell_id",
            "campaign_attempt_id",
            "repeat_index",
            "order_index",
            "pilot_id",
            "pilot_cell_id",
            "pilot_repeat_index",
            "pilot_order_index",
        )
    }


def _cell_id(identity: Mapping[str, Any], experiment_type: str) -> str:
    if experiment_type == "formal":
        value = str(identity.get("cell_id") or identity.get("campaign_cell_id") or "")
        return _REPEAT_SUFFIX.sub("", value)
    return str(identity.get("pilot_cell_id") or "")


def _platform(summary: Mapping[str, Any]) -> str:
    participants = summary.get("participant_nodes")
    if not isinstance(participants, list):
        return "unknown"
    values = {
        str(item.get("detected_platform") or item.get("configured_platform") or "unknown")
        for item in participants
        if isinstance(item, Mapping)
    }
    return "+".join(sorted(values)) if values else "unknown"


def _instrumentation(summary: Mapping[str, Any]) -> tuple[float | None, float | None]:
    instrumentation = summary.get("measurement_instrumentation")
    if not isinstance(instrumentation, Mapping):
        return None, None
    overall = instrumentation.get("overall")
    energy = _number(overall.get("generated_tokens_per_j")) if isinstance(overall, Mapping) else None
    nodes = instrumentation.get("nodes")
    peaks: list[float] = []
    if isinstance(nodes, Mapping):
        for item in nodes.values():
            if isinstance(item, Mapping):
                value = _number(item.get("peak_temperature_c"))
                if value is not None:
                    peaks.append(value)
    return energy, max(peaks) if peaks else None


@dataclass(frozen=True)
class PublicationDataset:
    experiment_type: str
    runs: tuple[dict[str, Any], ...]
    requests: tuple[dict[str, Any], ...]
    exclusions: tuple[dict[str, Any], ...]
    inputs: tuple[dict[str, Any], ...]


def load_result_dataset(results_root: Path, *, experiment_type: str) -> PublicationDataset:
    """Load one separated result root and reject type or identity mixing."""
    if experiment_type not in SUPPORTED_EXPERIMENT_TYPES:
        raise PublicationError("experiment_type must be formal or pilot")
    root = results_root.resolve()
    if not root.is_dir():
        raise PublicationError(f"result root does not exist: {root}")
    run_rows: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for run_dir in sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()):
        config_path = run_dir / "config.json"
        summary_path = run_dir / "summary.json"
        if not config_path.exists():
            continue
        config = _read_object(config_path)
        inputs.append(_input_entry(config_path, root))
        if not summary_path.exists():
            exclusions.append({"run_id": run_dir.name, "reason_code": "MISSING_SUMMARY", "detail": "run has config but no terminal summary"})
            continue
        summary = _read_object(summary_path)
        inputs.append(_input_entry(summary_path, root))
        identity = _research_identity(config, summary)
        observed_type = str(identity.get("experiment_type") or config.get("experiment_type") or "")
        if observed_type != experiment_type:
            raise PublicationError(
                f"experiment type mixing is forbidden: expected {experiment_type}, found {observed_type or 'untyped'} in {run_dir.name}"
            )
        if experiment_type == "formal" and not str(identity.get("campaign_id") or ""):
            raise PublicationError(f"formal run lacks campaign identity: {run_dir.name}")
        if experiment_type == "pilot" and not str(identity.get("pilot_id") or ""):
            raise PublicationError(f"pilot run lacks pilot identity: {run_dir.name}")
        energy, peak_temperature = _instrumentation(summary)
        nodes = summary.get("nodes") if isinstance(summary.get("nodes"), list) else config.get("node_names")
        row = {
            "run_id": str(summary.get("run_id") or run_dir.name),
            "status": str(summary.get("status") or "unknown"),
            "experiment_type": experiment_type,
            "campaign_id": str(identity.get("campaign_id") or ""),
            "pilot_id": str(identity.get("pilot_id") or ""),
            "cell_id": _cell_id(identity, experiment_type),
            "repeat_index": int(identity.get("repeat_index") or identity.get("pilot_repeat_index") or 0),
            "order_index": int(identity.get("order_index") or identity.get("pilot_order_index") or 0),
            "model_id": str(summary.get("model_id") or config.get("model_id") or ""),
            "prompt_id": str(identity.get("prompt_id") or ""),
            "prompt_sha256": str((summary.get("benchmark_parameters") or {}).get("prompt_sha256") or ""),
            "strategy": str(summary.get("execution_strategy") or config.get("execution_strategy") or ""),
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
            "nodes": ";".join(str(item) for item in nodes) if isinstance(nodes, list) else "",
            "platform": _platform(summary),
            "measurement_quality": _quality(summary, experiment_type),
            "cluster_tokens_per_s": _number(summary.get("cluster_tokens_per_s")),
            "requests_per_s": _number(summary.get("requests_per_s")),
            "ttft_p50_s": _number(summary.get("ttft_p50_s")),
            "e2e_p50_s": _number(summary.get("e2e_p50_s")),
            "success_rate": _number(summary.get("success_rate")),
            "generated_tokens_per_j": energy,
            "peak_temperature_c": peak_temperature,
            "successful_requests": int(summary.get("successful") or 0),
            "failed_requests": int(summary.get("failed") or 0),
            "finished_at": str(summary.get("finished_at") or ""),
        }
        run_rows.append(row)
        response_path = run_dir / "responses.jsonl"
        if response_path.exists():
            inputs.append(_input_entry(response_path, root))
        for response in _read_jsonl(response_path):
            request_rows.append(
                {
                    "run_id": row["run_id"],
                    "cell_id": row["cell_id"],
                    "request_id": response.get("request_id"),
                    "logical_request_id": response.get("logical_request_id"),
                    "node": str(response.get("node") or response.get("assigned_node") or ""),
                    "ok": bool(response.get("ok")),
                    "ttft_s": _number(response.get("ttft_s")),
                    "e2e_s": _number(response.get("e2e_s")),
                    "generated_tokens": response.get("generated_tokens"),
                    "tokens_per_s": _number(response.get("tokens_per_s")),
                    "error_code": str(response.get("error_code") or ""),
                }
            )
        if row["status"] != "completed":
            exclusions.append({"run_id": row["run_id"], "reason_code": "NON_COMPLETED_RUN", "detail": row["status"]})
    if not run_rows:
        raise PublicationError(f"no {experiment_type} run summaries found under {root}")
    return PublicationDataset(
        experiment_type=experiment_type,
        runs=tuple(run_rows),
        requests=tuple(request_rows),
        exclusions=tuple(exclusions),
        inputs=tuple(sorted(inputs, key=lambda item: item["path"])),
    )


def _input_entry(path: Path, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "size": len(data), "sha256": sha256_bytes(data)}


def _group_key(run: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(run.get("cell_id") or "untracked"),
        str(run.get("model_id") or "unknown-model"),
        str(run.get("prompt_id") or run.get("prompt_sha256") or "unknown-prompt"),
        str(run.get("strategy") or "unknown-strategy"),
        str(run.get("node_count") or 0),
        str(run.get("platform") or "unknown"),
    )


def _seed_for(seed: int, *parts: str) -> int:
    digest = hashlib.sha256((str(seed) + "\0" + "\0".join(parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def analyze_dataset(
    dataset: PublicationDataset,
    *,
    seed: int = 20260823,
    bootstrap_resamples: int = 10_000,
) -> dict[str, Any]:
    """Calculate run-level estimates and explicit quality sensitivity subsets."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise PublicationError("analysis seed must be an integer")
    if isinstance(bootstrap_resamples, bool) or not isinstance(bootstrap_resamples, int) or bootstrap_resamples < 100:
        raise PublicationError("bootstrap_resamples must be an integer of at least 100")
    completed = [item for item in dataset.runs if item["status"] == "completed"]
    subsets: dict[str, list[dict[str, Any]]] = {"all_completed": completed}
    if dataset.experiment_type == "formal":
        subsets.update(
            {
                "clean_only": [item for item in completed if item["measurement_quality"] == "clean"],
                "clean_plus_warning": [item for item in completed if item["measurement_quality"] in {"clean", "warning"}],
                "including_degraded": [item for item in completed if item["measurement_quality"] in {"clean", "warning", "degraded"}],
            }
        )
    else:
        subsets["pilot_only_non_formal"] = completed
    output_subsets: dict[str, list[dict[str, Any]]] = {}
    for subset_name, runs in subsets.items():
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for run in runs:
            grouped.setdefault(_group_key(run), []).append(run)
        values: list[dict[str, Any]] = []
        for key in sorted(grouped):
            group_runs = sorted(grouped[key], key=lambda item: (item["repeat_index"], item["run_id"]))
            metrics: dict[str, Any] = {}
            for metric in RUN_METRICS:
                samples = [float(item[metric]) for item in group_runs if item.get(metric) is not None]
                metrics[metric] = describe(
                    samples,
                    seed=_seed_for(seed, subset_name, *key, metric),
                    bootstrap_resamples=bootstrap_resamples,
                )
            total_requests = sum(int(item["successful_requests"]) + int(item["failed_requests"]) for item in group_runs)
            failed_requests = sum(int(item["failed_requests"]) for item in group_runs)
            values.append(
                {
                    "cell_id": key[0],
                    "model_id": key[1],
                    "prompt_identity": key[2],
                    "strategy": key[3],
                    "node_count": int(key[4]),
                    "platform": key[5],
                    "run_count": len(group_runs),
                    "failed_run_count": sum(item["status"] != "completed" for item in group_runs),
                    "request_failure_rate": failed_requests / total_requests if total_requests else None,
                    "metrics": metrics,
                }
            )
        output_subsets[subset_name] = values
    return {
        "schema_version": 1,
        "analysis_engine": ANALYSIS_ENGINE,
        "experiment_type": dataset.experiment_type,
        "formal_claim_allowed": dataset.experiment_type == "formal",
        "independent_unit": "run",
        "request_level_role": "nested_descriptive_only",
        "seed": seed,
        "bootstrap_resamples": bootstrap_resamples,
        "run_count": len(dataset.runs),
        "completed_run_count": len(completed),
        "request_count": len(dataset.requests),
        "exclusion_count": len(dataset.exclusions),
        "subsets": output_subsets,
    }


def _svg_document(title: str, subtitle: str, content: str, *, width: int = 1400, height: int = 850) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="180mm" height="{height * 180 / width:.3f}mm" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>\n'
        '<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#111}.t{font-size:30px;font-weight:700}.s{font-size:16px}.a{font-size:15px}.g{stroke:#D5D5D5;stroke-width:1}.f{fill:none;stroke:#222;stroke-width:1.5}</style>\n'
        f'<text class="t" x="92" y="55">{escape(title)}</text><text class="s" x="92" y="84">{escape(subtitle)}</text>\n'
        f'{content}\n</svg>\n'
    )


def _scale(value: float, low: float, high: float, start: float, end: float) -> float:
    if high <= low:
        return (start + end) / 2
    return start + (value - low) * (end - start) / (high - low)


def throughput_svg(dataset: PublicationDataset, analysis: Mapping[str, Any]) -> str:
    runs = [item for item in dataset.runs if item["status"] == "completed" and item["cluster_tokens_per_s"] is not None]
    title = f"{dataset.experiment_type.title()} run-level throughput"
    subtitle = "Independent runs; generated tokens per second; 95% CI is descriptive when n is small"
    if not runs:
        return _svg_document(title, subtitle, '<text class="a" x="700" y="430" text-anchor="middle">No eligible throughput observations</text>')
    groups = sorted({item["cell_id"] for item in runs})
    values = [float(item["cluster_tokens_per_s"]) for item in runs]
    y0, y1 = min(values), max(values)
    if y0 == y1:
        y0, y1 = max(0.0, y0 * 0.8), y1 * 1.2 if y1 else 1.0
    left, right, top, bottom = 170, 1345, 120, 720
    parts = [f'<rect class="f" x="{left}" y="{top}" width="{right-left}" height="{bottom-top}"/>']
    for tick in range(6):
        value = y0 + (y1 - y0) * tick / 5
        y = _scale(value, y0, y1, bottom, top)
        parts.append(f'<line class="g" x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}"/><text class="a" x="{left-14}" y="{y+5:.2f}" text-anchor="end">{value:.2f}</text>')
    for index, group in enumerate(groups):
        x = left + (index + 0.5) * (right - left) / len(groups)
        parts.append(f'<text class="a" x="{x:.2f}" y="{bottom+30}" text-anchor="middle">{escape(group[:28])}</text>')
        group_runs = [item for item in runs if item["cell_id"] == group]
        for offset, run in enumerate(group_runs):
            jitter = (offset - (len(group_runs) - 1) / 2) * 12
            y = _scale(float(run["cluster_tokens_per_s"]), y0, y1, bottom, top)
            parts.append(f'<circle cx="{x+jitter:.2f}" cy="{y:.2f}" r="7" fill="{OKABE_ITO[index % len(OKABE_ITO)]}"/>')
    parts.append(f'<text class="a" x="26" y="{(top+bottom)/2}" transform="rotate(-90 26 {(top+bottom)/2})" text-anchor="middle">Generated tokens/s</text>')
    parts.append(f'<text class="a" x="{(left+right)/2}" y="810" text-anchor="middle">Analysis cell</text>')
    return _svg_document(title, subtitle, "".join(parts))


def latency_ecdf_svg(dataset: PublicationDataset) -> str:
    title = f"{dataset.experiment_type.title()} nested request latency ECDF"
    subtitle = "Requests are nested descriptive observations; confidence claims use independent runs"
    series = {
        "TTFT": sorted(float(item["ttft_s"]) for item in dataset.requests if item["ok"] and item["ttft_s"] is not None),
        "E2E": sorted(float(item["e2e_s"]) for item in dataset.requests if item["ok"] and item["e2e_s"] is not None),
    }
    all_values = [value for values in series.values() for value in values]
    if not all_values:
        return _svg_document(title, subtitle, '<text class="a" x="700" y="430" text-anchor="middle">No successful request latency observations</text>')
    x0, x1 = 0.0, max(all_values)
    left, right, top, bottom = 170, 1345, 120, 720
    parts = [f'<rect class="f" x="{left}" y="{top}" width="{right-left}" height="{bottom-top}"/>']
    for tick in range(6):
        x_value = x0 + (x1 - x0) * tick / 5
        x = _scale(x_value, x0, x1, left, right)
        y_value = tick / 5
        y = _scale(y_value, 0, 1, bottom, top)
        parts.append(f'<line class="g" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}"/><text class="a" x="{x:.2f}" y="{bottom+28}" text-anchor="middle">{x_value:.1f}</text>')
        parts.append(f'<line class="g" x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}"/><text class="a" x="{left-14}" y="{y+5:.2f}" text-anchor="end">{y_value:.1f}</text>')
    for index, (label, values) in enumerate(series.items()):
        if not values:
            continue
        points = []
        for position, value in enumerate(values, start=1):
            x = _scale(value, x0, x1, left, right)
            y = _scale(position / len(values), 0, 1, bottom, top)
            points.append(f"{x:.2f},{y:.2f}")
        color = OKABE_ITO[index]
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="4"/>')
        parts.append(f'<line x1="{1040+index*145}" y1="98" x2="{1072+index*145}" y2="98" stroke="{color}" stroke-width="5"/><text class="a" x="{1080+index*145}" y="103">{label}</text>')
    parts.append(f'<text class="a" x="26" y="{(top+bottom)/2}" transform="rotate(-90 26 {(top+bottom)/2})" text-anchor="middle">Empirical cumulative probability</text>')
    parts.append(f'<text class="a" x="{(left+right)/2}" y="810" text-anchor="middle">Latency (seconds)</text>')
    return _svg_document(title, subtitle, "".join(parts))


def metric_bar_svg(dataset: PublicationDataset, metric: str, title: str, unit: str) -> str | None:
    runs = [item for item in dataset.runs if item["status"] == "completed" and item.get(metric) is not None]
    if not runs:
        return None
    values = [float(item[metric]) for item in runs]
    maximum = max(values) or 1.0
    left, right, top, bottom = 170, 1345, 120, 720
    slot = (right - left) / len(runs)
    parts = [f'<rect class="f" x="{left}" y="{top}" width="{right-left}" height="{bottom-top}"/>']
    for index, (run, value) in enumerate(zip(runs, values)):
        x = left + index * slot + slot * 0.18
        width = slot * 0.64
        y = _scale(value, 0, maximum * 1.1, bottom, top)
        parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{bottom-y:.2f}" fill="{OKABE_ITO[index % len(OKABE_ITO)]}"/>')
        parts.append(f'<text class="a" x="{x+width/2:.2f}" y="{bottom+28}" text-anchor="middle">{escape(str(run["run_id"])[-8:])}</text>')
        parts.append(f'<text class="a" x="{x+width/2:.2f}" y="{y-9:.2f}" text-anchor="middle">{value:.2f}</text>')
    parts.append(f'<text class="a" x="26" y="{(top+bottom)/2}" transform="rotate(-90 26 {(top+bottom)/2})" text-anchor="middle">{escape(unit)}</text>')
    parts.append(f'<text class="a" x="{(left+right)/2}" y="810" text-anchor="middle">Run ID suffix</text>')
    return _svg_document(title, "Independent completed runs; unavailable metrics are omitted, never zero-filled", "".join(parts))


def failure_svg(dataset: PublicationDataset) -> str:
    title = f"{dataset.experiment_type.title()} preserved run outcomes"
    counts: dict[str, int] = {}
    for run in dataset.runs:
        counts[run["status"]] = counts.get(run["status"], 0) + 1
    for exclusion in dataset.exclusions:
        if exclusion["reason_code"] == "MISSING_SUMMARY":
            counts["missing_summary"] = counts.get("missing_summary", 0) + 1
    total = sum(counts.values()) or 1
    left, right, top, bottom = 170, 1345, 250, 560
    cursor = left
    parts = [f'<rect class="f" x="{left}" y="{top}" width="{right-left}" height="{bottom-top}"/>']
    for index, (status, count) in enumerate(sorted(counts.items())):
        width = (right - left) * count / total
        color = OKABE_ITO[index % len(OKABE_ITO)]
        parts.append(f'<rect x="{cursor:.2f}" y="{top}" width="{width:.2f}" height="{bottom-top}" fill="{color}"/>')
        if width >= 70:
            parts.append(f'<text class="a" x="{cursor+width/2:.2f}" y="{(top+bottom)/2}" text-anchor="middle">{escape(status)} {count}</text>')
        parts.append(f'<rect x="{180+index*220}" y="640" width="18" height="18" fill="{color}"/><text class="a" x="{206+index*220}" y="655">{escape(status)} ({count})</text>')
        cursor += width
    return _svg_document(title, "Every attempt is retained; no selective deletion or imputation", "".join(parts))


def render_figures(dataset: PublicationDataset, analysis: Mapping[str, Any]) -> dict[str, str]:
    figures = {
        "throughput.svg": throughput_svg(dataset, analysis),
        "latency-ecdf.svg": latency_ecdf_svg(dataset),
        "run-outcomes.svg": failure_svg(dataset),
    }
    energy = metric_bar_svg(dataset, "generated_tokens_per_j", f"{dataset.experiment_type.title()} energy efficiency", "Generated tokens/J")
    thermal = metric_bar_svg(dataset, "peak_temperature_c", f"{dataset.experiment_type.title()} peak temperature", "Peak temperature (°C)")
    if energy is not None:
        figures["energy-efficiency.svg"] = energy
    if thermal is not None:
        figures["peak-temperature.svg"] = thermal
    return figures


def _csv_bytes(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in fields})
    return stream.getvalue().encode("utf-8")


def _cell_table(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for subset, groups in sorted((analysis.get("subsets") or {}).items()):
        for group in groups:
            base = {key: group.get(key) for key in ("cell_id", "model_id", "prompt_identity", "strategy", "node_count", "platform", "run_count", "request_failure_rate")}
            for metric, value in sorted((group.get("metrics") or {}).items()):
                rows.append(
                    {
                        "subset": subset,
                        **base,
                        "metric": metric,
                        "count": value.get("count"),
                        "mean": value.get("mean"),
                        "median": value.get("median"),
                        "sd": value.get("sd"),
                        "iqr": value.get("iqr"),
                        "min": value.get("min"),
                        "max": value.get("max"),
                        "p50": value.get("p50"),
                        "p95": value.get("p95"),
                        "ci95_low": (value.get("ci95") or [None, None])[0],
                        "ci95_high": (value.get("ci95") or [None, None])[1],
                        "coefficient_of_variation": value.get("coefficient_of_variation"),
                    }
                )
    return rows


def _private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _readme(dataset: PublicationDataset, analysis: Mapping[str, Any]) -> bytes:
    claim = "Formal estimates" if dataset.experiment_type == "formal" else "NON-FORMAL PILOT OUTPUT"
    lines = [
        f"# {dataset.experiment_type.title()} Publication Bundle",
        "",
        f"Status: **{claim}**",
        "",
        "This bundle was generated without manual spreadsheet edits. Run-level observations are the independent analysis unit; request rows are nested descriptive data only.",
        "",
        f"- analysis engine: `{ANALYSIS_ENGINE}`",
        f"- deterministic seed: `{analysis['seed']}`",
        f"- bootstrap resamples: `{analysis['bootstrap_resamples']}`",
        f"- retained runs: `{analysis['run_count']}`",
        f"- completed runs: `{analysis['completed_run_count']}`",
        f"- retained request rows: `{analysis['request_count']}`",
        f"- exclusions: `{analysis['exclusion_count']}`",
        "",
        "## Reproduce",
        "",
        "Run `scripts/research/phase11_publication.py build` with the same result root, analysis plan, locked config directory, experiment type, seed, and resample count. Compare `bundle-manifest.json` checksums.",
        "",
        "Pilot and smoke artifacts may never be relabelled or pooled with formal results.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _deterministic_zip(directory: Path, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix="phase11-", suffix=".zip", dir=destination.parent, delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                info = zipfile.ZipInfo(path.relative_to(directory).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, path.read_bytes())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def write_publication_bundle(
    *,
    results_root: Path,
    output_dir: Path,
    experiment_type: str,
    analysis_plan: Mapping[str, Any],
    locked_inputs: Mapping[str, Path],
    acknowledge_non_formal: bool = False,
) -> dict[str, Any]:
    """Atomically create a deterministic directory and matching zip archive."""
    if experiment_type == "pilot" and acknowledge_non_formal is not True:
        raise PublicationError("pilot export requires explicit acknowledgement that it is non-formal")
    if output_dir.exists() or output_dir.is_symlink():
        raise PublicationError(f"output path already exists: {output_dir}")
    confidence = analysis_plan.get("confidence_intervals")
    if not isinstance(confidence, Mapping):
        raise PublicationError("analysis plan lacks confidence_intervals")
    seed = confidence.get("seed")
    resamples = confidence.get("bootstrap_resamples")
    dataset = load_result_dataset(results_root, experiment_type=experiment_type)
    analysis = analyze_dataset(dataset, seed=seed, bootstrap_resamples=resamples)
    parent = output_dir.resolve().parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    os.chmod(temp, 0o700)
    try:
        _private_write(temp / "analysis" / "summary.json", canonical_json(analysis))
        _private_write(temp / "analysis" / "input-manifest.json", canonical_json({"files": list(dataset.inputs)}))
        _private_write(temp / "tables" / "runs.csv", _csv_bytes(dataset.runs, tuple(dataset.runs[0].keys())))
        request_fields = ("run_id", "cell_id", "request_id", "logical_request_id", "node", "ok", "ttft_s", "e2e_s", "generated_tokens", "tokens_per_s", "error_code")
        _private_write(temp / "tables" / "requests-nested.csv", _csv_bytes(dataset.requests, request_fields))
        exclusion_fields = ("run_id", "reason_code", "detail")
        _private_write(temp / "tables" / "exclusions.csv", _csv_bytes(dataset.exclusions, exclusion_fields))
        cell_rows = _cell_table(analysis)
        cell_fields = tuple(cell_rows[0].keys()) if cell_rows else ("subset", "cell_id", "metric")
        _private_write(temp / "tables" / "cell-summary.csv", _csv_bytes(cell_rows, cell_fields))
        for name, svg in sorted(render_figures(dataset, analysis).items()):
            _private_write(temp / "figures" / name, svg.encode("utf-8"))
        _private_write(temp / "README.md", _readme(dataset, analysis))
        for name, source in sorted(locked_inputs.items()):
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", name):
                raise PublicationError(f"unsafe locked input name: {name}")
            resolved = source.resolve()
            if not resolved.is_file() or source.is_symlink():
                raise PublicationError(f"locked input is not a regular file: {source}")
            _private_write(temp / "inputs" / name, resolved.read_bytes())
        files = []
        for path in sorted(item for item in temp.rglob("*") if item.is_file()):
            data = path.read_bytes()
            files.append({"path": path.relative_to(temp).as_posix(), "size": len(data), "sha256": sha256_bytes(data)})
        manifest = {
            "schema_version": 1,
            "artifact_type": "publication_bundle",
            "analysis_engine": ANALYSIS_ENGINE,
            "experiment_type": experiment_type,
            "formal_claim_allowed": experiment_type == "formal",
            "analysis_seed": seed,
            "bootstrap_resamples": resamples,
            "input_manifest_sha256": sha256_bytes(canonical_json({"files": list(dataset.inputs)})),
            "files": files,
        }
        _private_write(temp / "bundle-manifest.json", canonical_json(manifest))
        os.replace(temp, output_dir)
        archive = output_dir.with_suffix(".zip")
        if archive.exists() or archive.is_symlink():
            raise PublicationError(f"archive path already exists: {archive}")
        _deterministic_zip(output_dir, archive)
        return {**manifest, "output_dir": str(output_dir), "archive": str(archive), "archive_sha256": sha256_bytes(archive.read_bytes())}
    except Exception:
        if temp.exists():
            shutil.rmtree(temp)
        raise


__all__ = [
    "ANALYSIS_ENGINE",
    "PublicationDataset",
    "PublicationError",
    "analyze_dataset",
    "describe",
    "load_result_dataset",
    "render_figures",
    "write_publication_bundle",
]
