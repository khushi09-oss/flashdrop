# Acceptance criteria

| Requirement | Implementation or design evidence |
| --- | --- |
| Runnable backend | `python -m flashdrop`; no runtime dependencies |
| POST order and GET status | `flashdrop/api.py`; real HTTP contract tests |
| Inventory and input validation | Conditional stock update, database constraints, strict JSON validation |
| No overselling under concurrency | Database transaction; 180 requests competing for 100 units |
| Idempotent retries | Unique user/key constraint, persisted payload comparison, stable acceptance replay |
| Asynchronous queue and worker | Durable `jobs` table and background polling worker |
| Two backend instances, last unit | Separate-process test sharing SQLite; PostgreSQL change in `ARCHITECTURE.md` |
| Production architecture diagram | `architecture.drawio`, exported SVG and PNG |
| Gateway, authentication, rate limiting | Gateway + ALB in production diagram and deployment plan |
| Multiple API instances and workers | Separate ECS services in production design |
| Redis role | Optional metadata cache; never authoritative inventory or idempotency |
| Kafka/message broker | MSK with outbox relay and idempotent workers in production design |
| Durable database | Local SQLite; shared RDS PostgreSQL for production |
| Containers and cloud platform | Verified Docker image and Compose; proposed AWS deployment |
| CI/CD | GitHub Actions lint, format, tests, package build, and image build |
| No-downtime scaling | Rolling ECS replacement, draining, autoscaling, migration strategy |
| Secrets, persistence, backups, monitoring | `DEPLOYMENT.md` and deployment diagram |
| Broker/cache outages and single points of failure | Outage table and PostgreSQL dependency in `DEPLOYMENT.md` |
| Duplicate Kafka event | Conditional confirmation; duplicate-job delivery test |
| Redis unavailable after order acceptance | Acceptance depends only on durable database transaction |
| Worker crash after DB write before acknowledgment | Local atomic completion/ack; separate broker replay explained |
| API crash after acceptance before response | Actual child-process exit after commit, followed by safe retry |
| Clear assumptions and infrastructure boundaries | README scope plus architecture and deployment documentation |

Production integrations are a design, as permitted by the exercise. Local tests do not establish production throughput or verify an unprovisioned cloud environment.
