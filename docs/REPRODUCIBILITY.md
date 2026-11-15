# Reproducibility

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](REPRODUCIBILITY.vi.md)

Capture source SHA, checkpoint revision, CUDA/runtime image digest, request options and WAV SHA-256 for each acceptance. CPU CI alone is not GPU evidence.

Generated MOSS and VoxCPM2 dependencies are distributed as XZ-compressed binary lock archives. The runtime verifier restores the byte-identical original requirement text and verifies the original SHA-256 before Modal or uv may use it. Per-wheel hashes remain required; corruption fails closed.

Run python scripts/verify_source_checksums.py --check and python -m pytest -q. Do not retry indeterminate GPU requests.

See [testing](TESTING.md) and [release evidence](RELEASE_EVIDENCE.md).
