"""Pure Worker deployment identity types.

 The manifest proves the source tree deployed to a Worker without copying Git
metadata.  The source-tree fingerprint is stable across repeated deployments,
while the manifest self-hash covers the deployment timestamp as well as every
identity field.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .errors import DomainValidationError


DEPLOYMENT_SCHEMA_VERSION = 1
UNVERIFIED_RUNTIME = "unverified"
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").lower()
    if not _HEX_64.fullmatch(normalized):
        raise DomainValidationError(f"{label} must be a lowercase SHA-256")
    return normalized


def _validate_relative_path(value: Any, label: str) -> str:
    raw = str(value or "")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or raw != path.as_posix()
        or raw.startswith("./")
        or ".." in path.parts
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise DomainValidationError(f"{label} must be a canonical relative POSIX path")
    return raw


@dataclass(frozen=True, order=True)
class DeploymentSourceFile:
    path: str
    size_bytes: int
    sha256: str
    executable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _validate_relative_path(self.path, "source file path"))
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise DomainValidationError("source file size_bytes must be a non-negative integer")
        object.__setattr__(self, "sha256", _require_sha256(self.sha256, "source file sha256"))
        if not isinstance(self.executable, bool):
            raise DomainValidationError("source file executable must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "executable": self.executable,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DeploymentSourceFile":
        return cls(
            path=raw.get("path", ""),
            size_bytes=raw.get("size_bytes", -1),
            sha256=raw.get("sha256", ""),
            executable=raw.get("executable", False),
        )


def canonical_source_tree_sha256(files: Sequence[DeploymentSourceFile]) -> str:
    payload = [item.to_dict() for item in sorted(files, key=lambda item: item.path)]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DeploymentManifest:
    source_commit: str
    source_tree_sha256: str
    deployment_manifest_sha256: str
    requirements_sha256: tuple[tuple[str, str], ...]
    runtime_fingerprint: str
    llama_cpp_python_version: str
    rpc_commit: str
    deployed_at: str
    working_tree_clean: bool
    source_tree_verified: bool
    source_files: tuple[DeploymentSourceFile, ...]
    schema_version: int = DEPLOYMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEPLOYMENT_SCHEMA_VERSION:
            raise DomainValidationError("deployment manifest schema_version must be 1")
        if not _HEX_40.fullmatch(str(self.source_commit)):
            raise DomainValidationError("source_commit must be an exact lowercase Git commit")
        object.__setattr__(
            self,
            "source_tree_sha256",
            _require_sha256(self.source_tree_sha256, "source_tree_sha256"),
        )
        if self.deployment_manifest_sha256:
            object.__setattr__(
                self,
                "deployment_manifest_sha256",
                _require_sha256(
                    self.deployment_manifest_sha256,
                    "deployment_manifest_sha256",
                ),
            )
        if not _HEX_40.fullmatch(str(self.rpc_commit)):
            raise DomainValidationError("rpc_commit must be an exact lowercase Git commit")
        if not isinstance(self.working_tree_clean, bool) or not isinstance(self.source_tree_verified, bool):
            raise DomainValidationError("deployment verification flags must be booleans")
        if not isinstance(self.deployed_at, str) or not self.deployed_at.strip():
            raise DomainValidationError("deployed_at must be a non-empty timestamp")
        if not isinstance(self.runtime_fingerprint, str) or not self.runtime_fingerprint:
            raise DomainValidationError("runtime_fingerprint must be explicit")
        if not isinstance(self.llama_cpp_python_version, str) or not self.llama_cpp_python_version:
            raise DomainValidationError("llama_cpp_python_version must be explicit")
        files = tuple(sorted(self.source_files, key=lambda item: item.path))
        if not files or len({item.path for item in files}) != len(files):
            raise DomainValidationError("deployment source_files must be non-empty and unique")
        if canonical_source_tree_sha256(files) != self.source_tree_sha256:
            raise DomainValidationError("source_tree_sha256 does not match source_files")
        object.__setattr__(self, "source_files", files)
        requirements = tuple(sorted(self.requirements_sha256))
        if len({path for path, _ in requirements}) != len(requirements):
            raise DomainValidationError("requirements_sha256 paths must be unique")
        for path, digest in requirements:
            _validate_relative_path(path, "requirements path")
            _require_sha256(digest, f"requirements_sha256[{path}]")
        object.__setattr__(self, "requirements_sha256", requirements)

    def identity_payload(self) -> dict[str, Any]:
        """Return stable identity fields, excluding timestamp and self-hash."""
        return {
            "schema_version": self.schema_version,
            "source_commit": self.source_commit,
            "source_tree_sha256": self.source_tree_sha256,
            "requirements_sha256": dict(self.requirements_sha256),
            "runtime_fingerprint": self.runtime_fingerprint,
            "llama_cpp_python_version": self.llama_cpp_python_version,
            "rpc_commit": self.rpc_commit,
            "working_tree_clean": self.working_tree_clean,
            "source_tree_verified": self.source_tree_verified,
            "source_files": [item.to_dict() for item in self.source_files],
        }

    def expected_manifest_sha256(self) -> str:
        payload = {**self.identity_payload(), "deployed_at": self.deployed_at}
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def integrity_valid(self) -> bool:
        return self.deployment_manifest_sha256 == self.expected_manifest_sha256()

    def sealed(self) -> "DeploymentManifest":
        return replace(self, deployment_manifest_sha256=self.expected_manifest_sha256())

    def with_runtime(
        self,
        *,
        runtime_fingerprint: str,
        llama_cpp_python_version: str,
        source_tree_verified: bool,
    ) -> "DeploymentManifest":
        return replace(
            self,
            runtime_fingerprint=runtime_fingerprint,
            llama_cpp_python_version=llama_cpp_python_version,
            source_tree_verified=source_tree_verified,
            deployment_manifest_sha256="",
        ).sealed()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "deployment_manifest_sha256": self.deployment_manifest_sha256,
            "deployed_at": self.deployed_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, verify_integrity: bool = True) -> "DeploymentManifest":
        requirements = raw.get("requirements_sha256")
        if not isinstance(requirements, Mapping):
            raise DomainValidationError("requirements_sha256 must be an object")
        files = raw.get("source_files")
        if not isinstance(files, list):
            raise DomainValidationError("source_files must be a list")
        manifest = cls(
            schema_version=raw.get("schema_version", 0),
            source_commit=str(raw.get("source_commit", "")),
            source_tree_sha256=str(raw.get("source_tree_sha256", "")),
            deployment_manifest_sha256=str(raw.get("deployment_manifest_sha256", "")),
            requirements_sha256=tuple((str(path), str(digest)) for path, digest in requirements.items()),
            runtime_fingerprint=str(raw.get("runtime_fingerprint", "")),
            llama_cpp_python_version=str(raw.get("llama_cpp_python_version", "")),
            rpc_commit=str(raw.get("rpc_commit", "")),
            deployed_at=str(raw.get("deployed_at", "")),
            working_tree_clean=raw.get("working_tree_clean"),
            source_tree_verified=raw.get("source_tree_verified"),
            source_files=tuple(DeploymentSourceFile.from_dict(item) for item in files if isinstance(item, Mapping)),
        )
        if verify_integrity and not manifest.integrity_valid:
            raise DomainValidationError("deployment_manifest_sha256 does not match manifest content")
        return manifest


__all__ = [
    "DEPLOYMENT_SCHEMA_VERSION",
    "UNVERIFIED_RUNTIME",
    "DeploymentManifest",
    "DeploymentSourceFile",
    "canonical_source_tree_sha256",
]
