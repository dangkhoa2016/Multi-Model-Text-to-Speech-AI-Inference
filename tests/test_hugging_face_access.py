"""Verify Hugging Face preflight before billable checkpoint downloads."""
from types import SimpleNamespace

import httpx
import pytest
from huggingface_hub.utils import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from tts_inference.hugging_face_access import (
    ModelAccessError,
    check_model_download_access,
    resolve_hugging_face_token,
)

REVISION_SHA = "a" * 40
MODEL_REPOSITORY = "OpenMOSS-Team/MOSS-TTS-v1.5"


class FakeHuggingFaceClient:
    def __init__(self, error=None, gated=False):
        self.error = error
        self.gated = gated
        self.events = []

    def model_info(self, *, repo_id, token):
        self.events.append(("model_info", repo_id, token))
        if self.error:
            raise self.error
        return SimpleNamespace(sha=REVISION_SHA, gated=self.gated)

    def auth_check(self, *, repo_id, token):
        self.events.append(("auth_check", repo_id, token))
        if self.error:
            raise self.error


def _http_response(status_code):
    return httpx.Response(status_code, request=httpx.Request("GET", "https://huggingface.co/"))


def test_valid_token_checks_access_before_downloading():
    client = FakeHuggingFaceClient(gated=True)
    result = check_model_download_access(MODEL_REPOSITORY, hugging_face_token="hf_secret", client=client)
    assert result.revision_sha == REVISION_SHA
    assert result.repository_id == MODEL_REPOSITORY
    assert result.gated is True
    assert [event[0] for event in client.events] == ["model_info", "auth_check"]


def test_missing_token_is_actionable_and_does_not_call_network():
    client = FakeHuggingFaceClient()
    with pytest.raises(ModelAccessError, match="HUGGING_FACE_READ_TOKEN"):
        check_model_download_access(MODEL_REPOSITORY, hugging_face_token=None, client=client)
    assert client.events == []


@pytest.mark.parametrize("error,expected", [
    (GatedRepoError("SECRET-TOKEN", response=_http_response(403)), "accept/request model access"),
    (RepositoryNotFoundError("SECRET-TOKEN", response=_http_response(401)), "repository ID"),
    (HfHubHTTPError("SECRET-TOKEN", response=_http_response(403)), "HTTP 403"),
    (HfHubHTTPError("SECRET-TOKEN", response=_http_response(503)), "HTTP 503"),
])
def test_preflight_reports_safe_actionable_errors(error, expected):
    with pytest.raises(ModelAccessError) as raised:
        check_model_download_access(MODEL_REPOSITORY, hugging_face_token="hf_secret", client=FakeHuggingFaceClient(error))
    assert expected in str(raised.value)
    assert "SECRET-TOKEN" not in str(raised.value)


def test_missing_immutable_revision_is_rejected():
    client = FakeHuggingFaceClient()
    def bad_info(*, repo_id, token):
        return SimpleNamespace(sha="latest", gated=False)
    client.model_info = bad_info
    with pytest.raises(ModelAccessError, match="immutable revision"):
        check_model_download_access(MODEL_REPOSITORY, hugging_face_token="hf_secret", client=client)


@pytest.mark.parametrize(("environment", "expected"), [
    ({"HUGGING_FACE_READ_TOKEN": "legacy-token", "HF_TOKEN": "standard-token"}, "legacy-token"),
    ({"HF_TOKEN": "standard-token"}, "standard-token"),
    ({"HUGGING_FACE_READ_TOKEN": "  ", "HF_TOKEN": "standard-token"}, "standard-token"),
    ({"HUGGING_FACE_READ_TOKEN": " legacy-token "}, "legacy-token"),
    ({"HUGGING_FACE_READ_TOKEN": "", "HF_TOKEN": ""}, None),
    ({}, None),
])
def test_cpu_hugging_face_token_fallback(environment, expected):
    assert resolve_hugging_face_token(environment) == expected


def test_missing_both_hugging_face_tokens_has_safe_error():
    with pytest.raises(ModelAccessError) as raised:
        check_model_download_access(
            MODEL_REPOSITORY, hugging_face_token=None,
            client=FakeHuggingFaceClient(),
        )
    assert "HF_TOKEN" in str(raised.value)
    assert "standard-token" not in str(raised.value)
    assert "legacy-token" not in str(raised.value)
