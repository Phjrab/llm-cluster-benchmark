"""Pure benchmark metric calculations with schema-v2 formulas."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def aggregate_records(records: Sequence[Dict[str, Any]], wall_s: float) -> Dict[str, Any]:
    successful = [item for item in records if item["ok"]]
    ttft = [float(item["ttft_s"]) for item in successful if item["ttft_s"] is not None]
    e2e = [float(item["e2e_s"]) for item in successful]
    total_tokens = sum(int(item["generated_tokens"]) for item in successful)
    per_node: Dict[str, Dict[str, Any]] = {}
    for item in records:
        bucket = per_node.setdefault(item["node"], {
            "requests": 0, "successful": 0, "tokens": 0,
            "ttft_s": [], "e2e_s": [], "tokens_per_s_samples": [],
        })
        bucket["requests"] += 1
        if item["ok"]:
            bucket["successful"] += 1
            bucket["tokens"] += int(item["generated_tokens"])
            bucket["e2e_s"].append(float(item["e2e_s"]))
            if item.get("ttft_s") is not None:
                bucket["ttft_s"].append(float(item["ttft_s"]))
            if item.get("tokens_per_s") is not None:
                bucket["tokens_per_s_samples"].append(float(item["tokens_per_s"]))
    for bucket in per_node.values():
        node_ttft = bucket.pop("ttft_s")
        node_e2e = bucket.pop("e2e_s")
        node_tps = bucket.pop("tokens_per_s_samples")
        bucket["failed"] = bucket["requests"] - bucket["successful"]
        bucket["success_rate"] = round(bucket["successful"] / bucket["requests"], 6) if bucket["requests"] else 0.0
        bucket["effective_tokens_per_s"] = round(bucket["tokens"] / wall_s, 6) if wall_s > 0 else 0.0
        bucket["average_generation_tokens_per_s"] = round(sum(node_tps) / len(node_tps), 6) if node_tps else None
        bucket["ttft_p50_s"] = percentile(node_ttft, 0.50)
        bucket["ttft_p95_s"] = percentile(node_ttft, 0.95)
        bucket["e2e_p50_s"] = percentile(node_e2e, 0.50)
        bucket["e2e_p95_s"] = percentile(node_e2e, 0.95)
    logical_groups: Dict[tuple[str, int], List[Dict[str, Any]]] = {}
    for item in records:
        logical_groups.setdefault(
            (str(item.get("scenario_id") or "main"), int(item.get("logical_request_id") or item["request_id"])), []
        ).append(item)
    all_success = sum(1 for group in logical_groups.values() if group and all(item["ok"] for item in group))
    comparable = [group for group in logical_groups.values() if len(group) > 1 and all(item["ok"] for item in group)]
    agreement = sum(
        1 for group in comparable
        if len({str(item.get("output_sha256") or "") for item in group}) == 1
    )
    return {
        "requests": len(records),
        "logical_requests": len(logical_groups),
        "physical_requests": len(records),
        "successful": len(successful),
        "failed": len(records) - len(successful),
        "success_rate": round(len(successful) / len(records), 6) if records else 0.0,
        "wall_s": round(wall_s, 6),
        "requests_per_s": round(len(successful) / wall_s, 6) if wall_s > 0 else 0.0,
        "total_generated_tokens": total_tokens,
        "cluster_tokens_per_s": round(total_tokens / wall_s, 6) if wall_s > 0 else 0.0,
        "ttft_p50_s": percentile(ttft, 0.50),
        "ttft_p95_s": percentile(ttft, 0.95),
        "e2e_p50_s": percentile(e2e, 0.50),
        "e2e_p95_s": percentile(e2e, 0.95),
        "all_replicas_success_rate": round(all_success / len(logical_groups), 6) if logical_groups else 0.0,
        "answer_agreement_rate": round(agreement / len(comparable), 6) if comparable else None,
        "per_node": per_node,
    }


def add_cumulative_scaling(summaries: Sequence[Dict[str, Any]]) -> None:
    if not summaries:
        return
    baseline = float(summaries[0].get("cluster_tokens_per_s") or 0.0)
    for summary in summaries:
        throughput = float(summary.get("cluster_tokens_per_s") or 0.0)
        node_count = max(1, len(summary.get("nodes") or []))
        summary["speedup_vs_baseline"] = round(throughput / baseline, 6) if baseline > 0 else None
        summary["scaling_efficiency"] = round(throughput / baseline / node_count, 6) if baseline > 0 else None


__all__ = ["add_cumulative_scaling", "aggregate_records", "percentile"]
