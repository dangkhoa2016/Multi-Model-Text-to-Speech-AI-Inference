"""CPU-only access preflight for Hugging Face model checkpoints.

No model weights are downloaded. Exception text from the hosting service is
never forwarded to avoid exposing access tokens or request headers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

HUGGING_FACE_AUTHENTICATION_SECRET_NAME = "text-to-speech-hugging-face-authentication"
HUGGING_FACE_TOKEN_ENVIRONMENT_VARIABLE = "HUGGING_FACE_READ_TOKEN"


def resolve_hugging_face_token(
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Prefer the existing Secret key, then the standard Hugging Face token.

    Resolution is restricted to the CPU preflight/downloader environment; never
    surface token contents in exceptions or diagnostics.
    """
    values = os.environ if environment is None else environment
    return (
        values.get(HUGGING_FACE_TOKEN_ENVIRONMENT_VARIABLE, "").strip()
        or values.get("HF_TOKEN", "").strip()
        or None
    )


@dataclass(frozen=True)
class ModelAccessResult:
    repository_id: str
    revision_sha: str
    gated: bool


class ModelAccessError(RuntimeError):
    """Actionable failure from a model's independent access check."""


def check_model_download_access(
    repository_id: str, *, hugging_face_token: str | None, client=None
) -> ModelAccessResult:
    """Check access before any billable checkpoint download starts.

    `client` is injectable so the access checks have network-free tests.
    A checked commit SHA is returned for use in snapshot_download.
    """
    from huggingface_hub import HfApi
    from huggingface_hub.utils import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

    if not hugging_face_token or not hugging_face_token.strip():
        raise ModelAccessError(
            f"Missing {HUGGING_FACE_TOKEN_ENVIRONMENT_VARIABLE} or HF_TOKEN in Modal Secret "
            f"'{HUGGING_FACE_AUTHENTICATION_SECRET_NAME}'. Add a read token "
            "from the Hugging Face account that has been granted model access."
        )
    hugging_face_client = client if client is not None else HfApi()
    model_page = f"https://huggingface.co/{repository_id}"
    try:
        model_metadata = hugging_face_client.model_info(
            repo_id=repository_id, token=hugging_face_token
        )
        hugging_face_client.auth_check(repo_id=repository_id, token=hugging_face_token)
    except GatedRepoError:
        raise ModelAccessError(
            f"Access denied for {repository_id}. Visit {model_page} in the same "
            "Hugging Face account, accept/request model access and wait for "
            "approval if required. Then retry this CPU-only check."
        ) from None
    except RepositoryNotFoundError:
        raise ModelAccessError(
            f"Cannot find or read {repository_id}. Verify the repository ID, "
            "token account and read permissions. The model may be private."
        ) from None
    except HfHubHTTPError as error:
        http_status = getattr(getattr(error, "response", None), "status_code", None)
        if http_status in (401, 403):
            raise ModelAccessError(
                f"Hugging Face denied {repository_id} (HTTP {http_status}). "
                "Check token validity, account permissions and accepted model terms."
            ) from None
        raise ModelAccessError(
            f"Hugging Face could not verify {repository_id} "
            f"(HTTP {http_status or 'unknown'}). Retry later; no weights were downloaded."
        ) from None
    except (OSError, ConnectionError, TimeoutError):
        raise ModelAccessError(
            f"Could not contact Hugging Face to verify {repository_id}. "
            "Check network connectivity before retrying the download."
        ) from None

    revision_sha = getattr(model_metadata, "sha", None)
    if not revision_sha or len(revision_sha) != 40 or any(
        character not in "0123456789abcdef" for character in revision_sha.lower()
    ):
        raise ModelAccessError(
            f"Hugging Face did not provide a valid immutable revision for {repository_id}; "
            "no checkpoint download was started."
        )
    return ModelAccessResult(
        repository_id=repository_id,
        revision_sha=revision_sha,
        gated=bool(getattr(model_metadata, "gated", False)),
    )
