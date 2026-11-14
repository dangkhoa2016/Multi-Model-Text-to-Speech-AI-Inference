# TTS HTTP API contract

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](API.vi.md)


All `/v1/*` endpoints except `/v1/health` require HTTPS and `Authorization: Bearer <CLIENT_TOKEN>`. Never put tokens in query strings or public notebook outputs. Responses have `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. The live operator token is separate from client credentials.

## Static bilingual landing page

- `GET /` returns English HTML; `GET /?lang=vi` returns Vietnamese HTML. Unsupported language values fall back to English.
- Public HTML is entirely static and contains no secrets, user data, remote assets or GPU readiness claims; rendering never wakes GPU workers.
- `/v1/models` and all job endpoints remain Bearer-authenticated.

## Model discovery

- `GET /v1/health` — gateway liveness only, no live GPU check.
- `GET /v1/models` — IDs, checkpoint names, licensing, capabilities and per-model JSON Schema under `model_config_schema`.
- `GET /v1/models/{model_id}` — strict types, bounds and defaults for an individual backend.

Models: `moss-tts-v1.5`, `higgs-tts-3`, `voxcpm2`. Unrecognized or unsupported `model_config` keys are rejected before GPU dispatch with HTTP 422. Note that Vox voice design uses a **natural-language prefix**, not deterministic synthetic age/gender identity controls. On 21 November 2026, MOSS-TTS-v1.5 and VoxCPM2 completed separate authenticated HTTPS requests yielding WAV files in a single isolated Modal L40S canary using the P1 source (19/19 aggregate final API checks passed). This only verifies the tested configuration, not all deployments or model options. Higgs TTS 3 hosted inference remains unverified and separately licensed.

## Create a durable job

`POST /v1/audio/jobs` with the optional **strongly recommended** `Idempotency-Key` header (8–128 safe ASCII characters). Persist and reuse the same key for a logical request, including after network timeouts. If the same client/key/payload already exists, HTTP 202 returns its existing `job_id` and `reused=true`; different payload returns HTTP 409. Key scope is **per client**. A key is no longer reserved once the terminal record expires through retention cleanup; do not assume infinite deduplication.

```http
POST /v1/audio/jobs HTTP/1.1
Authorization: Bearer YOUR_PRIVATE_CLIENT_TOKEN
Idempotency-Key: speech-2026-001
Content-Type: application/json
```

```json
{
  "model": "voxcpm2",
  "input": "Hello, welcome to our podcast.",
  "model_config": {
    "voice_description": "Warm and reassuring radio host",
    "gender": "female", "age": "adult", "emotion": "cheerful",
    "pace": "slow", "cfg_value": 2.0, "inference_timesteps": 10
  }
}
```

Response (illustrative):

```json
{"job_id":"<32-hex-id>","model":"voxcpm2","status":"queued","reused":false,"status_url":"/v1/audio/jobs/<32-hex-id>"}
```

The persisted `queued` state is committed before any CPU coordinator wake is attempted. Redis job limits (12 admissions per five minutes, up to 100/day per client and 500/day globally by default) and database pending-job limits apply before admission; duplicate idempotent requests are deduplicated before charging a job unit in normal serial operation. A racing duplicate can consume a rate unit without creating duplicate GPU work.

## Poll and download

- `GET /v1/audio/jobs/{job_id}` — owner-scoped status: `queued`, `running`, `completed`, `failed`, `indeterminate`. No raw prompt/reference content is returned.
- `GET /v1/audio/jobs/{job_id}/audio` — authenticated `audio/wav` download when `completed`; returns 409 before result, 404 when inaccessible/expired.
- `GET /v1/ops/metrics` — **owner token only**, aggregate persistent state counts, no tokens, prompts, IPs, audio or client IDs. Other clients receive 404.

After success, status includes WAV metadata (`sample_rate`, `channels`, `duration_seconds`, `audio_bytes`), integer `inference_ms`, and `audio_url`. `indeterminate` means the GPU outcome is not confirmed. **Never automatically resubmit with a fresh idempotency key.** See [operations](DEPLOYMENT.md).

## Voice cloning

Optional Base64 **raw PCM WAV** (no `data:` URI) in `reference_audio_base64`: max 6 MB, max 30 seconds, mono/stereo, 8–96 kHz. Requires `reference_consent: true`, but this is a declaration by the caller rather than independent proof of speaker authorization. Optional `reference_text` is required when Vox `cloning_mode` is `continuation`.

```json
{
  "model": "voxcpm2",
  "input": "Thank you for listening.",
  "reference_audio_base64": "<authorized raw Base64 PCM WAV>",
  "reference_text": "The words in the reference clip.",
  "reference_consent": true,
  "model_config": {"cloning_mode": "continuation"}
}
```

**Production cloning policy:** `reference_consent: true` does not independently authorize cloning. The production CPU gateway rejects any `reference_audio_base64` request with **HTTP 403 before admission**, unless the operator explicitly sets `TEXT_TO_SPEECH_ENABLE_REFERENCE_VOICE_CLONING=1` **and** allowlists the authenticated client ID through `TEXT_TO_SPEECH_REFERENCE_VOICE_CLONING_CLIENT_IDS_JSON` in the **Modal deploy process** and redeploys. This non-secret decision is embedded in the CPU image. See [Deployment](DEPLOYMENT.md). A pre-admission rejection only describes that attempt. If an earlier POST timed out or lost its acknowledgment, retry against the original gateway with the same client, idempotency key, and payload while the job is still retained. If you know the job ID, poll it instead. See the [notebook walkthrough](../notebooks/tts_api_demo.ipynb).

## Model-specific controls

| Model | Controls |
| --- | --- |
| MOSS-TTS-v1.5 | `language`, `instructions`, `voice`, `duration_tokens`, `max_new_tokens`, `text_temperature`, `audio_temperature`, `text_top_p`, `audio_top_p`, `text_top_k`, `audio_top_k`, `audio_repetition_penalty`, `seed` |
| Higgs TTS 3 | `voice`, `emotion`, `style`, `speed`, `pitch`, `expressiveness`, `temperature`, `top_p`, `top_k`, `max_new_tokens`, `seed` |
| VoxCPM2 | `voice_description`, `gender`, `age`, `emotion`, `tone`, `pace`, `cloning_mode`, `cfg_value`, `inference_timesteps`, `min_len`, `max_len`, `normalize`, `denoise` (only `false`), `retry_badcase`, `retry_badcase_max_times`, `retry_badcase_ratio_threshold` |

The catalog returns `enabled` for each model. In the Modal deployment, enabled means operator selection, not GPU readiness. MOSS-TTS-v1.5 and VoxCPM2 use the committed audited runtime locks; selected Higgs requires an audited immutable SGLang image reference and confirmation of usage rights. Invalid required deployment configuration fails validation. Submitting an unselected model returns HTTP 503 before GPU dispatch; selected workers verify checkpoint integrity during startup. Consult `GET /v1/models/{id}` for schema/enum values and supported bounds. The gateway does not expose arbitrary decoder kwargs or loader filesystem paths. Higgs TTS 3 rights must be independently reviewed before hosted inference.

## Security and error semantics

- `401`: missing/invalid token; `429`: pre-auth requests, failed-token temporary lockout, job quota, optional estimated daily GPU budget, or poll quota (includes `Retry-After`). Three invalid attempts in a fixed five-minute window cause 15-minute lockout on that peer; **valid** authentication is still allowed if pre-auth budget permits.
- `413`: body above configured 8.5 MB limit, checked before JSON parsing; `422`: invalid body/model option/reference with redacted validation errors; `409`: conflicting idempotency payload or audio not ready.
- `404`: missing or cross-client job; `503`: Redis/backend unavailable (fail closed).
- Client request rate: max 120/peer/minute and 600/global/minute, atomic in Redis; submitted jobs 12/client/five minutes plus 100/day/client and 500/day globally, polling+audio GET 120/client/minute. Outstanding database caps: 20 jobs/client and 20/model, one running/uncertain per model.
- Limits are bounded by deployments' actual credentials, not a guarantee against edge attacks. Configure WAF and Modal workspace budgets separately.

Completed WAV bytes are Fernet-encrypted in PostgreSQL for a finite retention window (default seven days), with associated job audit metadata purged in the same transaction, **not currently in R2/S3**. All model licensing and consent limits still apply. See [Architecture](ARCHITECTURE.md) and [Security](SECURITY.md).

## Structured inference diagnostics

The authenticated, owner-scoped `GET /v1/audio/jobs/{job_id}` response preserves
the existing top-level `inference_ms` and WAV metadata while adding `timing`,
`usage`, and `diagnostics`. The same objects exist for queued or unsuccessful
jobs, with `null` when a measurement is unavailable.

Example of a completed job (illustrative numbers, **not** a GPU benchmark):

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "model": "voxcpm2",
  "status": "completed",
  "inference_ms": 2100,
  "audio": {
    "sample_rate": 16000, "channels": 1,
    "duration_seconds": 3.0, "audio_bytes": 96044
  },
  "audio_url": "/v1/audio/jobs/0123456789abcdef0123456789abcdef/audio",
  "timing": {
    "queue_wait_ms": 120,
    "worker_wall_ms": 2400,
    "inference_ms": 2100,
    "audio_validation_ms": 3,
    "model_load_ms": null,
    "total_ms": 2590
  },
  "usage": {
    "input_characters": 46,
    "input_tokens": null,
    "output_tokens": null,
    "audio_duration_seconds": 3.0,
    "realtime_factor": 0.7
  },
  "diagnostics": {
    "request_id": "0123456789abcdef0123456789abcdef",
    "error_code": null,
    "warnings": [],
    "events": [
      {"event": "job_accepted", "at": "2026-11-15T09:00:00+00:00"},
      {"event": "job_claimed", "at": "2026-11-15T09:00:00+00:00"},
      {"event": "job_completed", "at": "2026-11-15T09:00:02+00:00"}
    ]
  }
}
```

- `queue_wait_ms`: time from durable job admission to the successful CPU claim.
- `worker_wall_ms`: CPU coordinator wait for the GPU worker RPC, including
  possible cold-start, network and scheduling overhead.
- `inference_ms`: GPU worker-side processing after startup, including reference-file preparation, payload adaptation, synthesis invocation, WAV encoding (VoxCPM2), basic size checks and WAV metadata parsing. **Not** isolated model-generation time or end-to-end latency. Both the top-level field and `timing.inference_ms` retain this meaning.
- `audio_validation_ms`: first PCM WAV check by the CPU coordinator;
  excludes later encryption, validation and persistence.
- `total_ms`: admission to the completed database timestamp, excluding
  the client's subsequent HTTP WAV download.
- `model_load_ms`: `null`; current workers cannot isolate that measurement.
- `input_characters`: Unicode characters from the accepted text,
  **not token count**. Older database records may return `null`.
- `input_tokens` / `output_tokens`: `null` until the runtime provides
  actual tokenizer/codec metrics; do not infer tokens from text length.
- `audio_duration_seconds`: validated WAV duration; `realtime_factor` is
  measured worker-processing seconds divided by audio seconds (lower is faster). Preparation and WAV serialization contribute to this ratio; it is not a pure model-generation benchmark.
- `diagnostics.request_id`: the job ID for correlating application logs.
  `events`: at most 20 allowlisted, timestamped job lifecycle events from
  that owner's audit history. Error codes and warnings are constrained, with
  **no raw Modal logs, stack traces, prompts, audio or API keys** exposed.
- `audio_quality` is measured from every PCM sample of the completed WAV, not model claims: `rms_dbfs`, `peak_dbfs`, `clipped_samples`, `total_samples`, `clipped_fraction`, `sample_width_bits`, and `warnings`. Samples within **one quantization step** of either PCM endpoint are counted; `clipped_samples_present` also appears in `diagnostics.warnings`. These are **advisory measurements only**: the gateway neither rejects a structurally valid WAV nor modifies its samples. Silence has `null` dBFS rather than a fabricated floor. A near-clip count does not establish audible distortion.
- `audio_quality: null` for non-completed jobs and legacy completed jobs saved before this feature. No PostgreSQL migration is required because the existing JSON metadata field stores this additive value. The WAV download remains byte-for-byte unchanged.
- The local CPU mock explicitly reports generated silence; GPU timing and
  token values remain `null` rather than claiming real inference.

Alembic revision `0004` adds nullable telemetry fields. Revision `0005` normalizes fingerprint column nullability for older databases and adds the audit-polling index. Back up data and apply migrations before deploying an updated gateway. Apply migrations before
deploying the updated gateway/CPU coordinator; see [Deployment](DEPLOYMENT.md).
