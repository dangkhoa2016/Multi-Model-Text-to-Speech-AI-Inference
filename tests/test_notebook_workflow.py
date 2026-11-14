"""Offline notebook flow tests: no default POST, retry, resume and bounded polling."""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "tts_api_demo.ipynb"


def cell_source(step):
    """Locate an executable notebook step by stable code identity, not cell index."""
    markers = {
        "connect": "TEXT_TO_SPEECH_GATEWAY_URL = (",
        "catalog": "model_catalog_response = requests.get(",
        "prepare": "SELECTED_MODEL_ID =",
        "submit": "SUBMIT_PAID_INFERENCE_JOB = False",
        "poll": "POLL_TIMEOUT_SECONDS =",
    }
    marker = markers[step]
    matches = [
        "".join(cell["source"])
        for cell in json.loads(NOTEBOOK.read_text())["cells"]
        if cell["cell_type"] == "code" and marker in "".join(cell["source"])
    ]
    assert len(matches) == 1, f"Missing or duplicate code cell: {step}"
    return matches[0]


class FakeResponse:
    def __init__(self, *, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self.data = data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self.data


class FakeRequests:
    RequestException = requests.RequestException
    HTTPError = requests.HTTPError

    def __init__(self, *, status="running", rate_limited=False, submission_status=200):
        self.submission_status = submission_status
        self.posts = []
        self.gets = []
        self.status = status
        self.rate_limited = rate_limited

    def post(self, url, *, headers, json, timeout):
        self.posts.append((url, headers, json, timeout))
        return FakeResponse(
            status_code=self.submission_status,
            data={"job_id": "existing-job-id", "reused": len(self.posts) > 1},
        )

    def get(self, url, *, headers, timeout):
        self.gets.append(url)
        if self.rate_limited:
            return FakeResponse(status_code=429, headers={"Retry-After": "7"})
        return FakeResponse(data={"status": self.status})


class FakeClock:
    def __init__(self):
        self.elapsed = 0
        self.sleeps = []

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += max(0, seconds)


def context(fake_requests):
    return {
        "os": os, "hashlib": hashlib, "json": json, "uuid": uuid,
        "requests": fake_requests, "time": time,
        "TEXT_TO_SPEECH_GATEWAY_URL": "http://127.0.0.1:8765",
        "AUTHENTICATED_REQUEST_HEADERS": {"Authorization": "Bearer example"},
    }


def execute_checked_in_cell(source, variables):
    # Execute only the static notebook committed in this repository, never user input.
    exec(source, variables)  # noqa: S102


def test_submission_is_opt_in_and_rerun_uses_the_acknowledged_job(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    fake = FakeRequests()
    variables = context(fake)
    source = cell_source("prepare")
    execute_checked_in_cell(source, variables)
    assert not fake.posts
    assert variables["SUBMITTED_JOB_ID"] is None
    opt_in_source = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(opt_in_source, variables)
    job_id = variables["SUBMITTED_JOB_ID"]
    execute_checked_in_cell(opt_in_source, variables)
    assert len(fake.posts) == 1
    assert variables["SUBMITTED_JOB_ID"] == job_id


def test_rejected_retry_cannot_clear_an_earlier_uncertain_attempt(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    fake = FakeRequests(submission_status=500)
    variables = context(fake)
    prepare = cell_source("prepare")
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(prepare, variables)
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submit, variables)
    fake.submission_status = 403
    with pytest.raises(requests.HTTPError):
        execute_checked_in_cell(submit, variables)
    revised = prepare.replace("REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2", 1)
    with pytest.raises(ValueError, match="Prior POST outcome uncertain"):
        execute_checked_in_cell(revised, variables)


def test_resume_existing_job_does_not_post(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", "existing-job-id")
    fake = FakeRequests()
    variables = context(fake)
    execute_checked_in_cell(cell_source("prepare"), variables)
    assert variables["SUBMITTED_JOB_ID"] == "existing-job-id"
    assert not fake.posts


def test_retry_key_survives_kernel_reset_via_explicit_restore(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_SCOPE", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    prepare = cell_source("prepare")
    initial = context(FakeRequests())
    execute_checked_in_cell(prepare, initial)
    saved = initial["_PREPARED_TTS_REQUEST"]
    monkeypatch.setenv("TEXT_TO_SPEECH_REQUEST_ID", saved["key"])
    monkeypatch.setenv("TEXT_TO_SPEECH_REQUEST_SCOPE", saved["scope"])
    variables = context(FakeRequests())
    source = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(prepare, variables)
    execute_checked_in_cell(source, variables)
    assert variables["requests"].posts[0][1]["Idempotency-Key"] == saved["key"]

    for changed_context in (
        {"TEXT_TO_SPEECH_GATEWAY_URL": "https://different.example"},
        {"AUTHENTICATED_REQUEST_HEADERS": {"Authorization": "Bearer another-token"}},
    ):
        changed = context(FakeRequests()) | changed_context
        with pytest.raises(ValueError, match="original request scope"):
            execute_checked_in_cell(prepare, changed)
    with pytest.raises(ValueError, match="original request scope"):
        execute_checked_in_cell(
            prepare.replace('"gender": "female"', '"gender": "male"', 1),
            context(FakeRequests()),
        )


def test_restored_key_requires_original_request_scope(monkeypatch):
    monkeypatch.setenv("TEXT_TO_SPEECH_REQUEST_ID", "nb-previous-request")
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_SCOPE", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    fake = FakeRequests()
    with pytest.raises(ValueError, match="original request scope"):
        execute_checked_in_cell(cell_source("prepare"), context(fake))
    assert not fake.posts


def test_bounded_polling_allows_resume_without_post(capsys):
    fake = FakeRequests(status="running")
    clock = FakeClock()
    variables = context(fake)
    variables.update({"SUBMITTED_JOB_ID": "existing-job-id", "time": clock})
    execute_checked_in_cell(cell_source("poll"), variables)
    assert fake.gets
    assert not fake.posts
    assert "Restore with:" in capsys.readouterr().out


def test_polling_honors_retry_after_without_post(capsys):
    fake = FakeRequests(rate_limited=True)
    clock = FakeClock()
    variables = context(fake)
    variables.update({"SUBMITTED_JOB_ID": "existing-job-id", "time": clock})
    execute_checked_in_cell(cell_source("poll"), variables)
    assert len(fake.gets) > 1
    assert 7 in clock.sleeps
    assert not fake.posts
    assert "Restore with:" in capsys.readouterr().out


def test_resume_after_kernel_reset_uses_existing_id_and_get_only(monkeypatch, capsys):
    monkeypatch.setenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", "existing-job-id")
    fake = FakeRequests(status="running")
    variables = context(fake)
    variables["time"] = FakeClock()
    execute_checked_in_cell(cell_source("prepare"), variables)
    execute_checked_in_cell(cell_source("submit"), variables)
    execute_checked_in_cell(cell_source("poll"), variables)
    assert variables["SUBMITTED_JOB_ID"] == "existing-job-id"
    assert fake.gets and not fake.posts
    assert 'os.environ["TEXT_TO_SPEECH_EXISTING_JOB_ID"]' in capsys.readouterr().out


def test_new_generation_rejects_stale_restored_request_id(monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_SCOPE", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    fake = FakeRequests()
    variables = context(fake)
    execute_checked_in_cell(cell_source("prepare"), variables)
    saved = variables["_PREPARED_TTS_REQUEST"]
    monkeypatch.setenv("TEXT_TO_SPEECH_REQUEST_ID", saved["key"])
    monkeypatch.setenv("TEXT_TO_SPEECH_REQUEST_SCOPE", saved["scope"])
    next_request = cell_source("prepare").replace("REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2")
    with pytest.raises(ValueError, match="Clear TEXT_TO_SPEECH_REQUEST_ID"):
        execute_checked_in_cell(next_request, variables)
    assert not fake.posts
