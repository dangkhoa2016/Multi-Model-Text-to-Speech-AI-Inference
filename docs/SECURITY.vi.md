# Bảo mật API Gateway và quyền riêng tư

🌐 [English](SECURITY.md) | [Tiếng Việt](SECURITY.vi.md)

Đây là mô tả các biện pháp **đã có trong mã nguồn**, không phải giấy chứng nhận an toàn cho deployment Modal. Kiểm thử CPU/SQLite không chứng minh được PostgreSQL/Redis nhiều replica, bảo mật runtime GPU hoặc tính hợp lệ của sự đồng ý clone giọng.

## Xác thực và phân quyền

- Toàn bộ `/v1/*` ngoại trừ `/v1/health` xác thực **trước khi đọc JSON/body**. Token Bearer phải ngẫu nhiên, có entropy cao. Gateway so sánh SHA-256 token bằng constant-time; registry chỉ chứa hash và owner token hash riêng, đặt trong Secret `text-to-speech-api-authentication`, **không truyền cho GPU**.
- Mỗi client có ID cố định gắn với job, idempotency và quyền đọc audio; truy cập job của client khác trả 404. Thu hồi bằng việc xóa hash hoặc cấu hình `TEXT_TO_SPEECH_REVOKED_CLIENT_IDS`, sau đó rollout gateway.
- `/v1/ops/metrics` chỉ nhận owner token, trả số đếm trạng thái DB, không có tên client, prompt, audio hay token.
- Rate-limit theo peer ASGI, không tin `X-Forwarded-For` do client tự gửi. Nếu reverse proxy gom nhiều người vào cùng peer IP, các client có thể chia sẻ quota; cần kiểm tra topology hạ tầng.

## Rate-limit phân tán

`RedisGuard` dùng Lua và thời gian của Redis để kiểm soát nguyên tử các cửa sổ cố định, gồm hash-tag tương thích Redis Cluster; production chỉ nhận URL `rediss://`, Redis Secret chỉ có ở gateway. Redis key dùng hash của định danh, không lưu IP/token gốc.

| Biện pháp | Mặc định |
| --- | ---: |
| Yêu cầu trước auth mỗi peer | 120/phút |
| Yêu cầu trước auth toàn hệ thống | 600/phút |
| Sai token trước khi khóa | 3 lần trong một cửa sổ 5 phút |
| Thời gian khóa tạm | 900 giây |
| Tạo job mỗi client | 12/300 giây |
| Trần job theo ngày mỗi client / toàn hệ thống | 100/ngày / 500/ngày |
| GET trạng thái/audio mỗi client | 120/phút |
| Job chưa hoàn tất mỗi client (PostgreSQL) | 20 |
| Job chưa hoàn tất mỗi model (PostgreSQL) | 20 |
| Một model được chạy/chưa rõ kết quả | 1 job |
| Giới hạn body có xác thực | 8.500.000 byte |

Token hợp lệ sẽ xóa bộ đếm sai/lockout nếu chưa vượt quota pre-auth. Nếu Redis hỏng, gateway trả 503 (fail-closed), không tự bỏ qua rate-limit. CPU mock chỉ có giới hạn trong một tiến trình, không phải bảo vệ phân tán. Mở internet cần WAF và giới hạn ở tầng hạ tầng.

## Mã hóa dữ liệu và giới hạn quyền

- `encrypted_input` và `encrypted_audio` trong PostgreSQL được mã hóa Fernet bằng khóa ổn định, chỉ có ở gateway và CPU dispatcher. Input mã hóa được xóa sau thành công hoặc khi kết quả không chắc chắn. Mất khóa sẽ không giải mã được job/audio; đổi khóa phải mã hóa lại có kiểm soát.
- Metadata/audit trong DB chỉ có mã job, owner, loại sự kiện và thời gian, không chủ động ghi token, nội dung text, audio hoặc exception nhạy cảm.
- Lỗi Pydantic 422 đã loại bỏ `input`/`ctx`, kiểm tra kích thước body trước parse, chỉ chấp nhận WAV PCM, không nhận URL/path tùy ý để tránh SSRF và đường dẫn nguy hiểm.
- WAV đầu ra tối đa 32 MB mã hóa trong PostgreSQL BYTEA; **chưa tích hợp R2/S3**. CPU sweep xóa dữ liệu terminal sau retention (mặc định 7 ngày). Backup và nền tảng ngoài có thời hạn lưu độc lập.
- GPU chỉ nhận payload cần thiết, không có Secret của PostgreSQL, Redis hay Bearer token. SGLang-Omni bind `127.0.0.1`, không công khai cổng nội bộ. Image `:dev` và mã upstream vẫn cần kiểm toán và khóa phiên bản.

## Phục hồi khi GPU không chắc chắn

CPU sweep mỗi phút chỉ đánh thức `queued`. Khi đã `running`, heartbeat quá hạn được báo `indeterminate` nhưng không tự retry. Nếu RPC GPU thất bại không rõ hậu quả, DB lưu `indeterminate` và **giữ slot GPU**. Chỉ người vận hành sau khi kiểm tra Modal logs, xác nhận GPU đã ngừng mới được đánh dấu đóng job. Không coi heartbeat timeout là bằng chứng GPU đã dừng. Hệ thống không bảo đảm exactly-once khi mạng/worker lỗi.

## Còn phải hoàn thiện

Cần xác minh quyền dùng giọng mẫu (cờ `reference_consent=true` chỉ do client khai báo), chính sách chống lạm dụng, trần chi phí GPU thật, kiểm thử PostgreSQL/Redis nhiều replica và GPU, backup/restore, WAF, giám sát, quét dependency, khóa image/model revision và kiểm tra quyền thương mại Higgs. PostgreSQL BYTEA chỉ phù hợp ở quy mô nhỏ, cân nhắc R2/S3 có mã hóa/retention.

Chạy CPU tests bằng `python -m pytest -q`; đọc [Vận hành](DEPLOYMENT.vi.md) trước khi deploy GPU.
