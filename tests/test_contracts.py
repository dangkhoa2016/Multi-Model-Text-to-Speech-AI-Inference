import base64
import io
import wave

import pytest
from pydantic import ValidationError

from tts_inference.adapters import higgs_tts_3_payload, moss_tts_payload, voxcpm2_payload
from tts_inference.contracts import ModelID, SpeechRequest, model_manifest, prepare_request


def make_reference():
    sink = io.BytesIO()
    with wave.open(sink, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 16000)
    return base64.b64encode(sink.getvalue()).decode()


@pytest.mark.parametrize("model", ["moss-tts-v1.5", "higgs-tts-3", "voxcpm2"])
def test_manifest_has_schema(model):
    manifest = model_manifest(ModelID(model))
    assert manifest["model_config_schema"]["additionalProperties"] is False
    assert manifest["checkpoint"]
    assert manifest["capabilities"]


def test_vox_description_and_options():
    job = prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": "Hello!",
        "model_config": {"gender": "female", "age": "young", "emotion": "happy", "pace": "slow", "inference_timesteps": 16},
    }))
    payload = voxcpm2_payload(job)
    assert "young person" in payload["text"]
    assert "female voice" in payload["text"]
    assert "happy emotion" in payload["text"]
    assert payload["inference_timesteps"] == 16


def test_higgs_emotion_and_prosody():
    job = prepare_request(SpeechRequest.model_validate({
        "model": "higgs-tts-3", "input": "Hello!",
        "model_config": {"emotion": "amusement", "speed": "very_fast", "pitch": "high", "expressiveness": "high"},
    }))
    payload = higgs_tts_3_payload(job)
    assert payload["input"].startswith("<|emotion:amusement|><|prosody:speed_very_fast|><|prosody:pitch_high|><|prosody:expressive_high|>")


def test_moss_duration_and_language():
    job = prepare_request(SpeechRequest.model_validate({
        "model": "moss-tts-v1.5", "input": "Xin chào!",
        "model_config": {"language": "Vietnamese", "duration_tokens": 250, "max_new_tokens": 500},
    }))
    payload = moss_tts_payload(job)
    assert payload["token_count"] == 250
    assert payload["language"] == "Vietnamese"
    assert "text_temperature" not in payload
    controls = payload["stage_params"]["tts_engine"]
    assert controls["text_temperature"] == 1.5
    assert controls["audio_temperature"] == 1.7
    assert controls["text_top_p"] == 1.0
    assert controls["audio_top_p"] == 0.8
    assert controls["text_top_k"] == 50
    assert controls["audio_top_k"] == 25
    assert controls["audio_repetition_penalty"] == 1.0


def test_unknown_model_option_is_rejected():
    request = SpeechRequest.model_validate({
        "model": "moss-tts-v1.5", "input": "Hello", "model_config": {"emotion": "happy"},
    })
    with pytest.raises(ValidationError):
        prepare_request(request)


def test_invalid_emotion_rejected():
    request = SpeechRequest.model_validate({
        "model": "higgs-tts-3", "input": "Hello", "model_config": {"emotion": "not-real"},
    })
    with pytest.raises(ValidationError):
        prepare_request(request)


def test_reference_requires_consent():
    with pytest.raises(ValidationError):
        SpeechRequest.model_validate({"model": "voxcpm2", "input": "Hello", "reference_audio_base64": make_reference()})


def test_reference_wav_accepted():
    job = prepare_request(SpeechRequest.model_validate({
        "model": "voxcpm2", "input": "Hello", "reference_consent": True,
        "reference_audio_base64": make_reference(),
    }))
    assert job["reference_audio"].startswith(b"RIFF")
    assert voxcpm2_payload(job, "/tmp/ref.wav")["reference_wav_path"] == "/tmp/ref.wav"


def test_vox_continuation_requires_transcript():
    with pytest.raises(ValidationError):
        SpeechRequest.model_validate({
            "model": "voxcpm2", "input": "Hello", "reference_consent": True,
            "reference_audio_base64": make_reference(),
            "model_config": {"cloning_mode": "continuation"},
        })


def test_broken_wav_is_rejected():
    request = SpeechRequest.model_validate({
        "model": "higgs-tts-3", "input": "Hello", "reference_consent": True,
        "reference_audio_base64": base64.b64encode(b"bad audio").decode(),
    })
    with pytest.raises(ValueError):
        prepare_request(request)


def test_strict_types():
    request = SpeechRequest.model_validate({
        "model": "voxcpm2", "input": "Hello", "model_config": {"inference_timesteps": "10"},
    })
    with pytest.raises(ValidationError):
        prepare_request(request)
