# Text-to-Speech AI Inference

🌐 Language / Ngôn ngữ: [English](README.md) | **Tiếng Việt**

[![Python CI](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml/badge.svg)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml)

[![Giấy phép mã nguồn: MIT](https://img.shields.io/badge/Source%20License-MIT-green.svg)](LICENSE) [![Phiên bản](https://img.shields.io/badge/version-1.0.0-blue.svg)](pyproject.toml) [![Python](https://img.shields.io/badge/Python-3.11--3.13-blue.svg)](pyproject.toml)
[![Release](https://img.shields.io/badge/GitHub-release%20v1.0.0-blue)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/releases/tag/v1.0.0) [![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](docs/API.vi.md) [![Modal](https://img.shields.io/badge/Deploy-Modal-111827)](docs/DEPLOYMENT.vi.md)

**Bộ mã nguồn tự triển khai TTS API với một CPU Gateway, ba GPU worker độc lập, job bền vững được mã hóa và tùy chọn giọng đọc riêng cho từng model.** Chạy trong Modal workspace do người triển khai quản lý, không cung cấp sẵn API inference công khai.

| Model / checkpoint đầy đủ | Tính năng tùy chỉnh | Tài nguyên |
| --- | --- | --- |
| [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Ngôn ngữ, giọng tham chiếu, phát âm, thời lượng, sampling | GPU + Volume riêng |
| [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | Cảm xúc, thì thầm/hát/hét, cao độ, tốc độ, giọng tham chiếu, sampling | GPU + Volume riêng; **giấy phép model hạn chế** |
| [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Thiết kế giọng bằng mô tả tuổi, giới tính, sắc thái và tốc độ đọc; clone giọng; sampling | GPU + Volume riêng |

Mã nguồn dự án theo MIT, nhưng **trọng số và runtime upstream có giấy phép riêng**. Việc cung cấp Higgs TTS 3 theo hosted API có thể yêu cầu quyền thương mại riêng. Xem [Giấy phép model](MODEL_LICENSES.vi.md).

> CPU mock chỉ tạo **0,5 giây WAV im lặng**. Xem [phạm vi nghiệm thu và phiên bản đã thử](docs/RELEASE_EVIDENCE.vi.md) trước khi chọn model để triển khai.

## Chạy thử trên CPU, không cần GPU

Python 3.11–3.13, không cần tài khoản cloud:

```bash
git clone https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference.git
cd Multi-Model-Text-to-Speech-AI-Inference
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python scripts/local_demo.py
```

Terminal khác:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/audio/jobs \
  -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' \
  -H 'Idempotency-Key: example-tts-vi-0001' \
  -H 'Content-Type: application/json' \
  -d '{"model":"voxcpm2","input":"Xin chào!","model_config":{"gender":"female","age":"young","emotion":"cheerful"}}'
```

### Kiểm tra trạng thái và tải WAV

POST trả HTTP 202 với JSON ví dụ `{"job_id":"<32-hex-id>","model":"voxcpm2","status":"queued","reused":false}`. Hãy lấy **job_id thực tế** trong response rồi chạy:

```bash
JOB_ID="<job-id-thực-tế>"
curl -fsS -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' "http://127.0.0.1:8765/v1/audio/jobs/$JOB_ID"
curl -fsS -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' "http://127.0.0.1:8765/v1/audio/jobs/$JOB_ID/audio" -o cpu-mock-result.wav
```

**Mock vs production:** POST trả `queued`, GET có thể trả ngay `completed` với WAV im lặng. Mock chỉ tái dùng cùng client/key/payload trong RAM, mất khi restart; payload khác trả HTTP 409. Higgs TTS 3 mặc định tắt. Production dùng PostgreSQL bền vững và Redis phân tán.

**Notebook:** Mở [demo API EN/VI](notebooks/tts_api_demo.ipynb) bằng VS Code có tiện ích Jupyter hoặc JupyterLab. Trong `.venv`, chạy `python -m pip install -e '.[dev,notebook]'` khi dùng CPU mock, hoặc `python -m pip install -e '.[notebook]'` khi đã có gateway HTTPS. Chọn Python trong `.venv` làm kernel. Nếu dùng JupyterLab lần đầu, cài thêm `python -m pip install jupyterlab` rồi chạy `jupyter lab notebooks/tts_api_demo.ipynb`. Với mock, dùng URL `http://127.0.0.1:8765` và token ở ví dụ trên.

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

Trạng thái: `queued` → `running` → `completed` hoặc `indeterminate`; operator có thể đóng job đã xác nhận GPU dừng thành `failed`. Nếu không chắc GPU đã dừng, **không tự retry**; job tiếp tục giữ slot của model cho tới khi người vận hành xác nhận đã dừng. CPU sweep chỉ đánh thức lại job còn `queued`. Không cam kết exactly-once khi RPC GPU gặp lỗi không rõ kết quả. Xem [Kiến trúc](docs/ARCHITECTURE.vi.md).

## Endpoint API

| Method | Path | Mục đích |
| --- | --- | --- |
| `GET` | `/` | Landing page EN/VI công khai (`/?lang=vi`), không bật GPU |
| `GET` | `/v1/health` | Liveness CPU, không dùng để kiểm tra GPU |
| `GET` | `/v1/models` | Danh sách model và JSON Schema tùy chọn, có xác thực |
| `GET` | `/v1/models/{model_id}` | Tính năng, giấy phép, tham số hợp lệ |
| `POST` | `/v1/audio/jobs` | Đưa job vào database, idempotency, HTTP 202 |
| `GET` | `/v1/audio/jobs/{job_id}` | Trạng thái job, timing, usage và diagnostics an toàn theo client |
| `GET` | `/v1/audio/jobs/{job_id}/audio` | Tải WAV có xác thực sau khi hoàn tất |
| `GET` | `/v1/ops/metrics` | Số liệu trạng thái DB chỉ dành cho owner |

Landing page `/` dùng HTML/CSS song ngữ, không kích hoạt GPU, không tải asset bên ngoài hoặc đọc secrets. Chuyển EN/VI bằng `/?lang=en` và `/?lang=vi`; model catalog và inference vẫn cần Bearer token. API giữ nguyên `inference_ms` và cung cấp `timing`, `usage`, `diagnostics` cùng `audio_quality` (RMS/peak dBFS, số mẫu gần clipping) đã lọc theo client. Cảnh báo chất lượng không loại bỏ WAV hợp lệ hoặc sửa waveform. Token counts và thời gian nạp model chưa đo được sẽ là `null`, không tự ước lượng. Xem [Tài liệu API](docs/API.vi.md).

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

**Quy trình ngắn:** Cài Modal, chọn workspace/environment, tạo bốn Secret cần thiết (hash token API, PostgreSQL + Fernet key, Redis TLS, Hugging Face read token) và migrate database một lần. [Hướng dẫn triển khai](docs/DEPLOYMENT.vi.md) ghi rõ cách tạo các giá trị và chạy migration.

```bash
python -m pip install -e '.[modal,dev]'
modal setup
export MODAL_ENVIRONMENT="your-modal-environment"
# Sau khi cấu hình Secret và migrate PostgreSQL, tải trọng số bằng CPU:
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_tts_model
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_audio_tokenizer
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_voxcpm2_model

# Một nơi chọn model; dependency theo lockfile tự được build.
TEXT_TO_SPEECH_ENABLED_MODELS=moss-tts-v1.5,voxcpm2 \
  modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py
```

Mặc định **không model nào được bật** nếu chưa được chọn. Client gọi `GET /v1/models` có Bearer token để xem schema, sau đó tự chọn `model` và `model_config` tại `POST /v1/audio/jobs`. Schema của từng model quy định giá trị mặc định, ví dụ `voice: default` cho MOSS và Higgs. Xem schema trước khi đổi tham số. Model được chọn **không có nghĩa GPU đã sẵn sàng**: worker sẽ kiểm tra integrity checkpoint khi khởi động. Higgs TTS 3 cần quyền sử dụng phù hợp và image SGLang có digest bất biến được kiểm toán; ví dụ trên **không bật Higgs**.

Báo cáo ngày 21/11/2026 ghi nhận MOSS-TTS-v1.5 và VoxCPM2 đã chạy qua HTTPS và trả WAV từ GPU L40S trong một canary. Higgs TTS 3 chỉ có kết quả WAV nghiên cứu. [Bằng chứng nghiệm thu](docs/RELEASE_EVIDENCE.vi.md) ghi snapshot đã thử và cách đối chiếu với tag `v1.0.0` hoặc `main`.

## Bảo mật và vận hành

Gateway production yêu cầu PostgreSQL, Redis/Valkey qua TLS và Modal Secrets. Mặc định, yêu cầu clone giọng tham chiếu bị từ chối trước khi tạo job. Người vận hành phải bật tính năng và cấp quyền theo client bằng [cấu hình clone giọng](docs/DEPLOYMENT.vi.md); quyền sử dụng giọng cần được kiểm tra riêng. Xem [Bảo mật](docs/SECURITY.vi.md) về lưu trữ mã hóa và quota, và [Vận hành](docs/OPERATIONS.vi.md) về đối soát job chưa rõ kết quả và chi phí.

## Tài liệu

[Mục lục hướng dẫn kỹ thuật](docs/README.vi.md) · [API](docs/API.vi.md) · [Triển khai](docs/DEPLOYMENT.vi.md) · [Giấy phép model](MODEL_LICENSES.vi.md) · [Đóng góp](.github/CONTRIBUTING.vi.md) · [Quy tắc ứng xử](.github/CODE_OF_CONDUCT.vi.md)
