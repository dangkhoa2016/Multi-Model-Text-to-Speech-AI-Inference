# Operations for short-lived Modal demos

> 🌐 Language / Ngôn ngữ: **English** | [Tiếng Việt](OPERATIONS.vi.md)

Confirm workspace, environment and secrets before deployment. Run read-only database preflight to verify Alembic revision and unresolved jobs. Submit only a bounded, authenticated canary and record its idempotency key, terminal status and WAV checksum.

A queued job may be safely awakened by CPU scheduler. Never automatically retry running or indeterminate GPU outcomes; verify worker termination before reconciliation.

Stop Modal app after demo to stop API and scheduled CPU functions. Database and Volume storage incur independent costs. The estimated GPU budget ledger is not a billing statement.

See [deployment](DEPLOYMENT.md) and [troubleshooting](TROUBLESHOOTING.md).
