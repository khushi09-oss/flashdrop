"""Isolated two-instance load test: python -m tests.load_test --requests 10000."""

import argparse
import concurrent.futures
import http.client
import json
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import ExitStack, closing
from pathlib import Path

from flashdrop.store import Store
from tests.test_system import SERVER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=10000)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 100 or not 1 <= args.concurrency <= 256:
        parser.error("Use at least 100 requests and concurrency between 1 and 256")

    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        path = str(Path(directory) / "load.db")
        store = Store(path)
        store.initialize()

        def start(command, capture=False):
            process = subprocess.Popen(
                [sys.executable, *command],
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            def cleanup():
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                if process.stdout:
                    process.stdout.close()

            stack.callback(cleanup)
            return process

        ports = []
        for _ in range(2):
            process = start(["-c", SERVER, path], capture=True)
            ready = queue.Queue()
            threading.Thread(target=lambda p=process: ready.put(p.stdout.readline()), daemon=True).start()
            ports.append(int(ready.get(timeout=15)))

        def purchase(index):
            payload = json.dumps(
                {
                    "userId": "load_user",
                    "productId": "iphone_16",
                    "quantity": 1,
                    "idempotencyKey": f"load-{index}",
                }
            )
            began = time.monotonic()
            attempts = []
            errors = []
            status, body = 0, {}
            for attempt in range(3):
                connection = http.client.HTTPConnection("127.0.0.1", ports[(index + attempt) % 2], timeout=15)
                try:
                    connection.request(
                        "POST",
                        "/orders",
                        payload,
                        {"Content-Type": "application/json", "X-User-Id": "load_user"},
                    )
                    response = connection.getresponse()
                    status, body = response.status, json.loads(response.read())
                except (OSError, http.client.HTTPException, ValueError) as exc:
                    status, body = 0, {}
                    errors.append(type(exc).__name__)
                finally:
                    connection.close()
                attempts.append(status)
                if status not in (0, 503):
                    break
                time.sleep(0.05 * (attempt + 1))
            return status, body, time.monotonic() - began, attempts, errors

        began = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(purchase, range(args.requests)))
        duration = time.monotonic() - began
        latencies = sorted(result[2] for result in results)
        responses = Counter(result[0] for result in results)
        attempts = Counter(status for result in results for status in result[3])
        transport_errors = Counter(error for result in results for error in result[4])
        with closing(sqlite3.connect(path)) as db:
            orders, reserved = db.execute("SELECT COUNT(*), SUM(quantity) FROM orders").fetchone()
            jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]

        for _ in range(2):
            start(["-m", "flashdrop", "--worker-only", "--db", path])
        deadline = time.monotonic() + 15
        confirmed = 0
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(path, timeout=5)) as db:
                confirmed = db.execute("SELECT COUNT(*) FROM orders WHERE status = 'CONFIRMED'").fetchone()[0]
            if confirmed == orders:
                break
            time.sleep(0.05)

        out_of_stock = sum(
            status == 409 and body.get("error", {}).get("code") == "OUT_OF_STOCK"
            for status, body, *_ in results
        )
        passed = (
            responses[202] == orders == reserved == jobs == confirmed == 100
            and out_of_stock == args.requests - 100
            and store.inventory("iphone_16") == 0
            and len({body["orderId"] for status, body, *_ in results if status == 202}) == 100
            and integrity == "ok"
        )
        report = {
            "passed": passed,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "apiInstances": 2,
            "durationSeconds": round(duration, 3),
            "requestsPerSecond": round(args.requests / duration, 2),
            "finalHttpStatuses": dict(responses),
            "allAttemptStatuses": dict(attempts),
            "transportErrors": dict(transport_errors),
            "latencySeconds": {
                name: round(latencies[min(len(latencies) - 1, int(len(latencies) * fraction))], 3)
                for name, fraction in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
            },
            "database": {
                "orders": orders,
                "reservedUnits": reserved,
                "jobs": jobs,
                "confirmed": confirmed,
                "remainingStock": store.inventory("iphone_16"),
                "integrity": integrity,
            },
        }
        text = json.dumps(report, indent=2)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
