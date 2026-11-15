# Text-to-Speech AI Inference

🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](README.vi.md)

[![Python CI](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml/badge.svg)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml)

[![Source License: MIT](https://img.shields.io/badge/Source%20License-MIT-green.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](pyproject.toml) [![Python](https://img.shields.io/badge/Python-3.11--3.13-blue.svg)](pyproject.toml)

**Build a self-hosted TTS API with one CPU gateway, three isolated GPU workers, encrypted durable jobs and model-specific voice controls.** Intended for your own Modal workspace, not an existing public inference endpoint.

| Model / exact checkpoint | Per-request controls | Worker isolation |
| --- | --- | --- |
| [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Language, custom voice/reference, pronunciation, duration, sampling | Dedicated GPU + Volume |
| [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | Emotion, whisper/sing/shout style, pitch, speed, reference, sampling | Dedicated GPU + Volume; **restricted model license** |
| [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Natural-language voice design (age/gender/tone/pace), cloning, sampling | Dedicated GPU + Volume |

The **source code** is MIT licensed; the checkpoint licenses and upstream runtime terms are separate. In particular, Higgs TTS 3 may require commercial/hosted permission. See [Model licenses](MODEL_LICENSES.md).

> **Status:** CPU-only API and database behavior have automated offline tests. The real Modal SGLang/Vox GPU adapters, Redis and PostgreSQL multi-replica behavior have **not** been validated on live infrastructure. The local mock returns 0.5 seconds of **silence**, not model-generated speech. No GPUs, paid resources or GitHub repositories are created by this source archive.

## Architecture

```mermaid
flowchart TB
    Client[Trusted API client] --> API[Authenticated CPU FastAPI gateway]
    API --> Rate[(Redis/Valkey: shared rate limits and lockout)]
    API --> DB[(PostgreSQL: encrypted jobs, WAV and audit)]
    API --> Runner[CPU dispatcher]
    Sweep[Scheduled CPU-only queue sweep] --> Runner
    Runner --> DB
    Runner --> Moss[MOSS GPU]
    Runner --> Higgs[Higgs GPU]
    Runner --> Vox[VoxCPM2 GPU]
    Moss --- MVol[(MOSS Volume)]
    Higgs --- HVol[(Higgs Volume)]
    Vox --- VVol[(VoxCPM2 Volume)]
```

Every client receives an independent Bearer key/owner scope. The gateway checks hash-based authentication **before reading the body**, uses atomic Redis/Valkey limits and commits a job in PostgreSQL before waking a CPU coordinator. Job creation is idempotent by the optional, recommended `Idempotency-Key` header. PostgreSQL is the durable source of truth: job state, admission caps, encrypted prompts/reference audio, encrypted completed WAV and metadata. Worker classes are independently scaled to zero when idle; **the gateway has no GPU or model Volume**. Each model's options are validated against an independently published JSON Schema.

State machine: `queued` → `running` → `completed` or `indeterminate`. An uncertain GPU outcome is **never** automatically retried and continues to reserve that model's execution slot until an operator confirms it has stopped. A CPU scheduler can safely wake only still-queued jobs. No exactly-once inference guarantees are claimed across remote GPU failures. See [Architecture](docs/ARCHITECTURE.md).

## Local CPU-only quick start

Python 3.11–3.13, no cloud credentials required:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/local_demo.py
```

Use a second terminal to call the mock server:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/audio/jobs \
  -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' \
  -H 'Idempotency-Key: example-tts-0001' \
  -H 'Content-Type: application/json' \
  -d '{"model":"voxcpm2","input":"Hello!","model_config":{"gender":"female","age":"young","emotion":"cheerful"}}'
```

The mock accepts normal API contracts, but does **not** persist jobs into PostgreSQL and returns only a synthetic silence fixture. Database/HTTP integration tests use an isolated SQLite fixture with encryption, not a live PostgreSQL server or a GPU. The production gateway **requires PostgreSQL, Redis/Valkey TLS and configured Secrets**; it fails closed if unavailable. No production fallback to in-memory queue/rate-limit is enabled.

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/health` | Minimal public CPU liveness (never a GPU readiness probe) |
| `GET` | `/v1/models` | Authenticated list with individual JSON Schema controls |
| `GET` | `/v1/models/{model_id}` | Capabilities, license and validated per-model options |
| `POST` | `/v1/audio/jobs` | Durable asynchronous enqueue, optional idempotency key, HTTP 202 |
| `GET` | `/v1/audio/jobs/{job_id}` | Owner-scoped status, inference/audio metadata |
| `GET` | `/v1/audio/jobs/{job_id}/audio` | Authenticated WAV download after completion |
| `GET` | `/v1/ops/metrics` | Owner-only aggregate DB state counts |

Example VoxCPM2 job body:

```json
{
  "model": "voxcpm2",
  "input": "Welcome to our podcast.",
  "model_config": {
    "voice_description": "Warm, clear narrator",
    "gender": "female", "age": "adult", "emotion": "cheerful",
    "pace": "slow", "cfg_value": 2.0, "inference_timesteps": 10
  }
}
```

The API also supports authorized reference PCM WAV in `reference_audio_base64` with `reference_consent: true`. This **self-reported flag is not proof of consent**; the deployment operator must independently implement appropriate controls. Unsupported model options return 422 before GPU inference. Audio outputs are Fernet encrypted in PostgreSQL (not yet R2/S3) and terminal jobs are purged after a configurable retention period. See [API](docs/API.md).

## Deploy to your Modal workspace

**Do not deploy until you have reviewed the full [Deployment guide](docs/DEPLOYMENT.md).** The production path requires **four separate** Modal Secrets: `text-to-speech-api-authentication` (hashed client/owner keys), `text-to-speech-job-database` (PostgreSQL URL and Fernet key), `text-to-speech-rate-limiting` (TLS Redis URL), and `text-to-speech-hugging-face-authentication` (Hugging Face read token, used only by CPU model checks/downloads). Apply `alembic upgrade head` to PostgreSQL before deploying. CPU functions are isolated from the GPU workers and a scheduled CPU sweep runs every minute.

```bash
python -m pip install -e '.[modal,dev]'
modal setup
# Populate Secrets and migrate PostgreSQL first; see docs/DEPLOYMENT.md.
modal run modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run modal_app.py::check_hugging_face_model_access --model-id voxcpm2
modal run modal_app.py::download_moss_tts_model  # CPU download; billable storage/compute
modal run modal_app.py::download_voxcpm2_model   # CPU download; billable storage/compute
modal deploy modal_app.py              # Deploy only after preparation
```

For Higgs, confirm rights before explicitly downloading or enabling its worker. By default the model remains in the catalog with `enabled=false` and job submission is rejected without waking GPU; only an operator-provided deployment-time license acknowledgement enables admission. MOSS includes a model-specific SGLang-Omni pipeline YAML within its worker image, but serving compatibility still needs acceptance testing. The initial GPU choice is `L40S` for each model, adjustable independently with `MOSS_TTS_GPU_TYPE`, `HIGGS_TTS_3_GPU_TYPE` and `VOXCPM2_GPU_TYPE`. Image `lmsysorg/sglang-omni:dev` is currently a **mutable upstream tag** and must be replaced with a pinned, audited digest and verified runtime compatibility before public production use.

## Security and operations

- **Rate limiting:** pre-auth per peer/global, three failed tokens causing temporary lockout, Redis-wide per-client job/poll quotas and daily job admission ceilings; DB-wide per-client/model outstanding limits and one active or uncertain GPU task per model.
- **Privacy:** reference audio, text and generated WAV are encrypted at rest in PostgreSQL with a separate persistent Fernet Secret; minimal audit event metadata only. Do not log raw content or rotate the key without re-encrypting stored data.
- **Reliability:** idempotency scoped to client, CPU-only recovery of `queued` jobs, heartbeat and `indeterminate` state for uncertain GPU results; an operator must confirm termination before closing a stuck model slot.
- **Limitations:** no bill-denominated GPU spend cap, R2/S3 backend, public voice-consent verification, or live performance results. Redis/PostgreSQL replicas and model inference still need real integration tests. Configure a Modal Workspace budget and external WAF before exposing the service.

## Documentation

[API](docs/API.md) · [Kiến trúc / Architecture](docs/ARCHITECTURE.md) · [Deployment](docs/DEPLOYMENT.md) · [Security](docs/SECURITY.md) · [Bản tiếng Việt](README.vi.md) · [Model licenses](MODEL_LICENSES.md) · [Notebook demo](notebooks/tts_api_demo.ipynb) · [Naming conventions](docs/NAMING_CONVENTIONS.md)

Community code reviews and contributions are welcome. Never commit secrets, private voice recordings or fabricated benchmarks.
