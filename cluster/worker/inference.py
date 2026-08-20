"""Standalone llama.cpp inference backend used by Worker routes.

No FastAPI or Dashboard object is imported here.  The implementation preserves
the legacy model-loading retry schedule and chat-template fallback semantics.
"""

from __future__ import annotations

import gc
import os
import threading
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
        for path in sorted(self.models_dir.rglob("*.gguf")):
            try:
                resolved = path.resolve()
                relative = resolved.relative_to(self.models_dir.resolve())
            except (OSError, ValueError):
                continue
            try:
                size_mb = round(resolved.stat().st_size / (1024 * 1024), 2)
            except OSError:
                continue
            models.append(
                {
                    "id": relative.as_posix(),
                    "name": resolved.name,
                    "path": str(resolved),
                    "size_mb": size_mb,
                    "is_loaded": self.loaded_model_path is not None and resolved == self.loaded_model_path,
                }
            )
        return models

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
