"""SGLang-Omni 0.2.0 MOSS-TTS channel sampling compatibility patch.

The public speech request schema accepts stage_params, but the upstream MOSS
request builder does not unpack stage_params.tts_engine sampling controls.
The exact upstream source SHA-256 prevents cross-version modifications.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path

SOURCE_PATH = "sglang_omni/models/moss_tts/request_builders.py"
EXPECTED_VERSION = "0.2.0"
ORIGINAL_SHA256 = "bafb288b5655c1afaa1bcafb5c7cee2dc4cf87eaf641d831d066e2c8a13544db"
BEFORE_DEF = "def build_moss_tts_state(payload: StagePayload) -> MossTTSState:\n"
BEFORE_CALL = "generation_kwargs=build_generation_kwargs(params, tts_params=tts_params),"
AFTER_CALL = (
    "generation_kwargs=build_generation_kwargs(\n"
    "            {**params, **_extract_moss_channel_sampling(params.get(\"stage_params\"))},\n"
    "            tts_params=tts_params,\n"
    "        ),"
)
HELPER = '''def _extract_moss_channel_sampling(stage_params: object) -> dict[str, int | float]:
    """Require recognized MOSS channel controls in stage_params.tts_engine."""
    if stage_params is None:
        return {}
    if not isinstance(stage_params, dict):
        raise ValueError("MOSS stage_params must be an object")
    options = stage_params.get("tts_engine", {})
    if not isinstance(options, dict):
        raise ValueError("MOSS stage_params.tts_engine must be an object")
    permitted = {
        "text_temperature", "audio_temperature",
        "text_top_p", "audio_top_p",
        "text_top_k", "audio_top_k",
        "audio_repetition_penalty",
    }
    unknown = set(options) - permitted
    if unknown:
        raise ValueError("Unknown MOSS tts_engine sampling options: " + ", ".join(sorted(unknown)))
    if any(isinstance(value, bool) for value in options.values()):
        raise ValueError("MOSS sampling options must be numeric, not boolean")
    return {key: value for key, value in options.items() if value is not None}


'''


def patch_source(original: bytes) -> bytes:
    digest = hashlib.sha256(original).hexdigest()
    if digest != ORIGINAL_SHA256:
        raise RuntimeError(
            f"Unexpected SGLang-Omni 0.2.0 MOSS builder SHA-256: {digest}"
        )
    source = original.decode("utf-8")
    if source.count(BEFORE_DEF) != 1 or source.count(BEFORE_CALL) != 1:
        raise RuntimeError("SGLang-Omni MOSS patch anchors are not unique")
    modified = source.replace(BEFORE_DEF, HELPER + BEFORE_DEF, 1)
    modified = modified.replace(BEFORE_CALL, AFTER_CALL, 1)
    compile(modified, SOURCE_PATH, "exec")
    return modified.encode("utf-8")


def main() -> None:
    distribution = importlib.metadata.distribution("sglang-omni")
    if distribution.version != EXPECTED_VERSION:
        raise RuntimeError("Only sglang-omni==0.2.0 may be patched")
    target = Path(distribution.locate_file(SOURCE_PATH))
    original = target.read_bytes()
    modified = patch_source(original)
    target.write_bytes(modified)
    import py_compile
    for bytecode in (target.parent / "__pycache__").glob(target.stem + ".*.pyc"):
        bytecode.unlink()
    py_compile.compile(str(target), doraise=True)
    print("SGLANG_OMNI_MOSS_PATCH=APPLIED")
    print("PATCHED_SOURCE_SHA256=" + hashlib.sha256(modified).hexdigest())


if __name__ == "__main__":
    main()
