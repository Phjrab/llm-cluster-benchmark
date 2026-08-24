"""Legacy CSV Worker inventory contract used by the compatibility CLI.

The pure domain inventory intentionally has stricter migration semantics. This
module preserves the established CSV/API surface while keeping parsing and
validation out of the command dispatcher.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket
from typing import List, Sequence

from cluster.domain.identifiers import validate_node_id
from cluster.domain.worker import validate_worker_host


_USER_PATTERN = re.compile(r"^[a-z_][a-zA-Z0-9_-]*$")


def validate_identity_reference(identity_file: str) -> str:
    raw = identity_file.strip()
    if not raw:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ValueError("identity_file contains unsupported control characters")
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not expanded.is_absolute() or ".." in expanded.parts:
        raise ValueError("identity_file must resolve to an absolute path without traversal")
    return raw


def validate_project_dir(project_dir: str, user: str = "") -> str:
    if (
        not re.fullmatch(r"/(?:home|opt|srv)/[a-zA-Z0-9._/-]+", project_dir)
        or ".." in Path(project_dir).parts
    ):
        raise ValueError("project_dir must be a safe path below /home, /opt or /srv")
    normalized = str(Path(project_dir))
    broad = {"/", "/home", "/opt", "/srv"}
    if user:
        broad.add(f"/home/{user}")
    parts = Path(normalized).parts
    if normalized in broad or (len(parts) >= 2 and parts[1] == "home" and len(parts) < 4):
        raise ValueError(f"project_dir is too broad for code synchronization: {project_dir}")
    return normalized


@dataclass(frozen=True)
class Node:
    name: str
    role: str
    host: str
    user: str
    ssh_port: int
    api_port: int
    project_dir: str
    enabled: bool
    identity_file: str = ""
    platform: str = "auto"

    @property
    def api_url(self) -> str:
        return f"http://{self.host}:{self.api_port}"

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def is_local(self) -> bool:
        if self.role != "head":
            return False
        return self.host in {
            "127.0.0.1", "localhost", "::1", socket.gethostname(), socket.getfqdn()
        }


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def load_nodes(
    path: Path,
    include_disabled: bool = False,
    *,
    require_legacy_head: bool = True,
) -> List[Node]:
    if not path.exists():
        raise FileNotFoundError(
            f"Inventory not found: {path}. Run ./cluster/setup_head.sh to create "
            "a platform-aware head inventory, or copy cluster/config/nodes.example.csv "
            "to .run/cluster/nodes.local.csv for a manual setup."
        )
    nodes: List[Node] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"name", "role", "host", "user", "ssh_port", "api_port", "project_dir", "enabled"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Inventory is missing columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            if not row.get("name", "").strip():
                continue
            try:
                node = Node(
                    name=row["name"].strip(), role=row["role"].strip().lower(),
                    host=row["host"].strip(), user=row["user"].strip(),
                    ssh_port=int(row["ssh_port"]), api_port=int(row["api_port"]),
                    project_dir=row["project_dir"].strip(), enabled=_as_bool(row["enabled"]),
                    identity_file=row.get("identity_file", "").strip(),
                    platform=(row.get("platform", "auto") or "auto").strip().lower(),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid inventory row {line_number}: {exc}") from exc
            if node.role not in {"head", "worker"}:
                raise ValueError(f"Invalid role for {node.name}: {node.role}")
            if node.platform not in {"auto", "jetson", "raspberry-pi"}:
                raise ValueError(f"Invalid platform for {node.name}: {node.platform}")
            try:
                validate_node_id(node.name)
                validate_worker_host(node.host)
            except ValueError as exc:
                raise ValueError(f"Invalid node identity for {node.name}: {exc}") from exc
            if not _USER_PATTERN.fullmatch(node.user):
                raise ValueError(f"Invalid SSH user for {node.name}")
            try:
                validate_project_dir(node.project_dir, node.user)
                validate_identity_reference(node.identity_file)
            except ValueError as exc:
                field = "project_dir" if "project_dir" in str(exc) else "identity_file"
                raise ValueError(f"Invalid {field} for {node.name}: {exc}") from exc
            if not 1 <= node.ssh_port <= 65535 or not 1 <= node.api_port <= 65535:
                raise ValueError(f"Ports must be between 1 and 65535 for {node.name}")
            nodes.append(node)
    names = [node.name for node in nodes]
    if len(names) != len(set(names)):
        raise ValueError("Inventory contains duplicate node names")
    if require_legacy_head and sum(1 for node in nodes if node.role == "head" and node.enabled) != 1:
        raise ValueError("Inventory must contain exactly one enabled head node")
    return nodes if include_disabled else [node for node in nodes if node.enabled]


def select_nodes(nodes: Sequence[Node], names: Sequence[str], workers_only: bool = False) -> List[Node]:
    selected = list(nodes)
    if workers_only:
        selected = [node for node in selected if node.role == "worker"]
    if names:
        wanted = set(names)
        selected = [node for node in selected if node.name in wanted]
        missing = wanted.difference(node.name for node in selected)
        if missing:
            raise ValueError(f"Unknown or disabled nodes: {', '.join(sorted(missing))}")
    return selected


__all__ = ["Node", "load_nodes", "select_nodes", "validate_identity_reference", "validate_project_dir"]
