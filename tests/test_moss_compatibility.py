"""Offline regression tests for MOSS request and codec compatibility guards."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        f"test_{name}", ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sampling_patch_rejects_unexpected_upstream_source(monkeypatch):
    patch = load_script("sglang_moss_sampling_compat")
    assert patch.EXPECTED_VERSION == "0.2.0"
    with pytest.raises(RuntimeError, match="SHA-256"):
        patch.patch_source(b"unrecognized SGLang source")


def test_sampling_patch_preserves_patch_scope_and_compiles(monkeypatch):
    patch = load_script("sglang_moss_sampling_compat")
    original = (
        b"def build_moss_tts_state(payload: StagePayload) -> MossTTSState:\n"
        b"    return MossTTSState(\n"
        b"        generation_kwargs=build_generation_kwargs(params, tts_params=tts_params),\n"
        b"    )\n"
    )
    monkeypatch.setattr(patch, "ORIGINAL_SHA256", hashlib.sha256(original).hexdigest())
    modified = patch.patch_source(original)
    assert b"_extract_moss_channel_sampling" in modified
    assert b"Unknown MOSS tts_engine sampling options" in modified
    assert b"stage_params" in modified
    with pytest.raises(RuntimeError, match="SHA-256"):
        patch.patch_source(modified)


def test_language_patch_is_version_guarded_and_adds_vietnamese(monkeypatch):
    patch = load_script("sglang_moss_languages_compat")
    assert patch.EXPECTED_VERSION == "0.2.0"
    source = (
        b"class MossTTSPipelineConfig:\n"
        b'    architecture: ClassVar[str] = "MossTTSDelayModel"\n'
    )
    monkeypatch.setattr(patch, "ORIGINAL_SHA256", hashlib.sha256(source).hexdigest())
    modified = patch.patch_source(source)
    assert b"Vietnamese" in modified
    assert b"additional_speech_languages" in modified
    with pytest.raises(RuntimeError, match="SHA-256"):
        patch.patch_source(modified)


def test_local_only_codec_paths_are_declared_for_two_stages():
    config = (ROOT / "configs" / "moss_tts.yaml").read_text()
    assert config.count("codec_model_path: /codec") == 2
    assert "model_path: /weights" in config
    assert "config_cls: MossTTSSingleProcessPipelineConfig" in config
