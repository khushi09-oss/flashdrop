import concurrent.futures
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from flashdrop.api import make_server
from flashdrop.store import Store
from flashdrop.validation import validate_order


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "api.db")
        cls.store.initialize()
        cls.server = make_server(cls.store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, user="alice", raw=None, extra_headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if user is not None:
            headers["X-User-Id"] = user
        headers.update(extra_headers or {})
        try:
            conn.request(method, path, body=raw if raw is not None else json.dumps(body), headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            conn.close()

    def body(self, key="api-test"):
        return {"userId": "alice", "productId": "iphone_16", "quantity": 1, "idempotencyKey": key}

    def test_accept_retry_get_and_worker(self):
        status, order, headers = self.request("POST", "/orders", self.body())
        self.assertEqual(status, 202)
        self.assertEqual(headers["Idempotency-Replayed"], "false")
        self.assertEqual(order["status"], "PENDING")
        status, duplicate, headers = self.request("POST", "/orders", self.body())
        self.assertEqual(status, 202)
        self.assertEqual(duplicate, order)
        self.assertEqual(headers["Idempotency-Replayed"], "true")
        while self.store.process_next():
            pass
        status, current, _ = self.request("GET", "/orders/" + order["orderId"])
        self.assertEqual(status, 200)
        self.assertEqual(current["status"], "CONFIRMED")
        _, replay, _ = self.request("POST", "/orders", self.body())
        self.assertEqual(replay, order)

    def test_input_validation(self):
        for quantity in [0, -1, 1.5, True, "1", None, 10001]:
            with self.subTest(quantity=quantity):
                body = self.body()
                body["quantity"] = quantity
                self.assertEqual(self.request("POST", "/orders", body)[0], 422)
        for body in [
            None,
            [],
            {},
            {"userId": "alice"},
            {**self.body(), "admin": True},
            {**self.body(), "idempotencyKey": " "},
            {**self.body(), "userId": 12},
        ]:
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/orders", body)[0], 422)

    def test_missing_identity(self):
        self.assertEqual(self.request("POST", "/orders", self.body(), user=None)[0], 401)

    def test_identity_mismatch(self):
        self.assertEqual(self.request("POST", "/orders", self.body(), user="bob")[0], 403)

    def test_order_owner_is_checked(self):
        _, order, _ = self.request("POST", "/orders", self.body("owner-test"))
        self.assertEqual(self.request("GET", "/orders/" + order["orderId"], user="bob")[0], 404)

    def test_invalid_json(self):
        self.assertEqual(self.request("POST", "/orders", raw="{oops")[0], 400)

    def test_body_size_cap(self):
        self.assertEqual(self.request("POST", "/orders", raw="x" * 8193)[0], 413)

    def test_wrong_content_type(self):
        self.assertEqual(
            self.request("POST", "/orders", self.body(), extra_headers={"Content-Type": "text/plain"})[0], 415
        )

    def test_not_found_and_health(self):
        self.assertEqual(self.request("GET", "/missing")[0], 404)
        self.assertEqual(self.request("GET", "/orders/ord_" + "0" * 32)[0], 404)
        self.assertEqual(self.request("GET", "/health", user=None)[0], 200)

    def test_http_concurrent_idempotency(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(lambda _: self.request("POST", "/orders", self.body("race")), range(16))
            )
        self.assertTrue(all(r[0] == 202 for r in responses))
        self.assertEqual(len({r[1]["orderId"] for r in responses}), 1)

    def test_demo_assets_are_served_without_exposing_source(self):
        for path, content_type in [
            ("/", "text/html"),
            ("/app.css", "text/css"),
            ("/app.js", "text/javascript"),
        ]:
            with self.subTest(path=path):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                try:
                    conn.request("GET", path)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(content_type, response.getheader("Content-Type"))
                    self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
                    self.assertTrue(response.read())
                finally:
                    conn.close()
        self.assertEqual(self.request("GET", "/../store.py")[0], 404)
        self.assertEqual(self.request("GET", "/static/store.py")[0], 404)

    def test_stock_endpoint_reports_database_inventory(self):
        status, product, _ = self.request("GET", "/products/iphone_16", user=None)
        self.assertEqual(status, 200)
        self.assertEqual(product, {"productId": "iphone_16", "stock": self.store.inventory("iphone_16")})


class ValidationTests(unittest.TestCase):
    def test_valid_payload_is_preserved(self):
        body = {
            "userId": "user_123",
            "productId": "iphone_16",
            "quantity": 1,
            "idempotencyKey": "request_abc123",
        }
        self.assertEqual(validate_order(body), body)
