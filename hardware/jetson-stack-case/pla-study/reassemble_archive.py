#!/usr/bin/env python3
"""Verify and join raw byte-split ZIP parts without replacing a different file.

Uses only Python's standard library. The parts are not individual ZIP archives.
By default, reads ARCHIVE_RECONSTRUCTION.json beside this script and creates the
original archive in the same directory. Compatible with Linux, macOS and Windows.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile


CHUNK_BYTES = 1024 * 1024


def safe_name(value):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or ":" in value
    ):
        raise ValueError("Archive and part names must be simple filenames")
    return value


def identity(record):
    size = record["bytes"]
    digest = record["sha256"]
    if type(size) is not int or size < 0:
        raise ValueError("Invalid expected byte count")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("Invalid expected SHA-256")
    return size, digest


def verify_archive(path, expected_size, expected_sha256):
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            count += len(block)
            digest.update(block)
    if count != expected_size or digest.hexdigest() != expected_sha256:
        raise ValueError("Archive size or SHA-256 mismatch: {}".format(path))
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError("ZIP CRC failed for entry: {}".format(bad))


def run(manifest_path, output_override=None):
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported reconstruction manifest format")
    archive = manifest["original_archive"]
    archive_name = safe_name(archive["name"])
    expected_size, expected_sha256 = identity(archive)
    parts = manifest["parts"]
    if not parts or len(parts) != manifest["part_count"]:
        raise ValueError("Part count mismatch")
    names = [safe_name(part["name"]) for part in parts]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate part names")
    part_ids = [identity(part) for part in parts]
    if sum(size for size, _ in part_ids) != expected_size:
        raise ValueError("Part sizes do not sum to original archive size")
    limit = manifest["maximum_part_bytes"]
    if type(limit) is not int or limit <= 0 or any(size > limit for size, _ in part_ids):
        raise ValueError("Invalid part size limit")

    output = Path(output_override).absolute() if output_override else manifest_path.parent / archive_name
    if not output.parent.is_dir():
        raise ValueError("Output directory does not exist: {}".format(output.parent))
    # Never use an archive part as the output, even with --output.
    if output.resolve() in [(manifest_path.parent / name).resolve() for name in names]:
        raise ValueError("Output path cannot be an input part")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".reassemble-", suffix=".tmp", dir=output.parent, delete=False
        ) as target:
            temp_path = Path(target.name)
            whole_digest = hashlib.sha256()
            whole_count = 0
            for name, (part_size, part_sha256) in zip(names, part_ids):
                source_path = manifest_path.parent / name
                digest = hashlib.sha256()
                count = 0
                with source_path.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                        target.write(block)
                        digest.update(block)
                        whole_digest.update(block)
                        count += len(block)
                        whole_count += len(block)
                if count != part_size or digest.hexdigest() != part_sha256:
                    raise ValueError("Part size or SHA-256 mismatch: {}".format(name))
                print("Verified {} ({} bytes)".format(name, count))
            target.flush()
            os.fsync(target.fileno())
        if whole_count != expected_size or whole_digest.hexdigest() != expected_sha256:
            raise ValueError("Combined archive size or SHA-256 mismatch")
        verify_archive(temp_path, expected_size, expected_sha256)

        if output.exists() or output.is_symlink():
            try:
                verify_archive(output, expected_size, expected_sha256)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                raise FileExistsError(
                    "Refusing to overwrite existing different output: {}".format(output)
                ) from error
            print("Existing output already matches; left unchanged: {}".format(output))
        else:
            try:
                # Hard linking the verified temporary file publishes atomically
                # and never replaces an existing path, including a racing writer.
                os.link(temp_path, output)
            except FileExistsError:
                raise FileExistsError("Output appeared during reconstruction; left unchanged: {}".format(output))
            except OSError:
                # Filesystems without hard links still get exclusive creation.
                # The temporary archive has already passed every integrity check.
                created_identity = None
                try:
                    with output.open("xb") as destination:
                        stat = os.fstat(destination.fileno())
                        created_identity = (stat.st_dev, stat.st_ino)
                        with temp_path.open("rb") as source:
                            shutil.copyfileobj(source, destination, CHUNK_BYTES)
                        destination.flush()
                        os.fsync(destination.fileno())
                    verify_archive(output, expected_size, expected_sha256)
                except Exception:
                    if created_identity is not None:
                        try:
                            stat = output.stat()
                            if (stat.st_dev, stat.st_ino) == created_identity:
                                output.unlink()
                        except OSError:
                            pass
                    raise
            print("Created verified ZIP: {}".format(output))
        print("{} bytes; SHA-256 {}; ZIP CRCs passed".format(expected_size, expected_sha256))
        return 0
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path(__file__).with_name("ARCHIVE_RECONSTRUCTION.json"),
        help="Reconstruction manifest (parts are read from its directory)",
    )
    parser.add_argument("--output", type=Path, help="Optional output ZIP path")
    args = parser.parse_args()
    try:
        return run(args.manifest, args.output)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
