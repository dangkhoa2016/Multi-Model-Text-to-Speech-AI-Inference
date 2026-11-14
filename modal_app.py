"""Modal deployment: one CPU gateway, three isolated GPU pools and weight Volumes.

NO GPU launches on import, CI, or `python -m compileall`.
Downloading is only performed by explicitly invoking the download_* functions.
"""

import os
import subprocess
import tempfile
import time
import asyncio
from pathlib import Path

import modal

from tts_inference.adapters import higgs_tts_3_payload, moss_tts_payload, voxcpm2_payload
from tts_inference.wav import wave_metadata

MODAL_APPLICATION_NAME = "text-to-speech-ai-inference"
app = modal.App(MODAL_APPLICATION_NAME)

moss_tts_model_weights_volume = modal.Volume.from_name("text-to-speech-moss-tts-v1-5-model-weights", create_if_missing=True)
higgs_tts_3_model_weights_volume = modal.Volume.from_name("text-to-speech-higgs-tts-3-model-weights", create_if_missing=True)
voxcpm2_model_weights_volume = modal.Volume.from_name("text-to-speech-voxcpm2-model-weights", create_if_missing=True)

# CUDA dependencies are in the upstream container, but SGLang-Omni itself
# must be installed explicitly (per the upstream installation guide).
# Before production, pin the audited registry digest and all dependencies.
sglang_omni_worker_image = (
    modal.Image.from_registry("lmsysorg/sglang-omni:dev")
    .pip_install("sglang-omni==0.2.0", "httpx>=0.27,<1", pre=True)
    .add_local_file("configs/moss_tts.yaml", "/opt/text-to-speech/configs/moss_tts.yaml")
    .add_local_python_source("tts_inference")
)
voxcpm2_worker_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install("voxcpm", "soundfile", "huggingface_hub>=0.33,<2")
    .add_local_python_source("tts_inference")
)
gateway_and_dispatcher_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi>=0.115,<1", "pydantic>=2.10,<3", "httpx>=0.27,<1",
        "sqlalchemy>=2.0,<3", "psycopg[binary]>=3.2,<4", "redis>=5,<8",
        "cryptography>=43,<47",
    )
    .add_local_python_source("tts_inference")
)
model_download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub>=0.33,<2")
    .add_local_python_source("tts_inference")
)
# Licensing is a deploy-time decision, not a client request or a secret.
# The same explicit decision is embedded in both CPU admission and the Higgs worker.
# Changing it requires a fresh deployment. Never default to accepting the license.
higgs_tts_3_license_confirmed = os.getenv("HIGGS_TTS_3_LICENSE_CONFIRMED") == "1"
higgs_tts_3_license_environment = {
    "HIGGS_TTS_3_LICENSE_CONFIRMED": "1" if higgs_tts_3_license_confirmed else "0"
}
gateway_and_dispatcher_image = gateway_and_dispatcher_image.env(
    {"TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED": higgs_tts_3_license_environment["HIGGS_TTS_3_LICENSE_CONFIRMED"]}
)
higgs_tts_3_worker_image = sglang_omni_worker_image.env(higgs_tts_3_license_environment)
higgs_tts_3_model_download_image = model_download_image.env(higgs_tts_3_license_environment)


# Read access uses a dedicated Secret only in CPU-only download/preflight jobs.
# No Hugging Face credentials are passed to the public gateway or GPU workers.
HUGGING_FACE_AUTHENTICATION_SECRET = modal.Secret.from_name(
    "text-to-speech-hugging-face-authentication",
    required_keys=["HUGGING_FACE_READ_TOKEN"],
)


def download_model_checkpoint(repository_id: str, model_weights_volume: modal.Volume):
    """Authorize and pin a revision before downloading into one private Volume."""
    from huggingface_hub import snapshot_download
    from tts_inference.hugging_face_access import check_model_download_access

    hugging_face_token = os.environ.get("HUGGING_FACE_READ_TOKEN")
    access = check_model_download_access(
        repository_id, hugging_face_token=hugging_face_token
    )
    print(f"Verified access to {repository_id} at revision {access.revision_sha}")
    snapshot_download(
        repo_id=repository_id,
        revision=access.revision_sha,
        local_dir="/weights",
        token=hugging_face_token,
        max_workers=4,
    )
    model_weights_volume.commit()
    print(f"Committed the checkpoint for {repository_id} to its dedicated Modal Volume")


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
    from tts_inference.hugging_face_access import check_model_download_access

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
            hugging_face_token=os.environ.get("HUGGING_FACE_READ_TOKEN"),
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
        self.log = open("/tmp/sglang-omni.log", "ab", buffering=0)
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
    volumes={"/weights": moss_tts_model_weights_volume}, max_containers=1,
    min_containers=0, scaledown_window=180, timeout=900, startup_timeout=900,
)
class MossTtsWorker(_SGLangSpeechServer):
    @modal.enter()
    def setup(self):
        # MOSS uses a model-specific pipeline topology; never silently start
        # with the generic SGLang-Omni layout if the audited config is missing.
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
        if os.environ.get("HIGGS_TTS_3_LICENSE_CONFIRMED") != "1":
            raise RuntimeError("Higgs TTS 3 license confirmation required")
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
        from voxcpm import VoxCPM

        # Local-only weights: a download is a separate explicit operator action.
        self.model = VoxCPM.from_pretrained(
            "/weights", load_denoiser=False, optimize=False,
            local_files_only=True, device="cuda",
        )

    @modal.method()
    def generate(self, job: dict) -> dict:
        import io
        import soundfile as soundfile

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


def create_job_store():
    from tts_inference.persistence import JobStore

    return JobStore(
        os.environ["TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL"], os.environ["TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY"],
        max_pending_per_owner=int(os.getenv("TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT", "20")),
        max_pending_per_model=int(os.getenv("TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL", "20")),
        max_running_per_model=1, stale_after_seconds=180,
    )


async def maintain_job_heartbeat(job_store, job_id: str, stop_event: asyncio.Event):
    # Join this task BEFORE the terminal result write so it cannot overwrite it.
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            try:
                await asyncio.to_thread(job_store.heartbeat, job_id)
            except Exception:
                # A lost database heartbeat is operationally uncertain.
                # The GPU request may still finish and write a late success.
                pass


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
    enabled_models = {"moss-tts-v1.5", "voxcpm2"}
    if os.getenv("TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED") == "1":
        enabled_models.add("higgs-tts-3")
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
        generated_audio_result = await worker_classes[model]().generate.remote.aio(job)
        # This is a real WAV from the GPU worker, never a mock-generated file.
        from tts_inference.wav import wave_metadata
        audio = generated_audio_result["audio"]
        verified = wave_metadata(audio)
    except Exception:
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
            await asyncio.to_thread(store.complete, job_id, audio, verified, generated_audio_result["inference_ms"])
        except Exception:
            # A failed commit must not authorize rerunning the paid GPU call.
            try:
                await asyncio.to_thread(store.uncertain, job_id, code="output_commit_unknown")
            except Exception:
                pass

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
    for job_id in await asyncio.to_thread(store.queued_ids, 30):
        await dispatch_text_to_speech_job.spawn.aio(job_id)
    # Does not retry RUNNING or INDETERMINATE jobs.
    await asyncio.to_thread(store.delete_expired_terminal, int(os.getenv("TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS", "7")))


@app.function(
    image=gateway_and_dispatcher_image, cpu=1, memory=1024, max_containers=2,
    min_containers=0, secrets=[API_AUTHENTICATION_SECRET, JOB_DATABASE_SECRET, RATE_LIMITING_SECRET],
)
@modal.asgi_app()
def text_to_speech_api():
    import redis.asyncio as redis

    from tts_inference.contracts import ModelID
    from tts_inference.gateway import create_gateway
    from tts_inference.security import RedisGuard, RatePolicy

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

    enabled_models = {ModelID.MOSS_TTS, ModelID.VOXCPM2}
    if os.getenv("TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED") == "1":
        enabled_models.add(ModelID.HIGGS_TTS_3)
    return create_gateway(
        job_store=store, guard=guard, wake_job=wake, production=True,
        enabled_models=enabled_models,
    )
