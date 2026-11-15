"""Audited immutable Python dependency locks for isolated model runtimes.

Verify a shipped lock before handing it to Modal's hash-enforcing installer.
These checks perform local file I/O only; they never install or launch models.
"""

from __future__ import annotations

import hashlib
import lzma
import os
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MOSS_LOCK = "runtime_locks/moss-sglang-omni-python312-linux-x86_64.txt"
MOSS_SHA256 = "9e024c43bdc08b2f67b4f2b9b15b8fde30542663fab5cd31d728905968859297"
VOXCPM2_LOCK = "runtime_locks/voxcpm2-python312-linux-x86_64.txt"
VOXCPM2_SHA256 = "0432c003fcbf7e2eb877ea0bb73df19b81ff48f84dcb8aff4f41c4d652a901d2"
MOSS_PACKAGE_VERSION = "0.2.0"
VOXCPM_PACKAGE_VERSION = "2.0.3"
AUDITED_PYTHON_VERSION = "3.12"
AUDITED_UV_VERSION = "0.13.5"


def verified_lockfile(relative_path: str, expected_sha256: str) -> str:
    """Reject missing, changed, or redirected dependency manifests before build."""
    lock = REPOSITORY_ROOT / relative_path
    trusted = (REPOSITORY_ROOT / "runtime_locks").resolve()
    if lock.is_symlink() or lock.resolve().parent != trusted or not lock.parent.is_dir():
        raise RuntimeError("Missing or unsafe audited runtime lockfile: " + relative_path)
    if not lock.exists():
        archive = lock.with_name(lock.name + ".xz")
        if archive.is_symlink() or not archive.is_file() or archive.resolve().parent != trusted:
            raise RuntimeError("Missing or unsafe audited runtime lockfile: " + relative_path)
        try:
            content = lzma.decompress(archive.read_bytes(), memlimit=128*1024*1024)
        except (lzma.LZMAError, EOFError, ValueError) as error:
            raise RuntimeError("Corrupt compressed audited runtime lockfile") from error
        if len(content)>2*1024*1024 or hashlib.sha256(content).hexdigest()!=expected_sha256:
            raise RuntimeError("Audited runtime lockfile SHA-256 mismatch: " + relative_path)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=lock.parent, prefix=".lock-", delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(content)
            os.chmod(temporary,0o644)
            os.replace(temporary,lock)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    if not lock.is_file() or lock.is_symlink():
        raise RuntimeError("Missing or unsafe audited runtime lockfile: " + relative_path)
    actual=hashlib.sha256(lock.read_bytes()).hexdigest()
    if actual!=expected_sha256:
        raise RuntimeError("Audited runtime lockfile SHA-256 mismatch: " + relative_path)
    return relative_path
