"""Import and inspect Modal declarations without installing Modal or starting workers."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from tts_inference.contracts import SpeechRequest, prepare_request
from tts_inference.mock import mock_wav


def load_mocked_modal_deployment(monkeypatch):
    class Fluent:
        def __init__(self, name=""):
            self.name = name
            self.secrets = []
            self.mounts = []
            self.local_sources = []

        def pip_install(self, *args, **kwargs):
            return self

        def apt_install(self, *args, **kwargs):
            return self

        def add_local_file(self, local_path, remote_path):
            self.mounts.append((local_path, remote_path))
            return self

        def add_local_python_source(self, *sources):
            self.local_sources.extend(sources)
            return self

        def env(self, variables):
            return self

    class FakeImage:
        @staticmethod
        def debian_slim(**kwargs):
            return Fluent("debian")

        @staticmethod
        def from_registry(image):
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


def test_modal_declarations_resolve_without_running_gpu(monkeypatch):
    module = load_mocked_modal_deployment(monkeypatch)
    assert module.app.name == "text-to-speech-ai-inference"
    assert "tts_inference" in module.model_download_image.local_sources
    assert module.HUGGING_FACE_AUTHENTICATION_SECRET["required_keys"] == ["HUGGING_FACE_READ_TOKEN"]
    assert module.moss_tts_model_weights_volume["name"].startswith("text-to-speech-moss")
    assert module.higgs_tts_3_model_weights_volume["name"].startswith("text-to-speech-higgs")
    assert module.voxcpm2_model_weights_volume["name"].startswith("text-to-speech-voxcpm2")
    assert callable(module.download_moss_tts_model)
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
    assert "config_cls: MossTTSPipelineConfig" in (Path(__file__).parents[1] / "configs/moss_tts.yaml").read_text()
    worker = module._SGLangSpeechServer()
    with pytest.raises(RuntimeError, match="configuration file is missing"):
        worker.start(model_configuration_path=str(tmp_path / "not-there.yaml"))


def test_higgs_worker_requires_operator_license_confirmation(monkeypatch):
    monkeypatch.delenv("HIGGS_TTS_3_LICENSE_CONFIRMED", raising=False)
    module = load_mocked_modal_deployment(monkeypatch)
    worker = module.HiggsTts3Worker()
    with pytest.raises(RuntimeError, match="license confirmation required"):
        worker.setup()
    assert not module.higgs_tts_3_license_confirmed


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
