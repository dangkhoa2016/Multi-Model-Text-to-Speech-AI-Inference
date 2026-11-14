"""Explicitly labelled CPU-only mock for contract/HTTP demos, NEVER real speech."""

from __future__ import annotations

import io
import time
import uuid
import wave

from .wav import wave_metadata


def mock_wav() -> bytes:
    sink = io.BytesIO()
    with wave.open(sink, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 8000)
    return sink.getvalue()


class MockDispatcher:
    def __init__(self):
        self.jobs = {}

    async def submit(self, job: dict, owner: str) -> str:
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = (owner, job, time.time())
        return job_id

    async def inspect(self, job_id: str, owner: str) -> dict | None:
        record = self.jobs.get(job_id)
        if not record or record[0] != owner:
            return None
        _, job, created = record
        return {
            "job_id": job_id, "model": job["model"], "status": "completed",
            "is_mock_result": True, "audio_is_speech": False,
            "created_at": created, "audio_url": f"/v1/audio/jobs/{job_id}/audio",
            "audio": wave_metadata(mock_wav()),
        }

    async def fetch_audio(self, job_id: str, owner: str) -> bytes | None:
        if not (job_id in self.jobs and self.jobs[job_id][0] == owner):
            return None
        return mock_wav()
