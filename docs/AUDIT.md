# Verification and known limits

This is the current verification summary. [TESTING.md](TESTING.md) describes the scenarios, reproduction commands, fixed bugs, and recorded load observations. Test counts below describe the current suite, not a history of earlier milestones.

## Automated verification

| Check | Evidence |
| --- | --- |
| Test suite | 44 tests: 17 HTTP/input, 19 persistence/concurrency, 8 process-level system tests |
| Platform coverage | Passed on Windows/Python 3.14 and Linux/Python 3.12 |
| Static checks | Ruff lint and format checks; Python bytecode compilation |
| Packaging | Source distribution and wheel built; static UI files included |
| Container | Image built and ran as UID 10001; health, terminal demo, and persistence after restart checked |
| CI | Tests, lint, formatting, package build, and image build run on pushes and PRs; see the [workflow results](https://github.com/khushi09-oss/flashdrop/actions/workflows/ci.yml) |
| Documentation | Relative links and diagram XML checked; production diagram exports previously inspected |

The [application baseline CI run](https://github.com/khushi09-oss/flashdrop/actions/runs/41777069815) verifies commit `f65940d`. Subsequent documentation changes do not change that application code; use the workflow results to check a particular PR head.

## What the tests establish

Two real API processes sharing a local database cannot both sell the final unit. Concurrent retries reserve stock once; conflicting payloads using the same key are rejected. Failed order/job writes roll back the whole reservation, and worker failures leave work recoverable. Actual process termination tests cover uncommitted updates and accepted requests whose responses are not read. Two real workers drain the pending queue without a second stock decrement.

HTTP checks cover ownership filtering, strict quantities and identifiers, body limits, malformed/duplicate JSON fields, conflicting length headers, and controlled database-unavailable errors. The review checked transaction boundaries, uniqueness and stock constraints, parameterized SQL, owner-filtered lookups, and error responses that omit internal stack traces.

## Load observations

The recorded Windows and Linux runs each submitted 10,000 purchase intents through two API processes with at most 64 concurrent clients. Each produced 100 accepted and confirmed orders, 9,900 final stock rejections, and zero remaining stock. The Windows run included 13 temporary 503 attempts, recovered by same-key retries; the Linux run recorded none.

These are single observations on a shared development machine, not production capacity guarantees. Raw reports, latency, timing, retry accounting, and limitations are in [TESTING.md](TESTING.md#observed-10000-request-results).

## Unverified and out of scope

- The browser JavaScript was exercised with a DOM fixture against a live API, including creation, confirmation, immutable retries, and stock rejection. This is not an automated browser test. Desktop/mobile rendering and accessibility have not been fully verified in a browser.
- Real Redis/Kafka outages, PostgreSQL failover, JWT authentication, cloud deployment, and external payment effects have not been executed. Those integrations are proposed in the production design.
- SQLite serializes writes; bounded load tests do not prove resilience to unlimited connections, slow clients, host loss, or 10,000 simultaneous requests.
- The demo identity header is impersonable. Ownership filtering is demonstrated, but it is not a substitute for authentication. The HTTP server is for local demonstration.
- The application has no third-party runtime packages. Development tools and the base image still require ongoing vulnerability maintenance; this review is not a complete dependency or image security certification.
