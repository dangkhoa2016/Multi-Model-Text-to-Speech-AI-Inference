# Text-to-Speech AI Inference

🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](README.vi.md)

[![Python CI](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml/badge.svg)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/actions/workflows/ci.yml)

[![Source License: MIT](https://img.shields.io/badge/Source%20License-MIT-green.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](pyproject.toml) [![Python](https://img.shields.io/badge/Python-3.11--3.13-blue.svg)](pyproject.toml)
[![Release](https://img.shields.io/badge/GitHub-release%20v1.0.0-blue)](https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference/releases/tag/v1.0.0) [![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](docs/API.md) [![Modal](https://img.shields.io/badge/Deploy-Modal-111827)](docs/DEPLOYMENT.md)

**Build a self-hosted TTS API with one CPU gateway, three isolated GPU workers, encrypted durable jobs and model-specific voice controls.** Intended for your own Modal workspace, not an existing public inference endpoint.

| Model / exact checkpoint | Per-request controls | Worker isolation |
| --- | --- | --- |
| [OpenMOSS-Team/MOSS-TTS-v1.5](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) | Language, custom voice/reference, pronunciation, duration, sampling | Dedicated GPU + Volume |
| [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) | Emotion, whisper/sing/shout style, pitch, speed, reference, sampling | Dedicated GPU + Volume; **restricted model license** |
| [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Natural-language voice design (age/gender/tone/pace), cloning, sampling | Dedicated GPU + Volume |

The **source code** is MIT licensed; the checkpoint licenses and upstream runtime terms are separate. In particular, Higgs TTS 3 may require commercial/hosted permission. See [Model licenses](MODEL_LICENSES.md).

> The CPU mock returns **0.5 seconds of silence**. See the [validation results and source snapshots](docs/RELEASE_EVIDENCE.md) before selecting models to deploy.

## Local CPU-only quick start

Python 3.11–3.13, no cloud credentials required:

```bash
git clone https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference.git
cd Multi-Model-Text-to-Speech-AI-Inference
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python scripts/local_demo.py
```

Use a second terminal to call the mock server:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/audio/jobs \
  -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' \
  -H 'Idempotency-Key: example-tts-en-0001' \
  -H 'Content-Type: application/json' \
  -d '{"model":"voxcpm2","input":"Hello!","model_config":{"gender":"female","age":"young","emotion":"cheerful"}}'
```

### Retrieve a completed mock job

The POST above returns HTTP 202 and a response like `{"job_id":"<32-hex-id>","model":"voxcpm2","status":"queued","reused":false}`. Copy the actual `job_id` from the response. In another terminal, use the same client token:

```bash
JOB_ID="<actual-job-id>"
curl -fsS -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' "http://127.0.0.1:8765/v1/audio/jobs/$JOB_ID"
curl -fsS -H 'Authorization: Bearer local-demo-key-of-32-plus-characters' "http://127.0.0.1:8765/v1/audio/jobs/$JOB_ID/audio" -o cpu-mock-result.wav
```

**Mock vs production:** POST returns `queued`, but GET may immediately show `completed` (silent WAV). The mock reuses client/key/payload only within its running process; different payload returns HTTP 409. Higgs TTS 3 is disabled by default. Production instead uses durable PostgreSQL idempotency, distributed Redis limits and GPU workers.

**Notebook:** Open the [EN/VI API demo](notebooks/tts_api_demo.ipynb) in VS Code with the Jupyter extension or in JupyterLab. In `.venv`, run `python -m pip install -e '.[dev,notebook]'` for the CPU mock or `python -m pip install -e '.[notebook]'` for an existing HTTPS gateway. Select the Python interpreter in `.venv` as the kernel. For a fresh JupyterLab setup, install it with `python -m pip install jupyterlab` and run `jupyter lab notebooks/tts_api_demo.ipynb`. For the mock, use `http://127.0.0.1:8765` and the token from the example above.

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

State machine: `queued` → `running` → `completed` or `indeterminate`; an operator may close a confirmed-stopped job as `failed`. An uncertain GPU outcome is **never** automatically retried and continues to reserve that model's execution slot until an operator confirms it has stopped. A CPU scheduler can safely wake only still-queued jobs. No exactly-once inference guarantees are claimed across remote GPU failures. See [Architecture](docs/ARCHITECTURE.md).

## HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Static bilingual EN/VI landing page (`/?lang=vi`), no GPU activation |
| `GET` | `/v1/health` | Minimal public CPU liveness (never a GPU readiness probe) |
| `GET` | `/v1/models` | Authenticated list with individual JSON Schema controls |
| `GET` | `/v1/models/{model_id}` | Capabilities, license and validated per-model options |
| `POST` | `/v1/audio/jobs` | Durable asynchronous enqueue, optional idempotency key, HTTP 202 |
| `GET` | `/v1/audio/jobs/{job_id}` | Owner-scoped timing, usage, safe lifecycle diagnostics and WAV metadata |
| `GET` | `/v1/audio/jobs/{job_id}/audio` | Authenticated WAV download after completion |
| `GET` | `/v1/ops/metrics` | Owner-only aggregate DB state counts |

The public `/` landing page serves bilingual, self-contained HTML/CSS. It never opens a GPU, loads remote assets or reads deployment secrets. The language switch is `/?lang=vi` or `/?lang=en`; the model catalog and inference stay Bearer-protected. Job responses retain `inference_ms` and include owner-scoped `timing`, `usage`, redacted `diagnostics`, and `audio_quality` (RMS/peak dBFS and near-clipping sample counts). Quality warnings never discard a valid WAV or modify its waveform. Unsupported token counts and model-load timing are `null`, not estimates. See [API reference](docs/API.md).

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

**Minimal path:** Install Modal, select your workspace/environment, create the four required Secrets (hashed API tokens, PostgreSQL + Fernet key, TLS Redis, Hugging Face read token), and migrate your database once. The [deployment guide](docs/DEPLOYMENT.md) gives the exact secret values and migration procedure.

```bash
python -m pip install -e '.[modal,dev]'
modal setup
export MODAL_ENVIRONMENT="your-modal-environment"
# After configuring Secrets and migrating PostgreSQL, download chosen weights:
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_tts_model
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_audio_tokenizer
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_voxcpm2_model

# Select backends once; their hash-locked runtimes build automatically.
TEXT_TO_SPEECH_ENABLED_MODELS=moss-tts-v1.5,voxcpm2 \
  modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py
```

By default **no model is enabled** until selected. Discover supported per-request settings with authenticated `GET /v1/models`, then choose the `model` and `model_config` in `POST /v1/audio/jobs`. Each model schema supplies its own defaults, such as `voice: default` for MOSS and Higgs. Inspect the schema before overriding settings. Selection does **not** prove GPU readiness: workers verify checkpoint integrity when starting. Higgs TTS 3 additionally requires explicit authorization for the intended use and an immutable audited SGLang image reference; it is **not** enabled by the example.

The November 21, 2026 report records HTTPS requests that returned WAVs from an L40S GPU for MOSS-TTS-v1.5 and VoxCPM2 in one canary. Higgs TTS 3 has research WAV results only. The [release evidence](docs/RELEASE_EVIDENCE.md) identifies the tested snapshot and explains how it relates to the `v1.0.0` tag and `main`.

## Security and operations

The production gateway requires PostgreSQL, TLS-enabled Redis/Valkey, and Modal Secrets. Reference-voice requests are rejected before admission by default. Operators must enable the feature and grant access per client through the [reference cloning settings](docs/DEPLOYMENT.md), then verify speaker permission separately. See [Security](docs/SECURITY.md) for encrypted storage and quotas, and [Operations](docs/OPERATIONS.md) for reconciling uncertain jobs and managing costs.

## Documentation

[Engineering guide index](docs/README.md) · [API](docs/API.md) · [Deployment](docs/DEPLOYMENT.md) · [Model licenses](MODEL_LICENSES.md) · [Contributing](.github/CONTRIBUTING.md) · [Code of conduct](.github/CODE_OF_CONDUCT.md)
