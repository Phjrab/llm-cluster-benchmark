"""Pure scheduling-result aggregation for repeated benchmark campaigns."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Any, Mapping, Sequence


TERMINAL_ATTEMPT_STATES = frozenset(
    {"completed", "failed", "cancelled", "excluded"}
)


class StatisticalCampaignError(ValueError):
    """Repeated-run inputs cannot produce an auditable aggregate."""


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _metric_seed(seed: int, condition_id: str, metric: str) -> int:
    digest = hashlib.sha256(
        f"statistical-campaign-v1\0{seed}\0{condition_id}\0{metric}".encode(
            "utf-8"
        )
    ).digest()
    return int.from_bytes(digest[:8], "big")


def describe_samples(
    values: Sequence[float], *, seed: int, bootstrap_resamples: int = 10_000
) -> dict[str, Any]:
    """Return deterministic run-level descriptive and bootstrap statistics."""
    if bootstrap_resamples < 100:
        raise StatisticalCampaignError("bootstrap_resamples must be at least 100")
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "iqr": None,
            "bootstrap_ci95": [None, None],
        }
    q25 = _percentile(clean, 0.25)
    q75 = _percentile(clean, 0.75)
    if len(clean) == 1:
        interval: list[float | None] = [clean[0], clean[0]]
    else:
        rng = random.Random(seed)
        means = [
            statistics.fmean(rng.choices(clean, k=len(clean)))
            for _ in range(bootstrap_resamples)
        ]
        interval = [_percentile(means, 0.025), _percentile(means, 0.975)]
    return {
        "count": len(clean),
        "mean": statistics.fmean(clean),
        "median": statistics.median(clean),
        "standard_deviation": statistics.stdev(clean) if len(clean) > 1 else None,
        "iqr": q75 - q25 if q25 is not None and q75 is not None else None,
        "bootstrap_ci95": interval,
    }


def _platforms(attempt: Mapping[str, Any]) -> set[str]:
    raw = attempt.get("platform_families")
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence):
        values = [str(value) for value in raw]
    else:
        value = attempt.get("platform") or attempt.get("platform_cohort")
        values = [str(value)] if value else []
    normalized: set[str] = set()
    for value in values:
        lowered = value.lower().replace("-", "_")
        if "jetson" in lowered:
            normalized.add("jetson")
        elif "raspberry" in lowered or lowered.startswith("pi"):
            normalized.add("raspberry_pi")
        elif lowered:
            normalized.add(lowered)
    return normalized


def aggregate_campaign_attempts(
    attempts: Sequence[Mapping[str, Any]],
    *,
    metric_names: Sequence[str],
    seed: int = 0,
    bootstrap_resamples: int = 10_000,
) -> dict[str, Any]:
    """Aggregate completed repeats while preserving every failed exclusion."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise StatisticalCampaignError("seed must be an integer")
    if not attempts:
        raise StatisticalCampaignError("attempts must not be empty")
    metrics = [str(name).strip() for name in metric_names]
    if not metrics or any(not name for name in metrics) or len(set(metrics)) != len(metrics):
        raise StatisticalCampaignError("metric_names must be unique non-empty strings")

    groups: dict[str, list[Mapping[str, Any]]] = {}
    seen_attempts: set[str] = set()
    for index, attempt in enumerate(attempts):
        condition_id = str(attempt.get("condition_id") or "").strip()
        attempt_id = str(attempt.get("attempt_id") or "").strip()
        status = str(attempt.get("status") or "").strip()
        if not condition_id or not attempt_id:
            raise StatisticalCampaignError(
                f"attempts[{index}] requires condition_id and attempt_id"
            )
        if attempt_id in seen_attempts:
            raise StatisticalCampaignError(f"duplicate attempt_id: {attempt_id}")
        if status not in TERMINAL_ATTEMPT_STATES:
            raise StatisticalCampaignError(f"nonterminal or unknown status: {status}")
        seen_attempts.add(attempt_id)
        groups.setdefault(condition_id, []).append(attempt)

    aggregated: list[dict[str, Any]] = []
    for condition_id in sorted(groups):
        condition_attempts = sorted(
            groups[condition_id],
            key=lambda item: (
                int(item.get("repeat_index") or 0),
                str(item.get("attempt_id")),
            ),
        )
        included = [
            item
            for item in condition_attempts
            if item.get("status") == "completed" and item.get("included", True) is not False
        ]
        exclusions: list[dict[str, Any]] = []
        for item in condition_attempts:
            if item in included:
                continue
            reason = str(
                item.get("exclusion_reason")
                or item.get("failure_code")
                or f"STATUS_{str(item.get('status')).upper()}"
            )
            exclusions.append(
                {
                    "attempt_id": item.get("attempt_id"),
                    "run_id": item.get("run_id"),
                    "status": item.get("status"),
                    "reason": reason,
                    "measurement_quality": item.get("measurement_quality"),
                }
            )

        metric_output: dict[str, Any] = {}
        for metric in metrics:
            samples: list[float] = []
            missing: list[dict[str, Any]] = []
            for item in included:
                value = (item.get("metrics") or {}).get(metric)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    missing.append(
                        {
                            "attempt_id": item.get("attempt_id"),
                            "run_id": item.get("run_id"),
                            "reason": f"METRIC_UNAVAILABLE:{metric}",
                            "measurement_quality": item.get("measurement_quality"),
                        }
                    )
                else:
                    samples.append(float(value))
            metric_output[metric] = {
                **describe_samples(
                    samples,
                    seed=_metric_seed(seed, condition_id, metric),
                    bootstrap_resamples=bootstrap_resamples,
                ),
                "excluded_measurements": missing,
            }

        platforms = set().union(*(_platforms(item) for item in condition_attempts))
        comparison_class = "homogeneous" if len(platforms) == 1 else "exploratory"
        aggregated.append(
            {
                "condition_id": condition_id,
                "attempt_count": len(condition_attempts),
                "successful_repeat_count": len(included),
                "failed_repeat_count": sum(
                    item.get("status") in {"failed", "cancelled"}
                    for item in condition_attempts
                ),
                "excluded_repeat_count": len(exclusions),
                "exclusions": exclusions,
                "platform_families": sorted(platforms),
                "comparison_class": comparison_class,
                "formal_comparison_eligible": comparison_class == "homogeneous",
                "metrics": metric_output,
            }
        )

    return {
        "schema_version": 1,
        "artifact_type": "statistical_campaign_aggregate",
        "seed": seed,
        "bootstrap_resamples": bootstrap_resamples,
        "attempt_count": len(attempts),
        "condition_count": len(aggregated),
        "conditions": aggregated,
    }


def aggregate_run_summaries(
    summaries: Sequence[Mapping[str, Any]],
    *,
    metric_names: Sequence[str],
    seed: int = 0,
    bootstrap_resamples: int = 10_000,
) -> dict[str, Any]:
    """Normalize additive run summaries into the auditable campaign artifact."""
    attempts: list[dict[str, Any]] = []
    for summary in summaries:
        identity = summary.get("research_identity")
        if not isinstance(identity, Mapping):
            identity = {}
        campaign_cell_id = str(
            identity.get("campaign_cell_id")
            or summary.get("campaign_cell_id")
            or ""
        )
        condition_id = str(summary.get("condition_id") or campaign_cell_id)
        if "--repeat-" in condition_id:
            condition_id = condition_id.rsplit("--repeat-", 1)[0]
        participants = summary.get("participant_nodes")
        platform_families: list[str] = []
        if isinstance(participants, Sequence) and not isinstance(participants, (str, bytes)):
            for participant in participants:
                if not isinstance(participant, Mapping):
                    continue
                platform = participant.get("detected_platform") or participant.get(
                    "configured_platform"
                )
                if platform:
                    platform_families.append(str(platform))
        failure = summary.get("failure")
        failure_code = summary.get("error_code")
        if not failure_code and isinstance(failure, Mapping):
            failure_code = failure.get("code")
        attempts.append(
            {
                "condition_id": condition_id,
                "attempt_id": str(
                    identity.get("campaign_attempt_id")
                    or summary.get("campaign_attempt_id")
                    or summary.get("run_id")
                    or ""
                ),
                "run_id": summary.get("run_id"),
                "repeat_index": identity.get("repeat_index")
                or summary.get("repeat_index")
                or 0,
                "status": summary.get("status"),
                "failure_code": failure_code,
                "exclusion_reason": summary.get("exclusion_reason"),
                "included": summary.get("included_in_aggregate", True),
                "measurement_quality": summary.get("measurement_quality"),
                "platform_families": platform_families,
                "metrics": {name: summary.get(name) for name in metric_names},
            }
        )
    return aggregate_campaign_attempts(
        attempts,
        metric_names=metric_names,
        seed=seed,
        bootstrap_resamples=bootstrap_resamples,
    )


__all__ = [
    "StatisticalCampaignError",
    "TERMINAL_ATTEMPT_STATES",
    "aggregate_campaign_attempts",
    "aggregate_run_summaries",
    "describe_samples",
]
