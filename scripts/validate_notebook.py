"""Validate notebook schema, safe defaults and Python cells without GPU access."""

from pathlib import Path

import nbformat

NOTEBOOK_PATH = Path(__file__).resolve().parents[1] / "notebooks" / "tts_api_demo.ipynb"


def validate_notebook(path: Path = NOTEBOOK_PATH) -> None:
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    assert notebook.cells and notebook.cells[0].cell_type == "markdown"
    assert notebook.metadata.kernelspec.name, "A usable kernel name is required"
    code_cells = []
    for index, cell in enumerate(notebook.cells):
        if cell.cell_type == "markdown":
            assert "### English" in cell.source, "Every guide must include English"
            assert "### Tiếng Việt" in cell.source, "Every guide must include Vietnamese"
        if cell.cell_type == "code":
            assert index > 0 and notebook.cells[index - 1].cell_type == "markdown", (
                "Each code cell needs its own immediately preceding Markdown guide"
            )
            assert "### Expected result / Kết quả cần thấy" in notebook.cells[index - 1].source, (
                "Every code step must describe expected output in both languages"
            )
            compile(cell.source, f"{path.name}:cell{index}", "exec")
            assert cell.execution_count is None, "Public notebook must not contain execution counts"
            assert not cell.outputs, "Public notebook must not contain captured output"
            code_cells.append(cell.source)
    code = "\n".join(code_cells)
    assert 'job_status_data["status"]' in code
    assert 'job_status_data["job_status_response"]' not in code
    assert "SUBMIT_PAID_INFERENCE_JOB = False" in code
    assert "RESUME_EXISTING_JOB_ID" in code
    assert "STABLE_REQUEST_ID" in code
    assert "Retry-After" in code


if __name__ == "__main__":
    validate_notebook()
    print("Notebook schema, safe defaults and Python cells valid; no GPU activity")
