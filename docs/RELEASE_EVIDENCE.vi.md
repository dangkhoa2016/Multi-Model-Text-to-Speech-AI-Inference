# Bằng chứng và giới hạn phát hành

> 🌐 Language / Ngôn ngữ: [English](RELEASE_EVIDENCE.md) | **Tiếng Việt**

SHA nguồn trước đó 0d4f5a3d932b8acaceb2ded29a41fcc63fd5a977 đã PASS Python 3.11, 3.12, 3.13 và PostgreSQL 16 / Redis 7 trong [GitHub Actions run 44481659149](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/runs/44481659149).

GPU canary MOSS-TTS-v1.5 trên Modal production v4 dùng SHA nguồn b2f97b6869ff6c4cf74e64e165dc93b047310ef4, hoàn thành một job HTTPS có xác thực và trả WAV 24 kHz, PCM16 mono, 464.684 bytes (9,68 giây). WAV SHA-256: f30497bb3d54545abd99cada06d55a603794ebfb74a62e145de0654f4f94acce.

VoxCPM2 có nghiệm thu WAV GPU riêng; Higgs TTS 3 tắt. Modal production v4 đã stop sau demo.

Mã nguồn dùng lockfile nén không nằm trong deployment v4 trước đây. Nhận định này chỉ nói về v4; bằng chứng GPU canary P1 mới được ghi bên dưới. Chỉ PASS CPU tests và tái tạo lockfile chưa chứng minh GPU inference. Không khẳng định multi-replica, HA hay API 24/7. Credentials, WAV và evidence archive không được đưa lên release.

Xem [kiểm thử](TESTING.vi.md), [giấy phép](../MODEL_LICENSES.vi.md).


## Nghiệm thu P1 DX Simplification — 21/11/2026

Báo cáo nghiệm thu ngày 21/11/2026 ghi snapshot mã nguồn đã review f438860898f996b3013687c050f32ec7bb03502d. File modal_app.py của canary độc lập khớp snapshot này, ngoại trừ tên ứng dụng và tham chiếu Secret xác thực; kết quả được đối chiếu trực tiếp giữa hai file. Operator chọn MOSS-TTS-v1.5 cùng VoxCPM2 qua TEXT_TO_SPEECH_ENABLED_MODELS; Higgs vẫn tắt. Không cần các cờ thủ công GPU_ACCEPTED/CODEC_READY.

- **VoxCPM2:** HTTPS có xác thực -> CPU gateway -> GPU L40S -> WAV, mono 48.000 Hz, 3,04 giây, 291.884 bytes. SHA-256: 8e3cb530aef6b8108cdf7508c1f68576c23f4b84efebf74566028ab9edbda9ac.
- **MOSS-TTS-v1.5:** HTTPS có xác thực -> CPU gateway -> GPU L40S -> WAV, mono 24.000 Hz, 12,24 giây, 587.564 bytes. SHA-256: 37c0b21b782040ce34efd8e8f1611d2bd61998411ee444f33de6303aec5743cf.
- **Kiểm tra API cuối: 19/19 PASS** cho trạng thái job hoàn tất, tải WAV có xác thực, SHA-256/metadata, replay HTTP 202 reused, payload khác cùng key HTTP 409, phân quyền client/owner và từ chối Higgs tắt HTTP 503.
- Tổng 19/19 kiểm tra áp dụng cho hai job riêng biệt trong cùng một app canary.
- [GitHub Actions run 44866848762 trên main](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/runs/44866848762): PASS Python 3.11/3.12/3.13 và tích hợp PostgreSQL 16/Redis 7. CPU tests: 225 passed, 3 skipped. Source checksums: 113 verified.
- Đã dừng canary, **0 tasks đang chạy**. WAV, JSON và báo cáo được lưu trữ riêng ngoài repository (SHA-256 ZIP đã xác minh: 767378a5e0856b87f94f3f8194f973ecba2d0913ebd1785ccecc82982fcbdda2).

**Giới hạn:** Chỉ xác nhận hai model và cấu hình P1 đã thử, không bảo đảm tất cả operator, GPU, tham số model, thông lượng hoặc tải nhiều replica. Higgs hosted inference chưa được nghiệm thu và có giấy phép riêng. Thay đổi tài liệu, CI và thứ tự xác minh cấu hình triển khai Higgs sau thử nghiệm không phải một lượt GPU canary mới. Thay đổi thứ tự xác minh sau đó chỉ được kiểm thử regression trên CPU.

## Xác định phiên bản mã nguồn

Chạy `git rev-parse 'v1.0.0^{commit}'` để xem commit mà tag phát hành trỏ tới và `git rev-parse HEAD` để xem commit đang dùng. Tag phát hành từng trỏ tới commit lịch sử `cc431401081314590eaf79ce81b414988ed15003`. Kết quả P1 ở trên áp dụng cho snapshot canary `f438860898f996b3013687c050f32ec7bb03502d` và cấu hình đã ghi lại. SHA lịch sử này có thể không còn trong lịch sử Git đã được sắp xếp lại. Hãy đối chiếu SHA của checkout hoặc bản phát hành với snapshot đã thử trước khi trích dẫn kết quả. Liên kết GitHub Actions có thể yêu cầu quyền truy cập repo. WAV và hồ sơ nghiệm thu do người vận hành lưu giữ.
