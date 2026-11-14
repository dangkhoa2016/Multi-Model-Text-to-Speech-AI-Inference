"""Modal deployment: one CPU gateway, three isolated GPU pools and weight Volumes.

NO GPU launches on import, CI, or `python -m compileall`.
Downloading is only performed by explicitly invoking the download_* functions.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import modal

from tts_inference.adapters import higgs_tts_3_payload, moss_tts_payload, voxcpm2_payload
from tts_inference.runtime_locks import (
    AUDITED_PYTHON_VERSION,
    AUDITED_UV_VERSION,
    MOSS_LOCK,
    MOSS_SHA256,
    VOXCPM2_LOCK,
    VOXCPM2_SHA256,
    VOXCPM_PACKAGE_VERSION,
    verified_lockfile,
)
from tts_inference.wav import wave_metadata

logger = logging.getLogger(__name__)

MODAL_APPLICATION_NAME = "text-to-speech-ai-inference"
app = modal.App(MODAL_APPLICATION_NAME)

SUPPORTED_DEPLOYMENT_MODELS = frozenset({"moss-tts-v1.5", "voxcpm2", "higgs-tts-3"})


def parse_enabled_models(raw: str) -> frozenset[str]:
    """Parse a single operator selection; do not claim runtime readiness."""
    if not raw.strip():
        return frozenset()
    names = [name.strip() for name in raw.split(",")]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError("TEXT_TO_SPEECH_ENABLED_MODELS requires unique, nonempty model IDs")
    unknown = set(names) - SUPPORTED_DEPLOYMENT_MODELS
    if unknown:
        raise ValueError(f"Unknown model IDs: {sorted(unknown)}")
    return frozenset(names)


operator_enabled_models = parse_enabled_models(os.getenv("TEXT_TO_SPEECH_ENABLED_MODELS", ""))
model_selection_environment = {
    "TEXT_TO_SPEECH_ENABLED_MODELS": ",".join(sorted(operator_enabled_models))
}

moss_tts_model_weights_volume = modal.Volume.from_name("text-to-speech-moss-tts-v1-5-model-weights", create_if_missing=True)
moss_audio_tokenizer_volume = modal.Volume.from_name(
    "text-to-speech-moss-audio-tokenizer-weights", create_if_missing=True
)
MOSS_AUDIO_TOKENIZER_REPOSITORY = "OpenMOSS-Team/MOSS-Audio-Tokenizer"
MOSS_AUDIO_TOKENIZER_REVISION = "3cd226ba2947efa357ef453bcad111b6eafba782"
MOSS_AUDIO_TOKENIZER_EXPECTED_FILE_COUNT = 14
MOSS_AUDIO_TOKENIZER_EXPECTED_BYTES = 7_101_115_998
higgs_tts_3_model_weights_volume = modal.Volume.from_name("text-to-speech-higgs-tts-3-model-weights", create_if_missing=True)
voxcpm2_model_weights_volume = modal.Volume.from_name("text-to-speech-voxcpm2-model-weights", create_if_missing=True)

# The externally pinned SGLang image remains limited to the separately
# licensed Higgs runtime. MOSS uses Python 3.12 with hash-enforced dependencies.
# Never pull a mutable registry tag in a paid GPU deployment.
sglang_container_reference = os.getenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF", "")
if sglang_container_reference and not re.fullmatch(
    r"lmsysorg/sglang-omni@sha256:[0-9a-f]{64}", sglang_container_reference
):
    raise ValueError("SGLang-Omni image must be an audited immutable SHA-256 digest")
# The separately licensed Higgs image is built only when selected.
higgs_runtime_selected = "higgs-tts-3" in operator_enabled_models
# Licensing is a deploy-time decision, not a client request or a secret.
# The same explicit decision is embedded in both CPU admission and the Higgs worker.
# Changing it requires a fresh deployment. Never default to accepting the license.
higgs_tts_3_license_confirmed = os.getenv("HIGGS_TTS_3_LICENSE_CONFIRMED") == "1"
if "higgs-tts-3" in operator_enabled_models:
    if not higgs_tts_3_license_confirmed:
        raise ValueError("Selected Higgs TTS 3 requires separate model-license authorization")
    if not sglang_container_reference:
        raise ValueError("Selected Higgs TTS 3 requires an audited immutable SGLang image digest")
higgs_tts_3_license_environment = {
    "HIGGS_TTS_3_LICENSE_CONFIRMED": "1" if higgs_tts_3_license_confirmed else "0"
}
sglang_base_image = (
    modal.Image.from_registry(sglang_container_reference)
    if higgs_runtime_selected else modal.Image.debian_slim(python_version="3.12")
)
if higgs_runtime_selected:
    sglang_base_image = sglang_base_image.pip_install(
        "sglang-omni==0.2.0", "httpx>=0.27,<1", pre=True,
    )
sglang_omni_worker_base_image = sglang_base_image.env({
    **model_selection_environment,
    "TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF": sglang_container_reference,
    "TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA": os.getenv(
        "TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA", ""
    ),
    "TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA": os.getenv(
        "TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA", ""
    ),
})

# Validate the committed lockfiles before any Modal image is constructed.
moss_requirements = verified_lockfile(MOSS_LOCK, MOSS_SHA256)
voxcpm_requirements = verified_lockfile(VOXCPM2_LOCK, VOXCPM2_SHA256)


# The module is imported once more inside each Modal container. Include both
# audited manifests at their expected absolute paths before attaching source
# mounts, so import-time SHA-256 verification also works in remote containers.
def attach_audited_runtime_lockfiles(image: modal.Image) -> modal.Image:
    return (
        image.add_local_file(MOSS_LOCK, "/root/" + MOSS_LOCK, copy=True)
        .add_local_file(VOXCPM2_LOCK, "/root/" + VOXCPM2_LOCK, copy=True)
    )


# Build selected GPU runtimes using committed hash-locked dependencies.
moss_build_audited_image = "moss-tts-v1.5" in operator_enabled_models
voxcpm_package_version = (
    VOXCPM_PACKAGE_VERSION if "voxcpm2" in operator_enabled_models else ""
)
voxcpm_base_image = (
    modal.Image.debian_slim(python_version=AUDITED_PYTHON_VERSION)
    .apt_install("ffmpeg", "libsndfile1")
)
if voxcpm_package_version:
    voxcpm_base_image = voxcpm_base_image.uv_pip_install(
        requirements=[voxcpm_requirements],
        uv_version=AUDITED_UV_VERSION, extra_options="--require-hashes",
    )
voxcpm2_worker_image = voxcpm_base_image.env({
    **model_selection_environment,
    "TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA": os.getenv(
        "TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA", ""
    ),
})
gateway_and_dispatcher_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi>=0.115,<1", "pydantic>=2.10,<3", "httpx>=0.27,<1",
        "sqlalchemy>=2.0,<3", "psycopg[binary]>=3.2,<4", "redis>=5,<8",
        "cryptography>=43,<47",
    )
)
model_download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub>=0.33,<2", "hf_xet>=1.7,<2", "pydantic>=2.10,<3")
)
# CPU admission is deployment intent; worker setup verifies actual checkpoints.
runtime_model_enablement_environment = {
    "TEXT_TO_SPEECH_MOSS_TTS_ENABLED": "1" if moss_build_audited_image else "0",
    "TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED": (
        "1" if "higgs-tts-3" in operator_enabled_models else "0"
    ),
    "TEXT_TO_SPEECH_VOXCPM2_ENABLED": "1" if voxcpm_package_version else "0",
}
# A deploy-time, non-secret operator opt-in. Explicitly embed the decision
# in the CPU image: local deploy shell variables are not inherited at runtime.
# Without this value the production gateway rejects reference cloning (403).
raw_reference_client_ids = os.getenv(
    "TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON", "[]"
)
try:
    reference_client_ids = json.loads(raw_reference_client_ids)
except (TypeError, ValueError) as error:
    raise ValueError("Invalid reference cloning client allowlist") from error
if (not isinstance(reference_client_ids, list) or len(reference_client_ids) > 100
        or any(not isinstance(value, str)
               or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
               for value in reference_client_ids)):
    raise ValueError("Reference cloning client IDs must be valid and bounded")
gateway_runtime_policy_environment = {
    "TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING": (
        "1" if os.getenv("TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING") == "1" else "0"
    ),
    "TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON": json.dumps(
        sorted(set(reference_client_ids))
    ),
}
gateway_and_dispatcher_image = (
    attach_audited_runtime_lockfiles(gateway_and_dispatcher_image.env({
        **model_selection_environment,
        **runtime_model_enablement_environment,
        **gateway_runtime_policy_environment,
    }))
    .add_local_python_source("tts_inference")
)
voxcpm2_worker_image = attach_audited_runtime_lockfiles(
    voxcpm2_worker_image
).add_local_python_source("tts_inference")
# Build MOSS in one Python environment. The old upstream OCI image
# contains a different Python/SGLang stack and is not used by MOSS.
moss_worker_base_image = (
    modal.Image.debian_slim(python_version=AUDITED_PYTHON_VERSION)
    .apt_install("ffmpeg", "libsndfile1", "sox", "build-essential")
)
if moss_build_audited_image:
    moss_worker_base_image = moss_worker_base_image.uv_pip_install(
        requirements=[moss_requirements], uv_version=AUDITED_UV_VERSION,
        pre=True, extra_options="--require-hashes",
    )
moss_worker_base_image = moss_worker_base_image.env({
    **model_selection_environment,
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    "TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA": os.getenv(
        "TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA", ""
    ),
})
# Source-hash-guarded patches apply only to pinned MOSS, not Higgs.
if moss_build_audited_image:
    moss_worker_base_image = (
        moss_worker_base_image
        .add_local_file(
            "scripts/sglang_moss_sampling_compat.py",
            "/opt/text-to-speech/sglang_moss_sampling_compat.py", copy=True,
        )
        .run_commands("python /opt/text-to-speech/sglang_moss_sampling_compat.py")
        .add_local_file(
            "scripts/sglang_moss_languages_compat.py",
            "/opt/text-to-speech/sglang_moss_languages_compat.py", copy=True,
        )
        .run_commands("python /opt/text-to-speech/sglang_moss_languages_compat.py")
        .add_local_file(
            "scripts/moss_cuda_toolchain_preflight.py",
            "/opt/text-to-speech/moss_cuda_toolchain_preflight.py", copy=True,
        )
        .run_commands("python /opt/text-to-speech/moss_cuda_toolchain_preflight.py")
        .add_local_file(
            "scripts/moss_cuda_linker_preflight.py",
            "/opt/text-to-speech/moss_cuda_linker_preflight.py", copy=True,
        )
        .run_commands("python /opt/text-to-speech/moss_cuda_linker_preflight.py")
    )

sglang_omni_worker_image = (
    attach_audited_runtime_lockfiles(moss_worker_base_image)
    .add_local_file(
        "configs/moss_tts.yaml", "/opt/text-to-speech/configs/moss_tts.yaml"
    )
    .add_local_python_source("tts_inference")
)
higgs_tts_3_worker_image = (
    attach_audited_runtime_lockfiles(
        sglang_omni_worker_base_image.env(higgs_tts_3_license_environment)
    )
    .add_local_file(
        "configs/moss_tts.yaml", "/opt/text-to-speech/configs/moss_tts.yaml"
    )
    .add_local_python_source("tts_inference")
)
higgs_tts_3_model_download_image = (
    attach_audited_runtime_lockfiles(
        model_download_image.env(higgs_tts_3_license_environment)
    )
    .add_local_python_source("tts_inference")
)
model_download_image = attach_audited_runtime_lockfiles(
    model_download_image
).add_local_python_source("tts_inference")


# Read access uses a dedicated Secret only in CPU-only download/preflight jobs.
# No Hugging Face credentials are passed to the public gateway or GPU workers.
HUGGING_FACE_AUTHENTICATION_SECRET = modal.Secret.from_name(
    "text-to-speech-hugging-face-authentication",
    # Either HUGGING_FACE_READ_TOKEN or HF_TOKEN is accepted by the CPU functions.
)


def download_model_checkpoint(repository_id: str, model_weights_volume: modal.Volume):
    """Authorize and pin a revision before downloading into one private Volume."""
    from huggingface_hub import HfApi, snapshot_download

    from tts_inference.hugging_face_access import (
        check_model_download_access,
        resolve_hugging_face_token,
    )

    hugging_face_token = resolve_hugging_face_token()
    access = check_model_download_access(
        repository_id, hugging_face_token=hugging_face_token
    )
    print(f"Verified access to {repository_id} at revision {access.revision_sha}")
    upstream_information = HfApi().model_info(
        repo_id=repository_id,
        revision=access.revision_sha,
        files_metadata=True,
        token=hugging_face_token,
    )
    if upstream_information.sha != access.revision_sha:
        raise RuntimeError("Checkpoint upstream revision mismatch")
    upstream_files = list(upstream_information.siblings or [])
    snapshot_download(
        repo_id=repository_id,
        revision=access.revision_sha,
        local_dir="/weights",
        token=hugging_face_token,
        max_workers=4,
    )
    from tts_inference.checkpoint_integrity import record_checkpoint_integrity

    record_checkpoint_integrity(
        "/weights", repository_id=repository_id,
        revision_sha=access.revision_sha,
        expected_paths=[item.rfilename for item in upstream_files],
        upstream_files=upstream_files,
    )
    model_weights_volume.commit()
    print(f"Committed the verified checkpoint for {repository_id} to its Modal Volume")


@app.function(
    image=model_download_image,
    secrets=[HUGGING_FACE_AUTHENTICATION_SECRET],
    cpu=1,
    memory=512,
    timeout=180,
)
def check_hugging_face_model_access(model_id: str = "all"):
    """CPU-only access check; never downloads checkpoint files or launches GPU."""
    from tts_inference.contracts import CHECKPOINTS
    from tts_inference.hugging_face_access import (
        check_model_download_access,
        resolve_hugging_face_token,
    )

    selected_repositories = {
        model.value: repository for model, repository in CHECKPOINTS.items()
    }
    if model_id == "all":
        requested_models = selected_repositories
    elif model_id in selected_repositories:
        requested_models = {model_id: selected_repositories[model_id]}
    else:
        raise ValueError(
            "Unknown model ID. Choose moss-tts-v1.5, higgs-tts-3, voxcpm2 or all."
        )
    for selected_model, repository_id in requested_models.items():
        access = check_model_download_access(
            repository_id,
            hugging_face_token=resolve_hugging_face_token(),
        )
        print(f"ACCESS OK: {selected_model} — {repository_id} at {access.revision_sha}")


@app.function(
    image=model_download_image, secrets=[HUGGING_FACE_AUTHENTICATION_SECRET],
    volumes={"/weights": moss_tts_model_weights_volume}, cpu=4, memory=8192, timeout=7200,
)
def download_moss_tts_model():
    """Explicit CPU-only download for OpenMOSS-Team/MOSS-TTS-v1.5."""
    download_model_checkpoint("OpenMOSS-Team/MOSS-TTS-v1.5", moss_tts_model_weights_volume)


@app.function(
    image=model_download_image, cpu=4, memory=8192, timeout=7200,
    volumes={"/weights": moss_audio_tokenizer_volume},
)
def download_moss_audio_tokenizer():
    """Explicit CPU-only download of pinned MOSS audio tokenizer checkpoint."""
    from huggingface_hub import HfApi, snapshot_download

    from tts_inference.checkpoint_integrity import record_checkpoint_integrity

    information = HfApi().model_info(
        repo_id=MOSS_AUDIO_TOKENIZER_REPOSITORY,
        revision=MOSS_AUDIO_TOKENIZER_REVISION, files_metadata=True,
    )
    if information.sha != MOSS_AUDIO_TOKENIZER_REVISION:
        raise RuntimeError("Codec revision mismatch")
    upstream = {item.rfilename: item for item in information.siblings}
    if len(upstream) != MOSS_AUDIO_TOKENIZER_EXPECTED_FILE_COUNT:
        raise RuntimeError("Codec file count mismatch")
    if (any(item.size is None for item in upstream.values()) or sum(item.size or 0 for item in upstream.values()) != MOSS_AUDIO_TOKENIZER_EXPECTED_BYTES):
        raise RuntimeError("Codec snapshot size mismatch")
    snapshot_download(
        repo_id=MOSS_AUDIO_TOKENIZER_REPOSITORY,
        revision=MOSS_AUDIO_TOKENIZER_REVISION,
        local_dir="/weights", max_workers=4,
    )
    record_checkpoint_integrity(
        "/weights", repository_id=MOSS_AUDIO_TOKENIZER_REPOSITORY,
        revision_sha=MOSS_AUDIO_TOKENIZER_REVISION,
        expected_paths=sorted(upstream),
        upstream_files=list(upstream.values()),
    )
    moss_audio_tokenizer_volume.commit()
    print("Verified MOSS codec checkpoint committed to Modal Volume")


@app.function(
    image=model_download_image, secrets=[HUGGING_FACE_AUTHENTICATION_SECRET],
    volumes={"/weights": voxcpm2_model_weights_volume}, cpu=4, memory=8192, timeout=7200,
)
def download_voxcpm2_model():
    """Explicit CPU-only download for openbmb/VoxCPM2."""
    download_model_checkpoint("openbmb/VoxCPM2", voxcpm2_model_weights_volume)


@app.function(
    image=higgs_tts_3_model_download_image, secrets=[HUGGING_FACE_AUTHENTICATION_SECRET],
    volumes={"/weights": higgs_tts_3_model_weights_volume}, cpu=4, memory=8192, timeout=7200,
)
def download_higgs_tts_3_model():
    """Higgs TTS 3 requires a separately authorized model-license decision."""
    if os.environ.get("HIGGS_TTS_3_LICENSE_CONFIRMED") != "1":
        raise RuntimeError(
            "Set HIGGS_TTS_3_LICENSE_CONFIRMED=1 only after reviewing the "
            "model license for the intended deployment."
        )
    download_model_checkpoint("bosonai/higgs-tts-3-4b", higgs_tts_3_model_weights_volume)


class _SGLangSpeechServer:
    """Runs a private HTTP serving process within ONE GPU class container."""

    def start(self, *, model_configuration_path: str | None = None):
        import httpx

        reference_dir = Path("/tmp/tts_refs")
        reference_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        reference_dir.chmod(0o700)
        command = [
            "sgl-omni", "serve", "--model-path", "/weights",
            "--allowed-local-media-path", str(reference_dir), "--host", "127.0.0.1", "--port", "8000",
        ]
        if model_configuration_path is not None:
            configuration_file = Path(model_configuration_path)
            if not configuration_file.is_file():
                raise RuntimeError("SGLang-Omni model configuration file is missing")
            command.extend(["--config", str(configuration_file)])
        # The child process requires this file handle until stop(); a with-block
        # would close it before the lifetime of the speech server ends.
        self.log = open("/tmp/sglang-omni.log", "ab", buffering=0)  # noqa: SIM115
        try:
            self.server = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 780
            while time.monotonic() < deadline:
                if self.server.poll() is not None:
                    raise RuntimeError("SGLang-Omni exited during startup. See Modal container logs")
                try:
                    response = httpx.get("http://127.0.0.1:8000/health", timeout=3)
                    if response.status_code == 200:
                        # Resolve the served id rather than assuming the Hub repository id.
                        self.served_model_id = "/weights"
                        try:
                            listing = httpx.get("http://127.0.0.1:8000/v1/models", timeout=3)
                            listing.raise_for_status()
                            self.served_model_id = listing.json()["data"][0]["id"]
                        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
                            pass
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(3)
            raise TimeoutError("SGLang-Omni startup timeout")
        except Exception:
            self.stop()  # Release the process/log after a failed startup.
            raise

    def stop(self):
        if getattr(self, "server", None) and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server.kill()
        if getattr(self, "log", None):
            self.log.close()

    def synthesize_speech(self, job: dict, model_adapter):
        import httpx

        start = time.monotonic()
        reference_directory = Path("/tmp/tts_refs")
        reference_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        reference_directory.chmod(0o700)
        with tempfile.TemporaryDirectory(dir=str(reference_directory)) as temporary_directory:
            reference = None
            if job.get("reference_audio"):
                reference = str(Path(temporary_directory) / "consented-reference.wav")
                Path(reference).write_bytes(job["reference_audio"])
            payload = model_adapter(job, reference)
            payload["model"] = self.served_model_id
            with httpx.Client(timeout=600) as client:
                response = client.post("http://127.0.0.1:8000/v1/audio/speech", json=payload)
                response.raise_for_status()
                wav = response.content
        if not wav or len(wav) > 32_000_000:
            raise ValueError("Empty or oversized generated WAV (maximum 32 MB)")
        return {"audio": wav, "metadata": wave_metadata(wav), "inference_ms": round(1000 * (time.monotonic() - start), 1)}


@app.cls(
    image=sglang_omni_worker_image, gpu=os.getenv("MOSS_TTS_GPU_TYPE", "L40S"),
    volumes={
        "/weights": moss_tts_model_weights_volume,
        "/codec": moss_audio_tokenizer_volume,
    }, max_containers=1,
    min_containers=0, scaledown_window=180, timeout=900, startup_timeout=900,
)
class MossTtsWorker(_SGLangSpeechServer):
    @modal.enter()
    def setup(self):
        # MOSS uses a model-specific pipeline topology; never silently start
        # with the generic SGLang-Omni layout if the audited config is missing.
        if "moss-tts-v1.5" not in operator_enabled_models:
            raise RuntimeError("MOSS-TTS-v1.5 is not selected for this deployment")
        from tts_inference.checkpoint_integrity import verify_checkpoint_integrity

        verify_checkpoint_integrity(
            "/weights", repository_id="OpenMOSS-Team/MOSS-TTS-v1.5",
            expected_revision_sha=os.getenv("TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA") or None,
        )
        verify_checkpoint_integrity(
            "/codec", repository_id=MOSS_AUDIO_TOKENIZER_REPOSITORY,
            expected_revision_sha=MOSS_AUDIO_TOKENIZER_REVISION,
        )
        self.start(model_configuration_path="/opt/text-to-speech/configs/moss_tts.yaml")

    @modal.exit()
    def teardown(self):
        self.stop()

    @modal.method()
    def generate(self, job: dict) -> dict:
        return self.synthesize_speech(job, moss_tts_payload)


@app.cls(
    image=higgs_tts_3_worker_image, gpu=os.getenv("HIGGS_TTS_3_GPU_TYPE", "L40S"),
    volumes={"/weights": higgs_tts_3_model_weights_volume}, max_containers=1,
    min_containers=0, scaledown_window=180, timeout=900, startup_timeout=900,
)
class HiggsTts3Worker(_SGLangSpeechServer):
    @modal.enter()
    def setup(self):
        if "higgs-tts-3" not in operator_enabled_models:
            raise RuntimeError("Higgs TTS 3 is not selected for this deployment")
        if os.environ.get("HIGGS_TTS_3_LICENSE_CONFIRMED") != "1":
            raise RuntimeError("Higgs TTS 3 license confirmation required")
        if not sglang_container_reference:
            raise RuntimeError("Audited SGLang-Omni SHA-256 image digest is required")
        from tts_inference.checkpoint_integrity import verify_checkpoint_integrity

        verify_checkpoint_integrity(
            "/weights", repository_id="bosonai/higgs-tts-3-4b",
            expected_revision_sha=os.getenv("TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA") or None,
        )
        self.start()

    @modal.exit()
    def teardown(self):
        self.stop()

    @modal.method()
    def generate(self, job: dict) -> dict:
        return self.synthesize_speech(job, higgs_tts_3_payload)


@app.cls(
    image=voxcpm2_worker_image, gpu=os.getenv("VOXCPM2_GPU_TYPE", "L40S"),
    volumes={"/weights": voxcpm2_model_weights_volume}, max_containers=1,
    min_containers=0, scaledown_window=180, timeout=900, startup_timeout=900,
)
class VoxCPM2Worker:
    @modal.enter()
    def setup(self):
        if "voxcpm2" not in operator_enabled_models:
            raise RuntimeError("VoxCPM2 is not selected for this deployment")
        from tts_inference.checkpoint_integrity import verify_checkpoint_integrity

        verify_checkpoint_integrity(
            "/weights", repository_id="openbmb/VoxCPM2",
            expected_revision_sha=os.getenv("TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA") or None,
        )
        from voxcpm import VoxCPM

        # Local-only weights: a download is a separate explicit operator action.
        self.model = VoxCPM.from_pretrained(
            "/weights", load_denoiser=False, optimize=False,
            local_files_only=True, device="cuda",
        )

    @modal.method()
    def generate(self, job: dict) -> dict:
        import io

        import soundfile

        model_options = job["model_config"]
        if model_options["denoise"]:
            raise ValueError("denoise requires an optional VoxCPM denoiser; disabled in this deployment")
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as temporary_directory:
            reference = None
            if job.get("reference_audio"):
                reference = str(Path(temporary_directory) / "consented-reference.wav")
                Path(reference).write_bytes(job["reference_audio"])
            model_generation_parameters = voxcpm2_payload(job, reference)
            samples = self.model.generate(**model_generation_parameters)
        sink = io.BytesIO()
        soundfile.write(sink, samples, self.model.tts_model.sample_rate, format="WAV", subtype="PCM_16")
        wav = sink.getvalue()
        if not wav or len(wav) > 32_000_000:
            raise ValueError("Empty or oversized generated WAV (maximum 32 MB)")
        return {"audio": wav, "metadata": wave_metadata(wav), "inference_ms": round(1000 * (time.monotonic() - start), 1)}


# The gateway and CPU queue coordinator share PostgreSQL, but only the HTTP
# gateway receives authentication and Redis credentials. GPUs receive none.
JOB_DATABASE_SECRET = modal.Secret.from_name("text-to-speech-job-database")
API_AUTHENTICATION_SECRET = modal.Secret.from_name("text-to-speech-api-authentication")
RATE_LIMITING_SECRET = modal.Secret.from_name("text-to-speech-rate-limiting")


def enabled_model_ids_from_runtime_image() -> set[str]:
    """Only models with deployment-pinned runtimes can be admitted on CPU."""
    return {
        model_id
        for model_id, environment_name in (
            ("moss-tts-v1.5", "TEXT_TO_SPEECH_MOSS_TTS_ENABLED"),
            ("higgs-tts-3", "TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED"),
            ("voxcpm2", "TEXT_TO_SPEECH_VOXCPM2_ENABLED"),
        )
        if os.getenv(environment_name) == "1"
    }


def create_job_store():
    from tts_inference.gpu_budget import parse_estimated_gpu_budget
    from tts_inference.persistence import JobStore

    per_job_estimate, maximum_daily_estimate = parse_estimated_gpu_budget(
        os.getenv("TEXT_TO_SPEECH_ESTIMATED_GPU_USD_PER_JOB"),
        os.getenv("TEXT_TO_SPEECH_MAX_ESTIMATED_GPU_USD_PER_DAY"),
    )
    store = JobStore(
        os.environ["TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL"], os.environ["TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY"],
        max_pending_per_owner=int(os.getenv("TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT", "20")),
        max_pending_per_model=int(os.getenv("TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL", "20")),
        max_running_per_model=1, stale_after_seconds=180,
        estimated_gpu_micro_usd_per_job=per_job_estimate,
        maximum_estimated_gpu_micro_usd_per_day=maximum_daily_estimate,
    )
    store.migrate_legacy_payload_fingerprints()
    return store


async def maintain_job_heartbeat(job_store, job_id: str, stop_event: asyncio.Event):
    # Join this task BEFORE the terminal result write so it cannot overwrite it.
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except TimeoutError:
            try:
                await asyncio.to_thread(job_store.heartbeat, job_id)
            except Exception:  # noqa: BLE001 - preserve uncertain GPU outcome.
                # No raw database error, request content or secret in logs.
                logger.warning("tts_heartbeat_persist_failed")


@app.function(
    image=gateway_and_dispatcher_image, secrets=[JOB_DATABASE_SECRET], cpu=1, memory=1024,
    max_containers=6, min_containers=0, timeout=1800,
)
async def dispatch_text_to_speech_job(job_id: str):
    """Durable CPU-only dispatcher; calls exactly one selected GPU worker.

    A claim is made once under the PostgreSQL admission lock. An uncertain
    remote GPU outcome is NEVER auto-retried. Manual review required.
    """
    store = create_job_store()
    enabled_models = enabled_model_ids_from_runtime_image()
    claimed = await asyncio.to_thread(store.claim, job_id, enabled_models=enabled_models)
    if not claimed:
        return  # Duplicate wake or per-model concurrency already occupied.
    job, model = claimed
    worker_classes = {
        "moss-tts-v1.5": MossTtsWorker, "higgs-tts-3": HiggsTts3Worker,
        "voxcpm2": VoxCPM2Worker,
    }
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(maintain_job_heartbeat(store, job_id, stop))
    outcome_unknown = False
    try:
        worker_start = time.monotonic()
        generated_audio_result = await worker_classes[model]().generate.remote.aio(job)
        worker_wall_ms = (time.monotonic() - worker_start) * 1000
        # This is a real WAV from the GPU worker, never a mock-generated file.
        from tts_inference.wav import wave_metadata
        audio = generated_audio_result["audio"]
        validation_start = time.monotonic()
        verified = wave_metadata(audio)
        audio_validation_ms = (time.monotonic() - validation_start) * 1000
    except Exception:  # noqa: BLE001 - a remote GPU outcome may be unknown.
        logger.warning("tts_worker_result_unknown")
        outcome_unknown = True
    finally:
        stop.set()
        await heartbeat_task  # No heartbeat write can overtake a terminal-state write.

    if outcome_unknown:
        # Lost Modal acknowledgement may mean the GPU is STILL running.
        # Retain per-model admission slot pending operator reconciliation.
        await asyncio.to_thread(store.uncertain, job_id)
    else:
        try:
            await asyncio.to_thread(
                store.complete, job_id, audio, verified, generated_audio_result["inference_ms"],
                worker_wall_ms=worker_wall_ms, audio_validation_ms=audio_validation_ms,
            )
        except Exception:  # noqa: BLE001 - failed commit cannot trigger retry.
            logger.error("tts_completed_audio_commit_failed")
            # A failed commit must not authorize rerunning the paid GPU call.
            try:
                await asyncio.to_thread(store.uncertain, job_id, code="output_commit_unknown")
            except Exception:  # noqa: BLE001 - keep slot until manual review.
                logger.error("tts_uncertain_state_persist_failed")

    # Advance the durable queue without starting a different model's GPU.
    for next_id in await asyncio.to_thread(store.queued_ids, 1, model):
        await dispatch_text_to_speech_job.spawn.aio(next_id)


@app.function(
    image=gateway_and_dispatcher_image, secrets=[JOB_DATABASE_SECRET], cpu=1, memory=512,
    schedule=modal.Period(minutes=1), min_containers=0, timeout=120,
)
async def resume_queued_text_to_speech_jobs():
    """CPU-only backstop for accepted jobs whose initial wake acknowledgement was lost."""
    store = create_job_store()
    enabled_models = enabled_model_ids_from_runtime_image()
    ready_job_ids = await asyncio.to_thread(
        store.queued_ids_with_available_model_slots, enabled_models,
    )
    for job_id in ready_job_ids:
        await dispatch_text_to_speech_job.spawn.aio(job_id)
    # Does not retry RUNNING or INDETERMINATE jobs.
    await asyncio.to_thread(store.delete_expired_terminal, int(os.getenv("TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS", "7")))


def _operations_monitor_schedule():
    """Opt-in CPU-only hourly monitoring; no scheduled cost by default."""
    mode = os.getenv("TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE", "off")
    if mode not in {"off", "hourly"}:
        raise ValueError("Operations monitor schedule must be 'off' or 'hourly'")
    return modal.Period(minutes=60) if mode == "hourly" else None


@app.function(
    image=gateway_and_dispatcher_image, secrets=[JOB_DATABASE_SECRET],
    cpu=1, memory=512, min_containers=0, timeout=120,
    schedule=_operations_monitor_schedule(),
)
def text_to_speech_operations_safety_monitor():
    """Read-only aggregate state; raises a Modal alert without retrying GPU."""
    from tts_inference.operations import collect_operations_snapshot
    from tts_inference.persistence import JobStore

    store = JobStore(
        os.environ["TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL"],
        os.environ["TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY"],
    )
    snapshot = collect_operations_snapshot(store)
    print(json.dumps(snapshot, sort_keys=True))
    if snapshot["status"] != "ok":
        raise RuntimeError("TTS operations monitor found unresolved workloads")
    return snapshot


@app.function(
    image=gateway_and_dispatcher_image, cpu=1, memory=1024, max_containers=2,
    min_containers=0, secrets=[API_AUTHENTICATION_SECRET, JOB_DATABASE_SECRET, RATE_LIMITING_SECRET],
)
@modal.asgi_app()
def text_to_speech_api():
    import redis.asyncio as redis

    from tts_inference.contracts import ModelID
    from tts_inference.gateway import create_gateway
    from tts_inference.security import RatePolicy, RedisGuard

    store = create_job_store()
    if not store.check_ready():
        raise RuntimeError("Database migrations must be applied before deployment")
    redis_url = os.environ["TEXT_TO_SPEECH_REDIS_CONNECTION_URL"]
    if not redis_url.startswith("rediss://"):
        raise RuntimeError("Production Redis must use rediss:// (TLS)")
    guard = RedisGuard(
        redis.from_url(redis_url, decode_responses=True),
        namespace=os.getenv("TEXT_TO_SPEECH_REDIS_RATE_LIMIT_NAMESPACE", "tts-v1"),
        policy=RatePolicy.from_env(),
    )

    async def wake(job_id: str):
        await dispatch_text_to_speech_job.spawn.aio(job_id)

    enabled_models = {
        ModelID(model_id) for model_id in enabled_model_ids_from_runtime_image()
    }
    return create_gateway(
        job_store=store, guard=guard, wake_job=wake, production=True,
        enabled_models=enabled_models,
    )
