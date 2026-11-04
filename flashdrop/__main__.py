import argparse
import logging
import os
import signal
import threading

from flashdrop.api import make_server
from flashdrop.store import Store
from flashdrop.worker import run_worker


def main():
    parser = argparse.ArgumentParser(description="FlashDrop order service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=os.environ.get("DATABASE_PATH", "data/flashdrop.db"))
    parser.add_argument("--worker-only", action="store_true")
    parser.add_argument("--no-worker", action="store_true")
    args = parser.parse_args()
    if args.worker_only and args.no_worker:
        parser.error("--worker-only and --no-worker cannot be combined")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(args.db)
    store.initialize()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if args.worker_only:
        run_worker(store, stop)
        return
    server = make_server(store, args.host, args.port)
    api_thread = threading.Thread(target=server.serve_forever, daemon=True)
    api_thread.start()
    worker = None
    if not args.no_worker:
        worker = threading.Thread(target=run_worker, args=(store, stop), daemon=True)
        worker.start()
    logging.info("Listening on http://%s:%s", args.host, args.port)
    try:
        stop.wait()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        api_thread.join()
        if worker:
            worker.join(timeout=10)


if __name__ == "__main__":
    main()
