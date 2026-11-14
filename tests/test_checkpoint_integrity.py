"""CPU-only checkpoint provenance regressions; no Hub, Modal or GPU calls."""
import hashlib
import json
from types import SimpleNamespace

import pytest

import tts_inference.checkpoint_integrity as integrity
from tts_inference.checkpoint_integrity import (
    MANIFEST_NAME,
    CheckpointIntegrityError,
    record_checkpoint_integrity,
    verify_checkpoint_integrity,
)

MODEL = "openbmb/VoxCPM2"
REVISION = "a" * 40


def checkpoint(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"fake-weight-bytes")
    (tmp_path / "config.json").write_text('{"model_type":"fake"}')
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "hub-metadata").write_text("ignored local cache")
    return tmp_path


def test_checkpoint_records_full_bytes_and_revision(tmp_path):
    root = checkpoint(tmp_path)
    manifest = record_checkpoint_integrity(root, repository_id=MODEL, revision_sha=REVISION)
    assert manifest["revision_sha"] == REVISION
    assert len(manifest["files"]) == 2
    assert verify_checkpoint_integrity(
        root, repository_id=MODEL, expected_revision_sha=REVISION
    ) == manifest


@pytest.mark.parametrize("mutation", ["change", "missing", "extra"])
def test_corrupted_or_unrecorded_checkpoint_fails_closed(tmp_path, mutation):
    root = checkpoint(tmp_path)
    record_checkpoint_integrity(root, repository_id=MODEL, revision_sha=REVISION)
    weights = root / "model.safetensors"
    if mutation == "change":
        weights.write_bytes(b"tampered-weight-bytes")
    elif mutation == "missing":
        weights.unlink()
    else:
        (root / "additional.safetensors").write_bytes(b"untrusted")
    with pytest.raises(CheckpointIntegrityError):
        verify_checkpoint_integrity(root, repository_id=MODEL)


def test_checkpoint_identity_and_operator_revision_must_match(tmp_path):
    root = checkpoint(tmp_path)
    record_checkpoint_integrity(root, repository_id=MODEL, revision_sha=REVISION)
    with pytest.raises(CheckpointIntegrityError, match="wrong model repository"):
        verify_checkpoint_integrity(root, repository_id="OpenMOSS-Team/MOSS-TTS-v1.5")
    with pytest.raises(CheckpointIntegrityError, match="operator revision"):
        verify_checkpoint_integrity(root, repository_id=MODEL, expected_revision_sha="b" * 40)


def test_cpu_download_rejects_stale_revision_files(tmp_path):
    root = checkpoint(tmp_path)
    with pytest.raises(CheckpointIntegrityError, match="immutable Hub revision"):
        record_checkpoint_integrity(
            root, repository_id=MODEL, revision_sha=REVISION,
            expected_paths=["model.safetensors"],
        )
    assert not (root / MANIFEST_NAME).exists()


def test_checkpoint_without_manifest_is_rejected(tmp_path):
    checkpoint(tmp_path)
    with pytest.raises(CheckpointIntegrityError, match="Missing or invalid"):
        verify_checkpoint_integrity(tmp_path, repository_id=MODEL)


def test_checkpoint_symlinks_are_rejected(tmp_path):
    checkpoint(tmp_path)
    (tmp_path / "link.safetensors").symlink_to(tmp_path / "model.safetensors")
    with pytest.raises(CheckpointIntegrityError, match="symbolic link"):
        record_checkpoint_integrity(tmp_path, repository_id=MODEL, revision_sha=REVISION)


def test_corrupted_manifest_fails_without_leaking_contents(tmp_path):
    root = checkpoint(tmp_path)
    record_checkpoint_integrity(root, repository_id=MODEL, revision_sha=REVISION)
    data = json.loads((root / MANIFEST_NAME).read_text())
    data["files"][0]["sha256"] = "0" * 64
    (root / MANIFEST_NAME).write_text(json.dumps(data))
    with pytest.raises(CheckpointIntegrityError, match="changed"):
        verify_checkpoint_integrity(root, repository_id=MODEL)



def test_modal_volume_mount_alias_preserves_integrity(tmp_path, monkeypatch):
    volume_directory = tmp_path / "__modal" / "volumes"
    checkpoint_directory = volume_directory / "vo-verified-checkpoint"
    checkpoint_directory.mkdir(parents=True)
    checkpoint(checkpoint_directory)
    mount = tmp_path / "weights"
    mount.symlink_to(checkpoint_directory, target_is_directory=True)
    monkeypatch.setattr(integrity, "MODAL_WEIGHTS_MOUNT_PATH", mount)
    monkeypatch.setattr(integrity, "MODAL_VOLUME_DIRECTORY", volume_directory)

    manifest = record_checkpoint_integrity(
        mount, repository_id=MODEL, revision_sha=REVISION,
    )
    assert (checkpoint_directory / MANIFEST_NAME).is_file()
    assert verify_checkpoint_integrity(
        mount, repository_id=MODEL, expected_revision_sha=REVISION
    ) == manifest


def test_checkpoint_rejects_untrusted_root_alias(tmp_path):
    root = tmp_path / "checkpoint"
    root.mkdir()
    checkpoint(root)
    alias = tmp_path / "other"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(CheckpointIntegrityError, match="unsafe checkpoint"):
        record_checkpoint_integrity(alias, repository_id=MODEL, revision_sha=REVISION)


def test_modal_mount_alias_must_resolve_inside_modal_volumes(tmp_path, monkeypatch):
    root = tmp_path / "checkpoint"
    root.mkdir()
    checkpoint(root)
    alias = tmp_path / "weights"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(integrity, "MODAL_WEIGHTS_MOUNT_PATH", alias)
    monkeypatch.setattr(
        integrity, "MODAL_VOLUME_DIRECTORY", tmp_path / "__modal" / "volumes"
    )
    with pytest.raises(CheckpointIntegrityError, match="unsafe checkpoint"):
        record_checkpoint_integrity(alias, repository_id=MODEL, revision_sha=REVISION)


def test_modal_mount_alias_still_rejects_checkpoint_file_symlinks(tmp_path, monkeypatch):
    volume_directory = tmp_path / "__modal" / "volumes"
    checkpoint_directory = volume_directory / "vo-verified-checkpoint"
    checkpoint_directory.mkdir(parents=True)
    checkpoint(checkpoint_directory)
    (checkpoint_directory / "rogue.safetensors").symlink_to(
        checkpoint_directory / "model.safetensors"
    )
    mount = tmp_path / "weights"
    mount.symlink_to(checkpoint_directory, target_is_directory=True)
    monkeypatch.setattr(integrity, "MODAL_WEIGHTS_MOUNT_PATH", mount)
    monkeypatch.setattr(integrity, "MODAL_VOLUME_DIRECTORY", volume_directory)
    with pytest.raises(CheckpointIntegrityError, match="symbolic link"):
        record_checkpoint_integrity(mount, repository_id=MODEL, revision_sha=REVISION)

def test_codec_volume_mount_alias_accepts_only_trusted_modal_target(tmp_path, monkeypatch):
    volume_directory = tmp_path / "__modal" / "volumes"
    codec_directory = volume_directory / "vo-pinned-moss-codec"
    codec_directory.mkdir(parents=True)
    checkpoint(codec_directory)
    mount = tmp_path / "codec"
    mount.symlink_to(codec_directory, target_is_directory=True)
    monkeypatch.setattr(integrity, "MODAL_CODEC_MOUNT_PATH", mount)
    monkeypatch.setattr(integrity, "MODAL_VOLUME_DIRECTORY", volume_directory)
    repo = "OpenMOSS-Team/MOSS-Audio-Tokenizer"

    manifest = record_checkpoint_integrity(
        mount, repository_id=repo, revision_sha=REVISION
    )
    assert verify_checkpoint_integrity(
        mount, repository_id=repo, expected_revision_sha=REVISION
    ) == manifest

    (codec_directory / "rogue.safetensors").symlink_to(
        codec_directory / "model.safetensors"
    )
    with pytest.raises(CheckpointIntegrityError, match="symbolic link"):
        verify_checkpoint_integrity(mount, repository_id=repo)


def test_codec_volume_mount_wrong_alias_is_rejected(tmp_path, monkeypatch):
    directory = tmp_path / "__modal" / "volumes" / "vo-codec"
    directory.mkdir(parents=True)
    checkpoint(directory)
    alias = tmp_path / "bad_alias"
    alias.symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr(integrity, "MODAL_VOLUME_DIRECTORY", directory.parent)
    with pytest.raises(CheckpointIntegrityError, match="unsafe checkpoint"):
        record_checkpoint_integrity(alias, repository_id=MODEL, revision_sha=REVISION)


def _upstream_fixture(root):
    weight = root / "model.safetensors"
    config = root / "config.json"
    return [
        SimpleNamespace(
            rfilename="model.safetensors",
            size=weight.stat().st_size,
            lfs={"sha256": hashlib.sha256(weight.read_bytes()).hexdigest()},
        ),
        SimpleNamespace(
            rfilename="config.json", size=config.stat().st_size, lfs=None,
        ),
    ]


def test_download_manifest_matches_upstream_bytes_and_lfs_hashes(tmp_path):
    root = checkpoint(tmp_path)
    manifest = record_checkpoint_integrity(
        root, repository_id=MODEL, revision_sha=REVISION,
        upstream_files=_upstream_fixture(root),
    )
    assert len(manifest["files"]) == 2
    assert verify_checkpoint_integrity(
        root, repository_id=MODEL, expected_revision_sha=REVISION,
    ) == manifest


@pytest.mark.parametrize("mutation", [
    "wrong_size", "missing_size", "wrong_sha256", "invalid_sha256",
    "missing_weight_sha256", "wrong_file_list", "duplicate_file",
    "non_weight_lfs_mismatch",
])
def test_upstream_metadata_mismatch_rejected_before_manifest(tmp_path, mutation):
    root = checkpoint(tmp_path)
    upstream = _upstream_fixture(root)
    if mutation == "wrong_size":
        upstream[0].size += 1
    elif mutation == "missing_size":
        upstream[1].size = None
    elif mutation == "wrong_sha256":
        upstream[0].lfs["sha256"] = "0" * 64
    elif mutation == "invalid_sha256":
        upstream[0].lfs["sha256"] = "not-a-sha256"
    elif mutation == "missing_weight_sha256":
        upstream[0].lfs = None
    elif mutation == "wrong_file_list":
        upstream[1].rfilename = "different-config.json"
    elif mutation == "duplicate_file":
        upstream.append(upstream[0])
    elif mutation == "non_weight_lfs_mismatch":
        upstream[1].lfs = {"sha256": "0" * 64}

    with pytest.raises(CheckpointIntegrityError):
        record_checkpoint_integrity(
            root, repository_id=MODEL, revision_sha=REVISION,
            upstream_files=upstream,
        )
    assert not (root / MANIFEST_NAME).exists()
