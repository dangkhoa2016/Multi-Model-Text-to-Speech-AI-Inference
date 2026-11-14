"""Offline-checkable model checkpoint provenance and file integrity.

Checks accidental corruption, not a signature or proof of model licensing.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MANIFEST_NAME = ".tts-checkpoint-integrity.json"
WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".gguf", ".onnx"}
MODAL_WEIGHTS_MOUNT_PATH = Path("/weights")
MODAL_CODEC_MOUNT_PATH = Path("/codec")
MODAL_VOLUME_DIRECTORY = Path("/__modal/volumes")


class CheckpointIntegrityError(RuntimeError):
    """Missing, modified or untrusted checkpoint content."""


def _checkpoint_directory(root: Path) -> Path:
    """Permit Modal's trusted mount alias, but reject other root symlinks."""
    if root.is_symlink():
        try:
            destination = root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise CheckpointIntegrityError(
                "Missing or unsafe checkpoint directory"
            ) from None
        # Modal mounts /weights as an alias to /__modal/volumes/vo-*.
        # A checkpoint's internal symlinks remain prohibited.
        if (
            root not in (MODAL_WEIGHTS_MOUNT_PATH, MODAL_CODEC_MOUNT_PATH)
            or destination.parent != MODAL_VOLUME_DIRECTORY
            or not destination.name.startswith("vo-")
        ):
            raise CheckpointIntegrityError("Missing or unsafe checkpoint directory")
        root = destination
    if not root.is_dir() or root.is_symlink():
        raise CheckpointIntegrityError("Missing or unsafe checkpoint directory")
    return root


def _files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise CheckpointIntegrityError("Missing or unsafe checkpoint directory")
    files = []
    for path in root.rglob("*"):
        name = path.relative_to(root)
        if ".cache" in name.parts or name.name in {MANIFEST_NAME, MANIFEST_NAME + ".pending"}:
            continue
        if path.is_symlink():
            raise CheckpointIntegrityError("Checkpoint contains a symbolic link")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise CheckpointIntegrityError("Unsupported checkpoint file type")
    files.sort(key=lambda value: value.relative_to(root).as_posix())
    if not any(path.suffix.lower() in WEIGHT_SUFFIXES for path in files):
        raise CheckpointIntegrityError("No recognized model weight file")
    return files


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _records(root: Path) -> list[dict]:
    return [
        {"path": path.relative_to(root).as_posix(),
         "size_bytes": path.stat().st_size, "sha256": _file_hash(path)}
        for path in _files(root)
    ]


def _validate_identity(repository_id: str, revision_sha: str) -> None:
    if not isinstance(repository_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_id
    ):
        raise CheckpointIntegrityError("Invalid model repository identifier")
    if not isinstance(revision_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", revision_sha):
        raise CheckpointIntegrityError("Immutable model revision SHA is required")


def verify_upstream_checkpoint_files(
    local_records: list[dict], upstream_files: list[object],
) -> None:
    """Compare downloaded content with immutable Hugging Face file metadata.

    Every file must have a known upstream size. All recognized weight files
    must also carry an upstream LFS SHA-256; other LFS files are verified when
    that SHA-256 is available. Fail closed before recording an integrity manifest.
    """
    upstream = {}
    for item in upstream_files:
        path = getattr(item, "rfilename", None)
        if not isinstance(path, str) or not path or path in upstream:
            raise CheckpointIntegrityError("Invalid or duplicate upstream file metadata")
        upstream[path] = item

    if not upstream or set(upstream) != {item["path"] for item in local_records}:
        raise CheckpointIntegrityError("Checkpoint files differ from upstream revision")

    for record in local_records:
        upstream_file = upstream[record["path"]]
        size = getattr(upstream_file, "size", None)
        if type(size) is not int or size < 0 or size != record["size_bytes"]:
            raise CheckpointIntegrityError("Checkpoint size differs from upstream metadata")

        lfs = getattr(upstream_file, "lfs", None) or {}
        upstream_sha256 = lfs.get("sha256") if isinstance(lfs, dict) else None
        is_weight = Path(record["path"]).suffix.lower() in WEIGHT_SUFFIXES
        if upstream_sha256 is None:
            if is_weight:
                raise CheckpointIntegrityError("Missing upstream weight SHA-256")
            continue
        if (
            not isinstance(upstream_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", upstream_sha256)
            or upstream_sha256 != record["sha256"]
        ):
            raise CheckpointIntegrityError("Checkpoint SHA-256 differs from upstream metadata")


def record_checkpoint_integrity(
    root: str | Path, *, repository_id: str, revision_sha: str,
    expected_paths: list[str] | None = None,
    upstream_files: list[object] | None = None,
) -> dict:
    """Hash downloaded files then atomically commit a JSON manifest."""
    _validate_identity(repository_id, revision_sha)
    directory = _checkpoint_directory(Path(root))
    files = _records(directory)
    # A pinned Hub tree binds the manifest to the actual snapshot file set;
    # stale leftovers from a previously downloaded revision are rejected.
    if expected_paths is not None and (
        not isinstance(expected_paths, list)
        or len(expected_paths) != len(set(expected_paths))
        or {item["path"] for item in files} != set(expected_paths)
    ):
        raise CheckpointIntegrityError(
            "Checkpoint files do not match the immutable Hub revision"
        )
    if upstream_files is not None:
        verify_upstream_checkpoint_files(files, upstream_files)
    document = {
        "schema_version": 1, "repository_id": repository_id,
        "revision_sha": revision_sha, "files": files,
    }
    marker = directory / MANIFEST_NAME
    temporary = directory / (MANIFEST_NAME + ".pending")
    try:
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)
    return document


def verify_checkpoint_integrity(
    root: str | Path, *, repository_id: str, expected_revision_sha: str | None = None
) -> dict:
    """Validate each byte before starting an otherwise paid GPU runtime."""
    directory = _checkpoint_directory(Path(root))
    try:
        document = json.loads((directory / MANIFEST_NAME).read_text())
    except (OSError, ValueError, TypeError) as error:
        raise CheckpointIntegrityError("Missing or invalid integrity manifest") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CheckpointIntegrityError("Unsupported integrity manifest version")
    revision_sha = document.get("revision_sha")
    _validate_identity(document.get("repository_id"), revision_sha)
    if document["repository_id"] != repository_id:
        raise CheckpointIntegrityError("Manifest has the wrong model repository")
    if expected_revision_sha and revision_sha != expected_revision_sha:
        raise CheckpointIntegrityError("Manifest differs from pinned operator revision")
    if document.get("files") != _records(directory):
        raise CheckpointIntegrityError("Checkpoint files changed after download")
    return document
