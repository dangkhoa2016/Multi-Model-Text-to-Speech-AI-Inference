# Giấy phép model và runtime

> 🌐 Language / Ngôn ngữ: [English](MODEL_LICENSES.md) | **Tiếng Việt**

Giấy phép MIT của dự án chỉ áp dụng cho mã nguồn và tài liệu gốc, không cấp quyền tự động đối với checkpoint, dependency hoặc âm thanh được tạo.

| Model | Upstream | Giấy phép | Điều kiện sử dụng |
|---|---|---|---|
| MOSS-TTS-v1.5 | [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Apache-2.0 | Tuân thủ điều khoản upstream |
| Higgs TTS 3 | [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | Boson Higgs TTS 3 Research and Non-Commercial License | Hosted API và thương mại có thể cần cấp phép riêng |
| VoxCPM2 | [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Apache-2.0 | Tuân thủ giấy phép, không mạo danh |

Các runtime như SGLang-Omni, VoxCPM, PyTorch và codec có giấy phép riêng; kiểm tra trước khi phân phối image. Biến HIGGS_TTS_3_LICENSE_CONFIRMED chỉ biểu thị deployer đã xác minh quyền sử dụng, không phải giấy phép. Việc clone giọng cần quyền và sự đồng ý thực tế; reference_consent chỉ là lời khai của caller.
