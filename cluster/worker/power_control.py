#!/usr/bin/env python3
"""Jetson ``nvpmodel`` status and change helper for the Controller SSH path.

This module intentionally has no HTTP surface.  It is copied to a Worker by
the existing code-sync action, then invoked through the Controller's fixed SSH
executor.  Only the integer IDs returned by the local ``nvpmodel`` mode list
can be applied; it never accepts a shell command, password, or reboot answer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


_MODE_PATTERN = re.compile(
    r"POWER_MODEL:\s*ID=(?P<id>\d+)\s+NAME=(?P<name>[^\s>]+)", re.IGNORECASE
)
_MODE_ID_PATTERN = re.compile(r"^\s*(\d+)\s*$")
_MODE_NAME_PATTERN = re.compile(r"NV\s+Power\s+Mode:\s*(.+?)\s*$", re.IGNORECASE)
_WATT_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*W\b", re.IGNORECASE)


def _nvpmodel_path() -> str | None:
    fixed = Path("/usr/sbin/nvpmodel")
    if fixed.is_file():
        return str(fixed)
    discovered = shutil.which("nvpmodel")
    return discovered if discovered else None


def is_jetson() -> bool:
    return Path("/etc/nv_tegra_release").exists() or _nvpmodel_path() is not None


def _run(arguments: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def parse_modes(output: str) -> list[dict[str, Any]]:
    """Return unique local power modes from documented verbose nvpmodel output."""
    modes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for match in _MODE_PATTERN.finditer(output or ""):
        mode_id = int(match.group("id"))
        if mode_id in seen:
            continue
        seen.add(mode_id)
        name = match.group("name").strip()
        watt_match = _WATT_PATTERN.search(name)
        budget = float(watt_match.group(1)) if watt_match else None
        modes.append({"id": mode_id, "name": name, "power_budget_w": budget})
    return modes


def _recommended_mode(modes: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose a *display-only* maximum-consumption candidate when unambiguous."""
    values = list(modes)
    with_budget = [mode for mode in values if isinstance(mode.get("power_budget_w"), (int, float))]
    if with_budget:
        maximum = max(float(mode["power_budget_w"]) for mode in with_budget)
        candidates = [mode for mode in with_budget if float(mode["power_budget_w"]) == maximum]
        if len(candidates) == 1:
            return dict(candidates[0])
    maxn = [
        mode for mode in values
        if re.search(r"(?:^|[_-])MAXN(?:[_-]|$)", str(mode.get("name", "")), re.IGNORECASE)
    ]
    return dict(maxn[0]) if len(maxn) == 1 else None


def _current_mode(output: str, modes: list[dict[str, Any]]) -> dict[str, Any] | None:
    current_id: int | None = None
    current_name = ""
    for line in (output or "").splitlines():
        identifier = _MODE_ID_PATTERN.fullmatch(line)
        if identifier:
            current_id = int(identifier.group(1))
        name = _MODE_NAME_PATTERN.search(line)
        if name:
            current_name = name.group(1).strip()
    if current_id is not None:
        match = next((mode for mode in modes if mode["id"] == current_id), None)
        return dict(match) if match else {"id": current_id, "name": current_name or f"mode-{current_id}", "power_budget_w": None}
    if current_name:
        match = next((mode for mode in modes if mode["name"] == current_name), None)
        return dict(match) if match else {"id": None, "name": current_name, "power_budget_w": None}
    return None


def _sudo_available() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        return _run(["sudo", "-n", "true"], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def status() -> dict[str, Any]:
    executable = _nvpmodel_path()
    if not is_jetson() or executable is None:
        return {
            "ok": True,
            "supported": False,
            "platform": "not-jetson",
            "message": "nvpmodel is only available on NVIDIA Jetson devices.",
            "modes": [],
            "current": None,
            "recommended_mode": None,
            "can_apply": False,
        }
    try:
        listed = _run([executable, "-p", "--verbose"])
        queried = _run([executable, "-q"])
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "supported": True,
            "platform": "jetson",
            "message": "Could not inspect nvpmodel.",
            "error": type(exc).__name__,
            "modes": [],
            "current": None,
            "recommended_mode": None,
            "can_apply": False,
        }
    modes = parse_modes(listed.stdout)
    current = _current_mode(queried.stdout, modes)
    error = ""
    if listed.returncode != 0:
        error = "nvpmodel mode listing failed"
    elif queried.returncode != 0:
        error = "nvpmodel current mode query failed"
    elif not modes:
        error = "nvpmodel returned no parseable power modes"
    return {
        "ok": not error,
        "supported": True,
        "platform": "jetson",
        "message": error or "Jetson power modes read successfully.",
        "error": error,
        "modes": modes,
        "current": current,
        "recommended_mode": _recommended_mode(modes),
        "can_apply": _sudo_available(),
        "manual_command": "",
        "reboot_required": False,
        "jetson_clocks": {"state": "unknown", "message": "nvpmodel will reject incompatible clock locks."},
    }


def set_mode(mode_id: int) -> tuple[dict[str, Any], int]:
    report = status()
    if not report.get("supported") or not report.get("ok"):
        return report, 1
    mode_ids = {int(mode["id"]) for mode in report["modes"]}
    if mode_id not in mode_ids:
        report.update({
            "ok": False,
            "message": "Requested power mode is not in this Jetson's local nvpmodel configuration.",
            "error": "invalid_mode_id",
        })
        return report, 2
    executable = _nvpmodel_path()
    assert executable is not None
    if not report.get("can_apply"):
        report.update({
            "ok": False,
            "message": "Passwordless sudo is unavailable on this Worker. Run the displayed command directly on that Jetson, then refresh.",
            "error": "sudo_required",
            "manual_command": f"sudo {executable} -m {mode_id}",
        })
        return report, 1
    command = [executable, "-m", str(mode_id)] if os.geteuid() == 0 else ["sudo", "-n", executable, "-m", str(mode_id)]
    try:
        applied = _run(command, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        report.update({"ok": False, "message": "nvpmodel apply did not complete.", "error": type(exc).__name__})
        return report, 1
    refreshed = status()
    reboot_text = f"{applied.stdout}\n{applied.stderr}".lower()
    refreshed["reboot_required"] = "reboot" in reboot_text
    if applied.returncode != 0:
        refreshed.update({
            "ok": False,
            "message": "nvpmodel rejected the change. It may be locked by jetson_clocks or require manual recovery.",
            "error": "nvpmodel_apply_failed",
            "manual_command": f"sudo {executable} -m {mode_id}",
        })
        return refreshed, 1
    current = refreshed.get("current") or {}
    if current.get("id") != mode_id:
        refreshed.update({
            "ok": False,
            "message": "nvpmodel did not confirm the requested mode after apply.",
            "error": "verification_failed",
        })
        return refreshed, 1
    refreshed.update({"ok": True, "message": "Jetson power mode applied and verified."})
    return refreshed, 0


def _emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")), flush=True)


def main(argv: list[str]) -> int:
    if len(argv) == 1 and argv[0] == "status":
        report = status()
        _emit(report)
        return 0 if report.get("ok") else 1
    if len(argv) == 2 and argv[0] == "set" and re.fullmatch(r"\d{1,5}", argv[1]):
        report, code = set_mode(int(argv[1]))
        _emit(report)
        return code
    _emit({"ok": False, "supported": False, "error": "usage", "message": "Usage: power_control.py status | set <mode-id>"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
