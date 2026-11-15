"""CPU-only contracts for the audited MOSS CUDA compiler and linker runtime."""
from __future__ import annotations

import ast
from pathlib import Path

from test_modal_deployment_contract import load_mocked_modal_deployment

ROOT = Path(__file__).resolve().parents[1]


def test_moss_gpu_worker_embeds_operator_model_selection(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "moss-tts-v1.5")
    module = load_mocked_modal_deployment(monkeypatch)
    environment = module.sglang_omni_worker_image.environment
    assert environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == "moss-tts-v1.5"
    assert "TEXT_TO_SPEECH_MOSS_TTS_GPU_ACCEPTED" not in environment
    assert "TEXT_TO_SPEECH_MOSS_TTS_BUILD_AUDITED_IMAGE" not in environment
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "1"
    mounts = module.sglang_omni_worker_image.mounts
    assert ("scripts/moss_cuda_toolchain_preflight.py",
            "/opt/text-to-speech/moss_cuda_toolchain_preflight.py") in mounts
    assert ("scripts/moss_cuda_linker_preflight.py",
            "/opt/text-to-speech/moss_cuda_linker_preflight.py") in mounts


def test_unselected_moss_has_no_heavy_runtime_or_gpu_admission(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    environment = module.sglang_omni_worker_image.environment
    assert environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == ""
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "0"
    assert all("moss_cuda_" not in str(source)
               for source, _destination in module.sglang_omni_worker_image.mounts)


def test_moss_single_process_configuration_matches_accepted_gpu_profile():
    configuration = (ROOT / "configs" / "moss_tts.yaml").read_text()
    assert "config_cls: MossTTSSingleProcessPipelineConfig" in configuration
    assert "attention_backend: triton" in configuration
    assert "disable_cuda_graph: true" in configuration
    assert "mem_fraction_static: 0.70" in configuration
    assert "max_running_requests: 1" in configuration
    assert "max_concurrency: 1" in configuration
    assert "device: cpu" in configuration
    assert configuration.count("codec_model_path: /codec") == 2


def test_toolchain_preflight_is_cpu_only_and_attached_to_acceptance_image():
    toolchain_path = ROOT / "scripts" / "moss_cuda_toolchain_preflight.py"
    linker_path = ROOT / "scripts" / "moss_cuda_linker_preflight.py"
    acceptance_path = ROOT / "scripts" / "gpu_runtime_acceptance.py"
    for path in (toolchain_path, linker_path):
        ast.parse(path.read_text())
        assert "modal" not in path.read_text()
    toolchain = toolchain_path.read_text()
    linker = linker_path.read_text()
    assert "nvidia-cuda-nvcc" in toolchain
    assert "libcudart.so.13" in linker
    assert "lib64" in linker
    assert '"-lcudart"' in linker
    assert "ctypes.CDLL" in linker

    acceptance = acceptance_path.read_text()
    assert 'scripts/moss_cuda_toolchain_preflight.py' in acceptance
    assert 'scripts/moss_cuda_linker_preflight.py' in acceptance
    assert acceptance.index('moss_cuda_toolchain_preflight.py') < acceptance.index(
        'moss_cuda_linker_preflight.py'
    )
    assert "APPROVED_GPU_ACCEPTANCE_WITH_CHARGES" in acceptance
