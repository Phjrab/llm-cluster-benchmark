"""Deterministic source manifests for Worker deployments.

The Controller builds this manifest from the exact filesystem tree selected by
the deployment exclusions.  A Worker finalizes runtime fields and verifies the
same source files after rsync, so `.git` never has to be copied to a Worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from cluster.domain.deployment import (
    UNVERIFIED_RUNTIME,
    DeploymentManifest,
    DeploymentSourceFile,
    canonical_source_tree_sha256,
)
from cluster.domain.errors import DomainValidationError
from cluster.infrastructure.storage import read_json_object, write_json_object


DEPLOYMENT_MANIFEST_RELATIVE_PATH = Path(".run/cluster/deployment-manifest.json")
PINNED_RPC_COMMIT = "f49e9178767d557a522618b16ce8694f9ddac628"
REQUIREMENT_PATHS = (
    "requirements-controller.txt",
    "requirements-worker.txt",
    "cluster/requirements-runtime.txt",
)

# Keep these values aligned with clusterctl's rsync argv. Excluded directories
# retain Worker runtime, model, result, and local build state across deployments.
RSYNC_EXCLUDES = (
    ".git/",
    ".venv/",
    ".run/",
    "models/",
    "outputs/",
    "results/",
    "cluster/results/",
    "cluster/nodes.local.csv",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "build/",
    "dist/",
    "*.egg-info/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
)

_EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    ".run",
    "models",
    "outputs",
    "results",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
}
_EXCLUDED_FILENAMES = {".DS_Store", "nodes.local.csv"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class DeploymentVerificationError(ValueError):
    """A deployed source tree cannot be proven to match its manifest."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_deployment_source_path(relative_path: str) -> bool:
    """Return whether a canonical relative file belongs in Worker source."""
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    if any(part in _EXCLUDED_DIRECTORY_NAMES or part.endswith(".egg-info") for part in path.parts[:-1]):
        return False
    if path.name in _EXCLUDED_FILENAMES or path.suffix in _EXCLUDED_SUFFIXES:
        return False
    if path.as_posix() == "cluster/nodes.local.csv":
        return False
    return True


def collect_source_files(project_root: Path) -> tuple[DeploymentSourceFile, ...]:
    root = Path(project_root).resolve()
    values: list[DeploymentSourceFile] = []
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if not is_deployment_source_path(relative):
            continue
        if candidate.is_symlink():
            raise DeploymentVerificationError(f"deployment source must not contain symlinks: {relative}")
        if not candidate.is_file():
            continue
        mode = candidate.stat().st_mode
        if not stat.S_ISREG(mode):
            raise DeploymentVerificationError(f"deployment source must be a regular file: {relative}")
        values.append(
            DeploymentSourceFile(
                path=relative,
                size_bytes=candidate.stat().st_size,
                sha256=sha256_file(candidate),
                executable=bool(mode & stat.S_IXUSR),
            )
        )
    if not values:
        raise DeploymentVerificationError("deployment source tree is empty")
    return tuple(values)


def _git_output(project_root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(project_root), *arguments],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=10,
    ).strip()


def git_source_state(project_root: Path) -> tuple[str, bool]:
    try:
        commit = _git_output(project_root, "rev-parse", "HEAD")
        status = _git_output(project_root, "status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentVerificationError("Controller source must be a Git checkout") from exc
    return commit, not bool(status)


def _requirements_hashes(project_root: Path) -> tuple[tuple[str, str], ...]:
    values = []
    for relative in REQUIREMENT_PATHS:
        path = Path(project_root) / relative
        if not path.is_file():
            raise DeploymentVerificationError(f"required deployment input is missing: {relative}")
        values.append((relative, sha256_file(path)))
    return tuple(values)


def build_deployment_manifest(
    project_root: Path,
    *,
    deployed_at: str | None = None,
    source_commit: str | None = None,
    working_tree_clean: bool | None = None,
    runtime_fingerprint: str = UNVERIFIED_RUNTIME,
    llama_cpp_python_version: str = UNVERIFIED_RUNTIME,
) -> DeploymentManifest:
    root = Path(project_root).resolve()
    commit, clean = git_source_state(root) if source_commit is None else (
        source_commit,
        bool(working_tree_clean),
    )
    files = collect_source_files(root)
    manifest = DeploymentManifest(
        source_commit=commit,
        source_tree_sha256=canonical_source_tree_sha256(files),
        deployment_manifest_sha256="",
        requirements_sha256=_requirements_hashes(root),
        runtime_fingerprint=runtime_fingerprint,
        llama_cpp_python_version=llama_cpp_python_version,
        rpc_commit=PINNED_RPC_COMMIT,
        deployed_at=deployed_at or utc_now(),
        working_tree_clean=clean if working_tree_clean is None else working_tree_clean,
        source_tree_verified=False,
        source_files=files,
    )
    return manifest.sealed()


def write_deployment_manifest(path: Path, manifest: DeploymentManifest) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    write_json_object(path, manifest.to_dict(), default_mode=0o600)
    path.chmod(0o600)


def read_deployment_manifest(path: Path) -> DeploymentManifest:
    return DeploymentManifest.from_dict(read_json_object(Path(path)))


def verify_source_tree(project_root: Path, manifest: DeploymentManifest) -> tuple[bool, str]:
    try:
        observed = collect_source_files(Path(project_root))
    except (OSError, DomainValidationError, DeploymentVerificationError) as exc:
        return False, str(exc)
    expected_paths = [item.path for item in manifest.source_files]
    observed_paths = [item.path for item in observed]
    if observed_paths != expected_paths:
        missing = sorted(set(expected_paths).difference(observed_paths))
        unexpected = sorted(set(observed_paths).difference(expected_paths))
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing[:8]))
        if unexpected:
            detail.append("unexpected=" + ",".join(unexpected[:8]))
        return False, "source file set mismatch: " + " ".join(detail)
    observed_sha = canonical_source_tree_sha256(observed)
    if observed_sha != manifest.source_tree_sha256:
        return False, f"source tree checksum mismatch: expected {manifest.source_tree_sha256}, got {observed_sha}"
    return True, "source tree matches deployment manifest"


def probe_runtime_identity() -> tuple[str, str]:
    """Return llama runtime version/fingerprint without importing Worker/FastAPI."""
    try:
        version = metadata.version("llama-cpp-python")
        from llama_cpp import llama_cpp

        raw = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
        normalized = " ".join(raw.split())[:1000]
        if not normalized:
            return UNVERIFIED_RUNTIME, version
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16], version
    except Exception:
        return UNVERIFIED_RUNTIME, UNVERIFIED_RUNTIME


def finalize_deployment_manifest(project_root: Path, manifest_path: Path) -> DeploymentManifest:
    manifest = read_deployment_manifest(manifest_path)
    verified, detail = verify_source_tree(project_root, manifest)
    if not verified:
        raise DeploymentVerificationError(detail)
    runtime_fingerprint, llama_version = probe_runtime_identity()
    finalized = manifest.with_runtime(
        runtime_fingerprint=runtime_fingerprint,
        llama_cpp_python_version=llama_version,
        source_tree_verified=True,
    )
    write_deployment_manifest(manifest_path, finalized)
    return finalized


def deployment_status(project_root: Path) -> dict[str, Any]:
    manifest_path = Path(project_root) / DEPLOYMENT_MANIFEST_RELATIVE_PATH
    try:
        manifest = read_deployment_manifest(manifest_path)
        source_verified, detail = verify_source_tree(project_root, manifest)
        runtime_ready = (
            manifest.runtime_fingerprint != UNVERIFIED_RUNTIME
            and manifest.llama_cpp_python_version != UNVERIFIED_RUNTIME
        )
        verified = bool(
            manifest.integrity_valid
            and source_verified
            and manifest.source_tree_verified
            and manifest.working_tree_clean
            and runtime_ready
        )
        return {
            "available": True,
            "verified": verified,
            "source_commit": manifest.source_commit,
            "source_tree_sha256": manifest.source_tree_sha256,
            "deployment_manifest_sha256": manifest.deployment_manifest_sha256,
            "requirements_sha256": dict(manifest.requirements_sha256),
            "runtime_fingerprint": manifest.runtime_fingerprint,
            "llama_cpp_python_version": manifest.llama_cpp_python_version,
            "rpc_commit": manifest.rpc_commit,
            "deployed_at": manifest.deployed_at,
            "working_tree_clean": manifest.working_tree_clean,
            "source_tree_verified": source_verified,
            "source_file_count": len(manifest.source_files),
            "detail": detail,
        }
    except FileNotFoundError:
        return {"available": False, "verified": False, "error": "deployment manifest is missing"}
    except (OSError, ValueError, DomainValidationError) as exc:
        return {"available": True, "verified": False, "error": str(exc)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("finalize", "verify"):
        item = subparsers.add_parser(command)
        item.add_argument("--project-root", type=Path, required=True)
        item.add_argument("--manifest", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    manifest_path = args.manifest or args.project_root / DEPLOYMENT_MANIFEST_RELATIVE_PATH
    try:
        if args.command == "finalize":
            manifest = finalize_deployment_manifest(args.project_root, manifest_path)
            payload: Mapping[str, Any] = deployment_status(args.project_root)
            payload = {**payload, "deployment_manifest_sha256": manifest.deployment_manifest_sha256}
        else:
            payload = deployment_status(args.project_root)
    except (OSError, ValueError, DomainValidationError) as exc:
        print(json.dumps({"available": True, "verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(dict(payload), sort_keys=True))
    return 0 if payload.get("source_tree_verified") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEPLOYMENT_MANIFEST_RELATIVE_PATH",
    "DeploymentVerificationError",
    "PINNED_RPC_COMMIT",
    "REQUIREMENT_PATHS",
    "RSYNC_EXCLUDES",
    "build_deployment_manifest",
    "collect_source_files",
    "deployment_status",
    "finalize_deployment_manifest",
    "git_source_state",
    "is_deployment_source_path",
    "read_deployment_manifest",
    "verify_source_tree",
    "write_deployment_manifest",
]
