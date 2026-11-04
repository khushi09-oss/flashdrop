"""All correctness rules live here, next to the database transaction."""

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class OrderError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA synchronous = FULL')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            # Acquire the database write lock BEFORE checking keys or inventory.
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, stock=100):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, stock INTEGER NOT NULL CHECK(stock >= 0))''')
            db.execute('''CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                product_id TEXT NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING', 'CONFIRMED')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, idempotency_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS pending_jobs ON jobs(id) WHERE processed_at IS NULL')
            db.execute('INSERT OR IGNORE INTO products(id, stock) VALUES (?, ?)',
                       ('iphone_16', stock))

    @staticmethod
    def public_order(row):
        return {'orderId': row['id'], 'status': row['status']}

    def create_order(self, user_id, product_id, quantity, key):
        with self.transaction() as db:
            existing = db.execute('SELECT * FROM orders WHERE user_id = ? AND idempotency_key = ?',
                                  (user_id, key)).fetchone()
            if existing:
                if existing['product_id'] != product_id or existing['quantity'] != quantity:
                    raise OrderError(409, 'IDEMPOTENCY_CONFLICT', 'This key was already used for a different order.')
                # Replay acceptance, even if GET now reports CONFIRMED.
                return {'orderId': existing['id'], 'status': 'PENDING'}, True

            changed = db.execute('''UPDATE products SET stock = stock - ?
                WHERE id = ? AND stock >= ?''', (quantity, product_id, quantity)).rowcount
            if not changed:
                exists = db.execute('SELECT 1 FROM products WHERE id = ?', (product_id,)).fetchone()
                if not exists:
                    raise OrderError(404, 'PRODUCT_NOT_FOUND', 'Product does not exist.')
                raise OrderError(409, 'OUT_OF_STOCK', 'Insufficient stock.')

            order_id = 'ord_' + uuid.uuid4().hex
            db.execute('''INSERT INTO orders(id, user_id, product_id, quantity, idempotency_key, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')''', (order_id, user_id, product_id, quantity, key))
            # Durable simulated queue: no order can commit without its job.
            db.execute('INSERT INTO jobs(order_id) VALUES (?)', (order_id,))
            return {'orderId': order_id, 'status': 'PENDING'}, False

    def get_order(self, order_id, user_id):
        db = self.connect()
        try:
            row = db.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?',
                             (order_id, user_id)).fetchone()
            if row is None:
                raise OrderError(404, 'ORDER_NOT_FOUND', 'Order does not exist.')
            return self.public_order(row)
        finally:
            db.close()

    def inventory(self, product_id):
        db = self.connect()
        try:
            row = db.execute('SELECT stock FROM products WHERE id = ?', (product_id,)).fetchone()
            if row is None:
                raise OrderError(404, 'PRODUCT_NOT_FOUND', 'Product does not exist.')
            return row['stock']
        finally:
            db.close()

    def process_next(self):
        with self.transaction() as db:
            job = db.execute('SELECT * FROM jobs WHERE processed_at IS NULL ORDER BY id LIMIT 1').fetchone()
            if job is None:
                return False
            # A duplicate delivery cannot decrement inventory or reconfirm an order.
            db.execute("UPDATE orders SET status = 'CONFIRMED' WHERE id = ? AND status = 'PENDING'",
                       (job['order_id'],))
            db.execute('UPDATE jobs SET processed_at = CURRENT_TIMESTAMP WHERE id = ?', (job['id'],))
            return True
