# Naming conventions for contributors

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](NAMING_CONVENTIONS.vi.md)

🌐 **English** | [Tiếng Việt](NAMING_CONVENTIONS.vi.md)

This project prioritizes clarity over short names. Use meaningful, searchable English identifiers for new code, examples, Modal resources, and environment variables.

| Resource | Example | Why |
| --- | --- | --- |
| Environment | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` | Names the service, provider and purpose |
| Modal Secret | `text-to-speech-hugging-face-authentication` | Indicates the contents and usage boundary |
| GPU selector | `VOXCPM2_GPU_TYPE` | Names the exact upstream model and resource |
| Worker class | `MossTtsWorker` | Distinguishes the MOSS-TTS worker from other models |
| Download function | `download_voxcpm2_model` | Makes explicit which checkpoint is downloaded |
| Model Volume | `text-to-speech-higgs-tts-3-model-weights` | Distinguishes immutable model assets from jobs/audio |

The following *official names and standard protocol terms* remain unchanged: `OpenMOSS-Team/MOSS-TTS-v1.5`, `bosonai/higgs-tts-3-4b`, `openbmb/VoxCPM2`, public API model IDs, `model_config`, and upstream inference parameters such as `top_p`, `cfg_value`, and `max_new_tokens`. CPU, GPU, API, HTTPS, JSON, WAV, Redis, SHA-256 and TTS are conventional technical abbreviations; explain them in user-facing documents where useful.

The project provides separate, descriptive Modal Secrets: API authentication; PostgreSQL job database; Redis rate limiting; and Hugging Face read authentication for CPU-only downloads. **Never share the Hugging Face token with the public API gateway or GPU workers.**

When changing a public setting or Modal resource name, update code, `.env.example`, both README files, deployment guides, notebook, tests, and CI in the same revision. Resource renames create *new* Modal resources: existing volumes and secrets are not automatically migrated. Do not delete old volumes until their contents and access rights have been verified independently.
