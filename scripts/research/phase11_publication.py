#!/usr/bin/env python3
"""Build a deterministic Phase 11 publication bundle from one result class."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
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
    build.add_argument("--pilot-manifest", type=Path)
    build.add_argument("--pilot-analysis", type=Path)
    build.add_argument("--campaign-manifest", type=Path)
    build.add_argument(
        "--acknowledge-non-formal",
        action="store_true",
        help="required for pilot export; confirms that the bundle cannot be presented as formal evidence",
    )
    build.add_argument(
        "--svg-only",
        action="store_true",
        help="omit 300/600 DPI PNG files when Node.js Playwright is intentionally unavailable",
    )
    return value


def render_png(figures: Path) -> dict[str, Any]:
    node = shutil.which("node")
    renderer = Path(__file__).with_name("render_publication_png.js")
    if node is None:
        raise PublicationError("Node.js is required for PNG export; use --svg-only only when vector-only output is intentional")
    if not renderer.is_file():
        raise PublicationError(f"PNG renderer is missing: {renderer}")
    try:
        completed = subprocess.run(
            [node, str(renderer), "--figures", str(figures), "--dpi", "300,600", "--width-mm", "180"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationError("PNG renderer could not complete") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown renderer failure"
        raise PublicationError(detail)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PublicationError("PNG renderer returned invalid metadata") from exc
    if not isinstance(value, dict):
        raise PublicationError("PNG renderer metadata must be an object")
    return value


def command_build(args: argparse.Namespace) -> int:
    formal_authorized = False
    locked_inputs = {
        "analysis-engine.py": ROOT / "cluster" / "research" / "publication.py",
        "analysis-plan.json": args.analysis_plan,
        "formal-experiment-matrix.json": RESEARCH / "formal_experiment_matrix.json",
        "experiment-protocol.json": RESEARCH / "experiment_protocol.json",
        "model-lock.json": RESEARCH / "model_lock.json",
        "prompt-set.json": RESEARCH / "prompt_set.json",
        "runtime-lock.json": RESEARCH / "runtime_lock.json",
        "experiment-conditions.json": RESEARCH / "experiment_conditions.json",
        "phase11-publication.py": Path(__file__).resolve(),
        "render-publication-png.js": Path(__file__).with_name("render_publication_png.js"),
    }
    result_parent = args.results_root.resolve().parent
    if args.experiment_type == "pilot":
        if args.campaign_manifest is not None:
            raise PublicationError("campaign manifest cannot be supplied to a pilot bundle")
        locked_inputs["pilot-manifest.json"] = args.pilot_manifest or result_parent / "manifest.json"
        locked_inputs["pilot-analysis.json"] = args.pilot_analysis or result_parent / "analysis.json"
        pilot_manifest = read_object(locked_inputs["pilot-manifest.json"])
        pilot_analysis = read_object(locked_inputs["pilot-analysis.json"])
        if pilot_manifest.get("experiment_type") != "pilot" or pilot_analysis.get("experiment_type") != "pilot":
            raise PublicationError("pilot manifest and analysis must both declare experiment_type=pilot")
        if pilot_manifest.get("pilot_id") != pilot_analysis.get("pilot_id"):
            raise PublicationError("pilot manifest and analysis identities do not match")
    else:
        if args.pilot_manifest is not None or args.pilot_analysis is not None:
            raise PublicationError("pilot artifacts cannot be supplied to a formal bundle")
        locked_inputs["campaign-manifest.json"] = args.campaign_manifest or result_parent / "manifest.json"
        campaign = read_object(locked_inputs["campaign-manifest.json"])
        if campaign.get("artifact_type") != "formal_campaign" or campaign.get("experiment_type") != "formal":
            raise PublicationError("campaign manifest must be a formal_campaign artifact")
        if campaign.get("status") != "completed":
            raise PublicationError("formal publication requires a completed campaign manifest")
        formal_authorized = True
    result = write_publication_bundle(
        results_root=args.results_root,
        output_dir=args.output_dir,
        experiment_type=args.experiment_type,
        analysis_plan=read_object(args.analysis_plan),
        locked_inputs=locked_inputs,
        acknowledge_non_formal=args.acknowledge_non_formal,
        png_renderer=None if args.svg_only else render_png,
        formal_authorized=formal_authorized,
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
