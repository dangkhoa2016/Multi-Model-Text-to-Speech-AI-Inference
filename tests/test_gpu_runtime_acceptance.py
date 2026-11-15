"""Static and CPU-only checks for the standalone GPU acceptance command.

No test in this module allocates GPU or invokes a Modal Function.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import runpy
import sys
import types
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "scripts" / "gpu_runtime_acceptance.py"


@pytest.fixture(autouse=True)
def stub_optional_modal_sdk(monkeypatch):
    """Exercise the acceptance script without installing or calling Modal."""

    class Fluent:
        def __init__(self):
            self.copied_files = []

        def add_local_file(self, source, destination, **options):
            self.copied_files.append((source, destination, options.get("copy")))
            return self

        def __getattr__(self, _method):
            return lambda *_args, **_kwargs: self

    created_images = []

    class Image:
        @staticmethod
        def debian_slim(**_kwargs):
            result = Fluent()
            created_images.append(result)
            return result

    class Volume:
        @staticmethod
        def from_name(_name, **_kwargs):
            return Fluent()

    class App:
        def __init__(self, _name):
            pass

        def function(self, **_kwargs):
            return lambda function: function

        def local_entrypoint(self):
            return lambda function: function

    modal_stub = types.ModuleType("modal")
    modal_stub.Image = Image
    modal_stub.created_images = created_images
    modal_stub.Volume = Volume
    modal_stub.App = App
    monkeypatch.setitem(sys.modules, "modal", modal_stub)


def source_tree():
    return ast.parse(SOURCE_PATH.read_text(), filename=str(SOURCE_PATH))


def test_gpu_allocation_is_disabled_without_explicit_operator_approval():
    source = SOURCE_PATH.read_text()
    assert 'GPU_ALLOCATION = "L40S" if GPU_APPROVED else None' in source
    assert (
        'GPU_APPROVED = os.getenv("TTS_GPU_ACCEPTANCE_APPROVAL") == APPROVAL_VALUE'
        in source
    )
    assert "if os.getenv(\"TTS_GPU_ACCEPTANCE_APPROVAL\") != APPROVAL_VALUE:" in source
    assert "selected.remote()" in source
    assert "APPROVED_GPU_ACCEPTANCE_WITH_CHARGES" in source
    assert "modal deploy" not in source.lower()


def test_gpu_acceptance_isolated_from_production_and_lockfiles():
    source = SOURCE_PATH.read_text()
    tree = source_tree()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    names = (
        "moss_cuda_probe",
        "voxcpm_cuda_probe",
        "moss_wav_acceptance",
        "voxcpm_wav_acceptance",
        "higgs_cuda_probe",
        "higgs_wav_acceptance",
    )
    for name in names:
        function = functions[name]
        decorators = (
            decorator
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
        )
        assert any(
            isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "function"
            and any(
                keyword.arg == "gpu"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "GPU_ALLOCATION"
                for keyword in decorator.keywords
            )
            for decorator in decorators
        )
    assert source.count(".read_only()") == 4
    assert source.count('extra_options="--require-hashes"') == 3
    assert "create_if_missing=False" in source
    assert 'modal.App("tts-gpu-runtime-acceptance")' in source
    assert "modal.Secret.from_name" not in source
    assert "text_to_speech_api" not in source
    assert 'HIGGS_REPOSITORY = "bosonai/higgs-tts-3-4b"' in source
    assert '"/weights": higgs_volume' in source
    assert "from tts_inference.adapters import higgs_tts_3_payload" in source
    assert '("higgs", "cuda"): higgs_cuda_probe' in source
    assert '("higgs", "wav"): higgs_wav_acceptance' in source
    assert "TTS_HIGGS_CHECKPOINT_REVISION_SHA" in source
    assert '"TTS_HIGGS_CHECKPOINT_REVISION_SHA": HIGGS_CHECKPOINT_REVISION' in source
    assert "HiggsTts3Worker" not in source


def test_gpu_acceptance_images_include_both_audited_lockfiles():
    """Each remote GPU function imports all three image declarations."""
    from tts_inference.runtime_locks import MOSS_LOCK, VOXCPM2_LOCK

    with patch.dict(os.environ, {"TTS_GPU_ACCEPTANCE_APPROVAL": ""}):
        namespace = runpy.run_path(str(SOURCE_PATH), run_name="cpu_only_test")

    assert namespace["GPU_ALLOCATION"] is None
    images = sys.modules["modal"].created_images
    assert len(images) == 3
    required = {
        (MOSS_LOCK, "/root/" + MOSS_LOCK, True),
        (VOXCPM2_LOCK, "/root/" + VOXCPM2_LOCK, True),
    }
    for image in images:
        assert required.issubset(set(image.copied_files))


def _wav_inspector():
    with patch.dict(os.environ, {"TTS_GPU_ACCEPTANCE_APPROVAL": ""}):
        namespace = runpy.run_path(str(SOURCE_PATH), run_name="cpu_only_test")
    assert namespace["GPU_ALLOCATION"] is None
    return namespace["inspect_wav"]


def _make_wav(sample: int, frame_count: int = 16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(int(sample).to_bytes(2, "little", signed=True) * frame_count)
    return output.getvalue()


def test_wav_acceptance_validates_real_pcm_metadata():
    raw = _make_wav(1000)
    metadata = _wav_inspector()(raw, "sample")
    assert metadata["label"] == "sample"
    assert metadata["sample_rate"] == 16000
    assert metadata["channels"] == 1
    assert metadata["sample_width"] == 2
    assert metadata["duration_s"] == 1.0
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    "bad_wav",
    [b"not a wav", _make_wav(0), _make_wav(500, frame_count=2000)],
)
def test_wav_acceptance_rejects_invalid_silent_or_short_audio(bad_wav):
    with pytest.raises(RuntimeError):
        _wav_inspector()(bad_wav, "rejected")


@pytest.mark.parametrize("invalid_revision", ["bad-revision", "A" * 40, "f" * 39])
def test_higgs_checkpoint_revision_rejects_unverified_pin(invalid_revision):
    with patch.dict(os.environ, {
        "TTS_GPU_ACCEPTANCE_APPROVAL": "",
        "TTS_HIGGS_CHECKPOINT_REVISION_SHA": invalid_revision,
    }), pytest.raises(ValueError, match="exact 40-character SHA"):
        runpy.run_path(str(SOURCE_PATH), run_name="cpu_only_test")


@pytest.mark.parametrize("locale", ["DEPLOYMENT.md", "DEPLOYMENT.vi.md"])
def test_gpu_acceptance_documentation_requires_explicit_cost_opt_in(locale):
    guide = (ROOT / "docs" / locale).read_text()
    assert "scripts/gpu_runtime_acceptance.py" in guide
    assert "TTS_GPU_ACCEPTANCE_APPROVAL" in guide
    assert "--phase cuda" in guide
    assert "--phase wav" in guide
    assert "modal run" in guide
    assert "--model higgs --phase cuda" in guide
    assert "--model higgs --phase wav" in guide
