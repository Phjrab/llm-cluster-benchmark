"""Standalone llama.cpp inference backend used by Worker routes.

No FastAPI or Dashboard object is imported here.  The implementation preserves
the legacy model-loading retry schedule and chat-template fallback semantics.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence


DEFAULT_N_CTX = int(os.getenv("LLM_N_CTX", "1024"))
DEFAULT_N_GPU_LAYERS = int(os.getenv("LLM_N_GPU_LAYERS", "8"))
DEFAULT_N_THREADS = int(os.getenv("LLM_N_THREADS", str(min(6, os.cpu_count() or 1))))
DEFAULT_N_BATCH = int(os.getenv("LLM_N_BATCH", "256"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "256"))


class InferenceBackend(Protocol):
    """Minimal worker inference contract; routes never depend on a manager."""

    def list_models(self) -> List[Dict[str, object]]: ...

    def model_inventory(self) -> List[Dict[str, object]]: ...

    def verify_model(self, model_id: str, expected_sha256: Optional[str] = None) -> Dict[str, object]: ...

    def delete_model(self, model_id: str) -> Dict[str, object]: ...

    def install_model(self, model_id: str, source_url: str, expected_sha256: str, metadata: Optional[Dict[str, object]] = None) -> Dict[str, object]: ...

    def current_model_info(self) -> Dict[str, object]: ...

    def load_model(self, model_id: str, n_ctx: int, n_gpu_layers: int) -> Dict[str, object]: ...

    def unload_model(self) -> None: ...

    def stream_chat(
        self,
        *,
        message: str,
        history: Sequence[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        top_p: float,
        seed: Optional[int] = None,
    ) -> Iterable[str]: ...

    def tokenize(self, text: str) -> int: ...

    def set_seed(self, seed: int) -> None: ...

    def readiness(self) -> Dict[str, object]: ...


class LlamaCppInferenceBackend:
    """Repository-local port of the legacy ``web.app.ModelManager`` behavior."""

    def __init__(self, models_dir: Path, *, llama_factory: Any = None, torch_module: Any = None) -> None:
        self.models_dir = Path(models_dir)
        self.lock = threading.RLock()
        self._llama_factory = llama_factory
        self._torch = torch_module
        self._runtime_error: Optional[str] = None
        self.llm: Any = None
        self.loaded_model_path: Optional[Path] = None
        self.loaded_n_ctx: Optional[int] = None
        self.loaded_n_gpu_layers: Optional[int] = None
        self.loaded_n_batch: Optional[int] = None
        self.requested_n_ctx: Optional[int] = None
        self.requested_n_gpu_layers: Optional[int] = None
        self._model_hash_cache: Dict[Path, tuple[int, int, str]] = {}
        self._metadata_path = self.models_dir / ".cluster-model-metadata.json"

    def _read_model_metadata(self) -> Dict[str, Dict[str, object]]:
        try:
            raw = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {}
        records = raw.get("models") if isinstance(raw, dict) else None
        if not isinstance(records, dict):
            return {}
        return {str(key): dict(value) for key, value in records.items() if isinstance(key, str) and isinstance(value, dict)}

    def _write_model_metadata(self, records: Dict[str, Dict[str, object]]) -> None:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "models": records}, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._metadata_path)
        os.chmod(self._metadata_path, 0o600)

    @staticmethod
    def _safe_install_metadata(metadata: Optional[Dict[str, object]]) -> Dict[str, object]:
        source = metadata if isinstance(metadata, dict) else {}
        allowed = {"source_revision", "architecture", "chat_template_hash", "license_accepted", "source_repo", "provenance_status"}
        cleaned: Dict[str, object] = {}
        for key in allowed:
            value = source.get(key)
            if key == "license_accepted":
                if value is True:
                    cleaned[key] = True
            elif isinstance(value, str) and value.strip() and len(value) <= 256:
                cleaned[key] = value.strip()
        return cleaned

    def _factory(self) -> Any:
        if self._llama_factory is not None:
            return self._llama_factory
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            self._runtime_error = "llama-cpp-python is not installed on this Worker"
            raise RuntimeError(self._runtime_error) from exc
        self._llama_factory = Llama
        return Llama

    def _torch_module(self) -> Any:
        if self._torch is not None:
            return self._torch
        try:
            import torch
        except ImportError:
            torch = None
        self._torch = torch
        return torch

    def readiness(self) -> Dict[str, object]:
        try:
            self._factory()
        except RuntimeError:
            return {"ready": False, "error": self._runtime_error or "llama runtime unavailable"}
        return {"ready": True, "error": None}

    def list_models(self) -> List[Dict[str, object]]:
        if not self.models_dir.exists():
            return []
        models: List[Dict[str, object]] = []
        metadata_by_id = self._read_model_metadata()
        for path in sorted(self.models_dir.rglob("*.gguf")):
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(self.models_dir.resolve())
            except (OSError, ValueError):
                continue
            try:
                stat = resolved.stat()
                size_bytes = stat.st_size
                size_mb = round(size_bytes / (1024 * 1024), 2)
            except OSError:
                continue
            metadata = metadata_by_id.get(relative.as_posix(), {})
            models.append(
                {
                    "id": relative.as_posix(),
                    "name": resolved.name,
                    "filename": resolved.name,
                    "path": str(resolved),
                    "size_bytes": size_bytes,
                    "size_mb": size_mb,
                    "quantization": self._quantization_from_filename(resolved.name),
                    "is_loaded": self.loaded_model_path is not None and resolved == self.loaded_model_path,
                    "source_revision": metadata.get("source_revision", ""),
                    "architecture": metadata.get("architecture", ""),
                    "chat_template_hash": metadata.get("chat_template_hash", ""),
                    "license_accepted": metadata.get("license_accepted") is True,
                    "metadata_inspected": bool(metadata.get("architecture")),
                }
            )
        return models

    @staticmethod
    def _quantization_from_filename(filename: str) -> Optional[str]:
        match = re.search(r"(?:^|[-_.])(Q\d(?:_[A-Z0-9]+)*)(?:[-_.]|$)", filename.upper())
        return match.group(1) if match else None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _cached_sha256(self, path: Path) -> str:
        stat = path.stat()
        cached = self._model_hash_cache.get(path)
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            return cached[2]
        digest = self._sha256_file(path)
        self._model_hash_cache[path] = (stat.st_size, stat.st_mtime_ns, digest)
        return digest

    def model_inventory(self) -> List[Dict[str, object]]:
        """Return complete per-worker model metadata without hashing on health checks."""
        with self.lock:
            entries: List[Dict[str, object]] = []
            for model in self.list_models():
                model_id = str(model["id"])
                path = self._resolve_model_path(model_id)
                digest = self._cached_sha256(path)
                entries.append({**model, "sha256": digest, "checksum_valid": True})
            return entries

    def verify_model(self, model_id: str, expected_sha256: Optional[str] = None) -> Dict[str, object]:
        with self.lock:
            path = self._resolve_model_path(model_id)
            digest = self._cached_sha256(path)
            expected = expected_sha256.strip().lower() if expected_sha256 else ""
            if expected and (not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected):
                raise ValueError(f"Model checksum mismatch: {model_id}")
            metadata = self._read_model_metadata().get(model_id, {})
            return {
                "id": model_id,
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "quantization": self._quantization_from_filename(path.name),
                "checksum_valid": True,
                "source_revision": metadata.get("source_revision", ""),
                "architecture": metadata.get("architecture", ""),
                "chat_template_hash": metadata.get("chat_template_hash", ""),
                "license_accepted": metadata.get("license_accepted") is True,
                "metadata_inspected": bool(metadata.get("architecture")),
            }

    def delete_model(self, model_id: str) -> Dict[str, object]:
        with self.lock:
            path = self._resolve_model_path(model_id)
            if self.loaded_model_path is not None and path == self.loaded_model_path:
                raise RuntimeError("Unload the selected model before deleting it")
            size_bytes = path.stat().st_size
            path.unlink()
            self._model_hash_cache.pop(path, None)
            metadata = self._read_model_metadata()
            if metadata.pop(model_id, None) is not None:
                self._write_model_metadata(metadata)
            return {"id": model_id, "filename": path.name, "size_bytes": size_bytes, "deleted": True}

    def install_model(self, model_id: str, source_url: str, expected_sha256: str, metadata: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """Download directly to this Worker and atomically verify before READY."""
        from cluster.domain.experiment import validate_model_id

        validate_model_id(model_id)
        expected = expected_sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("A 64-character expected_sha256 is required for direct model installation")
        install_metadata = self._safe_install_metadata(metadata)
        parsed = urllib.parse.urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Model source_url must be a credential-free http(s) URL")
        with self.lock:
            target = self.models_dir / model_id
            resolved_root = self.models_dir.resolve()
            try:
                target.resolve().relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError("Model path is outside models directory") from exc
            if target.suffix.lower() != ".gguf":
                raise ValueError("Selected file is not a .gguf model")
            if self.loaded_model_path is not None and target.resolve() == self.loaded_model_path:
                raise RuntimeError("Unload the selected model before replacing it")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and self._cached_sha256(target) == expected:
                if install_metadata:
                    records = self._read_model_metadata(); records[model_id] = install_metadata; self._write_model_metadata(records)
                return {**self.verify_model(model_id, expected), "downloaded_bytes": 0, "already_present": True}
            temporary = target.with_name(target.name + ".part")
            temporary.unlink(missing_ok=True)
            downloaded = 0
            digest = hashlib.sha256()
            try:
                request = urllib.request.Request(source_url, headers={"User-Agent": "llm-cluster-worker/1"})
                with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if digest.hexdigest() != expected:
                    raise ValueError(f"Model checksum mismatch: {model_id}")
                os.replace(temporary, target)
                self._model_hash_cache.pop(target, None)
                if install_metadata:
                    records = self._read_model_metadata(); records[model_id] = install_metadata; self._write_model_metadata(records)
                return {**self.verify_model(model_id, expected), "downloaded_bytes": downloaded, "already_present": False}
            except Exception:
                temporary.unlink(missing_ok=True)
                raise

    def _resolve_model_path(self, model_id: str) -> Path:
        candidate = (self.models_dir / model_id).resolve()
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Model file not found: {model_id}")
        if candidate.suffix.lower() != ".gguf":
            raise ValueError("Selected file is not a .gguf model")
        try:
            candidate.relative_to(self.models_dir.resolve())
        except ValueError as exc:
            raise ValueError("Model path is outside models directory") from exc
        return candidate

    def _release_gpu_caches(self) -> None:
        gc.collect()
        torch = self._torch_module()
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

    def _unload_locked(self) -> None:
        if self.llm is not None:
            previous = self.llm
            self.llm = None
            del previous
        self.loaded_model_path = None
        self.loaded_n_ctx = None
        self.loaded_n_gpu_layers = None
        self.loaded_n_batch = None
        self.requested_n_ctx = None
        self.requested_n_gpu_layers = None
        self._release_gpu_caches()

    def unload_model(self) -> None:
        with self.lock:
            self._unload_locked()

    # Legacy method names remain as a compatibility adapter for web.app users.
    unload = unload_model

    @staticmethod
    def _gpu_layer_schedule(requested_layers: int) -> List[int]:
        schedule = [max(0, requested_layers)]
        while schedule[-1] > 0:
            candidate = max(0, schedule[-1] - 4)
            if candidate not in schedule:
                schedule.append(candidate)
        return schedule

    @staticmethod
    def _n_ctx_schedule(requested_ctx: int) -> List[int]:
        schedule: List[int] = []
        for value in [requested_ctx, 768, 512, 384, 256, 192, 128]:
            if 128 <= value <= requested_ctx and value not in schedule:
                schedule.append(value)
        return schedule or [max(128, requested_ctx)]

    @staticmethod
    def _n_batch_schedule(n_ctx_value: int) -> List[int]:
        schedule: List[int] = []
        for value in [max(32, min(DEFAULT_N_BATCH, n_ctx_value)), 128, 96, 64, 48, 32]:
            if 32 <= value <= n_ctx_value and value not in schedule:
                schedule.append(value)
        return schedule or [max(32, min(64, n_ctx_value))]

    def load_model(self, model_id: str, n_ctx: int, n_gpu_layers: int) -> Dict[str, object]:
        with self.lock:
            model_path = self._resolve_model_path(model_id)
            if (
                self.llm is not None
                and self.loaded_model_path == model_path
                and self.requested_n_ctx == n_ctx
                and self.requested_n_gpu_layers == n_gpu_layers
            ):
                return self.current_model_info()
            self._unload_locked()
            factory = self._factory()
            last_error: Optional[Exception] = None
            selected: Optional[tuple[int, int, int]] = None
            for candidate_n_ctx in self._n_ctx_schedule(n_ctx):
                for candidate_layers in self._gpu_layer_schedule(n_gpu_layers):
                    for candidate_n_batch in self._n_batch_schedule(candidate_n_ctx):
                        try:
                            self.llm = factory(
                                model_path=str(model_path),
                                n_ctx=candidate_n_ctx,
                                n_gpu_layers=candidate_layers,
                                n_batch=candidate_n_batch,
                                n_threads=DEFAULT_N_THREADS,
                                verbose=False,
                            )
                            selected = (candidate_n_ctx, candidate_layers, candidate_n_batch)
                            break
                        except Exception as exc:
                            last_error = exc
                            self._unload_locked()
                    if selected is not None:
                        break
                if selected is not None:
                    break
            if self.llm is None or selected is None:
                raise ValueError(f"Failed to load model after retries: {last_error}")
            self.loaded_model_path = model_path
            self.loaded_n_ctx, self.loaded_n_gpu_layers, self.loaded_n_batch = selected
            self.requested_n_ctx = n_ctx
            self.requested_n_gpu_layers = n_gpu_layers
            info = self.current_model_info()
            info.update(
                {
                    "requested_n_gpu_layers": n_gpu_layers,
                    "auto_adjusted_n_gpu_layers": self.loaded_n_gpu_layers != n_gpu_layers,
                    "requested_n_ctx": n_ctx,
                    "auto_adjusted_n_ctx": self.loaded_n_ctx != n_ctx,
                    "n_batch": self.loaded_n_batch,
                }
            )
            return info

    load = load_model

    def current_model_info(self) -> Dict[str, object]:
        if self.loaded_model_path is None:
            return {
                "loaded": False,
                "model_id": None,
                "model_path": None,
                "n_ctx": None,
                "n_gpu_layers": None,
                "n_batch": None,
            }
        return {
            "loaded": True,
            "model_id": self.loaded_model_path.relative_to(self.models_dir.resolve()).as_posix(),
            "model_path": str(self.loaded_model_path),
            "n_ctx": self.loaded_n_ctx,
            "n_gpu_layers": self.loaded_n_gpu_layers,
            "n_batch": self.loaded_n_batch,
            "requested_n_ctx": self.requested_n_ctx,
            "requested_n_gpu_layers": self.requested_n_gpu_layers,
        }

    def set_seed(self, seed: int) -> None:
        if self.llm is None:
            raise RuntimeError("No model loaded. Select a model first.")
        setter = getattr(self.llm, "set_seed", None)
        if callable(setter):
            setter(seed)

    def tokenize(self, text: str) -> int:
        if not text or self.llm is None:
            return 0
        tokenizer = getattr(self.llm, "tokenize", None)
        if not callable(tokenizer):
            return 0
        try:
            return len(tokenizer(text.encode("utf-8"), add_bos=False))
        except Exception:
            return 0

    @staticmethod
    def _sanitize_history(history: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
        cleaned: List[Dict[str, str]] = []
        for item in history:
            role = item.get("role", "")
            content = item.get("content", "")
            if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
                cleaned.append({"role": role, "content": content.strip()})
        return cleaned

    @staticmethod
    def _fallback_prompt(messages: Sequence[Dict[str, str]]) -> str:
        return "\n".join([*(f"{item['role'].upper()}: {item['content']}" for item in messages), "ASSISTANT:"])

    @staticmethod
    def _extract_token(chunk: Dict[str, object]) -> str:
        choices = chunk.get("choices", [{}])
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ""
        choice = choices[0]
        delta = choice.get("delta")
        if isinstance(delta, dict):
            token = delta.get("content") or delta.get("text") or ""
        else:
            token = choice.get("text", "")
        return token if isinstance(token, str) else ""

    def stream_chat(
        self,
        *,
        message: str,
        history: Sequence[Dict[str, str]],
        max_tokens: int,
        temperature: float,
        top_p: float,
        seed: Optional[int] = None,
    ) -> Iterable[str]:
        with self.lock:
            if self.llm is None:
                raise RuntimeError("No model loaded. Select a model first.")
            if seed is not None:
                self.set_seed(seed)
            messages = self._sanitize_history(history)
            messages.append({"role": "user", "content": message.strip()})
            try:
                stream = self.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=True,
                )
                for chunk in stream:
                    token = self._extract_token(chunk)
                    if token:
                        yield token
                return
            except Exception:
                pass
            for chunk in self.llm.create_completion(
                prompt=self._fallback_prompt(messages),
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=True,
            ):
                token = self._extract_token(chunk)
                if token:
                    yield token


class LegacyWebInferenceBackend(LlamaCppInferenceBackend):
    """Compatibility name for the former ``web.app.ModelManager`` consumer."""


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_N_BATCH",
    "DEFAULT_N_CTX",
    "DEFAULT_N_GPU_LAYERS",
    "DEFAULT_N_THREADS",
    "InferenceBackend",
    "LegacyWebInferenceBackend",
    "LlamaCppInferenceBackend",
]
