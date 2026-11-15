# Deploy and operate on Modal

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](DEPLOYMENT.vi.md)


**Read before operating.** The source includes a durable CPU gateway and three model-specific GPU adapters. Nothing here is deployed automatically. Deploying enables a CPU-only minute scheduler and may incur infrastructure costs; GPU inference occurs only when a client submits a claimable job. Configure spending limits in Modal and separately review upstream model licenses (especially Higgs TTS 3).

## 1. Choose a single Modal workspace and environment

Run commands from the repository root with Python 3.11–3.13. To obtain a fresh checkout, run `git clone https://github.com/dangkhoa2016/Multi-Model-Text-to-Speech-AI-Inference.git` followed by `cd Multi-Model-Text-to-Speech-AI-Inference`. Skip cloning if already in the project folder. On Windows, replace the POSIX virtual-environment activation command with the PowerShell equivalent:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[modal,dev]'
modal setup
modal config show --redact
modal environment list
# Choose an environment shown above; "main" is an example, not a default.
export MODAL_ENVIRONMENT="main"
modal secret list --env "$MODAL_ENVIRONMENT"
modal volume list --env "$MODAL_ENVIRONMENT"
```

The active Modal profile controls the **workspace**. Explicit `--env` / `MODAL_ENVIRONMENT` controls which environment holds the Secrets, Volumes and deployment. Confirm both before creating resources; names in different environments are not interchangeable. See the [Modal environment CLI](https://modal.com/docs/cli/latest/environment).

Run the offline tests after selecting the Modal workspace/environment:

```bash
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
| `text-to-speech-job-database` | `TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL` (e.g. `postgresql+psycopg://...`, TLS as configured by provider), `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` (Fernet key), optional `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_CLIENT`, `TEXT_TO_SPEECH_MAX_PENDING_JOBS_PER_MODEL`, `TEXT_TO_SPEECH_COMPLETED_JOB_RETENTION_DAYS`, and the paired estimated GPU USD budget settings | Gateway and CPU runners, **not GPU** |
| `text-to-speech-hugging-face-authentication` | `HUGGING_FACE_READ_TOKEN` or `HF_TOKEN` (authorized Read token); optional `HF_XET_HIGH_PERFORMANCE=1` | CPU-only model checks and downloads; **never gateway/GPU** |
| `text-to-speech-rate-limiting` | `TEXT_TO_SPEECH_REDIS_CONNECTION_URL` (`rediss://...`), optional `TEXT_TO_SPEECH_REDIS_RATE_LIMIT_NAMESPACE` and the `TEXT_TO_SPEECH_RATE_*` and `TEXT_TO_SPEECH_AUTHENTICATION_*` quotas documented in `.env.example` | Gateway only |

`TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON` sample **structure** (not real hashes):

```json
{"rails-app":"<64 lowercase hexadecimal SHA256 characters>","notebook":"<64 lowercase hexadecimal SHA256 characters>"}
```

Hashing assumes unguessable, high-entropy tokens. The example `.env.example` contains **no usable credentials**. Rotate keys by updating the Modal Secret and intentionally rolling the gateway. Do not rotate `TEXT_TO_SPEECH_FERNET_ENCRYPTION_KEY` without a planned data re-encryption procedure; it is required to recover queued payloads and finished audio after a restart.

## 3. Apply the PostgreSQL migration BEFORE deploying

**Upgrading an existing database:** Back up PostgreSQL; preserve its Fernet key. Stop old HTTP gateways and pause the periodic queued-job scheduler before changing the schema. Allow older CPU coordinators to finish in-flight GPU jobs; never cancel a GPU just to upgrade. Apply `alembic upgrade head` through expanded revision `0002`, compatibility revision `0003` and additive nullable diagnostics revision `0004` and schema normalization/audit-index revision `0005` and estimated GPU budget ledger revision `0006`. The legacy `payload_sha256` column remains readable/writable for in-flight old workers; the new gateway uses keyed HMAC. Startup rekeys metadata in bounded, restartable transactions without reading encrypted WAV. Deploy new gateway and scheduler only after migration. Do not run old and new HTTP gateways admitting jobs together. Existing databases that already applied the earlier rename-based `0002` gain the compatibility column in `0003`; this cannot retroactively restore results lost before the fix. Reconcile unfinished jobs. Do not rotate encryption keys without a separately verified migration.


From a trusted operator machine with an authorized migration database URL (prefer a direct, non-pooled connection):

```bash
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL='postgresql+psycopg://YOUR_USER:YOUR_PASSWORD@YOUR_HOST/YOUR_DB?sslmode=require'
alembic upgrade head
alembic current
```

Use secure environment/secret injection for the actual URL; the literal value above is a placeholder. The initial migration creates `tts_jobs`, `tts_audit_events`, an `admission_lock` singleton and indexes. **Never run `Base.metadata.create_all` in production.** Your migration role needs schema-creation permissions; runtime role only needs DML. Take a tested backup before any future schema change.

## 4. Verify Hugging Face permissions, then download models on CPU

1. Sign in to [Hugging Face](https://huggingface.co/), open each model repository and request/accept access **if its repository requires it**. Approval applies to your user account, not to Modal. Some models may be public and require no acceptance. Check the model page; do not assume their access policy is permanent.
2. Create a **read** token from that authorized account; place it under `HUGGING_FACE_READ_TOKEN` **or** `HF_TOKEN` in the Modal Secret named `text-to-speech-hugging-face-authentication`. A token stored in your local shell is **not** automatically available to Modal CPU containers.
**Hugging Face token and Xet acceleration.** CPU-only model access checks and checkpoint download functions accept either the existing `HUGGING_FACE_READ_TOKEN` or the standard `HF_TOKEN`; the existing name takes precedence if both have values. Set just one in the same `text-to-speech-hugging-face-authentication` Modal Secret. No Hugging Face token is passed to GPU workers or the HTTP gateway. The CPU download image installs `huggingface_hub` and `hf_xet>=1.7,<2`. Optional `HF_XET_HIGH_PERFORMANCE=1` belongs **in that CPU-only Secret**; omit it for default transfers. This setting can increase CPU, memory, network and disk I/O demands, and cost; improvements are not guaranteed. Starting with `hf_xet` 1.7.0, high-performance download buffers adapt to available memory. After benchmarking, remove the flag to revert. Do not use deprecated `HF_HUB_ENABLE_HF_TRANSFER`. See [Hugging Face Xet storage](https://huggingface.co/docs/hub/xet/using-xet-storage). Updating the Secret affects subsequent CPU function containers; verify in the selected Modal environment before an authorized download.

3. Verify access in the target Modal workspace/environment **before** downloading large files. The commands below allocate CPU only, not GPU:

```bash
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id moss-tts-v1.5
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id voxcpm2
# License review is separate from the technical access check for Higgs TTS 3:
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::check_hugging_face_model_access --model-id higgs-tts-3
```

The check validates repository access and returns an immutable revision SHA without downloading weights. Missing/invalid tokens, gated access denial, unavailable repositories, and Hugging Face failures produce actionable, sanitized errors. Each model is independent; failure to access one model does not undo other completed downloads. These checks do **not** grant model-license permission.

4. Download checkpoints only after the corresponding preflight succeeds:

The checkpoint Volumes are `text-to-speech-moss-tts-v1-5-model-weights`, `text-to-speech-higgs-tts-3-model-weights` and `text-to-speech-voxcpm2-model-weights`. Downloads can incur CPU, storage and egress charges but **do not allocate a GPU**:

```bash
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_tts_model
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_voxcpm2_model
# MOSS additionally requires its own pinned audio tokenizer (CPU-only):
modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_moss_audio_tokenizer
```

**MOSS codec dependency:** `OpenMOSS-Team/MOSS-Audio-Tokenizer` is a separate
Apache-2.0 checkpoint at immutable revision
`3cd226ba2947efa357ef453bcad111b6eafba782`. The CPU downloader
checks the 14-file snapshot (7,101,115,998 bytes), full file hashes and
upstream LFS hashes before committing its own Modal Volume,
`text-to-speech-moss-audio-tokenizer-weights`. The MOSS worker mounts it
at `/codec` and verifies the pinned revision and integrity manifest before
starting the speech server. Both preprocessing and vocoder use the
local `/codec` path with `HF_HUB_OFFLINE=1`.

**MOSS compatibility:** Two source-hash-guarded patches for
`sglang-omni==0.2.0` preserve seven independent text/audio sampling
controls via `stage_params.tts_engine`, and extend speech-language
validation to include Vietnamese. Unexpected upstream versions or source
changes fail the image build. CPU contract tests do not substitute for
CUDA, memory or real WAV inference verification.

The Higgs checkpoint is separately licensed. **Only after confirming your legal right to use its hosted API**, explicitly run:

```bash
HIGGS_TTS_3_LICENSE_CONFIRMED=1 modal run --env "$MODAL_ENVIRONMENT" modal_app.py::download_higgs_tts_3_model
```

The Higgs license acknowledgement is **not** a license grant. Higgs stays disabled unless it is explicitly included in `TEXT_TO_SPEECH_ENABLED_MODELS`; selecting it without independent authorization (`HIGGS_TTS_3_LICENSE_CONFIRMED=1`) and an immutable audited SGLang digest makes deployment fail fast. The worker also enforces the license at startup. Changing this policy requires a new deployment, never a client request. MIT licensing of the gateway does not change the Higgs checkpoint terms.

## 5. Deploy the CPU gateway, scheduler and isolated GPU definitions

Choose the models to expose using **one deploy-time setting**. It defaults to an empty list (all models disabled). MOSS and VoxCPM2 use the repository's verified dependency locks automatically; do not set internal GPU or codec acceptance flags.

```bash
# After Secrets, PostgreSQL migration and explicit CPU checkpoint downloads:
TEXT_TO_SPEECH_ENABLED_MODELS=moss-tts-v1.5,voxcpm2 \
  modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py

# Higgs is optional and has an independent licensing/runtime requirement.
# Do NOT select Higgs unless the intended hosted/API use is authorized.
# HIGGS_TTS_3_LICENSE_CONFIRMED=1 \
# TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF=lmsysorg/sglang-omni@sha256:<verified-digest> \
# TEXT_TO_SPEECH_ENABLED_MODELS=higgs-tts-3 \
#   modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py
```

`enabled` in the catalog represents the deployer's selection, **not an assertion of GPU readiness**. At worker startup, the system verifies pinned weight/codec checkpoint integrity and loads the actual runtime. Missing checkpoints or incompatible runtimes fail with explicit errors; no operator `GPU_ACCEPTED` or `CODEC_READY` flags are accepted as readiness evidence.

`text_to_speech_api()` runs on CPU, reads **hash-only** client authentication secrets, PostgreSQL and Redis. `dispatch_text_to_speech_job()` and the minute `resume_queued_text_to_speech_jobs()` functions run on CPU with only data-store credentials. GPU class methods have only their individual weight Volume. A queued request wakes a CPU coordinator; if that signal fails, the minute sweep safely re-wakes queued jobs. Neither startup, migration nor the scheduled sweep deliberately starts a GPU without a queued job.

MOSS/Higgs use loopback-only SGLang-Omni `127.0.0.1:8000`. The MOSS worker includes `configs/moss_tts.yaml` in its image and explicitly passes `--config /opt/text-to-speech/configs/moss_tts.yaml` to select the MOSS pipeline. The configuration is based on the [upstream MOSS cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/moss_tts.md); the bounded single-process profile, pinned CUDA compiler/runtime, and runtime linker have passed isolated L40S WAV acceptance. End-to-end HTTPS gateway acceptance was also demonstrated in an isolated canary on 21 November 2026; each operator must verify their own deployment. The Higgs checkpoint is served without the MOSS-specific pipeline flag; verify its checkpoint identifier and serving API separately.

**MOSS codec checks:** Selecting MOSS builds its hash-locked runtime.
The worker checks both model and audio tokenizer manifests before loading the
pipeline. Download the tokenizer explicitly with the CPU-only function before
sending real jobs; no manual codec acceptance gate is required.

**Hash-enforced runtime images:** The MOSS image uses Debian/Python 3.12
and an exact 283-package lock under `runtime_locks/`; VoxCPM2 uses a
separate 167-package lock on the same Python baseline. Both were resolved
for Linux x86_64/manylinux 2.34 and use Modal's pinned `uv==0.13.5`
installer with `--require-hashes`, preventing an unexpected dependency
artifact from silently replacing the reviewed distribution set. The
lockfile bytes are SHA-256 checked locally before Modal constructs images.

For VoxCPM2, the audited package version is pinned inside the committed
lockfile, so operators need not duplicate it in a deploy environment variable.
The worker verifies the checkpoint manifest before loading CUDA inference.

MOSS no longer layers the latest PyPI release over a historical SGLang
container: it builds a single Python 3.12/CUDA dependency environment
from its checked lock. The two MOSS compatibility patches still verify
the exact `sglang-omni==0.2.0` source SHA-256.

**Higgs remains separate:** Only the Higgs image uses
`TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF` with a pinned
`lmsysorg/sglang-omni@sha256:<digest>` reference. Its independently
restricted license and runtime must be reviewed before any deployment. Selecting Higgs without license confirmation or a valid immutable image reference fails before registry image construction.
Do not treat the MOSS and VoxCPM2 locks as evidence for Higgs.

Dependency integrity alone does not establish GPU readiness. Isolated L40S
WAV acceptance passed for MOSS-TTS-v1.5 and VoxCPM2 using the audited runtime
profiles. Operators must still verify the deployed gateway, CPU admission,
queue and storage before opening client traffic. Never infer Higgs acceptance
from either model's results.

**Standalone GPU acceptance (operator-authorized only):**
`scripts/gpu_runtime_acceptance.py` is an isolated research diagnostic, not
a production service. It covers **MOSS-TTS-v1.5, VoxCPM2, and Higgs TTS 3**
using SHA-256-locked packages, read-only model Volumes, and short fixed
sentences. It never downloads model weights or exposes a public endpoint.
Without explicit operator approval, it declares `gpu=None` and refuses
GPU execution.

Check the Modal Workspace Budget and test **one model and one phase**
at a time. The initial CUDA probe checks the real GPU driver, VRAM and
a small matrix multiplication; it does **not** load checkpoint weights.

```bash
# Choose exactly one: moss, voxcpm, or higgs.
TTS_GPU_ACCEPTANCE_APPROVAL=APPROVED_GPU_ACCEPTANCE_WITH_CHARGES modal run --env "$MODAL_ENVIRONMENT" scripts/gpu_runtime_acceptance.py --model higgs --phase cuda

# Only after CUDA passes and a separate GPU WAV run is authorized:
# TTS_GPU_ACCEPTANCE_APPROVAL=APPROVED_GPU_ACCEPTANCE_WITH_CHARGES modal run --env "$MODAL_ENVIRONMENT" scripts/gpu_runtime_acceptance.py --model higgs --phase wav
```

Replace `higgs` with `moss` or `voxcpm` to test the other models.
The WAV phase independently verifies checkpoint SHA-256, initializes
the model, synthesizes a short Vietnamese sentence, and records WAV
metadata, elapsed time, and peak PyTorch VRAM. Each function has one
L40S container and a finite timeout. Failed experiments can still
incur GPU charges.

The **Higgs research runtime** uses the same audited
`sglang-omni==0.2.0` Python lock as MOSS, without the MOSS-specific
patches. This is **not** the separate OCI image used by the production
Higgs worker. This test does not establish production compatibility.
Optional `TTS_HIGGS_CHECKPOINT_REVISION_SHA` can pin an independently
verified revision. Higgs remains subject to its research/non-commercial
model terms; commercial hosted deployment is a separate decision.
See the [upstream Higgs SGLang cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/higgs_tts.md).
Do not enable production model admission or redeploy the Gateway as
a side effect of this diagnostic.

**Optional CPU-only Higgs research checkpoint download:** The production
Higgs downloader requires a separate hosted-service licensing decision;
research acceptance does not change that production policy. To prepare the
research checkpoint without enabling the production worker, first verify
research eligibility, Hugging Face access, the Modal Workspace Budget, an
**existing empty** `text-to-speech-higgs-tts-3-model-weights` Volume in
the selected Modal environment, and the existing read-token Secret
`text-to-speech-hugging-face-authentication`. This explicitly approved CPU
task downloads an immutable revision, checks the repository file list,
records a SHA-256 manifest, and commits the Volume:

```bash
# CPU/storage/network charges are possible; this does not start a GPU.
TTS_HIGGS_RESEARCH_CHECKPOINT_APPROVAL=APPROVED_HIGGS_RESEARCH_CPU_DOWNLOAD_WITH_CHARGES \
modal run --env "$MODAL_ENVIRONMENT" scripts/higgs_research_checkpoint.py
```

To pin the current immutable revision, also set
`TTS_HIGGS_CHECKPOINT_REVISION_SHA=<verified-40-character-lowercase-sha>`
for this download **and** the later Higgs GPU acceptance. The downloader
fails closed if the Volume already contains data or the revision differs
from the pin; inspect any existing checkpoint before retrying. This
research-only approval does not set `HIGGS_TTS_3_LICENSE_CONFIRMED`,
deploy a public API, or permit hosted commercial use.

**Checkpoint integrity:** Each explicit CPU download now writes `.tts-checkpoint-integrity.json` (immutable Hugging Face revision, per-file SHA-256/size) into that model's Volume. The CPU download checks that the on-disk file list matches the immutable Hugging Face repository tree; each GPU worker checks the recorded bytes before loading its model. This detects missing/modified files but cannot independently authenticate a maliciously altered manifest. To bind an operator-approved revision at deployment, optionally set `TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA`, `TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA`, and/or `TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA` to exact 40-character lowercase Hugging Face revision SHAs before `modal deploy`. Earlier Volumes without the manifest must be deliberately reverified/repopulated on CPU before GPU use. Full hashing on GPU cold startup may add latency/cost.

**Optional cost admission:** Schema revision `0006` adds a PostgreSQL UTC-day estimate ledger. Add **both** `TEXT_TO_SPEECH_ESTIMATED_GPU_USD_PER_JOB` and `TEXT_TO_SPEECH_MAX_ESTIMATED_GPU_USD_PER_DAY` as positive USD decimals to the `text-to-speech-job-database` Modal Secret, or leave both absent to disable. Every newly admitted job reserves the fixed estimate atomically; a duplicate idempotency request is free, and exceeding the daily estimate rejects HTTP 429 with a UTC-day `Retry-After`. Estimates are **not actual GPU expenses**, are not refunded, and exclude cold starts and infrastructure. Keep a real Modal Workspace Budget independently.

**Production reference-cloning policy:** By default voice cloning is disabled. A caller's `reference_consent: true` is not independent proof of authorization. Only after checking speaker rights and abuse controls, opt in **on the machine running `modal deploy`** (not in the notebook or API request):

```bash
export TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING=1
export TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON='["rails-app"]'
modal deploy --env "$MODAL_ENVIRONMENT" modal_app.py  # Run only with explicit permission to deploy
```

`modal_app.py` captures this non-secret flag and embeds `1` or `0` into the CPU gateway image with `.env()`; the remote container does **not** inherit deploy-shell environment variables automatically. Without **both** global opt-in and an allowlisted active client ID, a production reference-audio request returns HTTP 403 **before enqueue or GPU dispatch**. To disable the feature again, unset/set the flag to `0` and redeploy. This flag does not independently verify voice consent.

**Optional CPU operations monitor:** By default `TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE=off` and no monitor is scheduled. On the **Modal deploy machine**, set `TEXT_TO_SPEECH_OPERATIONS_MONITOR_SCHEDULE=hourly` to enable a read-only hourly CPU check of overdue queued jobs, stale heartbeats, persisted `indeterminate` work, and the current UTC-day estimated budget. The monitor only prints aggregate counts to Modal Logs and fails its function invocation when operator attention is needed; it never starts/retries GPU, emails anyone, or sends webhooks. Disable with `off` and redeploy. The existing once-per-minute queued-job sweep remains separate and active.

## 6. Test safely and reconcile failures

First confirm authenticated catalog, rejection of invalid credentials, idempotency, POST 202, GET queued status and owner-only metrics. Then **with explicit authorization to incur GPU costs**, test one short task per model, sequentially: VoxCPM2 → MOSS → licensed Higgs. Verify WAV decoding and metadata, modal logs, cold/warm latency and actual bills. An offline CPU mock WAV is **silence**, not a verified inference result.

A `running` job whose heartbeat is too old is *reported* `indeterminate` without automatic retry. Unknown GPU outcomes are persisted `indeterminate`; they reserve the model slot. **Do not delete/requeue or submit a different ID** before examining the exact invocation in Modal logs. If you independently verify GPU work has stopped, use the operator-only CLI (not an API route):

```bash
python scripts/reconcile_job.py JOB_ID CLIENT_ID
python scripts/reconcile_job.py JOB_ID CLIENT_ID --confirm-stopped
```

The latter is an irreversible operational decision that marks a running/indeterminate job `failed` and frees its GPU slot. It does **not** rerun GPU inference. If GPU may still be executing, do not use `--confirm-stopped`. Keep credential/data secrets out of shell command text and logs.

Only `completed` and operator-confirmed `failed` jobs are purged after the configured retention window (default seven days) by the CPU scheduler. `indeterminate`, `running`, and `queued` jobs are never automatically purged; an uncertain job continues to reserve its model execution slot indefinitely until an authorized operator confirms remote GPU termination and uses the reconcile CLI. This protects against overlapping paid invocations. Database backups and Modal's transport storage need separate retention policies.

## 7. Operator acceptance boundaries

CPU unit tests and PostgreSQL/Redis integration tests verify software behavior
but are not substitutes for running the requested model on an actual GPU. When
setting up a deployment, verify model checkpoint access, runtime compatibility,
authenticated HTTPS job admission, completion status, output WAV validity and
audit events with a controlled, explicitly authorized request. A single GPU
canary does **not** establish multi-replica capacity, failover, continuous
availability or public multi-user readiness.

Before offering an API to others, operators must separately review real spend
limits, upstream checkpoint and dependency licenses, user/speaker consent,
privacy, data retention, edge controls, storage and disaster recovery. Generated
WAV is encrypted in PostgreSQL BYTEA; external R2/S3 object storage is not part
of this implementation. Higgs TTS 3 requires independent licensing permission
and successful runtime checks before it can be enabled.

**Short-lived demos:** Scheduled CPU functions (including the queued-job
recovery sweep) may incur Modal compute charges while an app is deployed, even
without GPU inference. Stop the application when the demo is finished; stopping
the app does not automatically remove database or checkpoint Volume storage
charges. Confirm backup recovery separately before deleting any persistent
storage.

See [Architecture](ARCHITECTURE.md), [API](API.md), [Security](SECURITY.md).

## CPU integration tests with PostgreSQL and Redis

The normal pytest suite runs without external services. For a real-service acceptance check, create a **disposable** PostgreSQL test database and a private Redis test instance; do not use production credentials or production data. Run Alembic migrations before the tests:

```bash
# Read both URLs from your local secret manager; do not commit their values.
# TTS_INTEGRATION_POSTGRESQL_URL must name a database containing "test" or "validation".
# TTS_INTEGRATION_REDIS_URL must refer to a disposable Redis instance/namespace.
export TEXT_TO_SPEECH_POSTGRESQL_DATABASE_URL="$TTS_INTEGRATION_POSTGRESQL_URL"
alembic upgrade head
python -m pytest -q tests/test_real_postgresql_redis_integration.py
```

This optional, CPU-only suite checks multiple independent PostgreSQL connections competing to enqueue or claim the same job, owner isolation, the reserved slot for uncertain outcomes, and Redis Lua quotas and lockout across independent clients. It creates test records and temporary Redis keys; it does **not** contact Modal or run inference. Without both URL variables, the integration tests are skipped. Do not infer multi-replica Modal readiness, backup/restore success, or GPU capability from this local integration run.

## 8. First-install acceptance checklist

Start with an empty environment; record pass/fail, never credentials.

1. [ ] Activate Python 3.11–3.13; install dependencies and run `python -m pytest -q`.
2. [ ] Confirm the workspace with `modal config show --redact` and select `MODAL_ENVIRONMENT` from `modal environment list`.
3. [ ] Set up PostgreSQL with TLS; use distinct migration (DDL) and runtime (DML) roles, with a verified backup.
4. [ ] Set up TLS Redis/Valkey (`rediss://`), including Lua support and an isolated rate-limit namespace.
5. [ ] Use a password manager's cryptographically secure generator to create **two different** random, printable ASCII Bearer tokens (48+ characters: client and owner). Save raw values in the manager, then hash each separately with `python scripts/hash_token.py`; never paste the raw values into logs.
6. [ ] Generate a persistent Fernet key once. Create all four named Modal Secrets in the selected environment; confirm names using `modal secret list --env "$MODAL_ENVIRONMENT"`.

7. [ ] Run `alembic upgrade head` with the migration connection and confirm `alembic current` is at `0006`.
8. [ ] Run Hugging Face access checks for selected models. `ACCESS OK` means authorization to read files, not GPU readiness.
9. [ ] With explicit approval for CPU/storage charges, download weights and confirm each Volume has its integrity manifest.
10. [ ] Pin audited runtime artifacts in the **deploy shell** (Section 9), or intentionally leave models disabled.
11. [ ] Deploy the CPU gateway only when authorized; finish read-only checks (Section 10) before submitting jobs.

## 9. Audit and pin GPU runtimes

MOSS-TTS-v1.5 and VoxCPM2 use the committed audited dependency locks. No preapproved Higgs SGLang-Omni image digest is shipped. Do **not** invent a digest or use a mutable tag.

- **MOSS/Higgs:** Inspect a trusted `lmsysorg/sglang-omni` container's registry digest and upstream compatibility with `sglang-omni==0.2.0` and the MOSS YAML. Record the exact `lmsysorg/sglang-omni@sha256:<64-lowercase-hex>` reference in `TEXT_TO_SPEECH_SGLANG_OMNI_IMAGE_REF` on the deploy machine.
- **VoxCPM2:** Review upstream releases and the package index (e.g. `python -m pip index versions voxcpm`); audit the selected exact `X.Y.Z` release, CUDA dependencies and `VoxCPM.from_pretrained` interface. Set `TEXT_TO_SPEECH_VOXCPM_PACKAGE_VERSION` only after that review.

- **Immutable checkpoints:** Record the real 40-hex Hugging Face revision after a successful CPU download; optionally pin it as `TEXT_TO_SPEECH_MOSS_TTS_REVISION_SHA`, `TEXT_TO_SPEECH_HIGGS_TTS_3_REVISION_SHA` or `TEXT_TO_SPEECH_VOXCPM2_REVISION_SHA`. It must match the Volume's manifest.
- **Higgs license:** Hosted/API permission is a separate legal decision. Keep `HIGGS_TTS_3_LICENSE_CONFIRMED=0` until authorized.

Unselected models appear as `enabled=false` in the CPU catalog. Invalid required deployment configuration fails validation. A successful build is **not** a passed GPU/audio acceptance test.

## 10. Read-only HTTP acceptance after authorized deployment

Get the **actual** URL for `text_to_speech_api` from the Modal Dashboard or application info. Never guess the hostname.

```bash
modal app info text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"
modal app logs text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT" --tail 30
export TTS_BASE_URL="https://YOUR-ACTUAL-DEPLOYED-ENDPOINT"
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/"
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/v1/health"
# One unauthenticated attempt should return 401; repeated failures trigger lockout.
curl --silent --show-error --include --max-time 20 "$TTS_BASE_URL/v1/models"
read -r -s -p "Client Bearer token: " TTS_CLIENT_TOKEN; echo
curl --silent --show-error --include --max-time 20 \
  -H "Authorization: Bearer $TTS_CLIENT_TOKEN" "$TTS_BASE_URL/v1/models"
unset TTS_CLIENT_TOKEN
```

Expected: `/` returns bilingual HTML (HTTP 200); `/v1/health` returns `{"status":"ok"}`-style CPU liveness (HTTP 200), **not GPU readiness**; unauthenticated `/v1/models` returns HTTP 401; with a valid client token it returns 200 and three model entries. `enabled=false` is expected for unselected models. Missing or invalid required deployment settings fail validation rather than implying runtime readiness. No POST, WAV or paid GPU invocation belongs in this stage.

## 11. Troubleshooting, rotation and rollback

| Symptom | First checks and next steps |
| --- | --- |
| Modal Secret not found | Verify profile, `MODAL_ENVIRONMENT` and `modal secret list --env "$MODAL_ENVIRONMENT"`; create the named Secret in the correct environment |
| Database readiness fails | Verify `alembic current`, TLS/CA trust, credentials and migration `0006`; do not run `create_all` in production |
| Redis HTTP 503 | Check the `rediss://` URL, certificate, firewall and Redis Lua support; do not bypass fail-closed checks |
| Hugging Face 401/403/gated | Re-run CPU-only access preflight with the account authorized for that model; do not blindly download |
| Model `enabled=false` or POST 503 | Check the selected-model list and, where applicable, Higgs runtime settings and license confirmation; redeploy intentionally |
| Checkpoint integrity error | Compare the Volume's manifest to the authorized revision; repeat CPU-only verification/download as appropriate |
| HTTP 401/429 | Check token-to-client hashes/revocation or respect `Retry-After`; never remove security limits |
| Job `indeterminate` | Inspect its precise Modal Function Call and independently verify GPU termination before operator reconciliation |

After rotating a client/owner hash in the authentication Secret, refresh gateway containers intentionally. For a **no-code** refresh, `modal app rollover text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"` replaces containers and may affect in-flight jobs. Never rotate the Fernet key without verified re-encryption.

Use `modal app history text-to-speech-ai-inference --env "$MODAL_ENVIRONMENT"` before any [Modal application rollback](https://modal.com/docs/cli/latest/app). Rollback of the application does **not** undo Alembic migrations or restore PostgreSQL backups. Do not stop a possibly running GPU invocation just to roll back.
