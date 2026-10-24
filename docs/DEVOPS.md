# DevOps and Operations

CI runs four GitHub Actions workflows (`python.yml`, `dotnet.yml`, `frontend.yml`, `docker.yml`): Python ruff + pytest, .NET build + xUnit tests, frontend npm build, and Docker image builds on main (no push without registry secrets). Promote the same immutable artifact. Deploy with readiness gates, rolling or blue/green rollout, schema compatibility, and tested rollback. Evolve financial schemas using expand/migrate/contract.

OpenTelemetry traces/metrics and structured logs should track API errors/latency, database saturation, outbox age, queue depth/DLQ, worker failures, reconciliation breaks, and payment state age. Backups are encrypted, monitored, access controlled, and restore-tested. Set RPO/RTO before choosing replication/failover. Compose does not implement disaster recovery.
