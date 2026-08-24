#!/usr/bin/env python3
"""Exercise the installed Controller lifecycle on a disposable macOS runner."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "scripts" / "llm-cluster"
ARTIFACT_DIR = ROOT / ".artifacts" / "ci"
REPORT_PATH = ARTIFACT_DIR / "macos-controller.json"
IDENTITY_PATH = ROOT / ".run" / "controller" / "dashboard.identity.json"
HEALTH_URL = "http://127.0.0.1:8080/dashboard/health"


class GateFailure(RuntimeError):
    pass


def run(action: str, *, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(COMMAND), action],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=45,
        check=False,
    )
    print(completed.stdout, end="")
    if completed.returncode not in allowed:
        raise GateFailure(f"llm-cluster {action} exited {completed.returncode}")
    return completed


def identity() -> dict[str, Any]:
    try:
        value = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot read Controller identity: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
        raise GateFailure("Controller identity is malformed")
    return value


def health(*, expected: bool) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        if expected:
            raise GateFailure("Dashboard health endpoint is unavailable")
        return None
    if not expected:
        raise GateFailure("Dashboard health endpoint remained available after stop")
    if response.status != 200 or payload.get("ok") is not True or payload.get("role") != "controller":
        raise GateFailure(f"unexpected Dashboard health payload: {payload!r}")
    return payload


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"status": "running", "steps": []}
    try:
        run("stop")
        run("status", allowed=(3,))
        health(expected=False)
        report["steps"].append("initially_stopped")

        run("start")
        first = identity()
        report["first_identity"] = first
        report["health"] = health(expected=True)
        run("status")
        report["steps"].append("started_and_healthy")

        run("start")
        if identity().get("pid") != first.get("pid"):
            raise GateFailure("idempotent start changed the Controller PID")
        report["steps"].append("idempotent_start")

        run("restart")
        second = identity()
        report["second_identity"] = second
        if (second.get("pid"), second.get("started_at")) == (first.get("pid"), first.get("started_at")):
            raise GateFailure("restart did not create a new Controller generation")
        health(expected=True)
        run("logs")
        report["steps"].append("restarted_with_new_generation")

        run("stop")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and health(expected=False) is not None:
            time.sleep(0.1)
        run("status", allowed=(3,))
        report["steps"].append("stopped_and_port_released")
        report["status"] = "passed"
        return 0
    except (GateFailure, subprocess.SubprocessError) as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            run("stop")
        except Exception as exc:  # the report must retain cleanup failures
            report.setdefault("cleanup_error", str(exc))
            if report.get("status") == "passed":
                report["status"] = "failed"
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
