#!/usr/bin/env python3
"""Build a deterministic Phase 11 publication bundle from one result class."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cluster.research.publication import PublicationError, write_publication_bundle


RESEARCH = ROOT / "config" / "research"


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"JSON document must be an object: {path}")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Generate separated, deterministic statistics, tables, SVG figures, and archive checksums.",
    )
    subparsers = value.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build one immutable publication bundle")
    build.add_argument("--results-root", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--experiment-type", choices=("formal", "pilot"), required=True)
    build.add_argument("--analysis-plan", type=Path, default=RESEARCH / "analysis_plan.json")
    build.add_argument(
        "--acknowledge-non-formal",
        action="store_true",
        help="required for pilot export; confirms that the bundle cannot be presented as formal evidence",
    )
    return value


def command_build(args: argparse.Namespace) -> int:
    locked_inputs = {
        "analysis-plan.json": args.analysis_plan,
        "formal-experiment-matrix.json": RESEARCH / "formal_experiment_matrix.json",
        "experiment-protocol.json": RESEARCH / "experiment_protocol.json",
        "model-lock.json": RESEARCH / "model_lock.json",
        "prompt-set.json": RESEARCH / "prompt_set.json",
        "runtime-lock.json": RESEARCH / "runtime_lock.json",
        "experiment-conditions.json": RESEARCH / "experiment_conditions.json",
    }
    result = write_publication_bundle(
        results_root=args.results_root,
        output_dir=args.output_dir,
        experiment_type=args.experiment_type,
        analysis_plan=read_object(args.analysis_plan),
        locked_inputs=locked_inputs,
        acknowledge_non_formal=args.acknowledge_non_formal,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "build":
            return command_build(args)
    except PublicationError as exc:
        print(f"phase11-publication: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
