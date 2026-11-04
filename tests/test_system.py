"""Real HTTP processes sharing a fresh database; never touches demo inventory."""

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
import unittest
from contextlib import closing
from pathlib import Path

from flashdrop.store import Store


SERVER = """
import sys
from flashdrop.api import make_server
from flashdrop.store import Store
server = make_server(Store(sys.argv[1]), port=0)
print(server.server_address[1], flush=True)
server.serve_forever()
"""


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "system.db")
        self.store = Store(self.path)
        self.store.initialize()
        self.ports = []
        self.servers = []
        for _ in range(2):
            process = self.start_process(["-c", SERVER, self.path], stdout=subprocess.PIPE)
            ready = queue.Queue()
            threading.Thread(target=lambda p=process: ready.put(p.stdout.readline()), daemon=True).start()
            self.ports.append(int(ready.get(timeout=15)))
            self.servers.append(process)

    def start_process(self, args, stdout=subprocess.DEVNULL):
        process = subprocess.Popen([sys.executable, *args], stdout=stdout, stderr=subprocess.DEVNULL)

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if process.stdout:
                process.stdout.close()

        self.addCleanup(cleanup)
        return process

    def buy(self, port, key, quantity=1):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            connection.request(
                "POST",
                "/orders",
                json.dumps(
                    {
                        "userId": "system_user",
                        "productId": "iphone_16",
                        "quantity": quantity,
                        "idempotencyKey": key,
                    }
                ),
                {"Content-Type": "application/json", "X-User-Id": "system_user"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def query(self, sql):
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            return db.execute(sql).fetchall()

    def wait_until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for the expected durable database state")

    def test_two_api_instances_race_for_last_unit(self):
        with self.store.transaction() as db:
            db.execute("UPDATE products SET stock = 1")
        barrier = threading.Barrier(2)

        def compete(index):
            barrier.wait(timeout=5)
            return self.buy(self.ports[index], f"last-{index}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(compete, range(2)))
        self.assertEqual(sorted(r[0] for r in responses), [202, 409])
        self.assertEqual(self.store.inventory("iphone_16"), 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM orders"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM jobs"), [(1,)])

    def test_two_api_instances_handle_300_competing_buyers(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(lambda i: self.buy(self.ports[i % 2], f"buyer-{i}"), range(300)))
        self.assertEqual(sum(r[0] == 202 for r in results), 100)
        self.assertEqual(sum(r[0] == 409 and r[1]["error"]["code"] == "OUT_OF_STOCK" for r in results), 200)
        self.assertEqual(self.store.inventory("iphone_16"), 0)
        self.assertEqual(self.query("SELECT SUM(quantity), COUNT(*) FROM orders"), [(100, 100)])

    def test_retries_across_instances_reserve_only_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda i: self.buy(self.ports[i % 2], "same-intent"), range(64)))
        self.assertTrue(all(status == 202 for status, _ in results))
        self.assertEqual(len({body["orderId"] for _, body in results}), 1)
        self.assertEqual(self.store.inventory("iphone_16"), 99)
        self.assertEqual(self.query("SELECT COUNT(*) FROM jobs"), [(1,)])

    def test_conflicting_payloads_race_for_same_key(self):
        barrier = threading.Barrier(2)

        def compete(index):
            barrier.wait(timeout=5)
            return self.buy(self.ports[index], "conflicting-intent", quantity=index + 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, range(2)))
        self.assertEqual(sorted(r[0] for r in results), [202, 409])
        conflict = next(body for status, body in results if status == 409)
        self.assertEqual(conflict["error"]["code"], "IDEMPOTENCY_CONFLICT")
        reserved = self.query("SELECT SUM(quantity) FROM orders")[0][0]
        self.assertEqual(self.store.inventory("iphone_16"), 100 - reserved)

    def test_database_lock_returns_503_and_same_key_recovers(self):
        with self.store.transaction():
            status, body = self.buy(self.ports[0], "locked-database")
            self.assertEqual(status, 503)
            self.assertEqual(body["error"]["code"], "DATABASE_UNAVAILABLE")
        self.assertEqual(self.store.inventory("iphone_16"), 100)
        status, _ = self.buy(self.ports[1], "locked-database")
        self.assertEqual(status, 202)
        self.assertEqual(self.store.inventory("iphone_16"), 99)

    def test_api_dies_after_commit_before_client_reads_response(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.ports[0], timeout=10)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/orders",
            json.dumps(
                {
                    "userId": "system_user",
                    "productId": "iphone_16",
                    "quantity": 1,
                    "idempotencyKey": "lost-http-response",
                }
            ),
            {"Content-Type": "application/json", "X-User-Id": "system_user"},
        )
        self.wait_until(lambda: self.query("SELECT COUNT(*) FROM orders")[0][0] == 1)
        original_id = self.query("SELECT id FROM orders")[0][0]
        self.servers[0].kill()
        self.servers[0].wait(timeout=10)
        status, replay = self.buy(self.ports[1], "lost-http-response")
        self.assertEqual(status, 202)
        self.assertEqual(replay["orderId"], original_id)
        self.assertEqual(self.store.inventory("iphone_16"), 99)

    def test_two_real_workers_drain_pending_jobs_without_double_processing(self):
        for i in range(100):
            self.store.create_order("system_user", "iphone_16", 1, f"worker-{i}")
        for _ in range(2):
            self.start_process(["-m", "flashdrop", "--worker-only", "--db", self.path])
        self.wait_until(lambda: self.query("SELECT COUNT(*) FROM jobs WHERE processed_at IS NULL") == [(0,)])
        self.assertEqual(
            self.query("SELECT status, COUNT(*) FROM orders GROUP BY status"), [("CONFIRMED", 100)]
        )
        self.assertEqual(self.store.inventory("iphone_16"), 0)

    def test_worker_crash_mid_transaction_rolls_back_and_recovers(self):
        order, _ = self.store.create_order("system_user", "iphone_16", 1, "worker-crash")
        script = """
import sqlite3, sys, time
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE orders SET status = 'CONFIRMED'")
print('written-but-uncommitted', flush=True)
time.sleep(60)
"""
        worker = self.start_process(["-c", script, self.path], stdout=subprocess.PIPE)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(worker.stdout.readline()), daemon=True).start()
        self.assertEqual(ready.get(timeout=15).strip(), b"written-but-uncommitted")
        worker.kill()
        worker.wait(timeout=10)
        self.assertEqual(self.store.get_order(order["orderId"], "system_user")["status"], "PENDING")
        self.assertEqual(self.query("SELECT processed_at FROM jobs"), [(None,)])
        self.start_process(["-m", "flashdrop", "--worker-only", "--db", self.path])
        self.wait_until(
            lambda: self.store.get_order(order["orderId"], "system_user")["status"] == "CONFIRMED"
        )
        self.assertEqual(self.store.inventory("iphone_16"), 99)
