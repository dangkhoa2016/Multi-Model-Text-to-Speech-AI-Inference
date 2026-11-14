"""Import and inspect Modal declarations without installing Modal or starting workers."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.mock import mock_wav


def load_mocked_modal_deployment(monkeypatch, registry_calls=None):
    class Fluent:
        def __init__(self, name=""):
            self.name = name
            self.secrets = []
            self.mounts = []
            self.local_sources = []
            self.environment = {}
            self.installed_packages = []

        def clone(self):
            image = Fluent(self.name)
            image.secrets = self.secrets.copy()
            image.mounts = self.mounts.copy()
            image.local_sources = self.local_sources.copy()
            image.environment = self.environment.copy()
            image.installed_packages = self.installed_packages.copy()
            return image

        def pip_install(self, *args, **kwargs):
            image = self.clone()
            image.installed_packages.extend(args)
            return image

        def apt_install(self, *args, **kwargs):
            return self.clone()

        def uv_pip_install(self, *args, **kwargs):
            image = self.clone()
            image.installed_packages.append(
                ("uv", tuple(kwargs.get("requirements", ())),
                 kwargs.get("uv_version"), kwargs.get("extra_options"),
                 kwargs.get("pre", False))
            )
            return image

        def add_local_file(self, local_path, remote_path, **kwargs):
            image = self.clone()
            image.mounts.append((local_path, remote_path))
            return image

        def run_commands(self, *commands):
            image = self.clone()
            return image

        def add_local_python_source(self, *sources):
            image = self.clone()
            image.local_sources.extend(sources)
            return image

        def env(self, variables):
            if self.mounts or self.local_sources:
                raise AssertionError(
                    "Modal image build steps must precede add_local_* source mounts"
                )
            image = self.clone()
            image.environment.update(variables)
            return image

    class FakeImage:
        @staticmethod
        def debian_slim(**kwargs):
            return Fluent("debian")

        @staticmethod
        def from_registry(image):
            if registry_calls is not None:
                registry_calls.append(image)
            return Fluent(image)

    class FakeSecret:
        @staticmethod
        def from_name(name, **kwargs):
            return {"name": name, "required_keys": kwargs.get("required_keys", [])}

    class FakeVolume:
        @staticmethod
        def from_name(name, **kwargs):
            return {"name": name}

    class FakeApp:
        def __init__(self, name):
            self.name = name
            self.declarations = []

        def function(self, **kwargs):
            self.declarations.append(("function", kwargs))
            return lambda function: function

        def cls(self, **kwargs):
            self.declarations.append(("class", kwargs))
            return lambda cls: cls

    modal_stub = types.ModuleType("modal")
    modal_stub.App = FakeApp
    modal_stub.Image = FakeImage
    modal_stub.Secret = FakeSecret
    modal_stub.Volume = FakeVolume
    modal_stub.Period = lambda **kwargs: kwargs
    modal_stub.asgi_app = lambda: lambda function: function
    modal_stub.enter = lambda: lambda function: function
    modal_stub.exit = lambda: lambda function: function
    modal_stub.method = lambda: lambda function: function

    monkeypatch.setitem(sys.modules, "modal", modal_stub)
    module_path = Path(__file__).resolve().parents[1] / "modal_app.py"
    specification = importlib.util.spec_from_file_location("mocked_modal_deployment", module_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_modal_image_source_mounts_are_last_and_license_is_scoped(monkeypatch):
    """The Modal mock detects image build steps after source mounts."""
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "1")
    module = load_mocked_modal_deployment(monkeypatch)
    images = (
        module.gateway_and_dispatcher_image,
        module.model_download_image,
        module.higgs_tts_3_model_download_image,
        module.voxcpm2_worker_image,
        module.sglang_omni_worker_image,
        module.higgs_tts_3_worker_image,
    )
    assert all("tts_inference" in image.local_sources for image in images)
    assert module.higgs_tts_3_worker_image.environment[
        "HIGGS_TTS_3_LICENSE_CONFIRMED"
    ] == "1"
    assert module.higgs_tts_3_model_download_image.environment[
        "HIGGS_TTS_3_LICENSE_CONFIRMED"
    ] == "1"
    assert "HIGGS_TTS_3_LICENSE_CONFIRMED" not in module.sglang_omni_worker_image.environment
    assert "HIGGS_TTS_3_LICENSE_CONFIRMED" not in module.model_download_image.environment
    assert module.higgs_tts_3_worker_image.mounts == module.sglang_omni_worker_image.mounts


def test_all_modal_runtime_images_ship_verified_dependency_lockfiles(monkeypatch):
    """Every Modal function imports modal_app.py; neither lock may be missing."""
    from tts_inference.runtime_locks import MOSS_LOCK, VOXCPM2_LOCK

    module = load_mocked_modal_deployment(monkeypatch)
    required_mounts = {
        (MOSS_LOCK, "/root/" + MOSS_LOCK),
        (VOXCPM2_LOCK, "/root/" + VOXCPM2_LOCK),
    }
    images = (
        module.gateway_and_dispatcher_image,
        module.model_download_image,
        module.higgs_tts_3_model_download_image,
        module.voxcpm2_worker_image,
        module.sglang_omni_worker_image,
        module.higgs_tts_3_worker_image,
    )
    for image in images:
        assert required_mounts.issubset(set(image.mounts))


def test_modal_declarations_resolve_without_running_gpu(monkeypatch):
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.app.name == "text-to-speech-ai-inference"
    assert "tts_inference" in module.model_download_image.local_sources
    assert module.HUGGING_FACE_AUTHENTICATION_SECRET["required_keys"] == []
    assert "hf_xet>=1.7,<2" in module.model_download_image.installed_packages
    assert module.moss_tts_model_weights_volume["name"].startswith("text-to-speech-moss")
    assert module.higgs_tts_3_model_weights_volume["name"].startswith("text-to-speech-higgs")
    assert module.voxcpm2_model_weights_volume["name"].startswith("text-to-speech-voxcpm2")
    assert callable(module.download_moss_tts_model)
    assert callable(module.download_moss_audio_tokenizer)
    assert module.moss_audio_tokenizer_volume["name"] == "text-to-speech-moss-audio-tokenizer-weights"
    assert module.MOSS_AUDIO_TOKENIZER_REVISION == "3cd226ba2947efa357ef453bcad111b6eafba782"
    assert callable(module.download_higgs_tts_3_model)
    assert callable(module.download_voxcpm2_model)
    assert callable(module.check_hugging_face_model_access)
    assert callable(module.dispatch_text_to_speech_job)
    assert callable(module.resume_queued_text_to_speech_jobs)
    assert callable(module.text_to_speech_api)

    gpu_classes = [value for label, value in module.app.declarations if label == "class"]
    assert len(gpu_classes) == 3
    assert all(declaration["min_containers"] == 0 for declaration in gpu_classes)
    assert all(not declaration.get("secrets") for declaration in gpu_classes)
    preflight_configuration = next(
        declaration
        for label, declaration in module.app.declarations
        if label == "function" and module.HUGGING_FACE_AUTHENTICATION_SECRET in declaration.get("secrets", [])
    )
    assert "gpu" not in preflight_configuration


@pytest.mark.parametrize(
    ("sglang", "voxcpm", "higgs_license", "expected_models"),
    [
        (False, False, False, set()),
        (True, False, False, {"moss-tts-v1.5"}),
        (False, True, False, {"voxcpm2"}),
        (True, True, False, {"moss-tts-v1.5", "voxcpm2"}),
        (True, True, True, {"moss-tts-v1.5", "higgs-tts-3", "voxcpm2"}),
        (False, True, True, {"voxcpm2"}),
    ],
)
def test_cpu_admission_requires_audited_runtime_before_gpu(
    monkeypatch, sglang, voxcpm, higgs_license, expected_models,
):
    """Absent runtime artifacts disable model admission before any paid GPU work."""
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF",
        "lmsysorg/sglang-omni@sha256:" + "a" * 64 if sglang else "",
    )
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", ",".join(sorted(expected_models)))
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "1" if higgs_license else "0")
    for name in (
        "TEXT_TO_SPEECH_MOSS_TTS_ENABLED",
        "TEXT_TO_SPEECH_HIGGS_TTS_3_ENABLED",
        "TEXT_TO_SPEECH_VOXCPM2_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    module = load_mocked_modal_deployment(monkeypatch)
    flags = module.runtime_model_enablement_environment
    assert module.gateway_and_dispatcher_image.environment == {
        **module.model_selection_environment,
        **flags, **module.gateway_runtime_policy_environment,
    }
    assert module.sglang_omni_worker_image.environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == (
        module.model_selection_environment["TEXT_TO_SPEECH_ENABLED_MODELS"]
    )
    assert module.voxcpm2_worker_image.environment["TEXT_TO_SPEECH_ENABLED_MODELS"] == (
        module.model_selection_environment["TEXT_TO_SPEECH_ENABLED_MODELS"]
    )
    for name, enabled in flags.items():
        monkeypatch.setenv(name, enabled)
    assert module.enabled_model_ids_from_runtime_image() == expected_models


@pytest.mark.parametrize("model_id", ["moss-tts-v1.5", "higgs-tts-3"])
def test_sglang_reference_audio_is_read_then_removed_without_gpu(monkeypatch, tmp_path, model_id):
    """Mock the HTTP speech server and check private reference-file lifetime."""
    module = load_mocked_modal_deployment(monkeypatch)
    import httpx

    sample = mock_wav()
    sent_paths = []
    reference_root = tmp_path / "tts_refs"
    # The worker uses this fixed private path, redirected only in the test.
    original_path = module.Path

    def remap_path(value):
        if value == "/tmp/tts_refs":
            return reference_root
        return original_path(value)

    monkeypatch.setattr(module, "Path", remap_path)

    class FakeResponse:
        content = sample

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, json):
            assert url == "http://127.0.0.1:8000/v1/audio/speech"
            reference = json["references"][0]["audio_path"]
            sent_paths.append(reference)
            assert Path(reference).read_bytes() == sample
            assert json["references"][0]["text"] == "Authorized recording"
            assert json["model"] == "/weights"
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    from tts_inference.adapters import higgs_tts_3_payload, moss_tts_payload

    request = SpeechRequest.model_validate({
        "model": model_id, "input": "Read this sentence",
        "reference_audio_base64": __import__("base64").b64encode(sample).decode(),
        "reference_text": "Authorized recording", "reference_consent": True,
    })
    job = prepare_request(request)
    worker = module._SGLangSpeechServer()
    worker.served_model_id = "/weights"
    adapter = moss_tts_payload if model_id == "moss-tts-v1.5" else higgs_tts_3_payload
    result = worker.synthesize_speech(job, adapter)
    assert result["audio"] == sample
    assert result["metadata"]["sample_rate"] > 0
    assert len(sent_paths) == 1
    assert not Path(sent_paths[0]).exists()
    assert list(reference_root.iterdir()) == []


def test_moss_pipeline_configuration_is_bundled_and_required(monkeypatch, tmp_path):
    module = load_mocked_modal_deployment(monkeypatch)
    assert ("configs/moss_tts.yaml", "/opt/text-to-speech/configs/moss_tts.yaml") in module.sglang_omni_worker_image.mounts
    assert "config_cls: MossTTSSingleProcessPipelineConfig" in (Path(__file__).parents[1] / "configs/moss_tts.yaml").read_text()
    configuration = (Path(__file__).parents[1] / "configs/moss_tts.yaml").read_text()
    assert configuration.count("codec_model_path: /codec") == 2
    gpu_classes = [
        entry for kind, entry in module.app.declarations
        if kind == "class" and entry["image"] is module.sglang_omni_worker_image
    ]
    assert len(gpu_classes) == 1
    assert gpu_classes[0]["volumes"]["/codec"] is module.moss_audio_tokenizer_volume
    assert module.sglang_omni_worker_image.environment["HF_HUB_OFFLINE"] == "1"
    worker = module._SGLangSpeechServer()
    with pytest.raises(RuntimeError, match="configuration file is missing"):
        worker.start(model_configuration_path=str(tmp_path / "not-there.yaml"))


def test_higgs_worker_requires_selection_and_runtime_license(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_ENABLED_MODELS", raising=False)
    monkeypatch.delenv("HIGGS_TTS_3_LICENSE_CONFIRMED", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    with pytest.raises(RuntimeError, match="not selected"):
        module.HiggsTts3Worker().setup()

    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "higgs-tts-3")
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "1")
    monkeypatch.setenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF",
                       "lmsysorg/sglang-omni@sha256:" + "a" * 64)
    module = load_mocked_modal_deployment(monkeypatch)
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", "0")
    with pytest.raises(RuntimeError, match="license confirmation required"):
        module.HiggsTts3Worker().setup()
    assert module.higgs_tts_3_license_confirmed


def test_sglang_startup_uses_config_and_releases_failed_process(monkeypatch, tmp_path):
    module = load_mocked_modal_deployment(monkeypatch)
    import httpx

    commands = []
    processes = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            commands.append(command)
            processes.append(self)
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    original_path = module.Path
    reference_root = tmp_path / "reference"
    monkeypatch.setattr(module, "Path", lambda value: reference_root if value == "/tmp/tts_refs" else original_path(value))
    configuration = tmp_path / "moss.yaml"
    configuration.write_text("config_cls: MossTTSPipelineConfig\nmodel_path: /weights\n")

    class SuccessResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "served-model"}]}

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: SuccessResponse())
    worker = module._SGLangSpeechServer()
    worker.start(model_configuration_path=str(configuration))
    assert commands[0][-2:] == ["--config", str(configuration)]
    assert worker.served_model_id == "served-model"
    worker.stop()
    assert processes[0].terminated
    assert worker.log.closed

    def server_unavailable(*_args, **_kwargs):
        raise RuntimeError("unexpected initialization failure")

    monkeypatch.setattr(httpx, "get", server_unavailable)
    second = module._SGLangSpeechServer()
    with pytest.raises(RuntimeError, match="unexpected initialization failure"):
        second.start(model_configuration_path=str(configuration))
    assert processes[1].terminated
    assert second.log.closed


def test_worker_inference_timer_includes_payload_preparation(monkeypatch):
    """The legacy metric measures worker processing, not only the HTTP RPC."""
    module = load_mocked_modal_deployment(monkeypatch)
    import httpx

    clock = [0.0]
    monkeypatch.setattr(
        module, "time", types.SimpleNamespace(monotonic=lambda: clock[0])
    )

    class FakeResponse:
        content = mock_wav()

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, _url, *, json):
            assert json["model"] == "cpu-simulated-model"
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    worker = module._SGLangSpeechServer()
    worker.served_model_id = "cpu-simulated-model"

    def slow_adapter(_job, _reference):
        clock[0] += 2.0
        return {"input": "synthetic CPU test"}

    result = worker.synthesize_speech(
        {"reference_audio": None}, slow_adapter
    )
    assert result["inference_ms"] == 2000.0
    assert result["audio"] == FakeResponse.content


@pytest.mark.parametrize("allowed", [False, True])
def test_cloning_operator_policy_is_captured_in_cpu_image(monkeypatch, allowed):
    monkeypatch.setenv(
        "TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING", "1" if allowed else "0"
    )
    module = load_mocked_modal_deployment(monkeypatch)
    expected = "1" if allowed else "0"
    assert module.gateway_runtime_policy_environment == {
        "TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING": expected,
        "TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON": "[]",
    }
    assert module.gateway_and_dispatcher_image.environment[
        "TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING"
    ] == expected
    # Cloning policy does not implicitly enable unconfigured GPU models.
    assert not module.enabled_model_ids_from_runtime_image()


def test_hugging_face_token_secret_stays_on_cpu_only(monkeypatch):
    module = load_mocked_modal_deployment(monkeypatch)
    secret = module.HUGGING_FACE_AUTHENTICATION_SECRET
    cpu_functions = [
        declaration for kind, declaration in module.app.declarations
        if kind == "function" and secret in declaration.get("secrets", [])
    ]
    assert len(cpu_functions) == 4  # access preflight and three CPU-only downloads
    assert all(not declaration.get("gpu") for declaration in cpu_functions)
    gpu_classes = [
        declaration for kind, declaration in module.app.declarations if kind == "class"
    ]
    assert len(gpu_classes) == 3
    assert all(not declaration.get("secrets") for declaration in gpu_classes)
    gateway_functions = [
        declaration for kind, declaration in module.app.declarations
        if kind == "function"
        and declaration.get("image") is module.gateway_and_dispatcher_image
    ]
    assert gateway_functions
    assert all(secret not in item.get("secrets", []) for item in gateway_functions)


def test_moss_gpu_setup_rejects_unverified_codec_without_starting_server(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "moss-tts-v1.5")
    module = load_mocked_modal_deployment(monkeypatch)
    import tts_inference.checkpoint_integrity as integrity

    checked = []
    started = []
    def verify(root, *, repository_id, expected_revision_sha=None):
        checked.append((root, repository_id, expected_revision_sha))
        if root == "/codec":
            raise integrity.CheckpointIntegrityError("Codec checkpoint unavailable")
        return {"files": []}

    monkeypatch.setattr(integrity, "verify_checkpoint_integrity", verify)
    worker = module.MossTtsWorker()
    monkeypatch.setattr(worker, "start", lambda **kwargs: started.append(kwargs))
    with pytest.raises(integrity.CheckpointIntegrityError, match="Codec checkpoint"):
        worker.setup()

    assert [entry[0] for entry in checked] == ["/weights", "/codec"]
    assert checked[-1][1] == "OpenMOSS-Team/MOSS-Audio-Tokenizer"
    assert checked[-1][2] == module.MOSS_AUDIO_TOKENIZER_REVISION
    assert started == []


def test_codec_download_is_explicit_cpu_only_and_moss_patch_does_not_reach_higgs(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "moss-tts-v1.5")
    module = load_mocked_modal_deployment(monkeypatch)
    cpu_codec_jobs = [
        declaration for kind, declaration in module.app.declarations
        if kind == "function"
        and declaration.get("volumes") == {"/weights": module.moss_audio_tokenizer_volume}
    ]
    assert len(cpu_codec_jobs) == 1
    assert not cpu_codec_jobs[0].get("gpu")
    assert not cpu_codec_jobs[0].get("secrets")
    moss_mounts = module.sglang_omni_worker_image.mounts
    assert ("scripts/sglang_moss_sampling_compat.py",
            "/opt/text-to-speech/sglang_moss_sampling_compat.py") in moss_mounts
    assert ("scripts/sglang_moss_languages_compat.py",
            "/opt/text-to-speech/sglang_moss_languages_compat.py") in moss_mounts
    assert ("scripts/sglang_moss_sampling_compat.py",
            "/opt/text-to-speech/sglang_moss_sampling_compat.py") not in (
                module.higgs_tts_3_worker_image.mounts
            )



def test_moss_selection_requires_real_codec_manifest_not_manual_flag(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "moss-tts-v1.5")
    monkeypatch.delenv("TEXT_TO_SPEECH_MOSS_CODEC_READY", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.runtime_model_enablement_environment["TEXT_TO_SPEECH_MOSS_TTS_ENABLED"] == "1"
    assert "TEXT_TO_SPEECH_MOSS_CODEC_READY" not in module.sglang_omni_worker_image.environment


@pytest.mark.parametrize("invalid", ["moss-tts-v1.5,,voxcpm2", "voxcpm2,voxcpm2", "unknown", "voxcpm2,unknown"])
def test_invalid_model_selection_fails_fast(monkeypatch, invalid):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", invalid)
    with pytest.raises(ValueError, match="model"):
        load_mocked_modal_deployment(monkeypatch)


@pytest.mark.parametrize(
    "license_value,digest,error",
    [
        ("0", "", "license"),
        ("0", "lmsysorg/sglang-omni@sha256:" + "a" * 64, "license"),
        ("1", "", "digest"),
        ("1", "lmsysorg/sglang-omni:latest", "immutable"),
    ],
)
def test_selection_cannot_enable_higgs_without_license_and_digest(
    monkeypatch, license_value, digest, error
):
    monkeypatch.setenv("TEXT_TO_SPEECH_ENABLED_MODELS", "higgs-tts-3")
    monkeypatch.setenv("HIGGS_TTS_3_LICENSE_CONFIRMED", license_value)
    monkeypatch.setenv("TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF", digest)
    registry_calls = []
    with pytest.raises(ValueError, match=error):
        load_mocked_modal_deployment(monkeypatch, registry_calls=registry_calls)
    assert registry_calls == []
