"""Explicit SSH host-key discovery and project-local pin storage."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from cluster.infrastructure.storage import atomic_write_text


FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]+={0,2}$")


def _endpoint(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _fingerprint(key_line: str, *, runner: Callable[..., Any] = subprocess.run) -> str:
    result = runner(
        ["ssh-keygen", "-E", "sha256", "-lf", "-"],
        input=key_line + "\n",
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ssh-keygen could not fingerprint the scanned host key")
    parts = result.stdout.split()
    value = next((part for part in parts if part.startswith("SHA256:")), "")
    if not FINGERPRINT.fullmatch(value):
        raise RuntimeError("ssh-keygen returned an invalid SHA-256 host-key fingerprint")
    return value


def scan_host_keys(
    host: str,
    port: int,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict[str, str]]:
    result = runner(
        ["ssh-keyscan", "-T", "5", "-p", str(port), host],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    if result.returncode not in {0, 1} and not result.stdout.strip():
        raise RuntimeError("ssh-keyscan could not read a host key")
    expected_endpoint = _endpoint(host, port)
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[0] != expected_endpoint or not parts[1].startswith("ssh-"):
            continue
        fingerprint = _fingerprint(line, runner=runner)
        key = (parts[1], fingerprint)
        if key not in seen:
            seen.add(key)
            candidates.append({
                "endpoint": expected_endpoint,
                "key_type": parts[1],
                "fingerprint": fingerprint,
                "known_hosts_line": line,
            })
    if not candidates:
        raise RuntimeError("No valid SSH host key was returned")
    return candidates


def pin_host_key(
    path: Path,
    host: str,
    port: int,
    expected_fingerprint: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, str]:
    if not FINGERPRINT.fullmatch(expected_fingerprint or ""):
        raise ValueError("Expected SSH fingerprint must use SHA256:... format")
    matches = [
        item for item in scan_host_keys(host, port, runner=runner)
        if item["fingerprint"] == expected_fingerprint
    ]
    if len(matches) != 1:
        raise ValueError("Scanned SSH host key does not match the confirmed fingerprint")
    selected = matches[0]
    endpoint = selected["endpoint"]
    existing: list[str] = []
    try:
        existing = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        pass
    retained = [line for line in existing if line.strip() and line.split(maxsplit=1)[0] != endpoint]
    retained.append(selected["known_hosts_line"])
    atomic_write_text(path, "\n".join(retained) + "\n", default_mode=0o600)
    path.chmod(0o600)
    return {key: value for key, value in selected.items() if key != "known_hosts_line"}


def list_pinned_host_keys(
    path: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    values: list[dict[str, str]] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 3:
            continue
        values.append({
            "endpoint": parts[0],
            "key_type": parts[1],
            "fingerprint": _fingerprint(line, runner=runner),
        })
    return values


__all__ = ["list_pinned_host_keys", "pin_host_key", "scan_host_keys"]
