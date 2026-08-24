#!/usr/bin/env python3
"""Fail-closed repository validation used by hosted CI.

This gate is intentionally dependency-free.  It parses every shipped JSON
document with duplicate-key detection, runs the product's scientific lock and
matrix validators, and rejects unpinned workflow actions or broad process-kill
patterns before a pull request can merge.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cluster.research.locks import (
    validate_condition_lock,
    validate_model_lock,
    validate_prompt_lock,
    validate_runtime_lock,
)
from cluster.research.matrix import (
    expand_formal_matrix,
    validate_analysis_plan,
    validate_experiment_protocol,
)


ACTION_REF = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)@([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
BROAD_KILL = re.compile(r"\b(?:pkill\s+-f|pgrep\s+-f|killall)\b")


class ValidationFailure(RuntimeError):
    pass


def _unique_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationFailure(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure(f"invalid JSON {path.relative_to(ROOT)}: {exc}") from exc


def validate_json_documents() -> int:
    paths = sorted((ROOT / "config").rglob("*.json")) + sorted((ROOT / "cluster" / "config").rglob("*.json"))
    if not paths:
        raise ValidationFailure("no shipped JSON documents found")
    for path in paths:
        read_json(path)
    return len(paths)


def validate_research_contracts() -> int:
    research = ROOT / "config" / "research"
    validate_model_lock(read_json(research / "model_lock.json"))
    validate_condition_lock(read_json(research / "experiment_conditions.json"))
    validate_prompt_lock(read_json(research / "prompt_set.json"))
    validate_runtime_lock(read_json(research / "runtime_lock.json"))
    validate_experiment_protocol(read_json(research / "experiment_protocol.json"))
    validate_analysis_plan(read_json(research / "analysis_plan.json"))
    cells = expand_formal_matrix(read_json(research / "formal_experiment_matrix.json"))
    if not cells:
        raise ValidationFailure("formal experiment matrix expands to zero cells")
    return len(cells)


def validate_workflows() -> int:
    workflow_dir = ROOT / ".github" / "workflows"
    paths = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    if not paths:
        raise ValidationFailure("no GitHub Actions workflows found")
    action_count = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "pull_request_target:" in text:
            raise ValidationFailure(f"unsafe pull_request_target trigger in {path.relative_to(ROOT)}")
        if not re.search(r"(?m)^permissions:\s*\n\s+contents:\s+read\s*$", text):
            raise ValidationFailure(f"workflow must set top-level contents: read permission: {path.relative_to(ROOT)}")
        for action, reference in ACTION_REF.findall(text):
            if action.startswith("./") or action.startswith("docker://"):
                continue
            action_count += 1
            if not FULL_SHA.fullmatch(reference):
                raise ValidationFailure(f"workflow action is not pinned to a full SHA: {action}@{reference}")
    if action_count == 0:
        raise ValidationFailure("no pinned third-party workflow actions found")
    return action_count


def validate_shell_safety() -> int:
    paths = sorted(ROOT.glob("*.sh")) + sorted((ROOT / "cluster").rglob("*.sh")) + sorted((ROOT / "scripts").rglob("*.sh"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if BROAD_KILL.search(text):
            raise ValidationFailure(f"broad process-kill pattern in {path.relative_to(ROOT)}")
    return len(paths)


def main() -> int:
    try:
        json_count = validate_json_documents()
        matrix_cells = validate_research_contracts()
        action_count = validate_workflows()
        shell_count = validate_shell_safety()
    except (ValidationFailure, ValueError, TypeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(
        f"[OK] repository validation: {json_count} JSON documents, "
        f"{matrix_cells} formal cells, {action_count} pinned actions, "
        f"{shell_count} shell scripts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
