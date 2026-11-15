"""CPU-only checks for the separate Higgs research checkpoint workflow."""

from __future__ import annotations

import ast
import os
import runpy
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "scripts" / "higgs_research_checkpoint.py"


def test_development_extras_include_modal_runtime_for_offline_checks():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    optional_dependencies = metadata["project"]["optional-dependencies"]
    assert set(optional_dependencies["modal"]).issubset(set(optional_dependencies["dev"]))


def test_higgs_research_checkpoint_requires_explicit_approval():
    source = SOURCE_PATH.read_text()
    with patch.dict(os.environ, {
        "TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL": "",
        "TTS_HIGGS_CHECKPOINT_REVISION_SHA": "",
    }):
        scope = runpy.run_path(str(SOURCE_PATH), run_name="cpu_only_test")
        assert scope["OPERATOR_APPROVED"] is False
        with pytest.raises(RuntimeError, match="Set TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL"):
            scope["main"]()

    assert "if os.environ.get(APPROVAL_VARIABLE) != \"1\":" in source
    assert 'APPROVAL_VARIABLE: "1" if OPERATOR_APPROVED else "0"' in source
    assert "HIGGS_TTS_3_LICENSE_CONFIRMED" not in source


def test_higgs_research_checkpoint_has_no_gpu_or_production_side_effects():
    tree = ast.parse(SOURCE_PATH.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "download_higgs_research_checkpoint"
    )
    modal_decorator = next(
        decorator for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "function"
    )
    assert not any(keyword.arg == "gpu" for keyword in modal_decorator.keywords)
    source = SOURCE_PATH.read_text()
    assert "modal deploy" not in source
    assert "create_if_missing=False" in source
    assert "if any(weights_directory.iterdir()):" in source
    assert "record_checkpoint_integrity(" in source
    assert "higgs_weights_volume.commit()" in source
    assert '"text-to-speech-hugging-face-authentication"' not in source
    assert "HUGGING_FACE_AUTHENTICATION_SECRET_NAME" in source


@pytest.mark.parametrize("pin", ["not-a-sha", "F" * 40, "f" * 39])
def test_higgs_research_checkpoint_rejects_invalid_revision(pin):
    with patch.dict(os.environ, {
        "TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL": "",
        "TTS_HIGGS_CHECKPOINT_REVISION_SHA": pin,
    }), pytest.raises(ValueError, match="40 lowercase"):
        runpy.run_path(str(SOURCE_PATH), run_name="cpu_only_test")


@pytest.mark.parametrize("document_name", ["DEPLOYMENT.md", "DEPLOYMENT.vi.md"])
def test_higgs_research_download_is_documented_separately(document_name):
    guide = (ROOT / "docs" / document_name).read_text()
    assert "scripts/higgs_research_checkpoint.py" in guide
    assert "TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL" in guide
    assert "APPROVED_HIGGS_RESEARCH_CPU_DOWNLOAD_WITH_CHARGES" in guide
    assert "TTS_HIGGS_CHECKPOINT_REVISION_SHA" in guide
