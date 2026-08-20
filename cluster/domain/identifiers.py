"""Pure validation for identifiers shared across transport and storage layers."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .errors import DomainValidationError


NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


def validate_node_id(node_id: str) -> str:
    if not isinstance(node_id, str) or not NODE_ID_PATTERN.fullmatch(node_id):
        raise DomainValidationError(
            "node name must start with an alphanumeric character and contain only letters, digits, _ or -"
        )
    return node_id


def _validate_legacy_id(value: str, kind: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not value.replace("-", "").replace("_", "").isalnum()
    ):
        raise DomainValidationError(f"{kind} contains unsupported characters")
    return value


def validate_experiment_id(experiment_id: str) -> str:
    return _validate_legacy_id(experiment_id, "experiment_id")


def validate_suite_id(suite_id: str) -> str:
    return _validate_legacy_id(suite_id, "suite_id")


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id or not run_id.replace("_", "").isalnum():
        raise DomainValidationError("run_id contains unsupported characters")
    return run_id


def validate_model_id(model_id: str) -> str:
    """Validate and return a repository-relative GGUF model identifier."""
    canonical = str(PurePosixPath(model_id)) if isinstance(model_id, str) else ""
    if (
        not isinstance(model_id, str)
        or not model_id.endswith(".gguf")
        or model_id.startswith("/")
        or "\\" in model_id
        or any(ord(character) < 32 or ord(character) == 127 for character in model_id)
        or ".." in PurePosixPath(model_id).parts
        or canonical != model_id
    ):
        raise DomainValidationError("model_id must be a safe relative GGUF path")
    return model_id
