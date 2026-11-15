# Triển khai và vận hành Modal

🌐 [English](DEPLOYMENT.md) | [Tiếng Việt](DEPLOYMENT.vi.md)

**Đọc trước khi vận hành.** Mã nguồn đã có CPU gateway với PostgreSQL/Redis và ba GPU adapter, nhưng **chưa kiểm thử GPU thật** và chưa tự triển khai. Deploy sẽ bật CPU sweep mỗi phút và có thể phát sinh chi phí hạ tầng; GPU chỉ được gọi khi thực sự có job hợp lệ. Đặt giới hạn chi tiêu tại Modal, kiểm tra giấy phép từng model (đặc biệt Higgs TTS 3).

## 1. Chuẩn bị một workspace/environment Modal nhất quán

Python 3.11–3.13, PostgreSQL có TLS/backup, Redis/Valkey dùng TLS (`rediss://`), workspace Modal có quota CPU/GPU/storage và client HTTPS. Tách quyền tài khoản runtime với tài khoản migration.

```bash
python -m pip install -e '.[modal,dev]'
modal setup
python -m pytest -q
```

## 2. Tạo bốn Modal Secret riêng biệt

Tạo token ngẫu nhiên đủ mạnh cho từng client và **một owner token riêng** (ASCII tối thiểu 24 ký tự, nên 48+). API chỉ lưu/so sánh SHA-256 của token đủ entropy. Giữ token gốc ở client hoặc secret manager, không lưu vào Git hay ảnh chụp màn hình. Hash token bằng `python scripts/hash_token.py`, tạo khóa mã hóa Fernet bằng:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Dùng Modal Dashboard để tạo **bốn Secret** trong cùng workspace/environment (tránh lộ token trong shell history):

| Secret | Biến môi trường | Nơi sử dụng |
| --- | --- | --- |
| `text-to-speech-api-authentication` | `TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` (JSON client ID → SHA256 hex), `TEXT_TO_SPEECH_OWNER_TOKEN_SHA256` (hash owner), tùy chọn `TEXT_TO_SPEECH_REVOKED_CLIENT_IDS` | Chỉ gateway |
| `text-to-speech-job-database` | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` (`postgresql+psycopg://...`, TLS theo nhà cung cấp), `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` (Fernet), tùy chọn `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT`, `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL`, `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS` | Gateway và CPU runner; **không cấp cho GPU** |
| `text-to-speech-hugging-face-authentication` | `HUGGING_FACE_READ_TOKEN` (token quyền đọc của tài khoản Hugging Face được cấp quyền tải model) | Chỉ kiểm tra/tải model bằng CPU; **không cấp cho gateway/GPU** |
| `text-to-speech-rate-limiting` | `TEXT_TO_SPEECH_REDIS_CONNECTION_URL` (`rediss://...`), tùy chọn `TEXT_TO_SPEECH_REDIS_RATE_LIMIT_NAMESPACE` và các biến `TEXT_TO_SPEECH_RATE_*` và `TEXT_TO_SPEECH_AUTHENTICATION_*` trong `.env.example` | Chỉ gateway |

Ví dụ **cấu trúc** `TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` (placeholder, không phải hash thật):

```json
{"rails-app":"<chuỗi SHA256 hex chữ thường 64 ký tự>","notebook":"<chuỗi SHA256 hex chữ thường 64 ký tự>"}
```

Phải giữ `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` ổn định để phục hồi job và audio sau restart. Muốn thay khóa phải có kế hoạch mã hóa lại, không đổi trực tiếp rồi mất dữ liệu. Khi đổi hoặc thu hồi client token, cập nhật Secret và chủ động rollover gateway.

## 3. Migration PostgreSQL trước khi deploy

Trên máy vận hành được cấp quyền migration (nên dùng kết nối trực tiếp, không đi qua pool):

```bash
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@YOUR_HOST/YOUR_DB?sslmode=require'
alembic upgrade head
alembic current
```

Đây là URL minh họa; cung cấp secret thật theo cách an toàn. Migration tạo `tts_jobs`, `tts_audit_events`, `admission_lock` (một hàng khóa chung) và indexes. **Không chạy `Base.metadata.create_all` cho production.** Tách tài khoản migration có quyền DDL, runtime chỉ có DML và kiểm tra backup trước các lần nâng cấp tiếp theo.

## 4. Kiểm tra quyền Hugging Face, sau đó tải model bằng CPU

1. Đăng nhập [Hugging Face](https://huggingface.co/), mở trang từng model và gửi yêu cầu/chấp nhận điều khoản **nếu model có hạn chế truy cập**. Quyền được cấp cho tài khoản người dùng, không tự động được cấp cho Modal. Model công khai có thể không cần bước này; luôn kiểm tra chính sách hiện tại.
2. Tạo token **Read** của chính tài khoản được cấp quyền, lưu vào `HUGGING_FACE_READ_TOKEN` trong Secret `text-to-speech-hugging-face-authentication`. Token có sẵn trong máy cá nhân không tự chuyển sang Modal container.
3. Kiểm tra quyền tải bằng CPU trong **đúng Modal workspace/environment** trước khi tải checkpoint lớn:

```bash
modal run modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run modal_app.py::check_hugging_face_model_access --model-id voxcpm2
# Kiểm tra quyền tải không đồng nghĩa với quyền thương mại của Higgs TTS 3:
modal run modal_app.py::check_hugging_face_model_access --model-id higgs-tts-3
```

Bước này không tải trọng số, không khởi chạy GPU. Thông báo lỗi phân biệt thiếu token, chưa được cấp quyền gated model, sai repository ID và lỗi dịch vụ Hugging Face; không in token. Mỗi model được kiểm tra riêng, nên thất bại của một model không xóa dữ liệu đã tải của model khác. Phải xem xét giấy phép sử dụng độc lập với quyền tải checkpoint.

4. Chỉ tải checkpoint sau khi bước kiểm tra tương ứng thành công:

- `text-to-speech-moss-tts-v1-5-model-weights`
- `text-to-speech-higgs-tts-3-model-weights`
- `text-to-speech-voxcpm2-model-weights`

```bash
modal run modal_app.py::download_moss_tts_model
modal run modal_app.py::download_voxcpm2_model
```

Tải dữ liệu có chi phí CPU/storage nhưng **không cấp phát GPU**. Với Higgs, chỉ sau khi đã xác minh có quyền sử dụng API host theo giấy phép:

```bash
HIGGS_TTS_3_LICENSE_CONFIRMED=1 modal run modal_app.py::download_higgs_tts_3_model
```

Cờ này không thay thế giấy phép. Mặc định Higgs sẽ có `enabled=false` trong catalog, gateway từ chối job mới với HTTP 503, CPU dispatcher không claim job Higgs cũ, và GPU worker cũng từ chối khởi động. Chỉ khi quyền sử dụng API hosted đã được xác minh và người vận hành chủ động truyền cờ lúc deploy thì Higgs mới được bật. Mọi thay đổi quyền cần redeploy, không thể bật tùy ý bằng yêu cầu HTTP.

## 5. Deploy sau khi chuẩn bị Secret và migration

```bash
# Mặc định: Higgs TTS 3 không được bật.
MOSS_TTS_GPU_TYPE=L40S HIGGS_TTS_3_GPU_TYPE=L40S VOXCPM2_GPU_TYPE=L40S modal deploy modal_app.py

# CHỈ khi có giấy phép phù hợp cho hosted/API:
# HIGGS_TTS_3_LICENSE_CONFIRMED=1 MOSS_TTS_GPU_TYPE=L40S HIGGS_TTS_3_GPU_TYPE=L40S VOXCPM2_GPU_TYPE=L40S modal deploy modal_app.py
```

- `text_to_speech_api`: chỉ CPU, nhận ba Secret, xác thực trước khi đọc JSON; không mount trọng số.
- `dispatch_text_to_speech_job`: CPU, chỉ nhận Secret PostgreSQL/mã hóa, claim job và gọi GPU tương ứng khi hợp lệ.
- `resume_queued_text_to_speech_jobs`: tác vụ **CPU theo lịch mỗi phút**, đọc job `queued` và đánh thức runner; không retry `running`/`indeterminate`.
- Mỗi GPU worker: một class + một Volume riêng, `max_containers=1`, `min_containers=0`.

MOSS/Higgs dùng SGLang-Omni loopback `127.0.0.1:8000`; VoxCPM2 dùng API Python upstream. Worker MOSS đóng gói `configs/moss_tts.yaml` vào image và truyền `--config /opt/text-to-speech/configs/moss_tts.yaml`, theo [cookbook MOSS của upstream](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/moss_tts.md). Chưa xác minh thực tế độ tương thích giữa YAML, phiên bản runtime, checkpoint, memory L40S hoặc audio output; cấu hình Higgs cần đối chiếu riêng.

Image `lmsysorg/sglang-omni:dev` vẫn là tag có thể thay đổi; package `voxcpm` và nhiều phụ thuộc gián tiếp chưa pin đầy đủ. **Không dùng cấu hình mặc định để triển khai production/public.** Phải xác minh rồi khóa image digest, dependency, checkpoint revision SHA, kiểm tra CPU image build và sau đó nghiệm thu GPU từng model. Không đoán digest. `denoise=true` vẫn bị từ chối vì chưa cài denoiser.

## 6. Kiểm thử và xử lý job không chắc chắn

Kiểm tra CPU/API trước: auth, catalog, lỗi 401/429, idempotency, HTTP 202, trạng thái `queued`, owner-only metrics. Sau đó, **khi cho phép phát sinh phí GPU**, thử từng model bằng một câu ngắn theo thứ tự VoxCPM2 → MOSS → Higgs (nếu đã có quyền). Đo WAV, hiệu năng thực, memory và hóa đơn. WAV mock chỉ là **âm thanh im lặng**.

Nếu heartbeat quá hạn, GET báo `indeterminate` mà không tự retry. Khi mất xác nhận GPU, job có trạng thái `indeterminate` lưu trong DB, **tiếp tục giữ slot GPU** để tránh chồng tác vụ. Chỉ sau khi xem log/modal invocation và xác minh GPU đã dừng mới chạy CLI operator:

```bash
python scripts/reconcile_job.py JOB_ID CLIENT_ID
python scripts/reconcile_job.py JOB_ID CLIENT_ID --confirm-stopped
```

`--confirm-stopped` đánh dấu job `failed` và giải phóng slot sau xác nhận thủ công; **không tự chạy lại GPU**. Không dùng khi GPU có thể còn đang xử lý.

CPU sweep **chỉ xóa `completed` và `failed` đã được xác nhận** sau `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS` (mặc định 7 ngày). `indeterminate`, `running` và `queued` **không bao giờ tự động bị xóa**; job không rõ kết quả giữ slot cho tới khi operator xác minh GPU đã ngừng và chạy CLI reconcile. Điều này tránh trùng lặp lời gọi GPU có tính phí. Backup database và chính sách lưu của Modal là độc lập.

## 7. Những bước vẫn cần trước production

Bản này đã kiểm thử CPU với SQLite, **chưa thử PostgreSQL/Redis nhiều replica hay GPU Modal thật**. Chưa có mức trần tiền GPU thực tế, Cloudflare R2/S3 (WAV đang được mã hóa vào PostgreSQL BYTEA), cơ chế xác minh chủ sở hữu giọng mẫu, WAF/edge, đánh giá bảo mật và license cuối cùng. Không nên mở API công khai khi chưa hoàn thành các bước này.

Tham khảo [Kiến trúc](ARCHITECTURE.vi.md), [API](API.vi.md), [Bảo mật](SECURITY.vi.md).
