import concurrent.futures
import multiprocessing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from flashdrop.store import OrderError, Store


def buy_in_process(path, key):
    try:
        return Store(path).create_order("alice", "iphone_16", 1, key)[0]
    except OrderError as exc:
        return exc.code


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "test.db")
        self.store = Store(self.path)
        self.store.initialize(stock=100)

    def count(self, table):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_order_and_queue_are_persisted_together(self):
        order, replay = self.store.create_order("alice", "iphone_16", 2, "one")
        self.assertFalse(replay)
        self.assertEqual(order["status"], "PENDING")
        self.assertEqual(self.store.inventory("iphone_16"), 98)
        self.assertEqual(self.count("jobs"), 1)

    def test_retry_after_lost_response_returns_same_order(self):
        original, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        reopened = Store(self.path)
        retry, replay = reopened.create_order("alice", "iphone_16", 1, "one")
        self.assertTrue(replay)
        self.assertEqual(original, retry)
        self.assertEqual(self.store.inventory("iphone_16"), 99)

    def test_key_reuse_with_different_payload_is_conflict(self):
        self.store.create_order("alice", "iphone_16", 1, "one")
        with self.assertRaises(OrderError) as caught:
            self.store.create_order("alice", "iphone_16", 2, "one")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_keys_are_scoped_to_user(self):
        a, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        b, _ = self.store.create_order("bob", "iphone_16", 1, "one")
        self.assertNotEqual(a["orderId"], b["orderId"])

    def test_unknown_product_does_not_create_order(self):
        with self.assertRaises(OrderError) as caught:
            self.store.create_order("alice", "missing", 1, "one")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.count("orders"), 0)

    def test_insufficient_stock_does_not_create_order_or_job(self):
        with self.assertRaises(OrderError):
            self.store.create_order("alice", "iphone_16", 101, "one")
        self.assertEqual(self.store.inventory("iphone_16"), 100)
        self.assertEqual(self.count("orders"), 0)
        self.assertEqual(self.count("jobs"), 0)

    def test_concurrent_requests_never_oversell(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
            results = list(executor.map(lambda n: buy_in_process(self.path, str(n)), range(180)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 100)
        self.assertEqual(results.count("OUT_OF_STOCK"), 80)
        self.assertEqual(self.store.inventory("iphone_16"), 0)
        self.assertEqual(self.count("orders"), 100)

    def test_concurrent_duplicate_requests_only_reserve_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(lambda _: buy_in_process(self.path, "same"), range(40)))
        self.assertEqual(len({r["orderId"] for r in results}), 1)
        self.assertEqual(self.store.inventory("iphone_16"), 99)
        self.assertEqual(self.count("jobs"), 1)

    def test_two_processes_compete_for_last_unit(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE products SET stock = 1")
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=2, mp_context=multiprocessing.get_context("spawn")
        ) as executor:
            results = list(executor.map(buy_in_process, [self.path] * 2, ["a", "b"]))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual(results.count("OUT_OF_STOCK"), 1)

    def test_duplicate_worker_delivery_is_harmless(self):
        order, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        self.assertTrue(self.store.process_next())
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("UPDATE jobs SET processed_at = NULL")
        self.assertTrue(self.store.process_next())
        self.assertEqual(self.store.get_order(order["orderId"], "alice")["status"], "CONFIRMED")
        self.assertEqual(self.store.inventory("iphone_16"), 99)

    def test_pending_job_survives_restart(self):
        order, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        self.assertTrue(Store(self.path).process_next())
        self.assertEqual(self.store.get_order(order["orderId"], "alice")["status"], "CONFIRMED")
        self.assertFalse(self.store.process_next())

    def test_cannot_read_another_users_order(self):
        order, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        with self.assertRaises(OrderError) as caught:
            self.store.get_order(order["orderId"], "bob")
        self.assertEqual(caught.exception.status, 404)

    def test_failed_order_insert_rolls_back_stock(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute(
                "CREATE TRIGGER fail_insert BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_order("alice", "iphone_16", 1, "one")
        self.assertEqual(self.store.inventory("iphone_16"), 100)
        self.assertEqual(self.count("jobs"), 0)

    def test_failed_queue_insert_rolls_back_order_and_inventory(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute(
                "CREATE TRIGGER fail_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'queue unavailable'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_order("alice", "iphone_16", 1, "queue-failure")
        self.assertEqual(self.store.inventory("iphone_16"), 100)
        self.assertEqual(self.count("orders"), 0)
        self.assertEqual(self.count("jobs"), 0)

    def test_concurrent_multi_quantity_orders_preserve_remaining_stock(self):
        def buy(index):
            try:
                return self.store.create_order("alice", "iphone_16", 13, f"batch-{index}")[0]
            except OrderError as exc:
                return exc.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(buy, range(20)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 7)
        self.assertEqual(results.count("OUT_OF_STOCK"), 13)
        self.assertEqual(self.store.inventory("iphone_16"), 9)

    def test_worker_failure_rolls_back_confirmation_and_ack(self):
        order, _ = self.store.create_order("alice", "iphone_16", 1, "one")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute(
                "CREATE TRIGGER fail_ack BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.process_next()
        self.assertEqual(self.store.get_order(order["orderId"], "alice")["status"], "PENDING")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("DROP TRIGGER fail_ack")
        self.assertTrue(self.store.process_next())

    def test_initialize_never_resets_sold_inventory(self):
        self.store.create_order("alice", "iphone_16", 1, "one")
        self.store.initialize()
        self.assertEqual(self.store.inventory("iphone_16"), 99)

    def test_process_death_before_commit_rolls_back_inventory(self):
        script = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE products SET stock = stock - 1')
os._exit(17)
"""
        result = subprocess.run([sys.executable, "-c", script, self.path], check=False)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.store.inventory("iphone_16"), 100)

    def test_process_death_after_commit_can_be_retried(self):
        script = """
import os, sys
from flashdrop.store import Store
Store(sys.argv[1]).create_order('alice', 'iphone_16', 1, 'lost-response')
os._exit(18)
"""
        result = subprocess.run([sys.executable, "-c", script, self.path], check=False)
        self.assertEqual(result.returncode, 18)
        _, replay = self.store.create_order("alice", "iphone_16", 1, "lost-response")
        self.assertTrue(replay)
        self.assertEqual(self.store.inventory("iphone_16"), 99)
        self.assertEqual(self.count("orders"), 1)
