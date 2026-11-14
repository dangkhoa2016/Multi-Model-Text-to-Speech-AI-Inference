# Gateway security and privacy

🌐 [English](SECURITY.md) | [Tiếng Việt](SECURITY.vi.md)

This document describes **implemented source controls**, not a certification of a hosted Modal deployment. CPU mock and SQLite tests cannot prove distributed PostgreSQL/Redis isolation, GPU runtime safety, or voice-consent legitimacy.

## Authentication and privileges

- Every `/v1/*` route except `/v1/health` is checked **before** JSON/body reading. The gateway uses long, random Bearer tokens and server-side SHA-256 token hashes with constant-time digest comparison. Hash-only client registry and a separate owner token hash belong in the `text-to-speech-api-authentication` Secret, **never** in source or in GPU workers.
- An authenticated client receives a stable client ID which scopes job creation, idempotency and retrieval; jobs from another client return 404. Client IDs can be revoked by removing their hash or setting `TEXT_TO_SPEECH_REVOKED_CLIENT_IDS`, followed by an intentional gateway rollout.
- `GET /v1/ops/metrics` is owner-only. Owner-only metrics report bounded aggregate database job counts; they do not expose names, reference voices, raw text, client IDs or tokens.
- The ASGI peer address (not user-supplied `X-Forwarded-For`/`Forwarded`) is the pre-auth lockout key. Reverse proxy topology may collapse several clients into the same peer; configure the trusted proxy and test before internet exposure.

## Distributed rate limits and lockout

`RedisGuard` performs atomic multi-key fixed-window checks using Redis server time and Lua. Redis Cluster keys use a common hash-tag for atomic checks. Production quotas can be adjusted through `TEXT_TO_SPEECH_RATE_*` and `TEXT_TO_SPEECH_AUTHENTICATION_*` Secret values (see `.env.example`); invalid limits fail startup. It uses a `rediss://` URL in production, with separate gateway-only credentials. No raw IP/token/client name is written into Redis keys; identities are SHA-256-derived. Defaults:

| Control | Default |
| --- | ---: |
| All `/v1/*` requests before auth, per ASGI peer | 120/minute |
| All `/v1/*` requests before auth, global | 600/minute |
| Failed Bearer attempts before lockout | 3 within one fixed five-minute window |
| Temporary lockout duration | 900 seconds |
| New job admissions, per client | 12/300 seconds |
| New jobs, per client / globally | 100/day / 500/day |
| Job-status and audio GET, per client | 120/minute |
| Outstanding jobs, per client (PostgreSQL) | 20 |
| Outstanding jobs, per model (PostgreSQL) | 20 |
| Running or uncertain jobs per model | 1 |
| Authenticated request body size | 8,500,000 bytes |

Success with a valid token clears the peer's failure counter/lockout, while still obeying the pre-auth budget. If Redis is unavailable, protected endpoints return HTTP 503 rather than failing open. The CPU mock uses a local process-only limiter and must not be treated as a multi-replica protection. A WAF and infrastructure-level request limits are still needed for public-facing traffic.

## Durable confidentiality boundaries

- PostgreSQL contains `encrypted_input` and `encrypted_audio` fields protected with a persistent Fernet key held only by the CPU gateway and coordinator. On success, input ciphertext is deleted. On uncertain outcome it is deleted to minimize exposure, and an operator must review before any new GPU request.
- Only minimal job metadata, owners and audit categories are stored in plaintext. The application does not intentionally log raw voice samples, prompt text, tokens, model reference text or complete exception strings.
- JSON/Pydantic 422 errors strip supplied `input`/`ctx`. The gateway caps request bodies **before** parsing JSON. Supported references are strict PCM WAV bytes, never arbitrary reference URL/path; avoid expanding model-loader args with unsanitized client inputs.
- Audio output up to 32 MB is encrypted in PostgreSQL BYTEA, not yet object storage. Terminal results are purged by CPU scheduler after configured retention (default seven days); backups and third-party infrastructure may retain copies longer. Fernet key rotation needs planned re-encryption.
- The GPU worker receives the minimum payload needed for its one inference. It **does not** receive PostgreSQL, Redis or API token Secrets. Underlying Modal remote invocation and weight/runtime mechanisms have their own trust and retention boundaries.
- SGLang-Omni must bind only to `127.0.0.1` inside its model GPU container. No SGLang HTTP port is registered as a public Modal endpoint. The mutable upstream `:dev` image and model code are unverified for production; pin and audit the dependency chain.

## Recovery and failure modes

A durable `queued` job may be re-signaled by a CPU cron every minute. Once `running`, **no automatic requeue/retry** happens; a stale heartbeat is exposed as `indeterminate`. If a remote GPU call returned an uncertain result, the DB state is explicitly `indeterminate`; it continues to occupy that model's dispatch slot until an operator checks Modal logs and explicitly closes it. This protects against duplicate GPU charges but may require manual intervention after faults. Do not interpret a heartbeat timeout as proof GPU execution ended. The service does **not** offer exactly-once execution guarantees.

## Risks that still require work

- Speaker authorization verification: `reference_consent=true` is self-asserted; no independent identity check is implemented. Add appropriate policies, moderation, consent collection and deletion mechanisms before opening voice cloning to unknown clients.
- Actual GPU cost budget and usage threshold, live PostgreSQL + Redis concurrency testing, provider credential/key lifecycle, security monitoring/alerting, WAF, dependency scanning, incident response and restore drills.
- Pin SGLang image digest and exact model checkpoints; verify all upstream model licenses, especially non-commercial Higgs hosted API rights.
- A PostgreSQL-based audio blob store is functional for small prototypes but not optimal for scale. Consider authenticated R2/S3 object storage with encryption, retention controls and range download support.

Run offline CPU checks with `python -m pytest -q` and inspect [Operations](DEPLOYMENT.md) before GPU deployment.
