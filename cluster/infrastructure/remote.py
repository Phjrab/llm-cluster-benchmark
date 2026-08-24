"""SSH and remote-command execution boundary for worker operations."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class RemoteTarget(Protocol):
    host: str
    ssh_port: int
    ssh_target: str
    is_local: bool


def build_ssh_command(
    target: RemoteTarget,
    identity_file: Path | None = None,
    *,
    known_hosts_file: Path | None = None,
    require_pinned: bool = False,
) -> list[str]:
    if require_pinned and known_hosts_file is None:
        raise ValueError("Pinned SSH mode requires a managed known_hosts file")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5"]
    if require_pinned:
        command.extend([
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts_file}",
            "-o", "GlobalKnownHostsFile=/dev/null",
        ])
    else:
        command.extend(["-o", "StrictHostKeyChecking=accept-new"])
    command.extend(["-p", str(target.ssh_port)])
    if identity_file is not None:
        command.extend(["-i", str(identity_file), "-o", "IdentitiesOnly=yes"])
    return [*command, target.ssh_target]


class SshRemoteExecutor:
    def run(
        self,
        target: RemoteTarget,
        args: Sequence[str],
        *,
        timeout: int = 120,
        identity_file: Path | None = None,
        known_hosts_file: Path | None = None,
        require_pinned: bool = False,
    ) -> CommandResult:
        command = list(args) if target.is_local else [
            *build_ssh_command(
                target,
                identity_file,
                known_hosts_file=known_hosts_file,
                require_pinned=require_pinned,
            ),
            " ".join(shlex.quote(part) for part in args),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
