# Vận hành demo Modal ngắn hạn

> 🌐 Language / Ngôn ngữ: [English](OPERATIONS.md) | **Tiếng Việt**

Xác minh workspace, environment và secrets trước khi deploy. Kiểm tra Alembic revision và job tồn đọng bằng database preflight chỉ đọc. Chỉ gửi canary có giới hạn và xác thực; lưu idempotency key, trạng thái terminal, WAV checksum.

CPU scheduler có thể đánh thức queued job. Không retry GPU job running hoặc indeterminate; xác minh worker cũ ngừng trước khi reconcile.

Stop Modal app sau demo để dừng API và scheduled CPU functions. Database và Volume phát sinh phí riêng. GPU budget ledger chỉ là ước tính, không phải hóa đơn.

Xem [triển khai](DEPLOYMENT.vi.md), [xử lý sự cố](TROUBLESHOOTING.vi.md).
