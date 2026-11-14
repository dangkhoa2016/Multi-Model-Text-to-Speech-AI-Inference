# TTS HTTP API contract

🌐 [English](API.md) | [Tiếng Việt](API.vi.md)

All `/v1/*` endpoints except `/v1/health` require HTTPS and `Authorization: Bearer <CLIENT_TOKEN>`. Never put tokens in query strings or public notebook outputs. Responses have `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. The live operator token is separate from client credentials.

## Model discovery

- `GET /v1/health` — gateway liveness only, no live GPU check.
- `GET /v1/models` — IDs, checkpoint names, licensing, capabilities and per-model JSON Schema under `model_config_schema`.
- `GET /v1/models/{model_id}` — strict types, bounds and defaults for an individual backend.

Models: `moss-tts-v1.5`, `higgs-tts-3`, `voxcpm2`. Unrecognized or unsupported `model_config` keys are rejected before GPU dispatch with HTTP 422. Note that Vox voice design uses a **natural-language prefix**, not deterministic synthetic age/gender identity controls. The runtime has not yet passed GPU acceptance testing.

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

## Model-specific controls

| Model | Controls |
| --- | --- |
| MOSS-TTS-v1.5 | `language`, `instructions`, `voice`, `duration_tokens`, `max_new_tokens`, `text_temperature`, `audio_temperature`, `text_top_p`, `audio_top_p`, `text_top_k`, `audio_top_k`, `audio_repetition_penalty`, `seed` |
| Higgs TTS 3 | `voice`, `emotion`, `style`, `speed`, `pitch`, `expressiveness`, `temperature`, `top_p`, `top_k`, `max_new_tokens`, `seed` |
| VoxCPM2 | `voice_description`, `gender`, `age`, `emotion`, `tone`, `pace`, `cloning_mode`, `cfg_value`, `inference_timesteps`, `min_len`, `max_len`, `normalize`, `denoise` (only `false`), `retry_badcase`, `retry_badcase_max_times`, `retry_badcase_ratio_threshold` |

The catalog returns `enabled` for each model. Higgs TTS 3 is disabled unless the operator explicitly confirms applicable rights at deploy time; attempts to enqueue a disabled model return HTTP 503 and do not consume GPU resources. Consult `GET /v1/models/{id}` for schema/enum values and supported bounds. The gateway does not expose arbitrary decoder kwargs or loader filesystem paths. Higgs TTS 3 rights must be independently reviewed before hosted inference.

## Security and error semantics

- `401`: missing/invalid token; `429`: pre-auth requests, failed-token temporary lockout, job quota or poll quota (includes `Retry-After`). Three invalid attempts in a fixed five-minute window cause 15-minute lockout on that peer; **valid** authentication is still allowed if pre-auth budget permits.
- `413`: body above configured 8.5 MB limit, checked before JSON parsing; `422`: invalid body/model option/reference with redacted validation errors; `409`: conflicting idempotency payload or audio not ready.
- `404`: missing or cross-client job; `503`: Redis/backend unavailable (fail closed).
- Client request rate: max 120/peer/minute and 600/global/minute, atomic in Redis; submitted jobs 12/client/five minutes plus 100/day/client and 500/day globally, polling+audio GET 120/client/minute. Outstanding database caps: 20 jobs/client and 20/model, one running/uncertain per model.
- Limits are bounded by deployments' actual credentials, not a guarantee against edge attacks. Configure WAF and Modal workspace budgets separately.

Completed WAV bytes are Fernet-encrypted in PostgreSQL for a finite retention window (default seven days), **not currently in R2/S3**. All model licensing and consent limits still apply. See [Architecture](ARCHITECTURE.md) and [Security](SECURITY.md).
