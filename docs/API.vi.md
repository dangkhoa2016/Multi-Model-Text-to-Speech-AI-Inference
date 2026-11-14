# Hợp đồng HTTP API TTS

> 🌐 Language / Ngôn ngữ: [English](API.md) | **Tiếng Việt**


Mọi endpoint `/v1/*` ngoại trừ `/v1/health` cần HTTPS và `Authorization: Bearer <CLIENT_TOKEN>`. Không để token trong URL, notebook công khai hoặc log. Response có `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Token operator độc lập với token client.

## Landing page song ngữ công khai

- `GET /` hiển thị HTML tiếng Anh, `GET /?lang=vi` hiển thị tiếng Việt. Giá trị ngôn ngữ không hỗ trợ sẽ trở về tiếng Anh.
- Trang hoàn toàn tĩnh, không chứa secrets, dữ liệu client, tài nguyên ngoài hoặc thông báo sai rằng GPU đã sẵn sàng; render không khởi chạy GPU.
- `/v1/models` và toàn bộ job API vẫn yêu cầu Bearer token.

## Khám phá model

- `GET /v1/health`: chỉ kiểm tra gateway, không khởi động GPU.
- `GET /v1/models`: danh sách model, checkpoint, giấy phép, capabilities và JSON Schema riêng `model_config_schema`.
- `GET /v1/models/{model_id}`: kiểu dữ liệu/enum/giới hạn/default của model.

Model ID: `moss-tts-v1.5`, `higgs-tts-3`, `voxcpm2`. Khóa `model_config` không hỗ trợ bị từ chối HTTP 422 trước khi gọi GPU. Thuộc tính giọng của Vox là mô tả bằng ngôn ngữ tự nhiên, không phải lựa chọn định danh người nói chính xác. Ngày 21/11/2026, MOSS-TTS-v1.5 và VoxCPM2 đã hoàn thành hai request HTTPS có xác thực riêng biệt, trả về WAV trong cùng một canary Modal L40S độc lập bằng source P1 (tổng cộng 19/19 kiểm tra API cuối PASS). Kết quả chỉ xác nhận cấu hình đã thử, không chứng nhận mọi deployment hoặc mọi tham số model. Higgs TTS 3 hosted inference chưa được nghiệm thu và có giấy phép riêng.

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

**Cloning production:** Chỉ khai báo `reference_consent: true` không đủ xác minh quyền sử dụng giọng. Gateway CPU từ chối request có `reference_audio_base64` bằng **HTTP 403 trước khi enqueue**, trừ khi operator bật `TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING=1` **và** đưa client ID vào `TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON` **trên máy chạy `modal deploy`**, rồi redeploy. Cờ không chứa bí mật được truyền vào CPU image; thao tác này không xác minh consent. Xem [Triển khai](DEPLOYMENT.vi.md). Lỗi từ chối trước khi nhận job chỉ cho biết kết quả của lần gửi đó. Nếu POST trước bị timeout hoặc mất phản hồi, hãy gửi lại trên gateway ban đầu với cùng client, key và payload trong thời gian gateway còn lưu job. Nếu đã có job ID, dùng ID để xem trạng thái. Xem [hướng dẫn notebook](../notebooks/tts_api_demo.ipynb).

## Tham số theo model

| Model | Tham số |
| --- | --- |
| MOSS-TTS-v1.5 | `language`, `instructions`, `voice`, `duration_tokens`, `max_new_tokens`, `text_temperature`, `audio_temperature`, `text_top_p`, `audio_top_p`, `text_top_k`, `audio_top_k`, `audio_repetition_penalty`, `seed` |
| Higgs TTS 3 | `voice`, `emotion`, `style`, `speed`, `pitch`, `expressiveness`, `temperature`, `top_p`, `top_k`, `max_new_tokens`, `seed` |
| VoxCPM2 | `voice_description`, `gender`, `age`, `emotion`, `tone`, `pace`, `cloning_mode`, `cfg_value`, `inference_timesteps`, `min_len`, `max_len`, `normalize`, `denoise` (chỉ `false`), `retry_badcase`, `retry_badcase_max_times`, `retry_badcase_ratio_threshold` |

Catalog trả thêm `enabled` cho từng model. Trong deployment Modal, enabled phản ánh lựa chọn của operator, không phải GPU readiness. MOSS-TTS-v1.5 và VoxCPM2 dùng runtime lock đã kiểm toán trong repository; khi chọn Higgs cần SGLang image reference bất biến đã kiểm toán và xác nhận quyền sử dụng. Cấu hình deployment bắt buộc không hợp lệ sẽ bị từ chối khi validation. Request cho model không được chọn trả HTTP 503 trước khi gọi GPU; worker được chọn kiểm tra integrity checkpoint lúc khởi động. `GET /v1/models/{id}` là nguồn chuẩn cho schema/enum/giới hạn. API không chấp nhận kwargs loader tùy ý hoặc đường dẫn file nội bộ. Cần kiểm tra giấy phép Higgs trước khi cung cấp inference dạng hosted API.

## Bảo mật và mã lỗi

- `401`: không có/sai token. `429`: vượt mức yêu cầu trước auth, 3 lần sai token trong cửa sổ 5 phút dẫn đến khóa tạm 15 phút, quota job, ngân sách GPU ước tính/ngày hoặc polling; có `Retry-After`. Token hợp lệ vẫn hoạt động trong thời gian lockout nếu chưa vượt pre-auth limit.
- `413`: body vượt giới hạn 8,5 MB; `422`: input/model option/WAV không hợp lệ, lỗi validation đã loại bỏ nội dung nhạy cảm; `409`: idempotency key xung đột hoặc audio chưa sẵn sàng.
- `404`: job không tồn tại hoặc không thuộc client; `503`: Redis/database/backend không sẵn sàng, từ chối an toàn.
- Pre-auth: 120 request/phút/peer, 600/phút toàn gateway replica, sử dụng Redis nguyên tử. Job mới: 12/5 phút/client, 100/ngày/client, 500/ngày toàn hệ thống; GET status/audio 120/phút/client. PostgreSQL: 20 job chưa xong/client, 20/model; một `running` hoặc `indeterminate` giữ slot của từng model.

Những giới hạn này không thay WAF và budget Modal. WAV đầu ra lưu mã hóa Fernet vào PostgreSQL trong thời gian hữu hạn (mặc định 7 ngày), audit event liên quan được xóa trong cùng transaction khi job hết hạn, **chưa có R2/S3**. Xem [Kiến trúc](ARCHITECTURE.vi.md), [Bảo mật](SECURITY.vi.md).

## Timing, usage và diagnostics có cấu trúc

Endpoint có xác thực `GET /v1/audio/jobs/{job_id}` chỉ trả dữ liệu cho
client sở hữu job. Trường `inference_ms` và metadata WAV cũ được giữ nguyên,
bổ sung `timing`, `usage` và `diagnostics`. Job đang chờ hoặc không thành
công sẽ có `null` ở các số liệu chưa đo.

- `timing.queue_wait_ms`: từ lúc database nhận job tới lúc CPU runner claim.
- `timing.worker_wall_ms`: thời gian CPU coordinator đợi GPU RPC;
  có thể bao gồm cold start, scheduler và network overhead.
- `timing.inference_ms`: thời gian xử lý trong GPU worker sau khởi động, bao gồm chuẩn bị reference, dựng payload, gọi tổng hợp, mã hóa WAV (VoxCPM2), kiểm tra kích thước và metadata. **Không phải** thời gian model generate thuần túy hoặc thời gian đầu-cuối. Giữ nguyên ý nghĩa của `inference_ms` cấp cao.
- `timing.audio_validation_ms`: lần đầu kiểm tra PCM WAV trên CPU,
  không tính mã hóa và lưu database sau đó.
- `timing.total_ms`: từ lúc nhận job tới timestamp hoàn tất trong database;
  không tính thời gian client tải WAV.
- `timing.model_load_ms`: `null` do backend chưa đo riêng.
- `usage.input_characters`: số ký tự Unicode của nội dung đã được nhận,
  **không phải** token. Job cũ trước migration có thể là `null`.
- `usage.input_tokens`, `usage.output_tokens`: `null` nếu backend không
  cung cấp số liệu tokenizer/codec thực, không tự ước lượng từ ký tự.
- `usage.audio_duration_seconds`: thời lượng WAV đã được xác thực;
  `usage.realtime_factor` = thời gian xử lý worker nêu trên / thời lượng WAV; không phải benchmark thuần model.
- `diagnostics.request_id`: dùng job ID làm mã đối chiếu;
  `diagnostics.events` trả tối đa 20 sự kiện vòng đời có timestamp
  chỉ của owner. `error_code` và `warnings` đã được giới hạn;
  **không** trả raw Modal logs, traceback, prompt, audio hoặc API tokens.
- `audio_quality` được đo từ toàn bộ mẫu PCM của WAV khi job hoàn thành, không lấy từ lời mô tả của model: `rms_dbfs`, `peak_dbfs`, `clipped_samples`, `total_samples`, `clipped_fraction`, `sample_width_bits`, `warnings`. Đếm các mẫu nằm trong **một bước lượng tử** sát đỉnh dương hoặc âm; cảnh báo `clipped_samples_present` cũng xuất hiện trong `diagnostics.warnings`, nhưng **không từ chối, không điều chỉnh âm lượng hay waveform**. Giá trị dBFS của tín hiệu im lặng là `null`, không tạo số đo giả. Kết quả có thể chỉ ra đỉnh gần clipping, **không khẳng định bằng tai có méo tiếng**.
- `audio_quality: null` với job chưa xong, thất bại, không xác định hoặc WAV của job cũ đã lưu trước thay đổi này. Không cần migration PostgreSQL vì chỉ bổ sung trường trong JSON metadata đã lưu. WAV tải qua endpoint audio giữ nguyên từng byte.
- CPU mock chỉ trả WAV im lặng, không được nhầm với GPU inference thực.

Xem JSON minh họa đầy đủ trong [API tiếng Anh](API.md); đây không phải số
liệu benchmark. Alembic `0004` thêm cột nullable; `0005` chuẩn hóa nullability của fingerprint ở schema cũ và tạo index audit. Phải backup và chạy migration trước khi thay CPU gateway. Tương thích với dữ liệu
và runner cũ. Phải migration database trước khi deploy CPU gateway mới;
xem [Triển khai](DEPLOYMENT.vi.md).
