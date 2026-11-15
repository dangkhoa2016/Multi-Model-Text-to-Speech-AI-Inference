# Tái lập kết quả

> 🌐 Language / Ngôn ngữ: [English](REPRODUCIBILITY.md) | **Tiếng Việt**

Ghi source SHA, checkpoint revision, CUDA/runtime image digest, tham số request và WAV SHA-256 theo từng nghiệm thu. CPU CI không thay thế bằng chứng GPU.

Dependency MOSS và VoxCPM2 được đóng gói XZ nhị phân. Verifier khôi phục file requirement gốc byte-for-byte và đối chiếu SHA-256 trước khi Modal hoặc uv dùng. Hash từng wheel vẫn bắt buộc; dữ liệu lỗi phải fail-closed.

Chạy python scripts/verify_source_checksums.py --check và python -m pytest -q. Không retry GPU job indeterminate.

Xem [kiểm thử](TESTING.vi.md) và [bằng chứng](RELEASE_EVIDENCE.vi.md).
