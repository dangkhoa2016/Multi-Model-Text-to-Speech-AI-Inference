# Deploy and operate on Modal

🌐 [English](DEPLOYMENT.md) | [Tiếng Việt](DEPLOYMENT.vi.md)

**Read before operating.** The source includes a durable CPU gateway and three *unverified* real-model GPU adapters. Nothing here is deployed automatically. Deploying enables a CPU-only minute scheduler and may incur infrastructure costs; GPU inference occurs only when a client submits a claimable job. Configure spending limits in Modal and separately review upstream model licenses (especially Higgs TTS 3).

## 1. Choose a single Modal workspace and environment

Use Python 3.11–3.13. Install dependencies locally and authenticate:

```bash
python -m pip install -e '.[modal,dev]'
modal setup
python -m pytest -q
```

Requirements: a reachable PostgreSQL instance with backups and TLS, a Redis/Valkey endpoint using TLS (`rediss://`), a Modal workspace with CPU/GPU/storage quotas, three weight Volumes, and HTTPS clients. Use **separate credentials** for different application roles and limit database permissions.

## 2. Generate secrets without committing them

Use a secure secret manager. For each client and the owner, generate unique, high-entropy random Bearer tokens (at least 24 ASCII characters, preferably 48+). Hash each token locally for the server registry; give the **raw** token only to its intended API client.

```bash
python scripts/hash_token.py
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Create the following four Modal Secrets in the *same workspace/environment* via the Modal Dashboard (recommended rather than putting values in shell history):

| Secret name | Environment variables | Scope |
| --- | --- | --- |
| `text-to-speech-api-authentication` | `TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` (client ID → SHA256 hex), `TEXT_TO_SPEECH_OWNER_TOKEN_SHA256` (separate owner digest), optional `TEXT_TO_SPEECH_REVOKED_CLIENT_IDS` | Gateway only |
| `text-to-speech-job-database` | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` (e.g. `postgresql+psycopg://...`, TLS as configured by provider), `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` (Fernet key), optional `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT`, `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL`, `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS` | Gateway and CPU runners, **not GPU** |
| `text-to-speech-hugging-face-authentication` | `HUGGING_FACE_READ_TOKEN` (read-only token belonging to an authorized Hugging Face account) | CPU-only model checks and downloads; **never gateway/GPU** |
| `text-to-speech-rate-limiting` | `TEXT_TO_SPEECH_REDIS_CONNECTION_URL` (`rediss://...`), optional `TEXT_TO_SPEECH_REDIS_RATE_LIMIT_NAMESPACE` and the `TEXT_TO_SPEECH_RATE_*` and `TEXT_TO_SPEECH_AUTHENTICATION_*` quotas documented in `.env.example` | Gateway only |

`TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` sample **structure** (not real hashes):

```json
{"rails-app":"<64 lowercase hexadecimal SHA256 characters>","notebook":"<64 lowercase hexadecimal SHA256 characters>"}
```

Hashing assumes unguessable, high-entropy tokens. The example `.env.example` contains **no usable credentials**. Rotate keys by updating the Modal Secret and intentionally rolling the gateway. Do not rotate `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` without a planned data re-encryption procedure; it is required to recover queued payloads and finished audio after a restart.

## 3. Apply the PostgreSQL migration BEFORE deploying

From a trusted operator machine with an authorized migration database URL (prefer a direct, non-pooled connection):

```bash
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@YOUR_HOST/YOUR_DB?sslmode=require'
alembic upgrade head
alembic current
```

Use secure environment/secret injection for the actual URL; the literal value above is a placeholder. The initial migration creates `tts_jobs`, `tts_audit_events`, an `admission_lock` singleton and indexes. **Never run `Base.metadata.create_all` in production.** Your migration role needs schema-creation permissions; runtime role only needs DML. Take a tested backup before any future schema change.

## 4. Verify Hugging Face permissions, then download models on CPU

1. Sign in to [Hugging Face](https://huggingface.co/), open each model repository and request/accept access **if its repository requires it**. Approval applies to your user account, not to Modal. Some models may be public and require no acceptance. Check the model page; do not assume their access policy is permanent.
2. Create a **read** token from that authorized account; place it under `HUGGING_FACE_READ_TOKEN` in the Modal Secret named `text-to-speech-hugging-face-authentication`. A token stored in your local shell is **not** automatically available to Modal CPU containers.
3. Verify access in the target Modal workspace/environment **before** downloading large files. The commands below allocate CPU only, not GPU:

```bash
modal run modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run modal_app.py::check_hugging_face_model_access --model-id voxcpm2
# License review is separate from the technical access check for Higgs TTS 3:
modal run modal_app.py::check_hugging_face_model_access --model-id higgs-tts-3
```

The check validates repository access and returns an immutable revision SHA without downloading weights. Missing/invalid tokens, gated access denial, unavailable repositories, and Hugging Face failures produce actionable, sanitized errors. Each model is independent; failure to access one model does not undo other completed downloads. These checks do **not** grant model-license permission.

4. Download checkpoints only after the corresponding preflight succeeds:

The three weight Volumes are `text-to-speech-moss-tts-v1-5-model-weights`, `text-to-speech-higgs-tts-3-model-weights` and `text-to-speech-voxcpm2-model-weights`. Downloads can incur CPU, storage and egress charges but **do not allocate a GPU**:

```bash
modal run modal_app.py::download_moss_tts_model
modal run modal_app.py::download_voxcpm2_model
```

The Higgs checkpoint is separately licensed. **Only after confirming your legal right to use its hosted API**, explicitly run:

```bash
HIGGS_TTS_3_LICENSE_CONFIRMED=1 modal run modal_app.py::download_higgs_tts_3_model
```

The explicit acknowledgement is a deploy-time setting, **not** a grant of rights. Without it, the production catalog displays Higgs with `enabled=false`, the gateway refuses new Higgs jobs (HTTP 503), the CPU dispatcher will not claim any previously queued Higgs jobs, and the worker independently refuses startup. A changed decision requires redeploying the application. Do not assume the weights can be used commercially because this project's code is MIT licensed.

## 5. Deploy the CPU gateway, scheduler and isolated GPU definitions

```bash
# Default: Higgs TTS 3 remains disabled in CPU admission and on its GPU worker.
MOSS_TTS_GPU_TYPE=L40S HIGGS_TTS_3_GPU_TYPE=L40S VOXCPM2_GPU_TYPE=L40S modal deploy modal_app.py

# ONLY if your intended hosted/API use is independently licensed:
# HIGGS_TTS_3_LICENSE_CONFIRMED=1 MOSS_TTS_GPU_TYPE=L40S HIGGS_TTS_3_GPU_TYPE=L40S VOXCPM2_GPU_TYPE=L40S modal deploy modal_app.py
```

`text_to_speech_api()` runs on CPU, reads **hash-only** client authentication secrets, PostgreSQL and Redis. `dispatch_text_to_speech_job()` and the minute `resume_queued_text_to_speech_jobs()` functions run on CPU with only data-store credentials. GPU class methods have only their individual weight Volume. A queued request wakes a CPU coordinator; if that signal fails, the minute sweep safely re-wakes queued jobs. Neither startup, migration nor the scheduled sweep deliberately starts a GPU without a queued job.

MOSS/Higgs use loopback-only SGLang-Omni `127.0.0.1:8000`. The MOSS worker includes `configs/moss_tts.yaml` in its image and explicitly passes `--config /opt/text-to-speech/configs/moss_tts.yaml` to select the MOSS pipeline. The configuration is based on the [upstream MOSS cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/moss_tts.md); its actual compatibility with the installed runtime, model weights and L40S memory layout still requires GPU acceptance. The Higgs checkpoint is served without the MOSS-specific pipeline flag; verify its checkpoint identifier and serving API separately.

The `lmsysorg/sglang-omni:dev` base image is **mutable and not production pinned**. The VoxCPM2 package and several transitive packages are likewise unresolved to exact release artifacts. **Do not use this default configuration for a public/production deployment.** First capture a supported immutable container digest, audited dependency constraints and checkpoint SHA manifests, then validate CPU image builds and the GPU serving APIs. Do not invent a digest or assume CPU tests guarantee a working audio model. The VoxCPM2 worker excludes the optional denoiser (`denoise=true` rejected).

## 6. Test safely and reconcile failures

First confirm authenticated catalog, rejection of invalid credentials, idempotency, POST 202, GET queued status and owner-only metrics. Then **with explicit authorization to incur GPU costs**, test one short task per model, sequentially: VoxCPM2 → MOSS → licensed Higgs. Verify WAV decoding and metadata, modal logs, cold/warm latency and actual bills. An offline CPU mock WAV is **silence**, not a verified inference result.

A `running` job whose heartbeat is too old is *reported* `indeterminate` without automatic retry. Unknown GPU outcomes are persisted `indeterminate`; they reserve the model slot. **Do not delete/requeue or submit a different ID** before examining the exact invocation in Modal logs. If you independently verify GPU work has stopped, use the operator-only CLI (not an API route):

```bash
python scripts/reconcile_job.py JOB_ID CLIENT_ID
python scripts/reconcile_job.py JOB_ID CLIENT_ID --confirm-stopped
```

The latter is an irreversible operational decision that marks a running/indeterminate job `failed` and frees its GPU slot. It does **not** rerun GPU inference. If GPU may still be executing, do not use `--confirm-stopped`. Keep credential/data secrets out of shell command text and logs.

Only `completed` and operator-confirmed `failed` jobs are purged after the configured retention window (default seven days) by the CPU scheduler. `indeterminate`, `running`, and `queued` jobs are never automatically purged; an uncertain job continues to reserve its model execution slot indefinitely until an authorized operator confirms remote GPU termination and uses the reconcile CLI. This protects against overlapping paid invocations. Database backups and Modal's transport storage need separate retention policies.

## 7. Remaining production work

The implementation is a validated **CPU-level code baseline**, not a completed public TTS service. Live PostgreSQL/Redis multi-replica load tests and SGLang/Vox GPU acceptance are outstanding. Real spend caps, Cloudflare R2/S3 object storage (current code encrypts WAV into PostgreSQL BYTEA), speaker-identity/consent verification, dependency scanning, pinned upstream artifacts, edge/WAF controls and privacy/legal assessment are still required before public multi-user access.

See [Architecture](ARCHITECTURE.md), [API](API.md), [Security](SECURITY.md).
