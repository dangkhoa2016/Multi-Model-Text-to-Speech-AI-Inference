# Xử lý sự cố

> 🌐 Language / Ngôn ngữ: [English](TROUBLESHOOTING.md) | **Tiếng Việt**

**HTTP 503 / model tắt:** kiểm tra lựa chọn model khi deploy; riêng Higgs, kiểm tra image reference bất biến và quyền sử dụng. Với lỗi 503 khác, kiểm tra dịch vụ không khả dụng. Nếu model đã chọn lỗi lúc khởi động, kiểm tra integrity checkpoint và runtime log. Không bỏ qua fail-closed.

**Lock sai hash:** xác minh XZ artifact và SHA-256 gốc, không sửa hash để chấp nhận dữ liệu lỗi.

**HTTP 401/429:** kiểm tra quyền client, peer lockout và Redis quota mà không lộ token.

**HTTP 409:** idempotency key đã dùng với payload khác.

**queued:** kiểm tra PostgreSQL và CPU dispatcher; scheduler có thể đánh thức job queued.

**indeterminate:** không tự retry GPU; phải xác minh worker cũ dừng trước khi owner reconcile.

**Thiếu WAV:** kiểm tra ownership, terminal status, encryption key và retention.

Xem [vận hành](OPERATIONS.vi.md), [bảo mật](SECURITY.vi.md).
