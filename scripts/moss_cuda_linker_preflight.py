"""Prepare the audited CUDA Runtime linker paths required by SGLang JIT.

Runs only at isolated Modal image-build time, without GPU or checkpoints.
"""
from __future__ import annotations

import ctypes
import pathlib
import subprocess
import tempfile


def prepare_cuda_linker() -> None:
    cuda_root = pathlib.Path("/usr/local/cuda")
    if not cuda_root.is_symlink():
        raise RuntimeError("Expected preflight-created audited CUDA root symlink")

    library_directory = cuda_root / "lib"
    runtime_library = library_directory / "libcudart.so.13"
    if not runtime_library.is_file():
        raise RuntimeError("Audited CUDA Runtime shared library is missing")

    linker_name = library_directory / "libcudart.so"
    if linker_name.is_symlink():
        if linker_name.resolve() != runtime_library.resolve():
            raise RuntimeError("Unexpected libcudart.so symlink target")
    elif linker_name.exists():
        if linker_name.resolve() != runtime_library.resolve():
            raise RuntimeError("Unexpected existing libcudart.so library")
    else:
        linker_name.symlink_to(runtime_library.name)

    lib64 = cuda_root / "lib64"
    if lib64.is_symlink():
        if lib64.resolve() != library_directory.resolve():
            raise RuntimeError("Unexpected CUDA lib64 symlink")
    elif lib64.exists():
        if lib64.resolve() != library_directory.resolve():
            raise RuntimeError("Unexpected existing CUDA lib64 directory")
    else:
        lib64.symlink_to("lib", target_is_directory=True)

    linker_config = pathlib.Path("/etc/ld.so.conf.d/tts-audited-cuda-runtime.conf")
    linker_config.write_text(str(library_directory.resolve()) + "\n")
    subprocess.run(["ldconfig"], check=True, capture_output=True, text=True, timeout=30)

    with tempfile.TemporaryDirectory() as name:
        work = pathlib.Path(name)
        source = work / "sm89.cu"
        obj = work / "sm89.o"
        shared_library = work / "sm89.so"
        source.write_text(
            'extern "C" __global__ void smoke(float *p) { p[0] += 1.0f; }\n'
        )
        subprocess.run([
            str(cuda_root / "bin" / "nvcc"),
            "-c", str(source), "-o", str(obj), "-Xcompiler", "-fPIC",
            "-gencode=arch=compute_89,code=sm_89",
            "-std=c++20", "-O3", "--expt-relaxed-constexpr",
        ], check=True, capture_output=True, text=True, timeout=90)
        subprocess.run([
            "c++", str(obj), "-shared",
            "-L/usr/local/cuda/lib64", "-lcudart",
            "-o", str(shared_library),
        ], check=True, capture_output=True, text=True, timeout=30)
        if not shared_library.is_file() or shared_library.stat().st_size == 0:
            raise RuntimeError("CUDA shared object was not created")
        ctypes.CDLL(str(shared_library))
        print("MOSS_NVCC_SM89_COMPILE_AND_LIBCUDART_LINK=PASS", flush=True)
        print("MOSS_NVCC_LINKED_SO_BYTES=" + str(shared_library.stat().st_size), flush=True)


if __name__ == "__main__":
    prepare_cuda_linker()
