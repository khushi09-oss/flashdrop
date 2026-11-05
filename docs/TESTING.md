# Tests and failure investigation

## Run safely

```powershell
python -m unittest discover -v
python -m tests.load_test --requests 10000 --concurrency 64 --output notes/load-results.json
```

Both commands create temporary databases and remove their child processes afterward. They do not connect to the running demo or consume its inventory. The load test is separate from the normal test suite so CI remains predictable. It submits 10,000 purchase intents through two API processes with at most 64 client requests running concurrently; it does not open 10,000 simultaneous connections.

## Automated scenarios

The suite covers HTTP/input handling, persistence/concurrency, and real-process system behavior. See [the current verification summary](AUDIT.md) for the suite count and platform coverage.

| Scenario | Required outcome |
| --- | --- |
| Two HTTP API processes race for the final unit | One 202, one OUT_OF_STOCK; one order and job |
| 300 buyers across two API processes compete for 100 units | Exactly 100 acceptances, 200 stock rejections, stock zero |
| 64 retries across both instances use one key | One order, one reservation, one job |
| Two concurrent payloads reuse one key | One acceptance, one IDEMPOTENCY_CONFLICT |
| Concurrent orders each request 13 units | Seven orders accepted, nine units remain |
| Another connection holds the database write lock | Retryable 503, no partial reservation; same key succeeds after release |
| API is killed after commit before the client reads its response | Surviving API returns the original order on retry |
| Process is killed during an uncommitted worker update | Status rolls back to PENDING; a restarted worker confirms it |
| Two real worker processes drain 100 jobs | All 100 orders confirmed; inventory is unchanged by workers |
| Order insertion or queue insertion fails | Inventory, order, and job roll back together |
| Worker acknowledgment update fails | Confirmation rolls back and work remains retryable |
| The same job is delivered again | No second stock decrement or harmful status transition |
| Caller reads someone else's order | 404 without disclosing ownership |
| Duplicate JSON fields or duplicate Content-Length headers | 400 before any reservation |
| Invalid UTF-8, malformed or deeply nested JSON | Controlled 4xx response, no internal server failure |

The API-loss test deliberately does not read the response before killing the process. It verifies an unknown client outcome; it does not claim to control the exact instruction at which the server sends bytes.

## Bugs found and fixed

1. **Duplicate JSON fields were silently accepted.** A body containing both `"quantity":1` and `"quantity":2` previously created an order using the last value. The JSON decoder now rejects repeated field names. The reproduction returned 202 before the fix and now returns 400.
2. **Conflicting Content-Length headers were accepted.** The handler previously trusted the first length. It now requires exactly one length header for order bodies. The reproduction returned 202 before the fix and now returns 400.

The decoder also catches recursion errors so parser-depth limits cannot escape as 500 errors. The body-size cap remains in place. These checks do not replace a hardened production HTTP gateway.

## Observed 10,000-request results

Both runs used a fresh database with 100 units, two API processes, 64 client threads, and two workers started after the request phase. Transient 503/transport failures can be retried up to twice with the same key. Every attempt is counted separately from the final outcome.

| Measurement | Windows / Python 3.14 | Linux container / Python 3.12 |
| --- | ---: | ---: |
| Purchase intents | 10,000 | 10,000 |
| Accepted orders | 100 | 100 |
| Final stock rejections | 9,900 | 9,900 |
| Temporary 503 attempts | 13 | 0 |
| Transport errors | 0 | 0 |
| Request phase duration | 25.793 s | 9.667 s |
| Completed intents / second | 387.71 | 1,034.47 |
| p95 end-to-end intent latency | 1.104 s | 0.240 s |
| p99 end-to-end intent latency | 2.795 s | 1.033 s |
| Orders subsequently confirmed | 100 | 100 |
| Remaining stock | 0 | 0 |
| SQLite integrity check | ok | ok |

Raw reports: [Windows](benchmarks/windows-10000.json), [Linux](benchmarks/linux-10000.json).

These are single observations on a shared development machine, not controlled cross-platform comparisons or production capacity guarantees. Most requests in this sale are rejected; this does not measure throughput for 10,000 successful purchases. Latency includes any same-key retries. No automatic latency threshold is inferred from one measurement.

## Where the implementation still reaches its limits

SQLite permits one writer at a time. The Windows run reached its five-second lock wait on 13 attempts and returned 503; all recovered with same-key retries. A client that does not retry sees a temporary failure. Increasing API instances cannot remove that serialization. Production needs the shared PostgreSQL adapter, bounded connection pools, gateway admission control, and workload-specific load tests described in the architecture guide.

The local HTTP server also lacks production connection/concurrency controls. This test bounds offered concurrency to 64; it does not prove resilience to unbounded connections or slow clients. A sustained overload test needs an agreed latency/error budget and a production server before declaring a safe operating limit.

Real broker outages, PostgreSQL failover, payment side effects, JWT verification, and cloud scaling remain outside the implemented demo. Their design is documented, and they require integration tests when those components are implemented.
