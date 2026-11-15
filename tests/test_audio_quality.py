"""Objective PCM quality telemetry is advisory and never changes the original WAV."""

import io
import json
import math
import wave

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tts_inference.gateway import create_gateway
from tts_inference.persistence import Job, JobStore
from tts_inference.wav import wave_metadata


def pcm_wav(sample_width: int, samples: list[int]) -> bytes:
    """Encode mono little-endian signed samples (8-bit WAV is unsigned)."""
    if sample_width == 1:
        encoded = bytes(value + 128 for value in samples)
    else:
        encoded = b"".join(value.to_bytes(sample_width, "little", signed=True) for value in samples)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(sample_width)
        audio.setframerate(24000)
        audio.writeframes(encoded)
    return output.getvalue()


@pytest.mark.parametrize("sample_width", [1, 2, 3, 4])
def test_pcm_formats_report_near_clipping_without_rejecting_audio(sample_width):
    full_scale = 1 << (sample_width * 8 - 1)
    raw = pcm_wav(sample_width, [0, full_scale - 1, -full_scale + 1, -full_scale])
    metadata = wave_metadata(raw, analyze_quality=True)
    quality = metadata["audio_quality"]
    assert quality["sample_width_bits"] == sample_width * 8
    assert quality["clipped_samples"] == 3
    assert quality["total_samples"] == 4
    assert quality["clipped_fraction"] == 0.75
    assert quality["warnings"] == ["clipped_samples_present"]
    assert quality["peak_dbfs"] == 0.0
    expected_rms = math.sqrt((2 * (full_scale - 1) ** 2 + full_scale ** 2) / 4) / full_scale
    assert quality["rms_dbfs"] == pytest.approx(20 * math.log10(expected_rms), abs=0.01)
    assert metadata["audio_bytes"] == len(raw)


def test_unclipped_and_silent_pcm_have_no_invented_warning():
    raw = pcm_wav(2, [0, 8192, -8192])
    quality = wave_metadata(raw, analyze_quality=True)["audio_quality"]
    assert quality["rms_dbfs"] == pytest.approx(-13.80, abs=0.01)
    assert quality["peak_dbfs"] == pytest.approx(-12.04, abs=0.01)
    assert quality["clipped_samples"] == 0
    assert quality["warnings"] == []
    silence = wave_metadata(pcm_wav(1, [0, 0, 0, 0]), analyze_quality=True)["audio_quality"]
    assert silence["rms_dbfs"] is None
    assert silence["peak_dbfs"] is None
    assert silence["warnings"] == []


def test_durable_status_exposes_quality_and_download_preserves_original(tmp_path):
    token = "audio-quality-client-token-for-tests-12345"
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'audio-quality.sqlite3'}",
        Fernet.generate_key(),
    )
    store.create_schema_for_tests()
    job_id, _ = store.enqueue("alice", {"model": "voxcpm2", "input": "hello"})
    assert store.inspect(job_id, "alice")["audio_quality"] is None
    assert store.claim(job_id)
    audio = pcm_wav(2, [0, 32767, -32767, 0])
    store.complete(job_id, audio, {}, 12)
    app = create_gateway(job_store=store, client_keys={"alice": token})
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get(f"/v1/audio/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    status = response.json()
    assert status["audio_quality"]["clipped_samples"] == 2
    assert status["audio_quality"]["warnings"] == ["clipped_samples_present"]
    assert status["diagnostics"]["warnings"] == ["clipped_samples_present"]
    assert status["audio"]["audio_bytes"] == len(audio)
    assert "audio_quality" not in status["audio"]
    assert client.get(f"/v1/audio/jobs/{job_id}").status_code == 401
    download = client.get(f"/v1/audio/jobs/{job_id}/audio", headers=headers)
    assert download.status_code == 200
    assert download.headers["content-type"] == "audio/wav"
    assert download.content == audio
    assert store.inspect(job_id, "other-client") is None


def test_legacy_completed_job_without_quality_metadata_is_compatible(tmp_path):
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'legacy-quality.sqlite3'}",
        Fernet.generate_key(),
    )
    store.create_schema_for_tests()
    job_id, _ = store.enqueue("alice", {"model": "moss-tts-v1.5", "input": "hello"})
    store.claim(job_id)
    audio = pcm_wav(2, [0, 32767, 0])
    store.complete(job_id, audio, {}, 3)
    with store.sessions.begin() as session:
        job = session.get(Job, job_id)
        job.audio_metadata = json.dumps(wave_metadata(audio))
    status = store.inspect(job_id, "alice")
    assert status["status"] == "completed"
    assert status["audio_quality"] is None
    assert status["diagnostics"]["warnings"] == []
    assert store.audio(job_id, "alice") == audio


def test_invalid_wav_is_still_rejected():
    raw = pcm_wav(2, [0, 32767, 0])
    with pytest.raises(ValueError):
        wave_metadata(raw[:-2], analyze_quality=True)
