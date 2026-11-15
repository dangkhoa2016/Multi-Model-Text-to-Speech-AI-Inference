"""Operator-controlled GPU runtime acceptance for MOSS-TTS, VoxCPM2 and Higgs TTS 3.

No checkpoint is downloaded and no production API is deployed.
Only use after the operator explicitly authorizes GPU expense and reviews budget.
Run from repository root so checked-in lockfiles resolve correctly.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import modal

AUDIT_ROOT = str(Path(__file__).resolve().parents[1])
if AUDIT_ROOT not in sys.path:
    sys.path.insert(0, AUDIT_ROOT)

from tts_inference.runtime_locks import (
    AUDITED_PYTHON_VERSION,
    AUDITED_UV_VERSION,
    MOSS_LOCK,
    MOSS_SHA256,
    VOXCPM2_LOCK,
    VOXCPM2_SHA256,
    verified_lockfile,
)

MOSS_REPOSITORY = "OpenMOSS-Team/MOSS-TTS-v1.5"
MOSS_REVISION = "cdd3b911b1585e3f2dbc7775ef10f9926f58850a"
CODEC_REPOSITORY = "OpenMOSS-Team/MOSS-Audio-Tokenizer"
CODEC_REVISION = "3cd226ba2947efa357ef453bcad111b6eafba782"
VOXCPM_REPOSITORY = "openbmb/VoxCPM2"
HIGGS_REPOSITORY = "bosonai/higgs-tts-3-4b"
HIGGS_CHECKPOINT_REVISION = os.getenv("TTS_HIGGS_CHECKPOINT_REVISION_SHA", "")
if HIGGS_CHECKPOINT_REVISION and not re.fullmatch(r"[0-9a-f]{40}", HIGGS_CHECKPOINT_REVISION):
    raise ValueError("Higgs checkpoint revision must be an exact 40-character SHA")
VOXCPM_REVISION = "7936080b41163874f38610f46f438e2c0757f9fd"
EXPECTED_GPU_NAME = "L40S"
EXPECTED_GPU_MIN_TOTAL_BYTES = 40 * 1024**3
APPROVAL_VALUE = "APPROVED_GPU_ACCEPTANCE_WITH_CHARGES"
# Directly invoking a named function also stays CPU-only without explicit
# approval; the local entrypoint alone is not a sufficient allocation guard.
GPU_APPROVED = os.getenv("TTS_GPU_ACCEPTANCE_APPROVAL") == APPROVAL_VALUE
GPU_ALLOCATION = "L40S" if GPU_APPROVED else None

app = modal.App("tts-gpu-runtime-acceptance")


def acceptance_image_base():
    """Ship audited locks into each GPU image before its module is imported."""
    return (
        modal.Image.debian_slim(python_version=AUDITED_PYTHON_VERSION)
        .add_local_file(MOSS_LOCK, "/root/" + MOSS_LOCK, copy=True)
        .add_local_file(VOXCPM2_LOCK, "/root/" + VOXCPM2_LOCK, copy=True)
    )


moss_image = (
    acceptance_image_base()
    .apt_install("ffmpeg", "libsndfile1", "sox", "build-essential")
    .uv_pip_install(
        requirements=[verified_lockfile(MOSS_LOCK, MOSS_SHA256)],
        uv_version=AUDITED_UV_VERSION, pre=True, extra_options="--require-hashes",
    )
    .env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    .add_local_file(
        "scripts/sglang_moss_sampling_compat.py",
        "/opt/tts/sglang_moss_sampling_compat.py", copy=True,
    )
    .run_commands("python /opt/tts/sglang_moss_sampling_compat.py")
    .add_local_file(
        "scripts/sglang_moss_languages_compat.py",
        "/opt/tts/sglang_moss_languages_compat.py", copy=True,
    )
    .run_commands("python /opt/tts/sglang_moss_languages_compat.py")
    .add_local_file(
        "scripts/moss_cuda_toolchain_preflight.py",
        "/opt/tts/moss_cuda_toolchain_preflight.py", copy=True,
    )
    .run_commands("python /opt/tts/moss_cuda_toolchain_preflight.py")
    .add_local_file(
        "scripts/moss_cuda_linker_preflight.py",
        "/opt/tts/moss_cuda_linker_preflight.py", copy=True,
    )
    .run_commands("python /opt/tts/moss_cuda_linker_preflight.py")
    .add_local_file("configs/moss_tts.yaml", "/opt/tts/configs/moss_tts.yaml")
    .add_local_python_source("tts_inference")
)
voxcpm_image = (
    acceptance_image_base()
    .apt_install("ffmpeg", "libsndfile1")
    .uv_pip_install(
        requirements=[verified_lockfile(VOXCPM2_LOCK, VOXCPM2_SHA256)],
        uv_version=AUDITED_UV_VERSION, extra_options="--require-hashes",
    )
    .env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    .add_local_python_source("tts_inference")
)
# Isolated research runtime: same audited SGLang-Omni Python lock as
# MOSS without MOSS-specific source patches; production OCI is unchanged.
higgs_image = (
    acceptance_image_base()
    .apt_install("ffmpeg", "libsndfile1", "sox")
    .uv_pip_install(
        requirements=[verified_lockfile(MOSS_LOCK, MOSS_SHA256)],
        uv_version=AUDITED_UV_VERSION, pre=True, extra_options="--require-hashes",
    )
    .env({
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TTS_HIGGS_CHECKPOINT_REVISION_SHA": HIGGS_CHECKPOINT_REVISION,
    })
    .add_local_python_source("tts_inference")
)

moss_volume = modal.Volume.from_name(
    "text-to-speech-moss-tts-v1-5-model-weights", create_if_missing=False
).read_only()
codec_volume = modal.Volume.from_name(
    "text-to-speech-moss-audio-tokenizer-weights", create_if_missing=False
).read_only()
voxcpm_volume = modal.Volume.from_name(
    "text-to-speech-voxcpm2-model-weights", create_if_missing=False
).read_only()
higgs_volume = modal.Volume.from_name(
    "text-to-speech-higgs-tts-3-model-weights", create_if_missing=False
).read_only()


def gpu_probe(model: str) -> dict:
    """Read actual driver/GPU and test a small CUDA kernel before model load."""
    import torch

    lines = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout.strip().splitlines()
    if len(lines) != 1:
        raise RuntimeError("Expected exactly one physical GPU; got " + str(len(lines)))
    gpu_name, driver, reported_mb = [part.strip() for part in lines[0].split(",")]
    if EXPECTED_GPU_NAME not in gpu_name:
        raise RuntimeError("Unexpected GPU: " + gpu_name)
    driver_major = int(driver.split(".")[0])
    if driver_major < 580:
        raise RuntimeError("CUDA 13.x needs driver branch R580 or later")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available on assigned GPU")
    gpu = torch.cuda.get_device_properties(0)
    if gpu.total_memory < EXPECTED_GPU_MIN_TOTAL_BYTES:
        raise RuntimeError("Insufficient physical GPU VRAM for configured L40S acceptance")
    major, minor = torch.cuda.get_device_capability(0)
    if (major, minor) != (8, 9):
        raise RuntimeError("Unexpected compute capability for L40S")
    torch.cuda.reset_peak_memory_stats(0)
    x = torch.randn(256, 256, device="cuda", dtype=torch.float16)
    result = x @ x
    torch.cuda.synchronize()
    if not torch.isfinite(result).all().item():
        raise RuntimeError("Non-finite CUDA matrix multiplication")
    free, total = torch.cuda.mem_get_info()
    report = {
        "model": model,
        "gpu_name": gpu_name,
        "driver_version": driver,
        "torch_cuda_version": torch.version.cuda,
        "device_capability": [major, minor],
        "nvidia_smi_memory_total_mb": int(reported_mb),
        "torch_total_vram_bytes": total,
        "torch_free_vram_after_probe_bytes": free,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(0),
        "cuda_kernel_smoke": "PASS",
        "cuda13_2_full_driver_branch": driver_major >= 595,
    }
    print("GPU_DRIVER_CUDA_PROBE=" + json.dumps(report, sort_keys=True), flush=True)
    return report


def inspect_wav(wav: bytes, label: str) -> dict:
    if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
        raise RuntimeError("Not a RIFF/WAVE file")
    if not 44 <= len(wav) <= 32_000_000:
        raise RuntimeError("Missing, truncated or oversized WAV")
    with wave.open(io.BytesIO(wav), "rb") as reader:
        sample_rate = reader.getframerate()
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        frames = reader.getnframes()
        seconds = frames / sample_rate
        pcm = reader.readframes(frames)
    if not (16000 <= sample_rate <= 96000 and 1 <= channels <= 2):
        raise RuntimeError("Unexpected generated WAV format")
    if not (0.20 <= seconds <= 30.0) or sample_width not in (2, 3, 4):
        raise RuntimeError("Generated WAV duration / bit depth outside smoke limits")
    if not any(byte != 0 for byte in pcm):
        raise RuntimeError("Generated WAV contains only zeros")
    return {
        "label": label, "wav_bytes": len(wav), "duration_s": round(seconds, 3),
        "sample_rate": sample_rate, "channels": channels, "sample_width": sample_width,
        "sha256": hashlib.sha256(wav).hexdigest(),
    }


@app.function(image=moss_image, gpu=GPU_ALLOCATION, cpu=2, memory=8192,
              timeout=240, max_containers=1, min_containers=0)
def moss_cuda_probe():
    return gpu_probe("moss-tts-v1.5")


@app.function(image=voxcpm_image, gpu=GPU_ALLOCATION, cpu=2, memory=8192,
              timeout=240, max_containers=1, min_containers=0)
def voxcpm_cuda_probe():
    return gpu_probe("voxcpm2")


@app.function(image=higgs_image, gpu=GPU_ALLOCATION, cpu=2, memory=8192,
              timeout=240, max_containers=1, min_containers=0)
def higgs_cuda_probe():
    return gpu_probe("higgs-tts-3")


@app.function(image=moss_image, gpu=GPU_ALLOCATION, cpu=4, memory=16384,
              timeout=900, max_containers=1, min_containers=0,
              volumes={"/weights": moss_volume, "/codec": codec_volume})
def moss_wav_acceptance():
    """No published API; one short non-cloning Vietnamese sentence."""
    import httpx
    import torch

    from tts_inference.adapters import moss_tts_payload
    from tts_inference.checkpoint_integrity import verify_checkpoint_integrity
    from tts_inference.contracts import SpeechRequest, prepare_request

    hardware = gpu_probe("moss-tts-v1.5")
    verify_checkpoint_integrity(
        "/weights", repository_id=MOSS_REPOSITORY, expected_revision_sha=MOSS_REVISION
    )
    verify_checkpoint_integrity(
        "/codec", repository_id=CODEC_REPOSITORY, expected_revision_sha=CODEC_REVISION
    )
    print("CHECKPOINTS_INDEPENDENTLY_VERIFIED=PASS", flush=True)
    start = time.monotonic()
    command = [
        "sgl-omni", "serve", "--model-path", "/weights",
        "--config", "/opt/tts/configs/moss_tts.yaml",
        "--host", "127.0.0.1", "--port", "8000",
        "--allowed-local-media-path", "/tmp",
    ]
    # Never leave stdout as an undrained PIPE: SGLang startup logs can exceed
    # the pipe buffer and deadlock before the HTTP health check becomes ready.
    server_log = open("/tmp/moss_gpu_acceptance_server.log", "wb")  # noqa: SIM115
    process = subprocess.Popen(command, stdout=server_log,
                               stderr=subprocess.STDOUT)
    try:
        with httpx.Client(timeout=4) as client:
            while time.monotonic() - start < 650:
                if process.poll() is not None:
                    raise RuntimeError("MOSS process exited before health check")
                try:
                    if client.get("http://127.0.0.1:8000/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            else:
                raise TimeoutError("MOSS server startup exceeded acceptance deadline")

        request = SpeechRequest.model_validate({
            "model": "moss-tts-v1.5",
            "input": "Xin chào, đây là thử nghiệm giọng nói tiếng Việt.",
            "model_config": {
                "language": "Vietnamese", "max_new_tokens": 256,
                "duration_tokens": 120, "seed": 12345,
            },
        })
        prepared = prepare_request(request)
        payload = moss_tts_payload(prepared)
        with httpx.Client(timeout=150) as client:
            listing = client.get("http://127.0.0.1:8000/v1/models", timeout=5)
            listing.raise_for_status()
            payload["model"] = listing.json()["data"][0]["id"]
            response = client.post("http://127.0.0.1:8000/v1/audio/speech", json=payload)
            response.raise_for_status()
        audio = inspect_wav(response.content, "moss-tts-v1.5")
        torch.cuda.synchronize()
        return {
            "model": "moss-tts-v1.5", "hardware": hardware, "wav": audio,
            "total_wall_seconds": round(time.monotonic() - start, 2),
            "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(0),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        server_log.close()
        # This private log is intended solely for an operator-reviewed failure.
        # No checkpoint, generated WAV, or reference voice is made public.


@app.function(image=voxcpm_image, gpu=GPU_ALLOCATION, cpu=4, memory=16384,
              timeout=900, max_containers=1, min_containers=0,
              volumes={"/weights": voxcpm_volume})
def voxcpm_wav_acceptance():
    import soundfile
    import torch
    from voxcpm import VoxCPM

    from tts_inference.adapters import voxcpm2_payload
    from tts_inference.checkpoint_integrity import verify_checkpoint_integrity
    from tts_inference.contracts import SpeechRequest, prepare_request

    hardware = gpu_probe("voxcpm2")
    verify_checkpoint_integrity(
        "/weights", repository_id=VOXCPM_REPOSITORY,
        expected_revision_sha=VOXCPM_REVISION,
    )
    start = time.monotonic()
    model = VoxCPM.from_pretrained(
        "/weights", load_denoiser=False,
        optimize=False, local_files_only=True, device="cuda",
    )
    job = prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2",
        "input": "Xin chào, đây là bài kiểm tra âm thanh ngắn.",
        "model_config": {
            "inference_timesteps": 4, "max_len": 256,
            "retry_badcase": False, "denoise": False,
        },
    }))
    payload = voxcpm2_payload(job)
    samples = model.generate(**payload)
    sink = io.BytesIO()
    soundfile.write(sink, samples, model.tts_model.sample_rate,
                    format="WAV", subtype="PCM_16")
    audio = inspect_wav(sink.getvalue(), "voxcpm2")
    torch.cuda.synchronize()
    return {
        "model": "voxcpm2", "hardware": hardware, "wav": audio,
        "total_wall_seconds": round(time.monotonic() - start, 2),
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(0),
    }




@app.function(image=higgs_image, gpu=GPU_ALLOCATION, cpu=4, memory=16384,
              timeout=900, max_containers=1, min_containers=0,
              volumes={"/weights": higgs_volume})
def higgs_wav_acceptance():
    """Independent research WAV smoke test; no production API."""
    import httpx
    import torch

    from tts_inference.adapters import higgs_tts_3_payload
    from tts_inference.checkpoint_integrity import verify_checkpoint_integrity
    from tts_inference.contracts import SpeechRequest, prepare_request

    hardware = gpu_probe("higgs-tts-3")
    # Manifest binds every local checkpoint file to an immutable revision.
    revision = HIGGS_CHECKPOINT_REVISION or None
    verify_checkpoint_integrity(
        "/weights", repository_id=HIGGS_REPOSITORY,
        expected_revision_sha=revision,
    )
    print("HIGGS_LOCAL_CHECKPOINT_INTEGRITY=PASS", flush=True)
    started = time.monotonic()
    command = [
        "sgl-omni", "serve", "--model-path", "/weights",
        "--allowed-local-media-path", "/tmp",
        "--host", "127.0.0.1", "--port", "8000",
    ]
    # Keep output off a pipe; unconsumed logs can deadlock startup.
    log = open("/tmp/higgs_gpu_acceptance_server.log", "wb")  # noqa: SIM115
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    try:
        with httpx.Client(timeout=4) as client:
            while time.monotonic() - started < 650:
                if process.poll() is not None:
                    raise RuntimeError("Higgs server exited before health check")
                try:
                    if client.get("http://127.0.0.1:8000/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            else:
                raise TimeoutError("Higgs server startup exceeded the acceptance deadline")

        request = SpeechRequest.model_validate({
            "model": "higgs-tts-3",
            "input": "Xin chào, đây là thử nghiệm giọng nói ngắn.",
            "model_config": {"max_new_tokens": 256, "seed": 12345},
        })
        payload = higgs_tts_3_payload(prepare_request(request))
        with httpx.Client(timeout=150) as client:
            listing = client.get("http://127.0.0.1:8000/v1/models", timeout=5)
            listing.raise_for_status()
            payload["model"] = listing.json()["data"][0]["id"]
            response = client.post(
                "http://127.0.0.1:8000/v1/audio/speech", json=payload
            )
            response.raise_for_status()
        audio = inspect_wav(response.content, "higgs-tts-3")
        torch.cuda.synchronize()
        return {
            "model": "higgs-tts-3", "hardware": hardware, "wav": audio,
            "total_wall_seconds": round(time.monotonic() - started, 2),
            "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(0),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        # No publicly accessible inference endpoint or audio artifacts.


@app.local_entrypoint()
def main(model: str = "none", phase: str = "none"):
    # Run only in a separate operator-approved acceptance session.
    if os.getenv("TTS_GPU_ACCEPTANCE_APPROVAL") != APPROVAL_VALUE:
        raise RuntimeError(
            "GPU acceptance requires explicit operator approval. "
            "Do not set approval based solely on CPU preflight results."
        )
    allowed = {
        ("moss", "cuda"): moss_cuda_probe,
        ("voxcpm", "cuda"): voxcpm_cuda_probe,
        ("moss", "wav"): moss_wav_acceptance,
        ("voxcpm", "wav"): voxcpm_wav_acceptance,
        ("higgs", "cuda"): higgs_cuda_probe,
        ("higgs", "wav"): higgs_wav_acceptance,
    }
    selected = allowed.get((model, phase))
    if selected is None:
        raise ValueError("Choose exactly one model (moss/voxcpm/higgs) and phase (cuda/wav)")
    result = selected.remote()
    print("GPU_ACCEPTANCE_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
