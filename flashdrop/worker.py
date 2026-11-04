import logging
import sqlite3

logger = logging.getLogger(__name__)


def run_worker(store, stop, poll_interval=0.25):
    while not stop.is_set():
        try:
            if store.process_next():
                logger.info("order_confirmed")
                continue
        except sqlite3.Error:
            # The transaction rolled back; leave the durable job for the next pass.
            logger.exception("worker_transaction_failed")
        stop.wait(poll_interval)
