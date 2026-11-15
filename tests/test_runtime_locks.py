"""CPU-only regression checks for dependency-pinned Modal model images."""

from __future__ import annotations

import hashlib
import re

import pytest
from test_modal_deployment_contract import load_mocked_modal_deployment

from tts_inference import runtime_locks


@pytest.mark.parametrize(
    ("relative", "digest", "expected_packages", "expected_hashes"),
    [
        (runtime_locks.MOSS_LOCK, runtime_locks.MOSS_SHA256, 283, 5624),
        (runtime_locks.VOXCPM2_LOCK, runtime_locks.VOXCPM2_SHA256, 167, 3886),
    ],
)
def test_all_distribution_pins_are_sha256_locked(
    relative, digest, expected_packages, expected_hashes
):
    assert runtime_locks.verified_lockfile(relative, digest) == relative
    text = (runtime_locks.REPOSITORY_ROOT / relative).read_text()
    pins = re.findall(r"(?m)^[A-Za-z0-9_.-]+==", text)
    hashes = re.findall(r"--hash=sha256:[a-f0-9]{64}", text)
    assert len(pins) == expected_packages
    assert len(hashes) == expected_hashes
    assert len(re.findall(r"(?m)^--only-binary ", text)) >= 1
    assert "--hash=sha256:" in text
    assert hashlib.sha256(text.encode()).hexdigest() == digest


def test_changed_or_redirected_lockfile_is_rejected(tmp_path, monkeypatch):
    folder = tmp_path / "runtime_locks"
    folder.mkdir()
    lock = folder / "moss.txt"
    lock.write_text("sglang-omni==0.2.0\n")
    monkeypatch.setattr(runtime_locks, "REPOSITORY_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        runtime_locks.verified_lockfile("runtime_locks/moss.txt", runtime_locks.MOSS_SHA256)
    lock.unlink()
    source = tmp_path / "untrusted.txt"
    source.write_text("sglang-omni==0.2.0\n")
    lock.symlink_to(source)
    with pytest.raises(RuntimeError, match="unsafe audited"):
        runtime_locks.verified_lockfile("runtime_locks/moss.txt", runtime_locks.MOSS_SHA256)


def test_moss_selection_builds_audited_runtime_without_acceptance_flags(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "moss-tts-v1.5")
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "1"
    assert module.sglang_omni_worker_image.environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == "moss-tts-v1.5"
    assert ("uv", (runtime_locks.MOSS_LOCK,), "0.13.5", "--require-hashes", True) in (
        module.sglang_omni_worker_image.installed_packages
    )
    assert not any(isinstance(item, str) and item.startswith("sglang-omni")
                   for item in module.sglang_omni_worker_image.installed_packages)


def test_unselected_moss_does_not_build_even_with_obsolete_acceptance_flags(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    monkeypatch.setenv("TEXT_TO_SPEECH_MOSS_TTS_GPU_ACCEPTED", "1")
    monkeypatch.setenv("TEXT_TO_SPEECH_MOSS_CODEC_READY", "1")
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "0"
    assert not any(isinstance(item, tuple) and item[0] == "uv"
                   for item in module.sglang_omni_worker_image.installed_packages)


def test_voxcpm_selected_uses_audited_lock_without_gpu_acceptance(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_VOXCPM2_ENABLED"] == "0"
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "voxcpm2")
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_VOXCPM2_ENABLED"] == "1"
    assert module.voxcpm2_worker_image.environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == "voxcpm2"
    assert ("uv", (runtime_locks.VOXCPM2_LOCK,), "0.13.5", "--require-hashes", False) in (
        module.voxcpm2_worker_image.installed_packages
    )
    assert "TEXT_TO_SPEECH_VOXCPM2_GPU_ACCEPTED" not in module.voxcpm2_worker_image.environment


def test_higgs_immutable_registry_pin_is_not_moss_base(monkeypatch):
    sha = "b" * 64
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF",
        "lmsysorg/sglang-omni@sha256:" + sha,
    )
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.higgs_tts_3_worker_image.name == "debian"
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "higgs-tts-3")
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "1")
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.higgs_tts_3_worker_image.name.endswith(sha)
    assert module.sglang_omni_worker_image.name == "debian"
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "0"


def test_xz_lock_restore(tmp_path, monkeypatch):
    import lzma
    from pathlib import Path
    folder=tmp_path/"runtime_locks"
    folder.mkdir()
    source=Path(__file__).resolve().parents[1]/runtime_locks.MOSS_LOCK
    data=source.read_bytes()
    (folder/(Path(runtime_locks.MOSS_LOCK).name+".xz")).write_bytes(lzma.compress(data))
    monkeypatch.setattr(runtime_locks,"REPOSITORY_ROOT",tmp_path)
    assert runtime_locks.verified_lockfile(runtime_locks.MOSS_LOCK,runtime_locks.MOSS_SHA256)==runtime_locks.MOSS_LOCK
    assert (folder/Path(runtime_locks.MOSS_LOCK).name).read_bytes()==data


def test_reject_corrupt_xz_lock(tmp_path, monkeypatch):
    from pathlib import Path
    folder=tmp_path/"runtime_locks"
    folder.mkdir()
    (folder/(Path(runtime_locks.MOSS_LOCK).name+".xz")).write_bytes(b"broken")
    monkeypatch.setattr(runtime_locks,"REPOSITORY_ROOT",tmp_path)
    with pytest.raises(RuntimeError,match="Corrupt compressed"):
        runtime_locks.verified_lockfile(runtime_locks.MOSS_LOCK,runtime_locks.MOSS_SHA256)
