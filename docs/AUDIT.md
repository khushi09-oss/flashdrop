# Verification and risk review

## Executed checks

- Expanded suite: 44 tests passed on Windows/Python 3.14 and Linux/Python 3.12, including eight tests with real API/worker subprocesses. Reproducible failure cases, fixed request parsing issues, and both 10,000-intent load reports are documented in [TESTING.md](TESTING.md).
- [GitHub CI passed](https://github.com/khushi09-oss/flashdrop/actions/runs/41774681722): lint, formatting, all 28 tests, isolated package build, and Docker image build.
- 28 tests passed on Windows/Python 3.14 and inside the Linux/Python 3.12 container.
- Real HTTP checks covered acceptance, replay, current status, ownership, malformed JSON, strict quantity types, missing identity, oversized bodies, and concurrent retries.
- Concurrent inventory test: 180 attempts for 100 units produced exactly 100 orders and 80 stock rejections, with zero inventory remaining.
- Concurrent retries produced one order and one job. Two independent processes sharing one database competed for the final unit; only one succeeded.
- Actual process termination before commit left stock unchanged. Termination after acceptance commit allowed the same key to recover the saved order.
- Database-trigger failures verified rollback of reservation/order creation and worker confirmation/job acknowledgment.
- Duplicate job delivery confirmed the same order without a second stock decrement.
- A temporary inverted inventory predicate caused the order test to fail; the original source was restored afterward.
- Ruff lint and formatting checks passed. Architecture and deployment SVG/draw.io XML parsed successfully; PNG exports were visually inspected.
- Python source distribution and wheel built successfully with `python -m build --no-isolation`; bytecode compilation also passed.
- Docker image built successfully; Compose service became healthy, ran as UID 10001, and passed the live create/retry/confirm demonstration.
- An accepted order remained accessible and CONFIRMED after restarting the container with its persistent named volume.

## Focused review

Inventory, order, retry identity, and durable work share one transaction. The stock predicate and nonnegative constraint prevent overselling. The unique user/key constraint and payload comparison protect retries. SQL uses parameters for user-controlled values. API lookups filter by owner. Error responses omit internal stack traces. Identity and request fields are bounded, request bodies are capped, and local ports bind to loopback.

No blocking correctness findings remain within the local demonstration scope. The committed application has no third-party runtime packages. Development tools and the base image still require routine vulnerability maintenance.

## Browser demo verification

The browser demo adds two HTTP tests, bringing the suite to 30. The page's actual JavaScript was executed with a small DOM fixture against a live container API: creation and confirmation passed, retrying after editing the form reused the original payload and order ID, stock decreased only once, and insufficient-stock errors reenabled the controls. Static routes and source-file isolation are covered by HTTP tests. Browser rendering could not be visually verified in this environment; desktop/mobile layout checks remain manual.

## Deployment limits

The local identity header is impersonable and is not production authentication. Python's demonstration HTTP server, a single SQLite host, and its serialized write transactions are not a capacity-tested public deployment. Production JWT verification, PostgreSQL adapters, outbox relay, Kafka consumers, Redis integration, image scanning, operational metrics, and cloud provisioning remain the documented production plan.

The bounded 10,000-intent test is documented in [TESTING.md](TESTING.md); it does not establish production capacity or 10,000 simultaneous connections. Real Kafka/Redis outages and cloud failover cannot be tested against unprovisioned services. Local crash, rollback, persistence, and duplicate-processing tests verify the implemented equivalents; production adapter tests are a release gate.
