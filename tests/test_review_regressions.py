"""CPU-only regressions for independent repository review (R1, R5, R7)."""

import ast
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

from scripts.validate_notebook import validate_notebook
from tts_inference.gateway import create_gateway
from tts_inference.mock import MockDispatcher

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer review-cpu-token-32-characters-long"}


def test_preflight_image_declares_pydantic_runtime_dependency():
    tree = ast.parse((ROOT / "modal_app.py").read_text())
    image = next(
        node.value for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "model_download_image"
                for target in node.targets)
    )
    packages = [
        argument.value for call in ast.walk(image)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.func.attr == "pip_install"
        for argument in call.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]
    assert any(item.startswith("pydantic") for item in packages)
    assert any(item.startswith("huggingface_hub") for item in packages)


def test_mock_reuses_request_id_and_rejects_changed_payload():
    client = TestClient(create_gateway(MockDispatcher(), client_keys={
        "local": "review-cpu-token-32-characters-long",
    }))
    models = client.get("/v1/models", headers=AUTH).json()["data"]
    assert next(item for item in models if item["id"] == "higgs-tts-3")["enabled"] is False
    payload = {"model": "voxcpm2", "input": "Hello", "model_config": {"gender": "female"}}
    headers = {**AUTH, "Idempotency-Key": "review-request-key-123"}
    first = client.post("/v1/audio/jobs", headers=headers, json=payload)
    second = client.post("/v1/audio/jobs", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "queued"
    assert not first.json()["reused"] and second.json()["reused"]
    assert first.json()["job_id"] == second.json()["job_id"]
    assert client.post(
        "/v1/audio/jobs", headers=headers, json={**payload, "input": "Changed"}
    ).status_code == 409
    assert client.post(
        "/v1/audio/jobs", headers=AUTH, json={"model": "higgs-tts-3", "input": "Hi"}
    ).status_code == 503


def test_standard_notebook_schema_rejects_missing_kernel_name(tmp_path):
    path = ROOT / "notebooks" / "tts_api_demo.ipynb"
    validate_notebook(path)
    notebook = nbformat.read(path, as_version=4)
    del notebook.metadata.kernelspec["name"]
    altered = tmp_path / "invalid.ipynb"
    nbformat.write(notebook, altered)
    with pytest.raises(nbformat.ValidationError):
        validate_notebook(altered)


def test_notebook_rejects_saved_output(tmp_path):
    notebook = nbformat.read(ROOT / "notebooks" / "tts_api_demo.ipynb", as_version=4)
    cell = next(cell for cell in notebook.cells if cell.cell_type == "code")
    cell.outputs = [nbformat.v4.new_output("stream", text="private output")]
    altered = tmp_path / "leaked.ipynb"
    nbformat.write(notebook, altered)
    with pytest.raises(AssertionError, match="captured output"):
        validate_notebook(altered)


def test_notebook_rejects_missing_vietnamese_guidance(tmp_path):
    notebook = nbformat.read(ROOT / "notebooks" / "tts_api_demo.ipynb", as_version=4)
    guide = next(cell for cell in notebook.cells if "## Step 4" in cell.source)
    guide.source = guide.source.replace("### Tiếng Việt", "### Missing translation", 1)
    altered = tmp_path / "missing-vi.ipynb"
    nbformat.write(notebook, altered)
    with pytest.raises(AssertionError, match="Vietnamese"):
        validate_notebook(altered)


def test_notebook_rejects_undocumented_code_cell(tmp_path):
    notebook = nbformat.read(ROOT / "notebooks" / "tts_api_demo.ipynb", as_version=4)
    assert notebook.cells[9].cell_type == "code"
    notebook.cells.insert(9, nbformat.v4.new_code_cell("print('unexplained')"))
    altered = tmp_path / "missing-code-guide.ipynb"
    nbformat.write(notebook, altered)
    with pytest.raises(AssertionError, match="immediately preceding"):
        validate_notebook(altered)
