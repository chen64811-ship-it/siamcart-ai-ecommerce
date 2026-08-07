"""
SQLite module for the Transaction Tracker Agent.

Read-only parameterized queries. Never performs INSERT, UPDATE, DELETE, or DDL
after initialisation. Initialisation is idempotent.
"""

import sqlite3
import os
from typing import Dict, Optional, List, Any
from pathlib import Path

# Schema columns expected by the spec
ORDER_COLUMNS = [
    "order_id",
    "customer_name",
    "product_name",
    "order_status",
    "payment_status",
    "shipment_status",
    "tracking_number",
    "shipping_provider",
    "purchase_date",
    "estimated_delivery_date",
]

SAMPLE_ORDERS: List[Dict[str, Any]] = [
    {
        "order_id": "ORD-1001",
        "customer_name": "สมหญิง ใจดี",
        "product_name": "เสื้อเชิ้ตผู้หญิง",
        "order_status": "shipped",
        "payment_status": "paid",
        "shipment_status": "in_transit",
        "tracking_number": "FLASH-TRK-1001",
        "shipping_provider": "Flash Express",
        "purchase_date": "2026-07-08",
        "estimated_delivery_date": "2026-07-14",
    },
    {
        "order_id": "ORD-1002",
        "customer_name": "มนัส ทรัพย์มั่นคง",
        "product_name": "หูฟัง Bluetooth",
        "order_status": "processing",
        "payment_status": "paid",
        "shipment_status": "pending_pickup",
        "tracking_number": "KERRY-TRK-1002",
        "shipping_provider": "Kerry Express",
        "purchase_date": "2026-07-10",
        "estimated_delivery_date": "2026-07-16",
    },
    {
        "order_id": "ORD-1003",
        "customer_name": "ณัฐวุฒิ รักดี",
        "product_name": "สมาร์ทโฟนรุ่น A พร้อมเคส",
        "order_status": "delivered",
        "payment_status": "paid",
        "shipment_status": "delivered",
        "tracking_number": "JNT-TRK-1003",
        "shipping_provider": "J&T Express",
        "purchase_date": "2026-07-01",
        "estimated_delivery_date": "2026-07-05",
    },
    {
        "order_id": "ORD-1004",
        "customer_name": "ปริญญา วงศ์ไชยา",
        "product_name": "โน้ตบุ๊ก",
        "order_status": "pending",
        "payment_status": "unpaid",
        "shipment_status": "not_shipped",
        "tracking_number": "",
        "shipping_provider": "",
        "purchase_date": "2026-07-10",
        "estimated_delivery_date": "รอการชำระเงิน",
    },
    {
        "order_id": "ORD-1005",
        "customer_name": "วิไล รักษ์ไทย",
        "product_name": "รองเท้าวิ่ง",
        "order_status": "return_requested",
        "payment_status": "paid",
        "shipment_status": "return_initiated",
        "tracking_number": "KERRY-TRK-1005",
        "shipping_provider": "Kerry Express",
        "purchase_date": "2026-06-28",
        "estimated_delivery_date": "N/A",
    },
]


def init_database(db_path: str) -> None:
    """Create the orders table and seed sample data.

    Idempotent — uses CREATE TABLE IF NOT EXISTS and INSERT OR IGNORE so
    repeated calls do not duplicate rows.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS orders (
            order_id           TEXT PRIMARY KEY,
            customer_name      TEXT NOT NULL,
            product_name       TEXT NOT NULL,
            order_status       TEXT NOT NULL,
            payment_status     TEXT NOT NULL,
            shipment_status    TEXT NOT NULL,
            tracking_number    TEXT DEFAULT '',
            shipping_provider  TEXT DEFAULT '',
            purchase_date      TEXT NOT NULL,
            estimated_delivery_date TEXT NOT NULL
        )
    """)

    for row in SAMPLE_ORDERS:
        cursor.execute(
            """INSERT OR IGNORE INTO orders
               (order_id, customer_name, product_name, order_status,
                payment_status, shipment_status, tracking_number,
                shipping_provider, purchase_date, estimated_delivery_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["order_id"],
                row["customer_name"],
                row["product_name"],
                row["order_status"],
                row["payment_status"],
                row["shipment_status"],
                row["tracking_number"],
                row["shipping_provider"],
                row["purchase_date"],
                row["estimated_delivery_date"],
            ),
        )

    conn.commit()
    conn.close()

    # Task 5C: ensure storefront schema (products, order_items, orders
    # migration, product seeding). Idempotent and fast — keeps the shared
    # orders.db consistent for real orders created via POST /api/orders.
    from app.db.store import init_store_database
    init_store_database(db_path)


def query_order(db_path: str, order_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single order by its order_id.

    Returns None when the order does not exist.
    This is a read-only parameterised query.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def database_available(db_path: str) -> bool:
    """Return True if the database file exists and contains orders."""
    if not os.path.isfile(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders")
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False
