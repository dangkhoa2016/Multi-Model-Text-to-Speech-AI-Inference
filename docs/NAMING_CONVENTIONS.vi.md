# Quy ước đặt tên dành cho người đóng góp

> 🌐 Language / Ngôn ngữ: [English](NAMING_CONVENTIONS.md) | **Tiếng Việt**

🌐 [English](NAMING_CONVENTIONS.md) | **Tiếng Việt**

Dự án ưu tiên tên dễ đọc và dễ tìm kiếm, không rút gọn chỉ để viết nhanh. Tên biến, hàm, Modal Secret, Volume, GPU worker và biến môi trường nên mô tả rõ mục đích bằng tiếng Anh.

| Thành phần | Ví dụ | Ý nghĩa |
| --- | --- | --- |
| Biến môi trường | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` | Nêu rõ dịch vụ, loại database và mục đích |
| Modal Secret | `text-to-speech-hugging-face-authentication` | Nêu rõ nguồn và giới hạn quyền |
| Cấu hình GPU | `VOXCPM2_GPU_TYPE` | Gắn với đúng model upstream |
| Class worker | `MossTtsWorker` | Không nhầm với các model khác |
| Hàm tải model | `download_voxcpm2_model` | Diễn đạt hành động và đối tượng |
| Modal Volume | `text-to-speech-higgs-tts-3-model-weights` | Chỉ chứa trọng số của đúng model |

Giữ **nguyên tên model/chỉ số kỹ thuật chính thức**: `OpenMOSS-Team/MOSS-TTS-v1.5`, `bosonai/higgs-tts-3-4b`, `openbmb/VoxCPM2`, API model ID, `model_config` và các tham số upstream như `top_p`, `cfg_value`, `max_new_tokens`. CPU, GPU, API, HTTPS, JSON, WAV, Redis, SHA-256 và TTS là những chữ viết tắt thông dụng; nên giải thích khi cần trong tài liệu dành cho người mới.

Có bốn Secret tách biệt: xác thực API, PostgreSQL jobs, Redis rate-limit và Hugging Face token quyền đọc chỉ dành cho CPU download. **Không cấp Hugging Face token cho public gateway hoặc GPU worker.**

Khi đổi tên một biến public hoặc Modal resource, cần đồng bộ source, `.env.example`, README EN/VI, deployment guides, notebook, tests và CI. Việc đổi tên Modal Volume/Secret tạo **resource mới**; không tự động di chuyển dữ liệu hoặc quyền truy cập từ resource cũ. Không xóa volume cũ trước khi kiểm tra dữ liệu và các quyền liên quan.
