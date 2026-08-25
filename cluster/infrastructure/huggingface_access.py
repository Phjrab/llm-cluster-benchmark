"""Controller-only Hugging Face authentication and verified cache downloads."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from cluster.domain.experiment import validate_model_id


_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class HuggingFaceAccessError(RuntimeError):
    pass


def _hub_api() -> tuple[Any, Any, Any]:
    try:
        from huggingface_hub import get_token, hf_hub_download, whoami
    except ImportError as exc:
        raise HuggingFaceAccessError(
            "huggingface-hub is not installed; rerun Controller setup"
        ) from exc
    return get_token, hf_hub_download, whoami


def huggingface_access_status(*, verify: bool = False) -> Dict[str, Any]:
    try:
        get_token, _download, whoami = _hub_api()
    except HuggingFaceAccessError:
        return {"installed": False, "configured": False, "verified": False, "account": ""}
    token = get_token()
    if not token:
        return {"installed": True, "configured": False, "verified": False, "account": ""}
    result: Dict[str, Any] = {
        "installed": True,
        "configured": True,
        "verified": False,
        "account": "",
    }
    if not verify:
        return result
    try:
        profile = whoami(token=token)
    except Exception as exc:
        raise HuggingFaceAccessError(
            "Hugging Face account verification failed; run `hf auth login` again"
        ) from exc
    result["verified"] = True
    if isinstance(profile, dict):
        result["account"] = str(profile.get("name") or profile.get("fullname") or "")[:120]
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified_model_to_controller_cache(
    *,
    project_root: Path,
    runtime_dir: Path,
    model_id: str,
    repo_id: str,
    revision: str,
    filename: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> Dict[str, Any]:
    """Download one exact artifact with the Controller's existing HF login."""
    validate_model_id(model_id)
    if not _REPO_RE.fullmatch(repo_id):
        raise HuggingFaceAccessError("Invalid Hugging Face repository identity")
    if not _COMMIT_RE.fullmatch(revision):
        raise HuggingFaceAccessError("Hugging Face revision must be an exact commit")
    if Path(filename).name != filename or not filename.lower().endswith(".gguf"):
        raise HuggingFaceAccessError("Hugging Face artifact must be one safe GGUF basename")
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise HuggingFaceAccessError("Expected model SHA-256 is invalid")
    if not isinstance(expected_size_bytes, int) or isinstance(expected_size_bytes, bool) or expected_size_bytes <= 0:
        raise HuggingFaceAccessError("Expected model size is invalid")

    models_root = (Path(project_root) / "models").resolve()
    target = (models_root / model_id).resolve()
    try:
        target.relative_to(models_root)
    except ValueError as exc:
        raise HuggingFaceAccessError("Unsafe Controller model cache target") from exc

    if target.is_file() and target.stat().st_size == expected_size_bytes:
        if _sha256_file(target) == expected_sha256:
            return {"path": str(target), "size_bytes": expected_size_bytes, "sha256": expected_sha256, "already_present": True}

    status = huggingface_access_status(verify=True)
    if not status["verified"]:
        raise HuggingFaceAccessError("Hugging Face account is not authenticated")
    get_token, hf_hub_download, _whoami = _hub_api()
    token = get_token()
    runtime_root = Path(runtime_dir)
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_root.chmod(0o700)
    cache_dir = runtime_root / "huggingface-cache"
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_dir.chmod(0o700)
    try:
        downloaded = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                token=token,
                cache_dir=cache_dir,
            )
        )
    except Exception as exc:
        raise HuggingFaceAccessError(
            "Hugging Face download failed; confirm repository access and accepted terms"
        ) from exc
    if not downloaded.is_file() or downloaded.stat().st_size != expected_size_bytes:
        raise HuggingFaceAccessError("Downloaded model size does not match the catalog lock")
    if _sha256_file(downloaded) != expected_sha256:
        raise HuggingFaceAccessError("Downloaded model SHA-256 does not match the catalog lock")

    models_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    models_root.chmod(0o700)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    temporary = target.with_name(f".{target.name}.part-{uuid.uuid4().hex}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with downloaded.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"path": str(target), "size_bytes": expected_size_bytes, "sha256": expected_sha256, "already_present": False}


__all__ = [
    "HuggingFaceAccessError",
    "download_verified_model_to_controller_cache",
    "huggingface_access_status",
]
