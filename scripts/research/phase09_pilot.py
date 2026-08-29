#!/usr/bin/env python3
"""Plan, execute, and analyze the separated Roadmap Phase 09 pilot."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cluster.application.job_process import unload_models
from cluster.benchmark.runner import run_experiment
from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.storage import read_json_object, write_json_object
from cluster.integrations.runtime_layout import repository_root, resolve_runtime_paths
from cluster.research.pilot import analyze_pilot, expand_pilot_plan, validate_pilot_plan


ROOT = repository_root()
RESEARCH_DIR = ROOT / "config" / "research"
DEFAULT_PLAN = RESEARCH_DIR / "pilot_plan.v5.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def read_research(name: str) -> dict[str, Any]:
    return read_json_object(RESEARCH_DIR / name)


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pilot_directory(plan: Mapping[str, Any], override: Path | None) -> Path:
    return Path(override) if override is not None else resolve_runtime_paths().pilots_dir / str(plan["pilot_id"])


@contextmanager
def exclusive_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / ".pilot.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise RuntimeError("another Phase 09 pilot process owns the durable lock") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def load_context(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = read_json_object(plan_path)
    matrix = read_research("formal_experiment_matrix.json")
    models = read_research("model_lock.json")
    prompts = read_research("prompt_set.json")
    validate_pilot_plan(plan, matrix=matrix, model_lock=models, prompt_lock=prompts)
    return plan, matrix, models, prompts


def initial_manifest(plan: Mapping[str, Any], matrix: Mapping[str, Any]) -> dict[str, Any]:
    runs = []
    for item in expand_pilot_plan(plan, matrix):
        runs.append({
            "pilot_cell_id": item["pilot_cell_id"],
            "pilot_stage": item["pilot_stage"],
            "pilot_repeat_index": item["pilot_repeat_index"],
            "pilot_order_index": item["pilot_order_index"],
            "cooldown_before_s": item["cooldown_before_s"],
            "status": "pending",
            "run_id": None,
            "summary_path": None,
            "error_code": None,
        })
    return {
        "schema_version": 1,
        "artifact_type": "pilot_manifest",
        "pilot_id": plan["pilot_id"],
        "experiment_type": "pilot",
        "status": "ready",
        "phase": "ready",
        "plan_sha256": canonical_sha256(plan),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "formal_pooling_allowed": False,
        "runs": runs,
        "observations": [],
        "analysis": None,
    }


def ensure_manifest(directory: Path, plan: Mapping[str, Any], matrix: Mapping[str, Any]) -> dict[str, Any]:
    path = directory / "manifest.json"
    if not path.exists():
        manifest = initial_manifest(plan, matrix)
        write_json_object(path, manifest, default_mode=0o600)
        return manifest
    manifest = read_json_object(path)
    if manifest.get("pilot_id") != plan.get("pilot_id") or manifest.get("plan_sha256") != canonical_sha256(plan):
        raise RuntimeError("persisted pilot manifest does not match the predeclared plan")
    return manifest


def persist_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    write_json_object(directory / "manifest.json", manifest, default_mode=0o600)


def model_map(lock: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["model_key"]): str(item["catalog_id"])
        for item in lock.get("models", [])
        if isinstance(item, Mapping) and (item.get("verification") or {}).get("status") == "approved"
    }


def prompt_map(lock: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["prompt_id"]): str(item["text"])
        for item in lock.get("prompts", []) if isinstance(item, Mapping)
    }


def experiment_config(
    plan: Mapping[str, Any], item: Mapping[str, Any], models: Mapping[str, str], prompts: Mapping[str, str]
) -> ExperimentConfig:
    workload = plan["workload"]
    is_jetson = str(item["platform_cohort"]).startswith("jetson-")
    return ExperimentConfig(
        experiment_id=f"phase09-{item['pilot_order_index']:03d}-{item['pilot_cell_id']}",
        name=f"Phase 09 pilot · {item['pilot_cell_id']} · repeat {item['pilot_repeat_index']}",
        node_names=list(item["node_set"]),
        model_id=models[str(item["model_lock_key"])],
        n_ctx=int(workload["n_ctx"]),
        n_gpu_layers=30 if is_jetson else 0,
        requests=int(workload["requests"]),
        concurrency=int(workload["concurrency"]),
        max_tokens=int(workload["max_tokens"]),
        temperature=float(workload["temperature"]),
        top_p=float(workload["top_p"]),
        seed=int(workload["seed"]),
        warmup_requests=int(workload["warmup_requests_per_node"]),
        prompt=prompts[str(item["prompt_id"])],
        persist_prompt=True,
        require_uniform_config=True,
        request_timeout_s=float(workload["request_timeout_s"]),
        execution_strategy=str(item["strategy"]),
        experiment_type="pilot",
        pilot_id=str(plan["pilot_id"]),
        pilot_cell_id=str(item["pilot_cell_id"]),
        pilot_repeat_index=int(item["pilot_repeat_index"]),
        pilot_order_index=int(item["pilot_order_index"]),
    )


def find_run(manifest: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    for run in manifest.get("runs") or []:
        if (
            run.get("pilot_cell_id") == item.get("pilot_cell_id")
            and run.get("pilot_repeat_index") == item.get("pilot_repeat_index")
        ):
            return run
    raise RuntimeError("pilot manifest lost a predeclared run")


def append_retry_run(manifest: dict[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """Append one distinct retry attempt without changing the original attempt."""
    runs = manifest.setdefault("runs", [])
    original_order = int(item["pilot_order_index"])
    for run in runs:
        if run.get("retry_of_order_index") == original_order:
            return run
    cell_id = str(item["pilot_cell_id"])
    retry = {
        "pilot_cell_id": cell_id,
        "pilot_stage": str(item["pilot_stage"]),
        "pilot_repeat_index": max(
            int(run.get("pilot_repeat_index") or 0)
            for run in runs
            if run.get("pilot_cell_id") == cell_id
        ) + 1,
        "pilot_order_index": max(int(run.get("pilot_order_index") or 0) for run in runs) + 1,
        "cooldown_before_s": float(item.get("cooldown_before_s") or 0.0),
        "status": "pending",
        "run_id": None,
        "summary_path": None,
        "error_code": None,
        "retry_of_order_index": original_order,
    }
    runs.append(retry)
    return retry


def reconcile_pre_run_failures(manifest: dict[str, Any]) -> bool:
    """Upgrade already-preserved cleanup failures to observations plus retries."""
    identities = {
        (item.get("pilot_cell_id"), item.get("pilot_repeat_index"))
        for item in manifest.get("observations") or []
    }
    changed = False
    for run in list(manifest.get("runs") or []):
        if (
            run.get("status") != "failed"
            or run.get("error_code") != "PRE_RUN_CLEANUP_FAILED"
        ):
            continue
        identity = (run.get("pilot_cell_id"), run.get("pilot_repeat_index"))
        if identity not in identities:
            manifest.setdefault("observations", []).append({
                "pilot_cell_id": run.get("pilot_cell_id"),
                "pilot_stage": run.get("pilot_stage"),
                "pilot_repeat_index": run.get("pilot_repeat_index"),
                "pilot_order_index": run.get("pilot_order_index"),
                "cooldown_before_s": float(run.get("cooldown_before_s") or 0.0),
                "status": "failed",
                "run_id": run.get("run_id"),
                "summary_path": run.get("summary_path"),
                "error_code": "PRE_RUN_CLEANUP_FAILED",
                "cleanup_errors": list(run.get("cleanup_errors") or []),
                "total_elapsed_s": float(run.get("total_elapsed_s") or 0.0),
            })
            identities.add(identity)
            changed = True
        before = len(manifest.get("runs") or [])
        append_retry_run(manifest, run)
        changed = changed or len(manifest.get("runs") or []) != before
    return changed


def reconcile_interrupted_runs(manifest: dict[str, Any]) -> bool:
    """Preserve an orphaned running attempt and append one durable retry."""
    identities = {
        (item.get("pilot_cell_id"), item.get("pilot_repeat_index"))
        for item in manifest.get("observations") or []
    }
    changed = False
    for run in list(manifest.get("runs") or []):
        if run.get("status") != "running":
            continue
        run.update({
            "status": "interrupted",
            "finished_at": utc_now(),
            "error_code": "PILOT_INTERRUPTED",
        })
        identity = (run.get("pilot_cell_id"), run.get("pilot_repeat_index"))
        if identity not in identities:
            manifest.setdefault("observations", []).append({
                "pilot_cell_id": run.get("pilot_cell_id"),
                "pilot_stage": run.get("pilot_stage"),
                "pilot_repeat_index": run.get("pilot_repeat_index"),
                "pilot_order_index": run.get("pilot_order_index"),
                "cooldown_before_s": float(run.get("cooldown_before_s") or 0.0),
                "status": "interrupted",
                "run_id": run.get("run_id"),
                "summary_path": run.get("summary_path"),
                "error_code": "PILOT_INTERRUPTED",
                "cleanup_errors": [],
                "total_elapsed_s": None,
            })
            identities.add(identity)
        append_retry_run(manifest, run)
        changed = True
    return changed


def manifest_execution_order(
    declared_order: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Resolve declared cells plus durable retry slots into execution items."""
    declared_by_order = {
        int(item["pilot_order_index"]): item for item in declared_order
    }
    resolved = list(declared_order)
    for run in manifest.get("runs") or []:
        retry_of = run.get("retry_of_order_index")
        if isinstance(retry_of, bool) or not isinstance(retry_of, int):
            continue
        original = declared_by_order.get(retry_of)
        if original is None:
            raise RuntimeError("pilot retry references an unknown declared run")
        item = dict(original)
        item.update({
            "pilot_repeat_index": int(run["pilot_repeat_index"]),
            "pilot_order_index": int(run["pilot_order_index"]),
            "cooldown_before_s": float(run.get("cooldown_before_s") or 0.0),
            "retry_of_order_index": retry_of,
        })
        resolved.append(item)
    return sorted(resolved, key=lambda item: int(item["pilot_order_index"]))


def observations_from_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for raw in manifest.get("observations") or []:
        value = dict(raw)
        summary_path = value.get("summary_path")
        if isinstance(summary_path, str) and summary_path:
            try:
                value["summary"] = read_json_object(Path(summary_path))
            except (FileNotFoundError, OSError, ValueError):
                value["summary"] = {"status": "failed", "error_code": "SUMMARY_UNAVAILABLE"}
        observations.append(value)
    return observations


def execute(args: argparse.Namespace) -> int:
    if not args.confirmed:
        raise RuntimeError("live pilot execution requires --confirmed")
    plan, matrix, model_lock, prompt_lock = load_context(args.plan)
    directory = pilot_directory(plan, args.pilot_dir)
    results = directory / "results"
    results.mkdir(parents=True, exist_ok=True)
    results.chmod(0o700)
    inventory = resolve_runtime_paths().inventory_path
    models = model_map(model_lock)
    prompts = prompt_map(prompt_lock)
    declared_order = expand_pilot_plan(plan, matrix)
    new_attempts = 0
    with exclusive_lock(directory):
        manifest = ensure_manifest(directory, plan, matrix)
        reconcile_pre_run_failures(manifest)
        reconcile_interrupted_runs(manifest)
        order = manifest_execution_order(declared_order, manifest)
        manifest.update({"status": "running", "phase": "executing"})
        persist_manifest(directory, manifest)
        for item in order:
            if args.stage != "all" and item["pilot_stage"] != args.stage:
                continue
            run = find_run(manifest, item)
            if run.get("status") in {"completed", "failed", "interrupted"}:
                continue
            pre_cleanup_started = time.monotonic()
            pre_cleanup_errors = unload_models(list(item["node_set"]), inventory)
            if pre_cleanup_errors:
                finished_at = utc_now()
                total_elapsed_s = time.monotonic() - pre_cleanup_started
                run.update({
                    "status": "failed",
                    "finished_at": finished_at,
                    "error_code": "PRE_RUN_CLEANUP_FAILED",
                    "cleanup_errors": pre_cleanup_errors,
                    "total_elapsed_s": total_elapsed_s,
                })
                manifest.setdefault("observations", []).append({
                    "pilot_cell_id": item["pilot_cell_id"],
                    "pilot_stage": item["pilot_stage"],
                    "pilot_repeat_index": item["pilot_repeat_index"],
                    "pilot_order_index": item["pilot_order_index"],
                    "cooldown_before_s": float(item.get("cooldown_before_s") or 0.0),
                    "status": "failed",
                    "run_id": None,
                    "summary_path": None,
                    "error_code": "PRE_RUN_CLEANUP_FAILED",
                    "cleanup_errors": pre_cleanup_errors,
                    "total_elapsed_s": total_elapsed_s,
                })
                append_retry_run(manifest, item)
                manifest.update({"status": "failed", "phase": "cleanup_failed"})
                persist_manifest(directory, manifest)
                return 1
            cooldown = float(item.get("cooldown_before_s") or 0.0)
            if cooldown > 0:
                print(f"[pilot] cooldown {cooldown:.0f}s before order {item['pilot_order_index']}", flush=True)
                time.sleep(cooldown)
            run.update({"status": "running", "started_at": utc_now()})
            persist_manifest(directory, manifest)
            config = experiment_config(plan, item, models, prompts)
            print(
                f"[pilot] order={item['pilot_order_index']} stage={item['pilot_stage']} "
                f"cell={item['pilot_cell_id']} repeat={item['pilot_repeat_index']} nodes={','.join(item['node_set'])}",
                flush=True,
            )
            summary: dict[str, Any]
            cleanup_errors: list[str] = []
            captured_run_id: str | None = None
            started_monotonic = time.monotonic()

            def progress(event: dict[str, Any]) -> None:
                nonlocal captured_run_id
                if event.get("run_id"):
                    event_run_id = str(event["run_id"])
                    if captured_run_id != event_run_id:
                        captured_run_id = event_run_id
                        run["run_id"] = event_run_id
                        persist_manifest(directory, manifest)

            try:
                summary = run_experiment(
                    config,
                    inventory_path=inventory,
                    results_root=results,
                    progress=progress,
                )
            except Exception as exc:
                summary_path = results / captured_run_id / "summary.json" if captured_run_id else None
                if summary_path is not None and summary_path.is_file():
                    summary = read_json_object(summary_path)
                else:
                    summary = {
                        "status": "failed",
                        "error_code": type(exc).__name__,
                        "error": str(exc),
                    }
            finally:
                cleanup_errors = unload_models(list(item["node_set"]), inventory)
            total_elapsed_s = time.monotonic() - started_monotonic
            result_dir = Path(str(summary.get("result_dir") or "")) if summary.get("result_dir") else None
            summary_path = result_dir / "summary.json" if result_dir else None
            status = "completed" if summary.get("status") == "completed" and not cleanup_errors else "failed"
            error_code = summary.get("error_code")
            if cleanup_errors:
                error_code = "CLEANUP_FAILED"
            run.update({
                "status": status,
                "finished_at": utc_now(),
                "run_id": summary.get("run_id"),
                "summary_path": str(summary_path) if summary_path else None,
                "error_code": error_code,
                "cleanup_errors": cleanup_errors,
                "total_elapsed_s": total_elapsed_s,
            })
            manifest.setdefault("observations", []).append({
                "pilot_cell_id": item["pilot_cell_id"],
                "pilot_stage": item["pilot_stage"],
                "pilot_repeat_index": item["pilot_repeat_index"],
                "pilot_order_index": item["pilot_order_index"],
                "cooldown_before_s": cooldown,
                "status": status,
                "run_id": summary.get("run_id"),
                "summary_path": str(summary_path) if summary_path else None,
                "error_code": error_code,
                "cleanup_errors": cleanup_errors,
                "total_elapsed_s": total_elapsed_s,
            })
            persist_manifest(directory, manifest)
            print(f"[pilot] {status} run={summary.get('run_id') or '-'}", flush=True)
            new_attempts += 1
            if cleanup_errors and plan["execution"]["stop_after_cleanup_failure"]:
                manifest.update({"status": "failed", "phase": "cleanup_failed"})
                persist_manifest(directory, manifest)
                return 1
            if args.max_new_runs is not None and new_attempts >= args.max_new_runs:
                break
        observations = observations_from_manifest(manifest)
        analysis = analyze_pilot(plan, observations)
        write_json_object(directory / "analysis.json", analysis, default_mode=0o600)
        manifest["analysis"] = {
            "path": str(directory / "analysis.json"),
            "freeze_ready": analysis["freeze_ready"],
            "selected_formal_repeats": analysis["precision_decision"]["selected_formal_repeats"],
            "selected_minimum_cooldown_s": analysis["thermal_decision"]["selected_minimum_cooldown_s"],
        }
        remaining = [item for item in manifest["runs"] if item["status"] == "pending"]
        manifest.update({
            "status": "completed" if not remaining and analysis["freeze_ready"] else "incomplete",
            "phase": "analyzed",
            "finished_at": utc_now() if not remaining else None,
        })
        persist_manifest(directory, manifest)
        print(json.dumps(manifest["analysis"], ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "completed" else 2


def analyze(args: argparse.Namespace) -> int:
    plan, matrix, _, _ = load_context(args.plan)
    directory = pilot_directory(plan, args.pilot_dir)
    with exclusive_lock(directory):
        manifest = ensure_manifest(directory, plan, matrix)
        reconcile_pre_run_failures(manifest)
        analysis = analyze_pilot(plan, observations_from_manifest(manifest))
        write_json_object(directory / "analysis.json", analysis, default_mode=0o600)
        manifest["analysis"] = {
            "path": str(directory / "analysis.json"),
            "freeze_ready": analysis["freeze_ready"],
            "selected_formal_repeats": analysis["precision_decision"]["selected_formal_repeats"],
            "selected_minimum_cooldown_s": analysis["thermal_decision"]["selected_minimum_cooldown_s"],
        }
        persist_manifest(directory, manifest)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    return 0 if analysis["freeze_ready"] else 2


def show_plan(args: argparse.Namespace) -> int:
    plan, matrix, _, _ = load_context(args.plan)
    order = expand_pilot_plan(plan, matrix)
    print(json.dumps({
        "pilot_id": plan["pilot_id"],
        "runs": len(order),
        "calibration_runs": sum(item["pilot_stage"] == "calibration" for item in order),
        "variance_runs": sum(item["pilot_stage"] == "variance" for item in order),
        "order": order,
    }, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    value.add_argument("--pilot-dir", type=Path)
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    execute_parser = commands.add_parser("execute")
    execute_parser.add_argument("--stage", choices=("calibration", "variance", "all"), default="all")
    execute_parser.add_argument("--confirmed", action="store_true")
    execute_parser.add_argument(
        "--max-new-runs",
        type=positive_int,
        help="stop cleanly after this many newly attempted runs without changing predeclared order",
    )
    commands.add_parser("analyze")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            return show_plan(args)
        if args.command == "execute":
            return execute(args)
        return analyze(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"phase09-pilot: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
