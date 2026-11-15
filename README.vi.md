# Text-to-Speech AI Inference

🌐 Language / Ngôn ngữ: [English](README.md) | **Tiếng Việt**

[![Python CI](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml/badge.svg)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml)

[![Giấy phép mã nguồn: MIT](https://img.shields.io/badge/Source%20License-MIT-green.svg)](LICENSE) [![Phiên bản](https://img.shields.io/badge/version-1.0.0-blue.svg)](pyproject.toml) [![Python](https://img.shields.io/badge/Python-3.11--3.13-blue.svg)](pyproject.toml)

**Bộ mã nguồn tự triển khai TTS API với một CPU Gateway, ba GPU worker độc lập, job bền vững được mã hóa và tùy chọn giọng đọc riêng cho từng model.** Chạy trong Modal workspace do người triển khai quản lý, không cung cấp sẵn API inference công khai.

| Model / checkpoint đầy đủ | Tính năng tùy chỉnh | Tài nguyên |
| --- | --- | --- |
| [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Ngôn ngữ, giọng tham chiếu, phát âm, thời lượng, sampling | GPU + Volume riêng |
| [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | Cảm xúc, kiểu giọng, cao độ, tốc độ, giọng tham chiếu, sampling | GPU + Volume riêng; **giấy phép model hạn chế** |
| [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Thiết kế giọng bằng mô tả tuổi/giới tính giọng/tone/tốc độ, clone giọng | GPU + Volume riêng |

Mã nguồn dự án theo MIT, nhưng **trọng số và runtime upstream có giấy phép riêng**. Việc cung cấp Higgs TTS 3 theo hosted API có thể yêu cầu quyền thương mại riêng. Xem [Giấy phép model](MODEL_LICENSES.md).

> **Trạng thái:** HTTP API và database logic đã có kiểm thử offline trên CPU. Ba adapter GPU Modal thật, Redis/PostgreSQL nhiều replica **chưa được nghiệm thu thực tế**. Mock chỉ tạo 0,5 giây **âm thanh im lặng**, không phải giọng nói AI. Bộ mã nguồn không tự tạo GPU/deployment hoặc repository GitHub.

## Kiến trúc

```mermaid
flowchart TB
    Client[Client tin cậy] --> API[FastAPI Gateway chỉ CPU]
    API --> Rate[(Redis/Valkey: quota và lockout)]
    API --> DB[(PostgreSQL: job, audio mã hóa, audit)]
    API --> Runner[CPU Dispatcher]
    Sweep[CPU sweep theo lịch] --> Runner
    Runner --> DB
    Runner --> Moss[MOSS GPU]
    Runner --> Higgs[Higgs GPU]
    Runner --> Vox[VoxCPM2 GPU]
    Moss --- MVol[(Volume MOSS)]
    Higgs --- HVol[(Volume Higgs)]
    Vox --- VVol[(Volume VoxCPM2)]
```

Mỗi client có Bearer token và phạm vi sở hữu job độc lập. Gateway so sánh token dưới dạng hash **trước khi đọc body**, rate-limit phân tán bằng Redis, ghi job vào PostgreSQL trước khi đánh thức CPU runner. `Idempotency-Key` cho phép tái sử dụng job ID thay vì vô tình chạy GPU trùng. PostgreSQL lưu trạng thái, quota, input/giọng tham chiếu đã mã hóa, WAV đã mã hóa và audit metadata. Ba GPU worker hoạt động độc lập và scale về 0 khi rảnh; **Gateway không có GPU hoặc mount trọng số model**. Mỗi model cung cấp JSON Schema option riêng.

Trạng thái: `queued` → `running` → `completed` hoặc `indeterminate`. Nếu không chắc GPU đã dừng, **không tự retry**; job tiếp tục giữ slot của model cho tới khi người vận hành xác nhận đã dừng. CPU sweep chỉ đánh thức lại job còn `queued`. Không cam kết exactly-once khi RPC GPU gặp lỗi không rõ kết quả. Xem [Kiến trúc](docs/ARCHITECTURE.vi.md).

## Chạy thử trên CPU, không cần GPU

Python 3.11–3.13:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/local_demo.py
```

Terminal khác:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/audio/jobs \
  -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' \
  -H 'Idempotency-Key: example-tts-0001' \
  -H 'Content-Type: application/json' \
  -d '{"model":"voxcpm2","input":"Xin chào!","model_config":{"gender":"female","age":"young","emotion":"cheerful"}}'
```

Mock tuân thủ hợp đồng API nhưng **không ghi vào PostgreSQL** và chỉ trả WAV im lặng. Test database/HTTP dùng SQLite và Fernet tại máy local, không thay thế thử PostgreSQL/GPU thật. Production **bắt buộc PostgreSQL + Redis/Valkey TLS + Modal Secrets**; hệ thống từ chối an toàn nếu thiếu, không dùng queue/rate-limit RAM thay thế.

## Endpoint API

| Method | Path | Mục đích |
| --- | --- | --- |
| `GET` | `/v1/health` | Liveness CPU, không dùng để kiểm tra GPU |
| `GET` | `/v1/models` | Danh sách model và JSON Schema tùy chọn |
| `GET` | `/v1/models/{model_id}` | Tính năng, giấy phép, tham số hợp lệ |
| `POST` | `/v1/audio/jobs` | Đưa job vào database, idempotency, HTTP 202 |
| `GET` | `/v1/audio/jobs/{job_id}` | Trạng thái job thuộc client, metadata suy luận |
| `GET` | `/v1/audio/jobs/{job_id}/audio` | Tải WAV có xác thực sau khi hoàn tất |
| `GET` | `/v1/ops/metrics` | Số liệu trạng thái DB chỉ dành cho owner |

Ví dụ yêu cầu VoxCPM2:

```json
{
  "model": "voxcpm2",
  "input": "Xin chào, chào mừng các bạn đến với chương trình!",
  "model_config": {
    "voice_description": "Giọng đọc ấm áp, rõ ràng",
    "gender": "female", "age": "young", "emotion": "cheerful",
    "pace": "slow", "cfg_value": 2.0, "inference_timesteps": 10
  }
}
```

Clone giọng bằng `reference_audio_base64` chứa WAV PCM, kèm `reference_consent: true`; **cờ này chỉ là lời khai từ client**, không chứng minh sự đồng ý của người nói. Cần kiểm tra quyền sử dụng giọng tại ứng dụng thực. Option không phù hợp model trả 422 trước khi gọi GPU. WAV đầu ra mã hóa Fernet trong PostgreSQL (chưa có R2/S3); job terminal được xóa theo retention. Xem [Tài liệu API](docs/API.vi.md).

## Triển khai trên Modal của bạn

**Đọc đầy đủ [Triển khai](docs/DEPLOYMENT.vi.md) trước khi thực hiện.** Production cần **bốn Modal Secret**, gồm: `text-to-speech-api-authentication` (hash client/owner token), `text-to-speech-job-database` (PostgreSQL URL và Fernet key), `text-to-speech-rate-limiting` (Redis TLS URL) và `text-to-speech-hugging-face-authentication` (Hugging Face Read token, chỉ CPU download). Chạy `alembic upgrade head` trước khi deploy. CPU runner và GPU worker tách riêng; CPU sweep chạy mỗi phút.

```bash
python -m pip install -e '.[modal,dev]'
modal setup
# Chuẩn bị Secrets và migrate PostgreSQL trước.
modal run modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run modal_app.py::check_hugging_face_model_access --model-id voxcpm2
modal run modal_app.py::download_moss_tts_model  # chỉ CPU, có phí CPU/storage
modal run modal_app.py::download_voxcpm2_model   # chỉ CPU, có phí CPU/storage
modal deploy modal_app.py              # chỉ deploy sau khi chuẩn bị
```

Higgs chỉ tải/chạy sau khi xác minh có quyền sử dụng. Mặc định model vẫn xuất hiện trong catalog với `enabled=false`, gửi job bị từ chối mà không gọi GPU; chỉ người vận hành xác nhận quyền sử dụng lúc deploy mới có thể bật. Worker MOSS đã đóng gói YAML pipeline riêng cho SGLang-Omni, nhưng vẫn cần kiểm thử tương thích runtime thật. GPU mặc định L40S riêng từng model, có thể đổi bằng `MOSS_TTS_GPU_TYPE`, `HIGGS_TTS_3_GPU_TYPE`, `VOXCPM2_GPU_TYPE`. Image SGLang `lmsysorg/sglang-omni:dev` **chưa khóa digest**, phải đánh giá dependency và kiểm thử trên GPU thật trước khi mở public.

## Bảo mật và vận hành

- **Rate-limit:** giới hạn pre-auth theo peer/toàn hệ thống, 3 lần sai token khóa tạm, job/poll quota và trần job/ngày chia sẻ trên Redis, giới hạn pending theo client/model và chỉ một GPU job đang chạy hoặc chưa rõ kết quả mỗi model.
- **Quyền riêng tư:** mã hóa input, audio tham chiếu và WAV đầu ra trong PostgreSQL; audit chỉ metadata giới hạn. Không ghi token/text/audio vào log, không đổi khóa Fernet nếu chưa có kế hoạch mã hóa lại.
- **Độ tin cậy:** idempotency theo client, CPU sweep đánh thức job `queued`, heartbeat/`indeterminate`, operator xác minh GPU đã dừng trước khi đóng job bị treo.
- **Hạn chế:** chưa có trần chi phí GPU theo USD, R2/S3, xác minh đồng ý clone giọng, benchmark thực; chưa kiểm thử tải PostgreSQL/Redis nhiều replica và runtime model. Thiết lập Modal budget và WAF trước khi cung cấp dịch vụ công khai.

## Tài liệu

[Hợp đồng API](docs/API.vi.md) · [Kiến trúc](docs/ARCHITECTURE.vi.md) · [Triển khai](docs/DEPLOYMENT.vi.md) · [Bảo mật](docs/SECURITY.vi.md) · [English README](README.md) · [Giấy phép model](MODEL_LICENSES.md) · [Notebook demo](notebooks/tts_api_demo.ipynb) · [Quy ước đặt tên](docs/NAMING_CONVENTIONS.vi.md)

Hoan nghênh việc review và đóng góp mã nguồn; không đưa secret, giọng riêng tư hoặc benchmark tự tạo không có bằng chứng vào Git.
