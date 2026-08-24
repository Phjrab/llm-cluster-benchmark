#!/usr/bin/env python3
"""Run the opt-in Jetson/Pi hardware smoke matrix on a self-hosted runner."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cluster.benchmark.runner import run_experiment
from cluster.clusterctl import Node, load_nodes, request_json
from cluster.domain.experiment import ExperimentConfig


UNAVAILABLE_EXIT = 78


@dataclass(frozen=True)
class HardwareCase:
    case_id: str
    description: str
    config: ExperimentConfig


def _config(case_id: str, nodes: Sequence[Node], model_id: str, strategy: str) -> ExperimentConfig:
    is_pi = all(node.platform == "raspberry-pi" for node in nodes)
    return ExperimentConfig(
        experiment_id=f"hardware-{case_id}",
        name=f"hardware-{case_id}",
        node_names=[node.name for node in nodes],
        model_id=model_id,
        n_ctx=512,
        n_gpu_layers=0 if is_pi else 30,
        requests=1,
        concurrency=1,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
        seed=42,
        warmup_requests=0,
        prompt="한 문장으로 엣지 LLM의 장점을 설명해줘.",
        require_uniform_config=False,
        request_timeout_s=300.0,
        execution_strategy=strategy,
        acknowledge_experimental_rpc=strategy == "model_parallel_rpc",
        rpc_coordinator_node=nodes[0].name if strategy == "model_parallel_rpc" else None,
    )


def build_plan(nodes: Sequence[Node], model_id: str) -> list[HardwareCase]:
    jetsons = [node for node in nodes if node.enabled and node.role == "worker" and node.platform == "jetson"]
    pis = [node for node in nodes if node.enabled and node.role == "worker" and node.platform == "raspberry-pi"]
    if not jetsons or not pis:
        raise ValueError("hardware gate requires at least one online Jetson and one online Raspberry Pi")
    homogeneous_pair = (jetsons[:2] if len(jetsons) >= 2 else pis[:2] if len(pis) >= 2 else [])
    if len(homogeneous_pair) != 2:
        raise ValueError("hardware gate requires two online workers from the same platform")
    return [
        HardwareCase("jetson-single", "Jetson single-node inference smoke", _config("jetson-single", jetsons[:1], model_id, "single_node")),
        HardwareCase("pi-single", "Raspberry Pi single-node inference smoke", _config("pi-single", pis[:1], model_id, "single_node")),
        HardwareCase("replicated-two-node", "Homogeneous two-worker replicated smoke", _config("replicated-two-node", homogeneous_pair, model_id, "replicated_round_robin")),
        HardwareCase("rpc-cleanup", "Homogeneous two-worker model-parallel RPC cleanup smoke", _config("rpc-cleanup", homogeneous_pair, model_id, "model_parallel_rpc")),
    ]


def online_nodes(inventory: Path, model_id: str) -> tuple[list[Node], list[dict[str, Any]]]:
    nodes = load_nodes(inventory, include_disabled=False, require_legacy_head=False)
    online: list[Node] = []
    observations: list[dict[str, Any]] = []
    for node in nodes:
        if node.role != "worker" or not node.enabled:
            continue
        try:
            health = request_json(f"{node.api_url}/cluster/health", timeout=5.0)
            model_ids = list(health.get("model_ids") or [])
            ok = health.get("ok") is True and model_id in model_ids
            observations.append({"node": node.name, "platform": node.platform, "api": health.get("ok") is True, "model_present": model_id in model_ids})
            if ok:
                online.append(node)
        except Exception as exc:
            observations.append({"node": node.name, "platform": node.platform, "api": False, "model_present": False, "error": f"{type(exc).__name__}: {exc}"})
    return online, observations


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=("manual", "nightly", "release-candidate"), default="manual")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": args.mode,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "inventory": str(args.inventory),
        "model_id": args.model_id,
        "status": "running",
        "cases": [],
    }
    try:
        online, observations = online_nodes(args.inventory, args.model_id)
        report["worker_observations"] = observations
        plan = build_plan(online, args.model_id)
    except (OSError, ValueError) as exc:
        report.update(status="unavailable", reason=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
        write_report(args.report, report)
        print(f"[SKIP] hardware unavailable: {exc}")
        return UNAVAILABLE_EXIT

    report["plan"] = [{"case_id": case.case_id, "description": case.description, "config": asdict(case.config)} for case in plan]
    if args.dry_run:
        report.update(status="planned", finished_at=datetime.now(timezone.utc).isoformat())
        write_report(args.report, report)
        print(f"[OK] planned {len(plan)} hardware smoke cases")
        return 0

    try:
        for case in plan:
            print(f"[RUN] {case.case_id}: {case.description}", flush=True)
            summary = run_experiment(case.config, inventory_path=args.inventory, results_root=args.results_dir / case.case_id)
            result = {
                "case_id": case.case_id,
                "run_id": summary.get("run_id"),
                "status": summary.get("status"),
                "result_dir": summary.get("result_dir"),
                "topology": summary.get("topology") or {},
            }
            report["cases"].append(result)
            if summary.get("status") != "completed":
                raise RuntimeError(f"{case.case_id} finished with {summary.get('status')}")
            if case.case_id == "rpc-cleanup" and (summary.get("topology") or {}).get("cleanup_status") not in {"completed", "completed_after_retry"}:
                raise RuntimeError("RPC cleanup did not complete")
        report["status"] = "passed"
        return 0
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(args.report, report)


if __name__ == "__main__":
    raise SystemExit(main())
