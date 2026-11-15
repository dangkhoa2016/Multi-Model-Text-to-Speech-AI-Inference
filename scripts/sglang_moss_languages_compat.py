"""SGLang-Omni 0.2.0 MOSS-TTS speech language admission compatibility patch.

The delay-pattern MOSS-TTS-v1.5 checkpoint supports the same 31 languages as
MOSS-TTS Local, whose config declares these 21 additions to the shared speech
API set. This is an exact-version source patch with an upstream SHA-256 guard.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path

SOURCE_PATH = "sglang_omni/models/moss_tts/config.py"
EXPECTED_VERSION = "0.2.0"
ORIGINAL_SHA256 = "1b873b316411c6a26a49aee83653e4a6fdb5da65020fed6f726211fd0ab1389e"
ANCHOR = '    architecture: ClassVar[str] = "MossTTSDelayModel"\n'
REPLACEMENT = ANCHOR + '''    additional_speech_languages: ClassVar[frozenset[str]] = frozenset(
        {
            "Cantonese", "Arabic", "Czech", "Danish", "Dutch", "Finnish",
            "Greek", "Hebrew", "Hindi", "Hungarian", "Macedonian", "Malay",
            "Persian (Farsi)", "Polish", "Romanian", "Swahili", "Swedish",
            "Tagalog", "Thai", "Turkish", "Vietnamese",
        }
    )
'''


def patch_source(original: bytes) -> bytes:
    digest = hashlib.sha256(original).hexdigest()
    if digest != ORIGINAL_SHA256:
        raise RuntimeError(f"Unexpected MOSS-TTS configuration SHA-256: {digest}")
    source = original.decode("utf-8")
    if source.count(ANCHOR) != 1:
        raise RuntimeError("MOSS-TTS language patch anchor is not unique")
    modified = source.replace(ANCHOR, REPLACEMENT, 1)
    compile(modified, SOURCE_PATH, "exec")
    return modified.encode("utf-8")


def main() -> None:
    distribution = importlib.metadata.distribution("sglang-omni")
    if distribution.version != EXPECTED_VERSION:
        raise RuntimeError("Only sglang-omni==0.2.0 may be patched")
    target = Path(distribution.locate_file(SOURCE_PATH))
    modified = patch_source(target.read_bytes())
    target.write_bytes(modified)
    import py_compile
    for bytecode in (target.parent / "__pycache__").glob(target.stem + ".*.pyc"):
        bytecode.unlink()
    py_compile.compile(str(target), doraise=True)
    print("SGLANG_OMNI_MOSS_LANGUAGE_PATCH=APPLIED")
    print("PATCHED_CONFIG_SHA256=" + hashlib.sha256(modified).hexdigest())


if __name__ == "__main__":
    main()
