"""Roadmap Phase 02 GGUF metadata identity tests."""

from __future__ import annotations

import hashlib
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.infrastructure.gguf import (
    GGUF_METADATA_CONTRACT,
    GGUFMetadataError,
    canonical_metadata_sha256,
    inspect_gguf_metadata,
)
from cluster.worker.inference import LlamaCppInferenceBackend


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _value(value: object) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return 7, struct.pack("<B", int(value))
    if isinstance(value, str):
        return 8, _string(value)
    if isinstance(value, int):
        return 5, struct.pack("<i", value)
    if isinstance(value, float):
        return 6, struct.pack("<f", value)
    if isinstance(value, list):
        if not value:
            return 9, struct.pack("<IQ", 8, 0)
        item_type, _ = _value(value[0])
        payload = b"".join(_value(item)[1] for item in value)
        return 9, struct.pack("<IQ", item_type, len(value)) + payload
    raise TypeError(value)


def write_gguf(path: Path, metadata: dict[str, object]) -> None:
    payload = [b"GGUF", struct.pack("<IQQ", 3, 0, len(metadata))]
    for key, value in metadata.items():
        value_type, encoded = _value(value)
        payload.extend((_string(key), struct.pack("<I", value_type), encoded))
    path.write_bytes(b"".join(payload))


class GGUFIdentityTests(unittest.TestCase):
    def test_actual_header_produces_separate_chat_and_tokenizer_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.gguf"
            write_gguf(path, {
                "general.architecture": "qwen2",
                "tokenizer.chat_template": "{{ messages }}",
                "tokenizer.ggml.model": "gpt2",
                "tokenizer.ggml.tokens": ["a", "b"],
                "tokenizer.ggml.scores": [0.0, float("-inf")],
                "general.name": "ignored by tokenizer identity",
            })
            identity = inspect_gguf_metadata(path)

        self.assertEqual(identity.architecture, "qwen2")
        self.assertEqual(identity.metadata_contract, GGUF_METADATA_CONTRACT)
        self.assertEqual(identity.chat_template_keys, ("tokenizer.chat_template",))
        self.assertEqual(identity.tokenizer_metadata_keys, (
            "tokenizer.ggml.model", "tokenizer.ggml.scores", "tokenizer.ggml.tokens",
        ))
        self.assertRegex(identity.chat_template_hash, r"^[0-9a-f]{64}$")
        self.assertRegex(identity.tokenizer_metadata_hash, r"^[0-9a-f]{64}$")

    def test_worker_verify_persists_only_file_inspected_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "official/model-Q4_K_M.gguf"
            path.parent.mkdir(parents=True)
            write_gguf(path, {
                "general.architecture": "qwen2",
                "tokenizer.chat_template": "{{ messages }}",
                "tokenizer.ggml.model": "gpt2",
            })
            backend = LlamaCppInferenceBackend(root)
            record = backend.verify_model("official/model-Q4_K_M.gguf")
            stored = (root / ".cluster-model-metadata.json").read_text(encoding="utf-8")

        self.assertTrue(record["metadata_inspected"])
        self.assertEqual(record["architecture"], "qwen2")
        self.assertEqual(record["metadata_contract"], GGUF_METADATA_CONTRACT)
        self.assertIn(record["chat_template_hash"], stored)
        self.assertIn(record["tokenizer_metadata_hash"], stored)

    def test_worker_install_rejects_claimed_metadata_before_promoting_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gguf"
            write_gguf(source, {
                "general.architecture": "qwen2",
                "tokenizer.chat_template": "{{ messages }}",
                "tokenizer.ggml.model": "gpt2",
            })
            content = source.read_bytes()

            class Response:
                def __init__(self) -> None:
                    self.offset = 0

                def __enter__(self) -> "Response":
                    return self

                def __exit__(self, *_: object) -> None:
                    return None

                def read(self, count: int) -> bytes:
                    chunk = content[self.offset:self.offset + count]
                    self.offset += len(chunk)
                    return chunk

            models = root / "models"
            backend = LlamaCppInferenceBackend(models)
            digest = hashlib.sha256(content).hexdigest()
            with mock.patch("urllib.request.urlopen", return_value=Response()):
                with self.assertRaisesRegex(ValueError, "architecture mismatch"):
                    backend.install_model(
                        "official/model-Q4_K_M.gguf",
                        "https://models.example/model.gguf",
                        digest,
                        {"architecture": "granite", "metadata_contract": GGUF_METADATA_CONTRACT},
                    )
            self.assertFalse((models / "official/model-Q4_K_M.gguf").exists())
            self.assertFalse((models / "official/model-Q4_K_M.gguf.part").exists())

            with mock.patch("urllib.request.urlopen", return_value=Response()):
                installed = backend.install_model(
                    "official/model-Q4_K_M.gguf",
                    "https://models.example/model.gguf",
                    digest,
                    {
                        "architecture": "qwen2",
                        "metadata_contract": GGUF_METADATA_CONTRACT,
                        "source_revision": "a" * 40,
                        "source_repo": "Qwen/example",
                        "provenance_status": "official",
                        "license_accepted": True,
                    },
                )
            self.assertFalse(installed["already_present"])
            self.assertEqual(installed["source_revision"], "a" * 40)
            self.assertTrue(installed["metadata_inspected"])

    def test_canonical_hash_is_key_order_stable_and_sensitive_to_values(self) -> None:
        first = canonical_metadata_sha256({"b": [1, 2], "a": -0.0})
        second = canonical_metadata_sha256({"a": -0.0, "b": [1, 2]})
        changed = canonical_metadata_sha256({"a": 0.0, "b": [1, 2]})
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertNotEqual(first, hashlib.sha256(b"untyped-json").hexdigest())

    def test_truncated_invalid_or_unsafe_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "invalid.gguf"
            invalid.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1))
            with self.assertRaisesRegex(GGUFMetadataError, "truncated"):
                inspect_gguf_metadata(invalid)
            wrong = root / "wrong.gguf"
            wrong.write_bytes(b"nope")
            with self.assertRaisesRegex(GGUFMetadataError, "magic"):
                inspect_gguf_metadata(wrong)

    def test_missing_architecture_fails_and_missing_template_stays_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.gguf"
            write_gguf(missing, {"tokenizer.ggml.model": "gpt2"})
            with self.assertRaisesRegex(GGUFMetadataError, "architecture"):
                inspect_gguf_metadata(missing)
            no_template = root / "no-template.gguf"
            write_gguf(no_template, {
                "general.architecture": "granite",
                "tokenizer.ggml.model": "gpt2",
            })
            identity = inspect_gguf_metadata(no_template)
        self.assertEqual(identity.chat_template_hash, "")
        self.assertRegex(identity.tokenizer_metadata_hash, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
