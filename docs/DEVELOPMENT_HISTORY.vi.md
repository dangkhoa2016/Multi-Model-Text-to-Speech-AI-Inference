# Lịch sử phát triển kỹ thuật

> 🌐 Language / Ngôn ngữ: [English](DEVELOPMENT_HISTORY.md) | **Tiếng Việt**

Tài liệu mô tả các giai đoạn phát triển, không dựng lại hoạt động cá nhân thiếu bằng chứng. Git timestamps là metadata, không chứng minh lúc GPU chạy.

1. Xây dựng model contracts, audio encoding và CPU mock.
2. Triển khai FastAPI Gateway bảo mật, Redis rate-limit, PostgreSQL lưu job mã hóa.
3. Tích hợp Modal GPU workers độc lập và xác minh checkpoint.
4. Bổ sung Alembic, idempotency, heartbeat, reconcile và GPU budget.
5. Hoàn thiện CI, PostgreSQL/Redis integration và runtime locks.
6. Bổ sung tài liệu EN/VI và giấy phép upstream.

MOSS deployment được nghiệm thu ngày 19/11/2026 bằng SHA nguồn cũ. Rewrite lịch sử không đồng nghĩa đã nghiệm thu lại. Xem [bằng chứng](RELEASE_EVIDENCE.vi.md).
