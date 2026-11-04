# Deployment strategy

## Local demonstration

Run `python -m flashdrop`, then `python scripts/demo.py`. No package installation is needed. Alternatively use `docker compose up --build -d`; the named volume retains SQLite data. The Docker process runs as an unprivileged user. The Compose port binds only to localhost.

The local implementation uses Python's demonstration HTTP server and a single-host SQLite database. Do not deploy this image unchanged as the horizontally scaled production architecture. A production implementation must replace the HTTP adapter with a maintained server/framework, validate JWT identity, replace the store with PostgreSQL, and add the broker/outbox relay adapters. The order transaction and idempotent transition remain the same.

## Proposed AWS rollout

1. **CI:** every push and pull request runs Ruff lint/format checks, all tests, builds a Python wheel, and builds the container. The supplied workflow implements these checks and has passed on GitHub. Before a production release, add PostgreSQL integration tests against the actual adapter, image vulnerability scanning, multi-instance load tests, and outage tests.
2. **Release:** merge reviewed changes only after checks pass. Build one immutable image, tag it with the commit SHA, scan it, push to Amazon ECR, and promote the same digest from staging to production. Use GitHub OIDC with a narrowly scoped AWS role instead of stored AWS access keys. Cloud push/deploy stages require account-specific configuration and are not implemented in this repository.
3. **Compute:** separate ECS Fargate services for the API, outbox relay, and Kafka workers. Keep at least two API tasks across availability zones behind an ALB; reach the ALB through an authenticated, throttled API gateway. Place tasks and data services in private subnets with explicit security groups.
4. **No-downtime changes:** use rolling deployment with `minimumHealthyPercent=100`, `maximumPercent=200`, enough spare capacity, health checks, and deployment circuit-breaker rollback. Drain API traffic, finish short transactions, and let workers stop polling and finish their current messages before shutdown. Run additive schema migrations as a one-off task before rollout; remove old columns only after old readers are gone. See [ECS deployment settings](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service_definition_parameters.html).
5. **Scale:** pre-scale for sale launch. Scale APIs using request volume and latency while capping database connections per task. Scale relay by outbox backlog/age and workers by Kafka consumer lag; worker count above partition count adds no consumption capacity. Scaling application containers does not bypass hot inventory-row contention.

## Data, secrets, and security

- RDS PostgreSQL Multi-AZ provides durable transactional storage and failover. Retain automated backups with point-in-time recovery, take snapshots before risky migrations, and test restores. Choose retention, recovery-point, and recovery-time targets with the business; a backup that has never been restored is unproven.
- Keep databases out of container filesystems in production. Use TLS connections, encrypted storage, restricted network access, separate service roles, and connection pools with statement/lock timeouts.
- Store connection credentials in AWS Secrets Manager and inject them at runtime using task roles. Never place credentials in an image, source repository, logs, or workflow text. Rotate them and restart connections safely.
- Verify JWT signature, issuer, audience, and expiration. Derive `userId` from the verified subject; never trust a public `X-User-Id` header. Enforce GET ownership. Use gateway throttling, request-size limits, and bounded service concurrency to protect the database.
- Kafka/MSK uses encrypted connections, access controls, replicated partitions across availability zones, and acknowledgment/replication settings appropriate for durable accepted events. Redis is optional metadata caching; cache bypass must remain functional.

## Outages and single points of failure

| Component | Strategy |
| --- | --- |
| Redis | Short cache timeout and circuit breaker; fall back to PostgreSQL. Load-shed if fallback saturates the database. |
| Kafka | Keep events in outbox, retry with bounded exponential backoff and jitter. Alert on oldest unpublished event. Stop accepting new work at a defined backlog threshold rather than exhausting disk. |
| Relay | Run redundant instances with recoverable leases; publish-then-mark can duplicate events. |
| Worker | Commit the idempotent database transition before committing its Kafka offset. Retry transient errors; quarantine poison messages after bounded retries and alert. Preserve event IDs for replay. |
| PostgreSQL | It is the logical central dependency. Multi-AZ failover reduces host/AZ failure risk but does not prevent brief write unavailability or region-wide failure. Fail closed with 503 and safe retries. |
| Local demo host/disk | A single point of failure. Persistent volume protects container restarts, not host or disk loss. This is why production moves to managed durable storage. |

## Observe and recover

Production metrics should cover API p50/p95/p99 latency, 5xx/429 rates, stock rejection rate, idempotency conflicts/replays, database lock wait/connections, outbox oldest age, Kafka lag, oldest PENDING order, and worker failures. Alert on stalled orders and growing queue age, not just CPU. Log request, order, and event identifiers without request bodies or credentials. The demo emits basic request/worker logs and exposes `/health`; it does not implement a metrics backend.

On a bad release, restore the previous ECS task definition/image digest and let durable queued work continue. Do not restore a database snapshot to undo an application release: that can lose accepted orders and restore already-sold stock. Prefer forward schema fixes and backward-compatible application rollback. For a real data-loss incident, restore into a separate database, reconcile orders and inventory, then perform a controlled cutover.

## Validation status

Local tests, formatting/linting, package build, container execution, and live HTTP demo results are recorded in [AUDIT.md](AUDIT.md). Cloud rollout requires an AWS account and the production adapters described above. No cloud resources have been provisioned.
