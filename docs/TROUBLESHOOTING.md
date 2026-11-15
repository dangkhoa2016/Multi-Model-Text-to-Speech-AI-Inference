# Troubleshooting

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](TROUBLESHOOTING.vi.md)

**HTTP 503 / disabled model:** check deployment model selection; for Higgs, verify the immutable image reference and usage rights. For other 503 responses, inspect the unavailable service. For selected-model startup failures, check checkpoint integrity and runtime logs. Do not bypass fail-closed checks.

**Lock mismatch:** verify XZ artifact and original SHA-256, never modify hashes to accept corrupt content.

**HTTP 401/429:** inspect client scope, peer lockout and Redis quotas without exposing tokens.

**HTTP 409:** idempotency key used with a different payload.

**queued:** inspect PostgreSQL and CPU dispatcher; scheduler may reawaken queued work.

**indeterminate:** never automatically retry GPU. Confirm worker termination before owner reconciliation.

**Missing WAV:** check ownership, terminal status, encryption key and retention window.

See [operations](OPERATIONS.md), [security](SECURITY.md).
