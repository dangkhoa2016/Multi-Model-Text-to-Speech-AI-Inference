"""Validate complete, frame-aligned PCM WAV including RIFF chunk boundaries."""

import io
import math
import struct
import wave


def _data_chunk_size(raw: bytes) -> int:
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("Invalid RIFF/WAV header")
    if struct.unpack_from("<I", raw, 4)[0] + 8 != len(raw):
        raise ValueError("RIFF size does not match file bytes")
    offset = 12
    found_format = False
    data_size = None
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated RIFF chunk header")
        name = raw[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        end = offset + 8 + chunk_size
        if end > len(raw):
            raise ValueError("Truncated RIFF chunk")
        if name == b"fmt ":
            if found_format:
                raise ValueError("Multiple WAV fmt chunks")
            found_format = True
        if name == b"data":
            if data_size is not None:
                raise ValueError("Multiple WAV data chunks")
            data_size = chunk_size
        offset = end + chunk_size % 2
        if offset > len(raw):
            raise ValueError("Missing RIFF padding")
    if offset != len(raw) or not found_format or data_size is None:
        raise ValueError("Incomplete WAV chunks")
    return data_size


def _pcm_sample_values(chunk: bytes, sample_width: int):
    """Decode little-endian PCM without NumPy or deprecated audioop."""
    if sample_width == 1:
        return (value - 128 for (value,) in struct.iter_unpack("B", chunk))
    if sample_width == 2:
        return (value for (value,) in struct.iter_unpack("<h", chunk))
    if sample_width == 3:
        return (
            int.from_bytes(chunk[offset:offset + 3], "little", signed=True)
            for offset in range(0, len(chunk), 3)
        )
    return (value for (value,) in struct.iter_unpack("<i", chunk))


def wave_metadata(
    raw: bytes, *, max_bytes: int = 32_000_000, max_duration_seconds: float | None = None,
    analyze_quality: bool = False,
) -> dict:
    """Validate PCM frames; optionally measure levels without rejecting clipping."""
    if not isinstance(raw, bytes) or len(raw) > max_bytes or not raw:
        raise ValueError("WAV must be nonempty bytes within the configured size limit")
    data_size = _data_chunk_size(raw)
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise ValueError("Only uncompressed PCM WAV is supported")
            sample_rate, channels, sample_width = (
                wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            )
            if channels not in (1, 2) or sample_width not in (1, 2, 3, 4):
                raise ValueError("Unsupported PCM WAV channels or sample width")
            if not 8000 <= sample_rate <= 96000:
                raise ValueError("Unsupported PCM WAV sample rate")
            frame_size = channels * sample_width
            if data_size <= 0 or data_size % frame_size:
                raise ValueError("PCM data is not aligned to complete audio frames")
            frames = wav.getnframes()
            if data_size != frames * frame_size:
                raise ValueError("WAV frame count does not match data bytes")
            duration = frames / sample_rate
            if max_duration_seconds is not None and duration > max_duration_seconds:
                raise ValueError("WAV duration exceeds the allowed limit")
            actual = 0
            sample_count = 0
            sum_squared = 0
            peak_absolute = 0
            near_clipping_count = 0
            full_scale = 1 << (sample_width * 8 - 1)
            # Detect samples within one least-significant bit of either PCM endpoint.
            positive_near_clip = full_scale - 2
            negative_near_clip = -full_scale + 1
            while actual < data_size:
                chunk = wav.readframes(min(4096, (data_size - actual) // frame_size))
                if not chunk:
                    raise ValueError("Truncated WAV PCM data")
                actual += len(chunk)
                if analyze_quality:
                    for value in _pcm_sample_values(chunk, sample_width):
                        sample_count += 1
                        sum_squared += value * value
                        peak_absolute = max(peak_absolute, abs(value))
                        if value >= positive_near_clip or value <= negative_near_clip:
                            near_clipping_count += 1
            if actual != data_size:
                raise ValueError("Incomplete PCM audio frames")
    except (wave.Error, EOFError, ZeroDivisionError, OSError) as error:
        raise ValueError("Invalid PCM WAV data") from error
    metadata = {
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_seconds": round(duration, 3),
        "audio_bytes": len(raw),
    }
    if analyze_quality:
        rms = math.sqrt(sum_squared / sample_count) / full_scale if sample_count else 0.0
        peak = peak_absolute / full_scale
        metadata["audio_quality"] = {
            "rms_dbfs": round(20 * math.log10(rms), 2) if rms else None,
            "peak_dbfs": round(20 * math.log10(peak), 2) if peak else None,
            "clipped_samples": near_clipping_count,
            "total_samples": sample_count,
            "clipped_fraction": round(near_clipping_count / sample_count, 8) if sample_count else 0.0,
            "sample_width_bits": sample_width * 8,
            "warnings": ["clipped_samples_present"] if near_clipping_count else [],
        }
    return metadata
