"""Run against a running service: python scripts/demo.py."""

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description="Demonstrate order, retry, and confirmation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    body = {
        "userId": "user_123",
        "productId": "iphone_16",
        "quantity": 1,
        "idempotencyKey": "demo_" + uuid.uuid4().hex,
    }

    def request(path, payload=None):
        req = urllib.request.Request(
            args.base_url + path,
            data=json.dumps(payload).encode() if payload else None,
            headers={"Content-Type": "application/json", "X-User-Id": "user_123"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)

    status, order = request("/orders", body)
    print("1. Accepted:", status, order)
    _, retry = request("/orders", body)
    assert order == retry, "Retry created a different order!"
    print("2. Same key returns the same acceptance:", retry)
    for _ in range(40):
        _, current = request("/orders/" + order["orderId"])
        if current["status"] == "CONFIRMED":
            print("3. Background worker confirmed:", current)
            return
        time.sleep(0.25)
    raise SystemExit("Order is still PENDING. Start the worker and try GET again.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode()}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach the API. Start python -m flashdrop first. {exc.reason}") from None
