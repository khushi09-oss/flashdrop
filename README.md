# FlashDrop

A small flash-sale backend that reserves inventory safely, deduplicates retries, and confirms orders asynchronously. Python 3.12+, standard library only; no runtime dependency installation required.

## Run in two terminals

From this directory:

```powershell
# Terminal 1: API and background worker; starts with 100 iPhone units.
python -m flashdrop

# Terminal 2: create an order, retry it, and watch it become CONFIRMED.
python scripts/demo.py
```

The database is created at `data/flashdrop.db`. Restarts preserve orders and inventory. Each demo run purchases one unit. For a separate fresh demo, use `python -m flashdrop --db data/fresh.db`.

## Documentation

- Architecture image: [docs/architecture.svg](docs/architecture.svg).
- Editable diagram: [docs/architecture.drawio](docs/architecture.drawio). Open https://app.diagrams.net, choose **Open Existing Diagram**, and select this file.
- Deployment image: [docs/deployment.svg](docs/deployment.svg).
- Editable deployment diagram: [docs/deployment.drawio](docs/deployment.drawio).
- Deployment notes: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Production architecture

![Production architecture](docs/architecture.png)

## Deployment and operations

![Deployment and operations](docs/deployment.png)

## API

`X-User-Id` selects a **demo identity**, not authenticated credentials. It must match `userId`; GET only returns orders belonging to that identity. This server binds to localhost by default. Public deployment requires verified JWT identity and a production HTTP server; see the deployment guide.

```powershell
$headers = @{ 'X-User-Id' = 'user_123' }
$body = '{"userId":"user_123","productId":"iphone_16","quantity":1,"idempotencyKey":"request_abc123"}'
$order = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/orders -Headers $headers -ContentType application/json -Body $body
$order
Invoke-RestMethod -Uri "http://127.0.0.1:8000/orders/$($order.orderId)" -Headers $headers
```

| Request | Result |
| --- | --- |
| `POST /orders` | `202 {"orderId":"ord_...","status":"PENDING"}` |
| Same user, key, and payload | Same 202 acceptance; `Idempotency-Replayed: true` |
| Same user and key, different product or quantity | 409 `IDEMPOTENCY_CONFLICT` |
| `GET /orders/:orderId` | 200 with current `PENDING` or `CONFIRMED` status |
| Insufficient stock | 409 `OUT_OF_STOCK` |
| Invalid fields or quantity | 422 `VALIDATION_ERROR` |
| Unknown product/order or another user's order | 404 |
| Missing identity / identity mismatch | 401 / 403 |
| `GET /health` | 200 if the database can be read |
| Database busy or unavailable | 503; retry with the same key |

POST always replays the original acceptance status. Use GET for the latest order status. Validation requires exactly the four documented fields, quantity as an integer from 1 to 10,000, and identifiers of 1–128 letters, digits, `.`, `:`, `_`, or `-`. JSON bodies are limited to 8 KiB.

## How correctness works

One transaction checks the idempotency key, conditionally decrements stock, creates a PENDING order, and inserts a durable job. Either all changes commit or none do. The worker confirms the order and marks the job processed in a second transaction. It never deducts inventory again.

```sql
UPDATE products SET stock = stock - :quantity
WHERE id = :product_id AND stock >= :quantity;
```

If zero rows change, the order is rejected. A database constraint also prevents negative stock. SQLite's `BEGIN IMMEDIATE` serializes writers across connections and processes sharing this file. A Python lock alone would not protect separate processes.

## Structure

```text
flashdrop/
  __main__.py    CLI, startup, graceful shutdown
  api.py         HTTP routes, identity boundary, JSON errors
  validation.py  Request validation
  store.py       Schema, transactions, inventory, idempotency
  worker.py      Background polling and retry loop
tests/           HTTP, concurrency, rollback, and process-crash tests
scripts/demo.py  End-to-end demonstration
docs/            Architecture, deployment, and interview explanation
```

## Verify

```powershell
python -m unittest discover -v
# Optional development tools:
python -m pip install ruff==0.14.3 build==1.3.0
python -m ruff check .
python -m ruff format --check .
python -m build
```

The test suite includes actual child-process termination, concurrent retries, competing processes, rollback, ownership checks, and real HTTP requests. It verifies correctness at modest test concurrency; it is not evidence of 10,000-request production capacity.

## Docker

```powershell
docker compose up --build -d
python scripts/demo.py
docker compose logs -f
docker compose down
```

The named volume retains data after `down`. Docker Desktop must be running. This container is a single-host demonstration, not the multi-instance cloud system in the production diagram.

For two local API processes, run `python -m flashdrop --port 8000 --no-worker` and `python -m flashdrop --port 8001 --no-worker`, then `python -m flashdrop --worker-only` in a third terminal. All must use the same database path on the same host.

## Scope and assumptions

- The assignment permits a simulated broker and local persistence. SQLite and the `jobs` table implement that scope; Redis, Kafka, and PostgreSQL are production design components.
- PENDING means inventory is already reserved. CONFIRMED means background processing completed. Payments, cancellations, restocking, and reservation expiration are outside this exercise.
- Idempotency is scoped to user plus key and retained with the order indefinitely in this demo. Only successful acceptances bind a key; rejected requests may be retried and reevaluated.
- Different keys represent different purchase intents, even for the same customer. No per-customer purchase limit was specified.
- SQLite on separate machines with independent disks is unsafe for shared inventory. Production needs the shared database transaction described in [ARCHITECTURE.md](docs/ARCHITECTURE.md).
