"""Validate notebook JSON and executable Python cells without cloud/GPU access."""
import json
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "notebooks" / "tts_api_demo.ipynb"
notebook = json.loads(path.read_text(encoding="utf-8"))
assert notebook["nbformat"] == 4
assert notebook["cells"] and notebook["cells"][0]["cell_type"] == "markdown"
for index, cell in enumerate(notebook["cells"]):
    if cell["cell_type"] == "code":
        compile("".join(cell["source"]), f"tts_api_demo.ipynb:cell{index}", "exec")
        assert not cell.get("outputs"), "Public notebook must have no captured output"
code_cells = "\n".join("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
assert 'job_status_data["status"]' in code_cells, "Polling must use the public API status field"
assert 'job_status_data["job_status_response"]' not in code_cells
assert 'SUBMIT_PAID_INFERENCE_JOB = False' in code_cells, "Notebook must not start GPU by default"
print("Text-to-Speech notebook schema, API keys and Python cells valid; no GPU activity")
