"""Explicit CPU-only checkpoint download for Higgs TTS 3 research acceptance.

This standalone tool never deploys the production Gateway or allocates a GPU.
CPU compute, storage and Hugging Face transfer may still incur charges.
Run from the repository root after verifying the Modal Workspace Budget.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import modal

from tts_inference.hugging_face_access import (
    HUGGING_FACE_AUTHENTICATION_SECRET_NAME,
)

REPOSITORY_ID = "bosonai/higgs-tts-3-4b"
APPROVAL_VARIABLE = "TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL"
APPROVAL_VALUE = "APPROVED_HIGGS_RESEARCH_CPU_DOWNLOAD_WITH_CHARGES"
OPERATOR_APPROVED = os.getenv(APPROVAL_VARIABLE) == APPROVAL_VALUE
PIN_VARIABLE = "TTS_HIGGS_CHECKPOINT_REVISION_SHA"
PINNED_REVISION = os.getenv(PIN_VARIABLE, "")
if PINNED_REVISION and not re.fullmatch(r"[0-9a-f]{40}", PINNED_REVISION):
    raise ValueError("Higgs checkpoint pin must be exactly 40 lowercase hex characters")

app = modal.App("tts-higgs-research-checkpoint-download")
higgs_weights_volume = modal.Volume.from_name(
    "text-to-speech-higgs-tts-3-model-weights", create_if_missing=False
)

# The explicit local approval is embedded into the remote image. Invoking the
# named function directly cannot bypass the approval guard in its container.
download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "huggingface_hub>=0.33,<2", "hf_xet>=1.7,<2", "pydantic>=2.10,<3",
    )
    .env({
        APPROVAL_VARIABLE: "1" if OPERATOR_APPROVED else "0",
        PIN_VARIABLE: PINNED_REVISION,
    })
    .add_local_python_source("tts_inference")
)


@app.function(
    image=download_image,
    secrets=[modal.Secret.from_name(HUGGING_FACE_AUTHENTICATION_SECRET_NAME)],
    volumes={"/weights": higgs_weights_volume},
    cpu=4,
    memory=8192,
    timeout=7200,
    max_containers=1,
)
def download_higgs_research_checkpoint():
    """Download an immutable snapshot to an existing empty Volume, using CPU."""
    if os.environ.get(APPROVAL_VARIABLE) != "1":
        raise RuntimeError("Higgs research checkpoint download requires explicit cost approval")

    from huggingface_hub import HfApi, snapshot_download

    from tts_inference.checkpoint_integrity import record_checkpoint_integrity
    from tts_inference.hugging_face_access import (
        check_model_download_access,
        resolve_hugging_face_token,
    )

    weights_directory = Path("/weights")
    if any(weights_directory.iterdir()):
        raise RuntimeError(
            "Higgs Volume is not empty. Inspect its existing files and manifest "
            "before deciding whether it needs replacement."
        )

    hugging_face_token = resolve_hugging_face_token()
    access = check_model_download_access(
        REPOSITORY_ID, hugging_face_token=hugging_face_token,
    )
    expected_revision = os.environ.get(PIN_VARIABLE, "")
    if expected_revision and access.revision_sha != expected_revision:
        raise RuntimeError("Higgs model revision differs from operator-approved pin")

    print(f"Higgs research checkpoint: downloading immutable revision {access.revision_sha}")
    snapshot_download(
        repo_id=REPOSITORY_ID,
        revision=access.revision_sha,
        local_dir="/weights",
        token=hugging_face_token,
        max_workers=4,
    )
    expected_paths = HfApi().list_repo_files(
        repo_id=REPOSITORY_ID,
        revision=access.revision_sha,
        token=hugging_face_token,
    )
    manifest = record_checkpoint_integrity(
        "/weights",
        repository_id=REPOSITORY_ID,
        revision_sha=access.revision_sha,
        expected_paths=expected_paths,
    )
    higgs_weights_volume.commit()
    print(
        "HIGGS_RESEARCH_CHECKPOINT_VERIFIED="
        + f"{manifest['revision_sha']} files={len(manifest['files'])}"
    )


@app.local_entrypoint()
def main():
    if not OPERATOR_APPROVED:
        raise RuntimeError(
            f"Set {APPROVAL_VARIABLE}={APPROVAL_VALUE} only after reviewing "
            "research purpose, CPU/storage charges and the Modal Workspace Budget."
        )
    download_higgs_research_checkpoint.remote()
