"""Bounded, dependency-free GGUF metadata inspection.

Only the metadata header is parsed; tensor bytes are never read. Canonical
hashes let the formal model lock distinguish files that share a filename and
weight checksum policy but embed different chat templates or tokenizers.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


GGUF_METADATA_CONTRACT = "gguf-metadata-v1"
_MAX_METADATA_ENTRIES = 1_000_000
_MAX_STRING_BYTES = 64 * 1024 * 1024
_MAX_ARRAY_ITEMS = 10_000_000


class GGUFMetadataError(ValueError):
    """The GGUF header or metadata contract is invalid."""


@dataclass(frozen=True)
class GGUFMetadataIdentity:
    architecture: str
    chat_template_hash: str
    tokenizer_metadata_hash: str
    chat_template_keys: tuple[str, ...]
    tokenizer_metadata_keys: tuple[str, ...]
    metadata_count: int
    metadata_contract: str = GGUF_METADATA_CONTRACT

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture": self.architecture,
            "chat_template_hash": self.chat_template_hash,
            "tokenizer_metadata_hash": self.tokenizer_metadata_hash,
            "chat_template_keys": list(self.chat_template_keys),
            "tokenizer_metadata_keys": list(self.tokenizer_metadata_keys),
            "metadata_count": self.metadata_count,
            "metadata_contract": self.metadata_contract,
            "metadata_inspected": True,
        }


def _read_exact(handle: BinaryIO, count: int) -> bytes:
    value = handle.read(count)
    if len(value) != count:
        raise GGUFMetadataError("truncated GGUF metadata")
    return value


def _unpack(handle: BinaryIO, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    return struct.unpack(fmt, _read_exact(handle, size))[0]


def _read_string(handle: BinaryIO) -> str:
    size = _unpack(handle, "<Q")
    if size > _MAX_STRING_BYTES:
        raise GGUFMetadataError("GGUF metadata string exceeds safety limit")
    try:
        return _read_exact(handle, size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GGUFMetadataError("GGUF metadata string is not UTF-8") from exc


def _read_value(handle: BinaryIO, value_type: int, *, in_array: bool = False) -> Any:
    scalar_formats = {
        0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
        6: "<f", 10: "<Q", 11: "<q", 12: "<d",
    }
    if value_type in scalar_formats:
        return _unpack(handle, scalar_formats[value_type])
    if value_type == 7:
        raw = _unpack(handle, "<B")
        if raw not in (0, 1):
            raise GGUFMetadataError("GGUF boolean metadata must be 0 or 1")
        return bool(raw)
    if value_type == 8:
        return _read_string(handle)
    if value_type == 9 and not in_array:
        item_type = _unpack(handle, "<I")
        count = _unpack(handle, "<Q")
        if count > _MAX_ARRAY_ITEMS:
            raise GGUFMetadataError("GGUF metadata array exceeds safety limit")
        if item_type == 9:
            raise GGUFMetadataError("nested GGUF metadata arrays are unsupported")
        return [_read_value(handle, item_type, in_array=True) for _ in range(count)]
    raise GGUFMetadataError(f"unsupported GGUF metadata value type: {value_type}")


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    candidate = Path(path)
    with candidate.open("rb") as handle:
        if _read_exact(handle, 4) != b"GGUF":
            raise GGUFMetadataError("file does not have GGUF magic")
        version = _unpack(handle, "<I")
        if version not in (2, 3):
            raise GGUFMetadataError(f"unsupported GGUF version: {version}")
        _unpack(handle, "<Q")  # tensor count; tensor data is never read
        metadata_count = _unpack(handle, "<Q")
        if metadata_count > _MAX_METADATA_ENTRIES:
            raise GGUFMetadataError("GGUF metadata entry count exceeds safety limit")
        metadata: dict[str, Any] = {}
        for _ in range(metadata_count):
            key = _read_string(handle)
            if not key or key in metadata:
                raise GGUFMetadataError("GGUF metadata keys must be non-empty and unique")
            value_type = _unpack(handle, "<I")
            metadata[key] = _read_value(handle, value_type)
    return metadata


def _canonical_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            encoded = "nan"
        elif math.isinf(value):
            encoded = "+inf" if value > 0 else "-inf"
        else:
            encoded = value.hex()
        return {"__gguf_float__": encoded}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise GGUFMetadataError(f"unsupported canonical GGUF value: {type(value).__name__}")


def canonical_metadata_sha256(values: dict[str, Any]) -> str:
    payload = {key: _canonical_value(values[key]) for key in sorted(values)}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_gguf_metadata(path: Path) -> GGUFMetadataIdentity:
    metadata = read_gguf_metadata(path)
    architecture = metadata.get("general.architecture")
    if not isinstance(architecture, str) or not architecture.strip():
        raise GGUFMetadataError("GGUF general.architecture is missing")
    chat = {
        key: value
        for key, value in metadata.items()
        if key == "tokenizer.chat_template" or key.startswith("tokenizer.chat_template.")
    }
    tokenizer = {
        key: value
        for key, value in metadata.items()
        if key.startswith("tokenizer.") and key not in chat
    }
    return GGUFMetadataIdentity(
        architecture=architecture.strip(),
        chat_template_hash=canonical_metadata_sha256(chat) if chat else "",
        tokenizer_metadata_hash=canonical_metadata_sha256(tokenizer) if tokenizer else "",
        chat_template_keys=tuple(sorted(chat)),
        tokenizer_metadata_keys=tuple(sorted(tokenizer)),
        metadata_count=len(metadata),
    )


__all__ = [
    "GGUF_METADATA_CONTRACT",
    "GGUFMetadataError",
    "GGUFMetadataIdentity",
    "canonical_metadata_sha256",
    "inspect_gguf_metadata",
    "read_gguf_metadata",
]
