"""Refresh and verify SHA-256 hashes for tracked repository files."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKSUM_FILE = PROJECT_ROOT / "SHA256SUMS.txt"


def list_source_paths(*, include_untracked: bool) -> list[str]:
    command = ["git", "-C", str(PROJECT_ROOT), "ls-files", "-z"]
    if include_untracked:
        command.extend(["--cached", "--others", "--exclude-standard"])
    output = subprocess.check_output(command)
    paths = {os.fsdecode(path) for path in output.split(b"\0") if path}
    paths.discard("SHA256SUMS.txt")
    for relative_path in paths:
        source_path = PROJECT_ROOT / relative_path
        if (
            relative_path.startswith("/")
            or ".." in Path(relative_path).parts
            or source_path.is_symlink()
            or not source_path.is_file()
        ):
            raise ValueError(f"Missing or unsafe source file: {relative_path}")
    return sorted(paths)


def calculate_checksums(*, include_untracked: bool) -> dict[str, str]:
    result = {}
    for relative_path in list_source_paths(include_untracked=include_untracked):
        file_hasher = hashlib.sha256()
        with (PROJECT_ROOT / relative_path).open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                file_hasher.update(chunk)
        result[relative_path] = file_hasher.hexdigest()
    return result


def read_checksums() -> dict[str, str]:
    checksums = {}
    for line_number, line in enumerate(CHECKSUM_FILE.read_text().splitlines(), start=1):
        if len(line) < 67 or line[64:66] != "  ":
            raise ValueError(f"Malformed checksum on line {line_number}")
        checksum, relative_path = line[:64], line[66:]
        if not relative_path or any(character not in "0123456789abcdef" for character in checksum):
            raise ValueError(f"Invalid checksum on line {line_number}")
        if relative_path in checksums:
            raise ValueError(f"Duplicate path: {relative_path}")
        checksums[relative_path] = checksum
    return checksums


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Verify tracked file checksums (default)")
    mode.add_argument("--write", action="store_true", help="Regenerate manifest, including new files")
    arguments = parser.parse_args()

    try:
        current = calculate_checksums(include_untracked=arguments.write)
        if arguments.write:
            CHECKSUM_FILE.write_text(
                "".join(f"{digest}  {path}\n" for path, digest in current.items())
            )
            print(f"Updated SHA256SUMS.txt for {len(current)} source files")
            return 0

        recorded = read_checksums()
        missing = sorted(current.keys() - recorded.keys())
        obsolete = sorted(recorded.keys() - current.keys())
        modified = sorted(
            path for path in current.keys() & recorded.keys() if current[path] != recorded[path]
        )
        for label, paths in [("Missing", missing), ("Obsolete", obsolete), ("Modified", modified)]:
            if paths:
                print(f"{label} entries: {', '.join(paths)}")
        if missing or obsolete or modified:
            return 1
        print(f"Verified {len(current)} source checksums")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Checksum validation error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
