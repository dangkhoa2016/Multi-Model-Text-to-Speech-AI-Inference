"""HTTP-level retry regressions for a lost POST acknowledgement; CPU only."""

import hashlib
import json
import os
import time
import uuid

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_notebook_workflow import cell_source, execute_checked_in_cell

from tts_inference.contracts import ModelID
from tts_inference.gateway import create_gateway
from tts_inference.persistence import JobStore


class LostAcknowledgementClient:
    RequestException = requests.RequestException
    HTTPError = requests.HTTPError

    def __init__(self, client, *, lose_first_ack=True):
        self.client = client
        self.lose_first_ack = lose_first_ack
        self.post_count = 0
        self.accepted_ids = []

    def post(self, url, *, headers, json, timeout):
        response = self.client.post("/v1/audio/jobs", headers=headers, json=json)
        self.post_count += 1
        if response.status_code == 202:
            self.accepted_ids.append(response.json()["job_id"])
        if self.lose_first_ack and self.post_count == 1:
            assert response.status_code == 202
            raise requests.Timeout("server accepted request, client lost the response")
        notebook_response = requests.Response()
        notebook_response.status_code = response.status_code
        notebook_response._content = response.content
        return notebook_response


def make_context(tmp_path, *, enabled_models=None, lose_first_ack=True):
    encryption_key = Fernet.generate_key()
    store = JobStore(
        f"sqlite+pysqlite:///{tmp_path / 'accepted.sqlite3'}", encryption_key
    )
    store.create_schema_for_tests()
    wakeups = []

    async def wake(job_id):
        wakeups.append(job_id)

    bearer = "test-notebook-ack-lost-token-1234567890"
    client = TestClient(create_gateway(
        job_store=store, wake_job=wake, enabled_models=enabled_models,
        client_keys={"notebook": bearer, "other": "test-other-client-token-1234567890"},
    ))
    adapter = LostAcknowledgementClient(client, lose_first_ack=lose_first_ack)
    variables = {
        "hashlib": hashlib,
        "json": json,
        "os": os,
        "time": time,
        "uuid": uuid,
        "requests": adapter,
        "TEXT_TO_SPEECH_GATEWAY_URL": "http://127.0.0.1:8765",
        "AUTHENTICATED_REQUEST_HEADERS": {"Authorization": "Bearer " + bearer},
    }
    return variables, adapter, store, wakeups


def test_lost_ack_retry_preserves_one_durable_job(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    variables, adapter, store, wakeups = make_context(tmp_path)
    prepare = cell_source("prepare")
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(prepare, variables)
    key = variables["STABLE_REQUEST_ID"]
    with pytest.raises(requests.Timeout):
        execute_checked_in_cell(submit, variables)
    assert variables["SUBMITTED_JOB_ID"] is None
    assert variables["_PREPARED_TTS_REQUEST"]["acknowledgement"] == "uncertain"

    # Even re-executing request preparation must NOT mint a new key.
    execute_checked_in_cell(prepare, variables)
    assert variables["STABLE_REQUEST_ID"] == key
    execute_checked_in_cell(submit, variables)
    assert adapter.post_count == 2
    assert adapter.accepted_ids == [adapter.accepted_ids[0]] * 2
    assert wakeups == [adapter.accepted_ids[0]]
    assert store.safe_metrics()["queued"] == 1


@pytest.mark.parametrize("connection_change", [
    {"AUTHENTICATED_REQUEST_HEADERS": {"Authorization": "Bearer test-other-client-token-1234567890"}},
    {"TEXT_TO_SPEECH_GATEWAY_URL": "https://another-gateway.example"},
])
def test_lost_ack_cannot_be_retried_after_switching_connection(tmp_path, monkeypatch, connection_change):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    variables, adapter, store, _ = make_context(tmp_path)
    execute_checked_in_cell(cell_source("prepare"), variables)
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    with pytest.raises(requests.Timeout):
        execute_checked_in_cell(submit, variables)

    variables.update(connection_change)
    with pytest.raises(ValueError, match="gateway or credentials changed"):
        execute_checked_in_cell(submit, variables)
    assert adapter.post_count == 1
    assert store.safe_metrics()["queued"] == 1


def test_disabled_model_rejection_allows_selecting_an_enabled_model(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    variables, adapter, store, _ = make_context(
        tmp_path, enabled_models={ModelID.MOSS_TTS}, lose_first_ack=False,
    )
    prepare = cell_source("prepare")
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(prepare, variables)
    with pytest.raises(requests.HTTPError) as error:
        execute_checked_in_cell(submit, variables)
    assert error.value.response.status_code == 503
    assert store.safe_metrics()["queued"] == 0

    revised = prepare.replace("REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2", 1).replace(
        'SELECTED_MODEL_ID = "voxcpm2"', 'SELECTED_MODEL_ID = "moss-tts-v1.5"', 1,
    ).replace('"model_config": MODEL_INFERENCE_OPTIONS', '"model_config": {}', 1)
    execute_checked_in_cell(revised, variables)
    execute_checked_in_cell(submit, variables)
    assert variables["SUBMITTED_JOB_ID"] == adapter.accepted_ids[0]
    assert store.safe_metrics()["queued"] == 1


def test_new_generation_does_not_poll_previous_job_after_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXT_TO_SPEECH_REQUEST_ID", raising=False)
    monkeypatch.delenv("TEXT_TO_SPEECH_EXISTING_JOB_ID", raising=False)
    variables, _, store, _ = make_context(tmp_path)
    prepare = cell_source("prepare")
    submit = cell_source("submit").replace(
        "SUBMIT_PAID_INFERENCE_JOB = False", "SUBMIT_PAID_INFERENCE_JOB = True", 1
    )
    execute_checked_in_cell(prepare, variables)
    variables["SUBMITTED_JOB_ID"] = "previous-successful-job"
    execute_checked_in_cell(
        prepare.replace("REQUEST_GENERATION = 1", "REQUEST_GENERATION = 2"), variables
    )
    assert variables["SUBMITTED_JOB_ID"] is None
    with pytest.raises(requests.Timeout):
        execute_checked_in_cell(submit, variables)
    assert variables["SUBMITTED_JOB_ID"] is None
    assert store.safe_metrics()["queued"] == 1
