# Hợp đồng HTTP API TTS

🌐 [English](API.md) | [Tiếng Việt](API.vi.md)

Mọi endpoint `/v1/*` ngoại trừ `/v1/health` cần HTTPS và `Authorization: Bearer <CLIENT_TOKEN>`. Không để token trong URL, notebook công khai hoặc log. Response có `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Token operator độc lập với token client.

## Khám phá model

- `GET /v1/health`: chỉ kiểm tra gateway, không khởi động GPU.
- `GET /v1/models`: danh sách model, checkpoint, giấy phép, capabilities và JSON Schema riêng `model_config_schema`.
- `GET /v1/models/{model_id}`: kiểu dữ liệu/enum/giới hạn/default của model.

Model ID: `moss-tts-v1.5`, `higgs-tts-3`, `voxcpm2`. Khóa `model_config` không hỗ trợ bị từ chối HTTP 422 trước khi gọi GPU. Thuộc tính giọng của Vox là mô tả bằng ngôn ngữ tự nhiên, không phải lựa chọn định danh người nói chính xác. Runtime GPU chưa được nghiệm thu.

## Gửi job bền vững

`POST /v1/audio/jobs` với header `Idempotency-Key` **được khuyến nghị mạnh** (8–128 ký tự ASCII hợp lệ). Giữ nguyên key cho cùng một yêu cầu khi mạng bị timeout. Gửi lại cùng client/key/payload trả HTTP 202 cùng `job_id` và `reused=true`; cùng key với payload khác trả HTTP 409. Namespace key thuộc từng client. Khi dữ liệu terminal bị xóa theo retention, key cũng hết hiệu lực; không bảo đảm dedup mãi mãi.

```http
POST /v1/audio/jobs HTTP/1.1
Authorization: Bearer YOUR_PRIVATE_CLIENT_TOKEN
Idempotency-Key: speech-2026-001
Content-Type: application/json
```

```json
{
  "model": "voxcpm2", "input": "Xin chào, chào mừng các bạn!",
  "model_config": {
    "gender": "female", "age": "young", "emotion": "cheerful",
    "pace": "slow", "cfg_value": 2.0, "inference_timesteps": 10
  }
}
```

Phản hồi minh họa:

```json
{"job_id":"<32-hex-id>","model":"voxcpm2","status":"queued","reused":false,"status_url":"/v1/audio/jobs/<32-hex-id>"}
```

Phải lưu thành công trạng thái `queued` trong PostgreSQL trước khi đánh thức CPU runner. Rate-limit Redis cho việc nhận job mặc định 12 lần/5 phút/client, thêm trần 100 job/ngày/client và 500 job/ngày toàn hệ thống; DB giới hạn số job chưa hoàn tất. Retry idempotent thường không tiêu hao lại quota job; khi nhiều request cùng lúc tranh một key có thể tốn quota nhưng không tạo duplicate GPU.

## Trạng thái và tải audio

- `GET /v1/audio/jobs/{job_id}`: trạng thái thuộc client — `queued`, `running`, `completed`, `failed`, `indeterminate`, không lộ prompt/audio tham chiếu.
- `GET /v1/audio/jobs/{job_id}/audio`: WAV có xác thực khi `completed`; HTTP 409 nếu chưa sẵn sàng, 404 nếu không có quyền hoặc dữ liệu hết hạn.
- `GET /v1/ops/metrics`: **chỉ owner token**, tổng hợp trạng thái từ database; không có token, dữ liệu giọng, IP, tên client hoặc prompt. Token client khác nhận 404.

Kết quả thành công trả metadata WAV (`sample_rate`, `channels`, `duration_seconds`, `audio_bytes`), `inference_ms` và `audio_url`. `indeterminate` nghĩa là không xác định được kết quả GPU; **không tự gửi lại job với idempotency key mới**. Đọc [Vận hành](DEPLOYMENT.vi.md).

## Clone giọng

`reference_audio_base64` nhận Base64 thô của **PCM WAV** (không có `data:`), tối đa 6 MB/30 giây, mono/stereo, 8–96 kHz. Cần `reference_consent: true`, nhưng đây chỉ là xác nhận do client khai báo, không phải bằng chứng pháp lý/xác minh người nói. Với Vox `cloning_mode: continuation`, cần thêm `reference_text`.

## Tham số theo model

| Model | Tham số |
| --- | --- |
| MOSS-TTS-v1.5 | `language`, `instructions`, `voice`, `duration_tokens`, `max_new_tokens`, `text_temperature`, `audio_temperature`, `text_top_p`, `audio_top_p`, `text_top_k`, `audio_top_k`, `audio_repetition_penalty`, `seed` |
| Higgs TTS 3 | `voice`, `emotion`, `style`, `speed`, `pitch`, `expressiveness`, `temperature`, `top_p`, `top_k`, `max_new_tokens`, `seed` |
| VoxCPM2 | `voice_description`, `gender`, `age`, `emotion`, `tone`, `pace`, `cloning_mode`, `cfg_value`, `inference_timesteps`, `min_len`, `max_len`, `normalize`, `denoise` (chỉ `false`), `retry_badcase`, `retry_badcase_max_times`, `retry_badcase_ratio_threshold` |

Catalog trả thêm `enabled` cho từng model. Higgs TTS 3 mặc định không bật; chỉ được bật khi người vận hành xác nhận quyền sử dụng thích hợp tại thời điểm deploy. Gửi job cho model bị tắt trả HTTP 503 và không kích hoạt GPU. `GET /v1/models/{id}` là nguồn chuẩn cho schema/enum/giới hạn. API không chấp nhận kwargs loader tùy ý hoặc đường dẫn file nội bộ. Cần kiểm tra giấy phép Higgs trước khi cung cấp inference dạng hosted API.

## Bảo mật và mã lỗi

- `401`: không có/sai token. `429`: vượt mức yêu cầu trước auth, 3 lần sai token trong cửa sổ 5 phút dẫn đến khóa tạm 15 phút, quota job hoặc polling; có `Retry-After`. Token hợp lệ vẫn hoạt động trong thời gian lockout nếu chưa vượt pre-auth limit.
- `413`: body vượt giới hạn 8,5 MB; `422`: input/model option/WAV không hợp lệ, lỗi validation đã loại bỏ nội dung nhạy cảm; `409`: idempotency key xung đột hoặc audio chưa sẵn sàng.
- `404`: job không tồn tại hoặc không thuộc client; `503`: Redis/database/backend không sẵn sàng, từ chối an toàn.
- Pre-auth: 120 request/phút/peer, 600/phút toàn gateway replica, sử dụng Redis nguyên tử. Job mới: 12/5 phút/client, 100/ngày/client, 500/ngày toàn hệ thống; GET status/audio 120/phút/client. PostgreSQL: 20 job chưa xong/client, 20/model; một `running` hoặc `indeterminate` giữ slot của từng model.

Những giới hạn này không thay WAF và budget Modal. WAV đầu ra lưu mã hóa Fernet vào PostgreSQL trong thời gian hữu hạn (mặc định 7 ngày), **chưa có R2/S3**. Xem [Kiến trúc](ARCHITECTURE.vi.md), [Bảo mật](SECURITY.vi.md).
