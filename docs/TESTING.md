# Testing and qualification

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](TESTING.vi.md)

CPU suite: python -m pytest -q; Ruff: ruff check .; notebook validator: python scripts/validate_notebook.py; checksum check: python scripts/verify_source_checksums.py --check.

PostgreSQL 16 and Redis 7 integration runs on isolated GitHub Actions services. Never use production credentials.

Real GPU acceptance is a separate controlled test. A single job does not qualify concurrency, high availability or Higgs TTS 3 hosted rights.

See [release evidence](RELEASE_EVIDENCE.md).
