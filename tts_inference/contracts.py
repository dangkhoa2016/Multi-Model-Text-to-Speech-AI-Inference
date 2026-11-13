"""Public model capabilities. Reject unavailable controls instead of silently ignoring them."""

import base64
import binascii
import io
import wave
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelID(str, Enum):
    MOSS_TTS = "moss-tts-v1.5"
    HIGGS_TTS_3 = "higgs-tts-3"
    VOXCPM2 = "voxcpm2"


class MossTtsOptions(StrictConfig):
    language: str | None = Field(default=None, min_length=2, max_length=48)
    instructions: str | None = Field(default=None, max_length=512)
    voice: str = Field(default="default", min_length=1, max_length=64)
    duration_tokens: int | None = Field(default=None, ge=1, le=4096)
    max_new_tokens: int = Field(default=2048, ge=64, le=8192)
    text_temperature: float = Field(default=1.5, ge=0.1, le=3.0)
    audio_temperature: float = Field(default=1.7, ge=0.1, le=3.0)
    text_top_p: float = Field(default=1.0, gt=0, le=1)
    audio_top_p: float = Field(default=0.8, gt=0, le=1)
    text_top_k: int = Field(default=50, ge=1, le=200)
    audio_top_k: int = Field(default=25, ge=1, le=200)
    audio_repetition_penalty: float = Field(default=1.0, ge=0.8, le=2.0)
    seed: int | None = Field(default=None, ge=0, le=2147483647)

    @model_validator(mode="after")
    def check_duration(self):
        if self.duration_tokens and self.duration_tokens > self.max_new_tokens:
            raise ValueError("duration_tokens cannot exceed max_new_tokens")
        return self


HiggsEmotion = Literal[
    "elation", "amusement", "enthusiasm", "determination", "pride", "contentment",
    "affection", "relief", "contemplation", "confusion", "surprise", "awe",
    "longing", "arousal", "anger", "fear", "disgust", "bitterness", "sadness",
    "shame", "helplessness",
]


class HiggsTts3Options(StrictConfig):
    voice: str = Field(default="default", min_length=1, max_length=64)
    emotion: HiggsEmotion | None = None
    style: Literal["singing", "shouting", "whispering"] | None = None
    speed: Literal["very_slow", "slow", "normal", "fast", "very_fast"] = "normal"
    pitch: Literal["normal", "low", "high"] = "normal"
    expressiveness: Literal["normal", "low", "high"] = "normal"
    temperature: float = Field(default=0.8, gt=0, le=2.0)
    top_p: float = Field(default=1.0, gt=0, le=1.0)
    top_k: int = Field(default=50, ge=1, le=200)
    max_new_tokens: int = Field(default=2048, ge=64, le=8192)
    seed: int | None = Field(default=None, ge=0, le=2147483647)


class VoxCPM2Options(StrictConfig):
    voice_description: str | None = Field(default=None, max_length=400)
    gender: Literal["male", "female", "androgynous"] | None = None
    age: Literal["child", "young", "adult", "senior"] | None = None
    emotion: str | None = Field(default=None, max_length=64)
    tone: str | None = Field(default=None, max_length=64)
    pace: Literal["very_slow", "slow", "normal", "fast", "very_fast"] | None = None
    cloning_mode: Literal["reference", "continuation"] = "reference"
    cfg_value: float = Field(default=2.0, ge=0.1, le=5.0)
    inference_timesteps: int = Field(default=10, ge=4, le=30)
    min_len: int = Field(default=2, ge=1, le=4096)
    max_len: int = Field(default=4096, ge=64, le=8192)
    normalize: bool = False
    denoise: Literal[False] = False  # Optional upstream denoiser is not installed
    retry_badcase: bool = True
    retry_badcase_max_times: int = Field(default=3, ge=0, le=5)
    retry_badcase_ratio_threshold: float = Field(default=6.0, ge=1.0, le=20.0)

    @model_validator(mode="after")
    def check_length(self):
        if self.min_len > self.max_len:
            raise ValueError("min_len must not exceed max_len")
        return self


OPTIONS_BY_MODEL = {ModelID.MOSS_TTS: MossTtsOptions, ModelID.HIGGS_TTS_3: HiggsTts3Options, ModelID.VOXCPM2: VoxCPM2Options}
CHECKPOINTS = {
    ModelID.MOSS_TTS: "OpenMOSS-Team/MOSS-TTS-v1.5",
    ModelID.HIGGS_TTS_3: "bosonai/higgs-tts-3-4b",
    ModelID.VOXCPM2: "openbmb/VoxCPM2",
}
MODEL_LICENSES = {ModelID.MOSS_TTS: "Apache-2.0", ModelID.HIGGS_TTS_3: "Research / Non-commercial", ModelID.VOXCPM2: "Apache-2.0"}
CAPABILITIES = {
    ModelID.MOSS_TTS: ["multilingual", "voice_cloning", "duration", "pronunciation", "pause_markup"],
    ModelID.HIGGS_TTS_3: ["multilingual", "voice_cloning", "emotion", "style", "prosody", "inline_tags"],
    ModelID.VOXCPM2: ["multilingual", "voice_design", "voice_cloning", "emotion_description", "pace_description"],
}


class SpeechRequest(StrictConfig):
    model: ModelID = Field(strict=False)
    input: Annotated[str, StringConstraints(min_length=1, max_length=1500, strip_whitespace=True)]
    model_config_overrides: dict = Field(default_factory=dict, alias="model_config")
    reference_audio_base64: str | None = Field(default=None, max_length=8_000_000)
    reference_text: str | None = Field(default=None, max_length=3000)
    reference_consent: bool = False

    @model_validator(mode="after")
    def validate_reference(self):
        if self.reference_audio_base64:
            if not self.reference_consent:
                raise ValueError("reference_consent=true is required for voice cloning")
            if self.model == ModelID.VOXCPM2 and self.model_config_overrides.get("cloning_mode") == "continuation" and not self.reference_text:
                raise ValueError("reference_text is required for VoxCPM2 continuation cloning")
        elif self.reference_text:
            raise ValueError("reference_text requires reference_audio_base64")
        return self

    def validated_options(self) -> StrictConfig:
        return OPTIONS_BY_MODEL[self.model].model_validate(self.model_config_overrides)

    def reference_bytes(self) -> bytes | None:
        if not self.reference_audio_base64:
            return None
        try:
            raw = base64.b64decode(self.reference_audio_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("reference_audio_base64 must contain raw base64 WAV data") from exc
        if len(raw) > 6_000_000:
            raise ValueError("Reference WAV must be at most 6 MB")
        try:
            with wave.open(io.BytesIO(raw), "rb") as stream:
                if stream.getcomptype() != "NONE":
                    raise ValueError("Only uncompressed PCM WAV references are supported")
                duration = stream.getnframes() / stream.getframerate()
                if not 0 < duration <= 30:
                    raise ValueError("Reference audio must be at most 30 seconds")
                if stream.getnchannels() not in (1, 2):
                    raise ValueError("Reference WAV must have 1 or 2 channels")
                if not 8000 <= stream.getframerate() <= 96000:
                    raise ValueError("Unsupported sample rate")
        except (wave.Error, EOFError, ZeroDivisionError) as exc:
            raise ValueError("Invalid PCM WAV reference") from exc
        return raw


def model_manifest(model: ModelID) -> dict:
    return {
        "id": model.value,
        "checkpoint": CHECKPOINTS[model],
        "license": MODEL_LICENSES[model],
        "capabilities": CAPABILITIES[model],
        "model_config_schema": OPTIONS_BY_MODEL[model].model_json_schema(),
    }


def prepare_request(request: SpeechRequest) -> dict:
    options = request.validated_options().model_dump(exclude_none=True)
    raw = request.reference_bytes()
    if raw and request.model == ModelID.VOXCPM2 and options["cloning_mode"] == "continuation" and not request.reference_text:
        raise ValueError("reference_text required for continuation mode")
    return {
        "model": request.model.value,
        "input": request.input,
        "model_config": options,
        "reference_audio": raw,
        "reference_text": request.reference_text,
    }
