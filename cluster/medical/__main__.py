"""Offline tools only. Installation and Start remain explicit Dashboard actions."""
import argparse
import json
from pathlib import Path

from .planning import BLOCKS, inventory_workers, prepare, write_bundle
from .datasets import SOURCES, import_subset, write_subset
from .results import read_json, index_runs, write_index, combine_indexes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="write one block's real Sweep preview payloads; never execute")
    p.add_argument("block", choices=BLOCKS)
    p.add_argument("--inventory", required=True, type=Path)
    p.add_argument("--workers", required=True, nargs="+")
    p.add_argument("--models", nargs="+")
    p.add_argument("--prompts", nargs="+")
    p.add_argument("--category")
    p.add_argument("--stress-2n", action="store_true")
    p.add_argument("--no-persist-prompt", action="store_true")
    p.add_argument("--response-storage", choices=["full", "hash_only", "none"], default="full")
    p.add_argument("--public-questions", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("import-subset", help="import already obtained permitted JSONL; separate keys")
    p.add_argument("--dataset", choices=SOURCES, required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--split", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--answer-base", type=int, choices=[0, 1])
    p.add_argument("--calibration", type=int, default=20)
    p.add_argument("--evaluation", type=int, default=200)
    p.add_argument("--seed", type=int, default=20261023)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("index", help="join existing private results to the exported resolved plan")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--package", required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--runs", type=Path, nargs="*", default=[])
    p.add_argument("--keys", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("compare", help="combine explicitly selected local medical indexes")
    p.add_argument("--indexes", type=Path, nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        bundle = prepare(args.block, inventory_workers(args.inventory, args.workers), models=args.models,
                         prompt_ids=args.prompts, category=args.category, stress=args.stress_2n,
                         persist_prompt=not args.no_persist_prompt, response_storage_mode=args.response_storage,
                         public_questions=read_json(args.public_questions) if args.public_questions else None)
        write_bundle(bundle, args.output)
        print(json.dumps({"mode": "PREPARE_ONLY", "counts": bundle["counts"], "packages": len(bundle["packages"]), "output": str(args.output)}, ensure_ascii=False))
    elif args.command == "import-subset":
        if args.input.stat().st_size > 64 * 1024 * 1024:
            parser.error("local subset input exceeds 64 MiB")
        rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
        subset = import_subset(rows, dataset=args.dataset, config=args.config, split=args.split, revision=args.revision,
                               answer_base=args.answer_base, seed=args.seed, calibration=args.calibration, evaluation=args.evaluation)
        write_subset(subset, args.output)
        print(json.dumps(subset["manifest"], ensure_ascii=False))
    elif args.command == "compare":
        index = combine_indexes([read_json(path) for path in args.indexes])
        write_index(index, args.output)
        print(json.dumps({"status": index["status"], "runs": len(index["runs"])}))
    else:
        index = index_runs(read_json(args.manifest), args.package, read_json(args.plan), args.runs,
                           extra_keys=read_json(args.keys) if args.keys else None)
        write_index(index, args.output)
        print(json.dumps({"status": index["status"], "runs": len(index["runs"]), "remaining_trials": index["remaining_trials"]}))


if __name__ == "__main__":
    main()
