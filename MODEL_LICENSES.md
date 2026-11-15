# Model and runtime licenses / Giấy phép model và runtime

The [MIT project license](LICENSE) covers only this repository's original source, examples and documentation. It does **not** relicense third-party checkpoints, dependencies or generated media. Review the actual license text and acceptable use conditions for every upstream release you deploy.

Giấy phép [MIT của dự án](LICENSE) chỉ áp dụng cho mã nguồn và tài liệu do dự án này tạo ra. Nó **không thay thế** giấy phép của model, thư viện bên thứ ba hoặc tự động cấp quyền cho nội dung âm thanh được tạo.

| Model | Upstream | Model license | Deployment note |
|---|---|---|---|
| MOSS-TTS-v1.5 | [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Apache-2.0 | Observe Apache notices and acceptable-use conditions |
| Higgs TTS 3 | [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | **Boson Higgs TTS 3 Research and Non-Commercial License** | Hosted/API/product/commercial use generally requires separate commercial rights; do not treat source MIT as sufficient |
| VoxCPM2 | [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Apache-2.0 | Observe upstream terms and avoid impersonation |

Runtime dependencies such as [SGLang-Omni](https://github.com/sgl-project/sglang-omni), VoxCPM, PyTorch, codecs and other bundled packages have independent licenses; audit the resolved dependency tree and container layers before redistributing a built image.

Higgs must be explicitly authorized and `HIGGS_TTS_3_LICENSE_CONFIRMED=1` set **by the deployer**, only after checking that the actual use is permitted. An environment flag is a safeguard, not a license grant. Free creator rights for monetized videos should not be assumed to authorize hosting an inference API.

Voice cloning requires consent and any required rights to the referenced recording and voice. `reference_consent` is a caller declaration, not biometric proof of consent. Do not use the service for impersonation, fraud, deceptive elections or other unlawful conduct.
