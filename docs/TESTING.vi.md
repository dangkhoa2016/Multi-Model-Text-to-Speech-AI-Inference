# Kiểm thử và nghiệm thu

> 🌐 Language / Ngôn ngữ: [English](TESTING.md) | **Tiếng Việt**

Kiểm thử CPU chạy bằng python -m pytest -q, ruff check . và checksum. Integration PostgreSQL 16 / Redis 7 chạy trong môi trường CI tách biệt, không dùng production credentials.

Nghiệm thu GPU là bước riêng, không thể suy luận từ CPU CI. Một GPU canary không chứng minh tải nhiều replica, HA hoặc quyền hosted Higgs TTS 3.

Xem [bằng chứng phát hành](RELEASE_EVIDENCE.vi.md).
