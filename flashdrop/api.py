"""HTTP boundary. Local demonstration server; production uses a hardened gateway."""

import json
import logging
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from flashdrop.store import OrderError
from flashdrop.validation import validate_order

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 8192


def make_server(store, host="127.0.0.1", port=8000):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FlashDrop"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_json(self, status, body, headers=None):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def identity(self):
            # shortcut: local demo identity only; use verified JWT claims in production.
            user = self.headers.get("X-User-Id", "")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", user):
                raise OrderError(401, "IDENTITY_REQUIRED", "Set X-User-Id for this local demo.")
            return user

        def read_body(self):
            if self.headers.get("Transfer-Encoding"):
                raise OrderError(400, "INVALID_BODY", "Transfer-Encoding is not supported.")
            if self.headers.get_content_type() != "application/json":
                raise OrderError(415, "INVALID_CONTENT_TYPE", "Use application/json.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise OrderError(400, "INVALID_BODY", "Invalid Content-Length.") from None
            if not 0 < length <= MAX_BODY_BYTES:
                raise OrderError(413, "INVALID_BODY_SIZE", "Body must contain 1-8192 bytes.")
            try:
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError
                return json.loads(data)
            except (ValueError, UnicodeDecodeError):
                raise OrderError(400, "INVALID_JSON", "Body must be valid JSON.") from None

        def dispatch(self):
            try:
                path = urlsplit(self.path).path
                if self.command == "GET" and path == "/health":
                    store.inventory("iphone_16")
                    self.send_json(200, {"status": "ok"})
                elif self.command == "POST" and path == "/orders":
                    user = self.identity()
                    body = validate_order(self.read_body())
                    if body["userId"] != user:
                        raise OrderError(403, "IDENTITY_MISMATCH", "userId must match X-User-Id.")
                    order, replay = store.create_order(
                        user, body["productId"], body["quantity"], body["idempotencyKey"]
                    )
                    self.send_json(
                        202,
                        order,
                        {
                            "Location": "/orders/" + order["orderId"],
                            "Idempotency-Replayed": str(replay).lower(),
                        },
                    )
                elif self.command == "GET" and re.fullmatch(r"/orders/ord_[0-9a-f]{32}", path):
                    self.send_json(200, store.get_order(path.rsplit("/", 1)[1], self.identity()))
                else:
                    raise OrderError(404, "NOT_FOUND", "Route does not exist.")
            except OrderError as exc:
                self.send_json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
            except sqlite3.OperationalError:
                logger.exception("database_unavailable")
                self.send_json(
                    503,
                    {
                        "error": {
                            "code": "DATABASE_UNAVAILABLE",
                            "message": "Retry with the same idempotency key.",
                        }
                    },
                    {"Retry-After": "1"},
                )
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                logger.info("client_disconnected")
            except Exception:
                logger.exception("request_failed")
                self.send_json(
                    500, {"error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error."}}
                )

        do_GET = dispatch
        do_POST = dispatch

        def log_message(self, format, *args):
            logger.info(format, *args)

    return ThreadingHTTPServer((host, port), Handler)
