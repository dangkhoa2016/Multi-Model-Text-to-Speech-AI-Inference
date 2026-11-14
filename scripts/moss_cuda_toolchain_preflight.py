"""Verify pinned CUDA compiler and its standard location for MOSS JIT kernels.

This runs only while building an explicitly audited MOSS worker image.
It does not allocate GPU or access model checkpoint Volumes.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sysconfig
from importlib.metadata import version


def prepare_cuda_toolchain() -> None:
    expected_versions = {
        "nvidia-cuda-nvcc": "13.5.37",
        "nvidia-cuda-runtime": "13.0.96",
    }
    for package, expected in expected_versions.items():
        actual = version(package)
        if actual != expected:
            raise RuntimeError(
                f"Audited CUDA package mismatch: {package} expected {expected}, got {actual}"
            )

    package_root = (
        pathlib.Path(sysconfig.get_paths()["purelib"]) / "nvidia" / "cu13"
    )
    compiler = package_root / "bin" / "nvcc"
    if not compiler.is_file():
        raise RuntimeError("Audited CUDA compiler binary is missing")

    cuda_home = pathlib.Path("/usr/local/cuda")
    if cuda_home.is_symlink():
        if cuda_home.resolve() != package_root.resolve():
            raise RuntimeError("CUDA path points to an unaudited toolkit")
    elif cuda_home.exists():
        raise RuntimeError("CUDA root already exists and must not be overwritten")
    else:
        cuda_home.symlink_to(package_root, target_is_directory=True)

    result = subprocess.run(
        [str(cuda_home / "bin" / "nvcc"), "--version"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    if re.search(r"release\s+13\.5", result.stdout) is None:
        raise RuntimeError("CUDA compiler release differs from audited lock")

    print("MOSS_AUDITED_CUDA_TOOLCHAIN=PASS", flush=True)
    print("MOSS_CUDA_HOME=" + str(cuda_home.resolve()), flush=True)


if __name__ == "__main__":
    prepare_cuda_toolchain()
