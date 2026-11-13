"""Lightweight CPU WAV metadata reading; GPU workers return WAV bytes."""

import io
import wave


def wave_metadata(raw: bytes) -> dict:
    with wave.open(io.BytesIO(raw), "rb") as f:
        sample_rate = f.getframerate()
        return {
            "sample_rate": sample_rate,
            "channels": f.getnchannels(),
            "duration_seconds": round(f.getnframes() / sample_rate, 3),
            "audio_bytes": len(raw),
        }
