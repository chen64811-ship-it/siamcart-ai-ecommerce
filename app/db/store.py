"""SQLite store module — products, order_items, and real order creation.

Task 5C: connects the storefront to the existing data/orders.db.

Design notes:
- All writes use parameterized SQL.
- Write connections enable SQLite foreign keys (PRAGMA foreign_keys=ON).
- Schema creation is idempotent (CREATE TABLE IF NOT EXISTS).
- The orders table is migrated safely (ADD COLUMN only when absent); every
  existing Transaction Tracker column is preserved.
- Order creation runs inside a single BEGIN IMMEDIATE transaction.
- No ChromaDB, SentenceTransformer, or DeepSeek imports in this module.
"""

import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.config import DATA_DIR
from app.db.product_seed import PRODUCT_SEED

# Default database path (same file the Transaction Tracker reads).
# Overridable via STORE_DB_PATH for deployment (persistent volume mount).
STORE_DB_PATH = os.getenv("STORE_DB_PATH", str(DATA_DIR / "orders.db"))

# Columns that must exist on the orders table for the storefront flow.
# customer_name already exists (used by Transaction Tracker) — everything else
# is added only when absent.
ORDER_EXTRA_COLUMNS = [
    ("customer_email", "TEXT"),
    ("customer_phone", "TEXT"),
    ("shipping_address", "TEXT"),
    ("district", "TEXT"),
    ("province", "TEXT"),
    ("postal_code", "TEXT"),
    ("total_amount", "REAL"),
    ("payment_method", "TEXT"),
    ("created_at", "TEXT"),
    # Added by Task 5C-3 (demo payment). ADD COLUMN only when absent.
    ("paid_at", "TEXT"),
    # Added by the demo-cancellation repair. ADD COLUMN only when absent.
    ("cancellation_reason", "TEXT"),
    ("cancelled_at", "TEXT"),
    ("refund_type", "TEXT"),
    # Added by Task 8C (demo shipment notification). ADD COLUMN only when
    # absent. No real SMS/email is ever sent — these flags simulate the
    # notification for the research prototype.
    ("notification_sent", "INTEGER DEFAULT 0"),
    ("notification_sent_at", "TEXT"),
    # Added by Task 8D (demo shipment lifecycle). ADD COLUMN only when
    # absent. shipped_at records when the simulated shipment starts;
    # delivered_at records when the demo parcel is marked delivered.
    ("shipped_at", "TEXT"),
    ("delivered_at", "TEXT"),
]

_ORDER_ID_RE = re.compile(r"^ORD-(\d+)$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SORT_SQL = {
    "featured": "CASE WHEN badge = 'Bestseller' THEN 0 ELSE 1 END ASC, rating DESC, product_id ASC",
    "price_asc": "price ASC, product_id ASC",
    "price_desc": "price DESC, product_id ASC",
    "newest": "created_at DESC, product_id ASC",
}


# ── Connection helpers ──────────────────────────────────────────────────


def get_connection(db_path: str, write: bool = False) -> sqlite3.Connection:
    """Open a connection. Write connections enable foreign keys."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if write:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Schema + migration ─────────────────────────────────────────────────


def ensure_order_schema(conn: sqlite3.Connection) -> None:
    """Add storefront columns to the orders table when absent.

    Preserves every existing column — only ADD COLUMN when missing.
    Safe for the 5 seeded orders (ORD-1001 … ORD-1005).
    """
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(orders)").fetchall()
    }
    for col, coltype in ORDER_EXTRA_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {coltype}")


def init_store_database(db_path: str) -> None:
    """Create products + order_items tables, migrate orders, seed products.

    Idempotent. Products are seeded only when the products table is empty.
    """
    conn = get_connection(db_path, write=True)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id       TEXT PRIMARY KEY,
                name             TEXT NOT NULL,
                short_description TEXT NOT NULL,
                full_description TEXT NOT NULL,
                category         TEXT NOT NULL,
                price            REAL NOT NULL,
                original_price   REAL,
                rating           REAL NOT NULL DEFAULT 0,
                review_count     INTEGER NOT NULL DEFAULT 0,
                stock_quantity   INTEGER NOT NULL,
                badge            TEXT,
                image_url        TEXT NOT NULL,
                thumbnail_urls   TEXT,
                features         TEXT,
                active           INTEGER NOT NULL DEFAULT 1,
                created_at       TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                item_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id     TEXT NOT NULL,
                product_id   TEXT NOT NULL,
                product_name TEXT NOT NULL,
                quantity     INTEGER NOT NULL,
                unit_price   REAL NOT NULL,
                line_total   REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(order_id),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id)"
        )
        ensure_order_schema(conn)

        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count == 0:
            _seed_products(conn)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _seed_products(conn: sqlite3.Connection) -> None:
    """Seed the 12 approved storefront products (PROD-001 … PROD-012).

    created_at is backdated from newInDays so the `newest` sort behaves like
    the approved storefront ordering.
    """
    now = datetime.now()
    for p in PRODUCT_SEED:
        new_in_days = p.get("newInDays")
        created = now - timedelta(days=new_in_days or 365)
        conn.execute(
            """
            INSERT INTO products (
                product_id, name, short_description, full_description,
                category, price, original_price, rating, review_count,
                stock_quantity, badge, image_url, thumbnail_urls, features,
                active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["id"],
                p["name"],
                p["shortDescription"],
                p["fullDescription"],
                p["category"],
                float(p["price"]),
                float(p["originalPrice"]) if p.get("originalPrice") is not None else None,
                float(p["rating"]),
                int(p["reviewCount"]),
                int(p["stock"]),
                p.get("badge"),
                p["image"],
                json.dumps(p.get("thumbnails") or [p["image"]], ensure_ascii=False),
                json.dumps(p.get("features") or [], ensure_ascii=False),
                1,
                created.isoformat(timespec="seconds"),
            ),
        )


# ── Product queries ────────────────────────────────────────────────────


def _row_to_product(row: sqlite3.Row) -> Dict[str, Any]:
    """Deserialize a products row into an API-ready dict (arrays restored)."""
    def _loads(raw):
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else [value]
        except (TypeError, ValueError):
            return []

    return {
        "product_id": row["product_id"],
        "name": row["name"],
        "short_description": row["short_description"],
        "full_description": row["full_description"],
        "category": row["category"],
        "price": row["price"],
        "original_price": row["original_price"],
        "rating": row["rating"],
        "review_count": row["review_count"],
        "stock_quantity": row["stock_quantity"],
        "badge": row["badge"],
        "image_url": row["image_url"],
        "thumbnail_urls": _loads(row["thumbnail_urls"]),
        "features": _loads(row["features"]),
        "active": row["active"],
        "created_at": row["created_at"],
    }


def list_products(
    db_path: str,
    search: Optional[str] = None,
    category: Optional[str] = None,
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return active products, optionally filtered/sorted.

    search matches name, category, short description and full description.
    """
    where = ["active = 1"]
    params: List[Any] = []

    if category:
        if category.lower() == "new":
            where.append("badge = ?")
            params.append("New")
        else:
            where.append("category = ?")
            params.append(category)

    if search:
        like = f"%{search}%"
        where.append(
            "(name LIKE ? OR category LIKE ? OR short_description LIKE ? OR full_description LIKE ?)"
        )
        params.extend([like, like, like, like])

    order_by = SORT_SQL.get(sort or "featured", SORT_SQL["featured"])
    sql = (
        "SELECT * FROM products WHERE "
        + " AND ".join(where)
        + f" ORDER BY {order_by}"
    )

    conn = get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_product(r) for r in rows]
    finally:
        conn.close()


def get_product(db_path: str, product_id: str) -> Optional[Dict[str, Any]]:
    """Return one active product, or None when unknown/inactive."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM products WHERE product_id = ? AND active = 1",
            (product_id.strip().upper(),),
        ).fetchone()
        return _row_to_product(row) if row else None
    finally:
        conn.close()


# ── Order helpers ──────────────────────────────────────────────────────


def normalize_order_id(order_id: str) -> str:
    """Normalize whitespace/case: ' ord1006 ' -> 'ORD-1006'."""
    raw = str(order_id or "").strip().upper()
    if not raw:
        return raw
    if raw.startswith("ORD") and "-" not in raw[:5]:
        raw = raw[:3] + "-" + raw[3:]
    return raw


def _next_order_id(conn: sqlite3.Connection) -> str:
    """ORD-1006, ORD-1007 … from the highest numeric existing identifier."""
    max_num = 1005
    for row in conn.execute("SELECT order_id FROM orders").fetchall():
        m = _ORDER_ID_RE.match((row["order_id"] or "").strip())
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"ORD-{max_num + 1}"


def _order_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    # key fields always present for the API consumers
    for field in (
        "order_id", "customer_name", "customer_email", "customer_phone",
        "shipping_address", "district", "province", "postal_code",
        "order_status", "payment_status", "shipment_status",
        "tracking_number", "shipping_provider", "total_amount",
        "payment_method", "purchase_date", "estimated_delivery_date",
        "created_at", "paid_at",
        # demo-cancellation fields (NULL until an order is cancelled)
        "cancellation_reason", "cancelled_at", "refund_type",
        # Task 8C demo-shipment-notification fields (0/NULL until shipped)
        "notification_sent", "notification_sent_at",
        # Task 8D demo-shipment-lifecycle timestamps (NULL until they happen)
        "shipped_at", "delivered_at",
    ):
        d.setdefault(field, None)
    return d


def create_order(db_path: str, customer: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create an order atomically: validate -> insert -> reduce stock.

    Runs in one BEGIN IMMEDIATE transaction; rolls back on any failure.
    Prices are always read from SQLite — never trusted from the client.

    Returns the complete order dict (with items).
    Raises ValueError with a clear message on validation/business failures.
    """
    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── validate products + stock (server-side prices) ──────────
        resolved = []
        for item in items:
            product_id = str(item["product_id"]).strip().upper()
            quantity = int(item["quantity"])
            if quantity <= 0:
                raise ValueError("Quantity must be a positive integer")
            row = conn.execute(
                "SELECT product_id, name, price, stock_quantity, active "
                "FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if row is None or not row["active"]:
                raise ValueError(f"Product {product_id} is not available")
            if quantity > row["stock_quantity"]:
                raise ValueError(
                    f"Only {row['stock_quantity']} unit(s) of {row['name']} "
                    f"({product_id}) left in stock"
                )
            resolved.append(
                {
                    "product_id": row["product_id"],
                    "product_name": row["name"],
                    "quantity": quantity,
                    "unit_price": float(row["price"]),
                    "line_total": round(float(row["price"]) * quantity, 2),
                }
            )

        total_amount = round(sum(i["line_total"] for i in resolved), 2)

        # ── generate unique order id ────────────────────────────────
        order_id = _next_order_id(conn)
        now = datetime.now()
        created_at = now.isoformat(timespec="seconds")
        purchase_date = now.date().isoformat()

        # ── insert order (defaults per spec) ────────────────────────
        conn.execute(
            """
            INSERT INTO orders (
                order_id, customer_name, customer_email, customer_phone,
                shipping_address, district, province, postal_code,
                order_status, payment_status, shipment_status,
                tracking_number, shipping_provider, purchase_date,
                estimated_delivery_date, total_amount, payment_method, created_at,
                product_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                customer["customer_name"],
                customer.get("customer_email"),
                customer.get("customer_phone"),
                customer.get("shipping_address"),
                customer.get("district"),
                customer.get("province"),
                customer.get("postal_code"),
                "processing",
                "pending",
                "not_shipped",
                "",
                "",
                purchase_date,
                "",
                total_amount,
                customer.get("payment_method"),
                created_at,
                ", ".join(i["product_name"] for i in resolved),
            ),
        )

        # ── insert order_items ──────────────────────────────────────
        for i in resolved:
            conn.execute(
                """
                INSERT INTO order_items (
                    order_id, product_id, product_name, quantity,
                    unit_price, line_total
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    i["product_id"],
                    i["product_name"],
                    i["quantity"],
                    i["unit_price"],
                    i["line_total"],
                ),
            )

        # ── reduce stock ────────────────────────────────────────────
        for i in resolved:
            conn.execute(
                "UPDATE products SET stock_quantity = stock_quantity - ? "
                "WHERE product_id = ?",
                (i["quantity"], i["product_id"]),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-read the full order for the response (outside the txn)
    order = get_order_with_items(db_path, order_id)
    if order is None:  # pragma: no cover — defensive
        raise ValueError("Order was created but could not be read back")
    return order


def get_order_with_items(db_path: str, order_id: str) -> Optional[Dict[str, Any]]:
    """Return order details + order_items, or None when the order does not exist."""
    normalized = normalize_order_id(order_id)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            return None
        order = _order_row_to_dict(row)
        items = conn.execute(
            "SELECT item_id, order_id, product_id, product_name, quantity, "
            "unit_price, line_total FROM order_items WHERE order_id = ? "
            "ORDER BY item_id ASC",
            (normalized,),
        ).fetchall()
        order["items"] = [dict(i) for i in items]
        return order
    finally:
        conn.close()


def validate_customer_payload(customer: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    """Shape-level validation (called before opening the transaction)."""
    if not items:
        raise ValueError("At least one item is required")

    name = str(customer.get("customer_name") or "").strip()
    if not name:
        raise ValueError("Customer name is required")

    email = str(customer.get("customer_email") or "").strip()
    if not email or not _EMAIL_RE.match(email):
        raise ValueError("A valid customer email is required")

    phone = str(customer.get("customer_phone") or "").strip()
    if not phone:
        raise ValueError("Customer phone is required")

    address = str(customer.get("shipping_address") or "").strip()
    if not address:
        raise ValueError("Shipping address is required")

    if not str(customer.get("payment_method") or "").strip():
        raise ValueError("Payment method is required")

    for item in items:
        if not str(item.get("product_id") or "").strip():
            raise ValueError("Every item needs a product_id")
        try:
            qty = int(item.get("quantity"))
        except (TypeError, ValueError):
            raise ValueError("Quantity must be a positive integer")
        if qty <= 0:
            raise ValueError("Quantity must be a positive integer")


# ── Order list (Task 5C-3 — My Orders) ───────────────────────────────


def list_orders(
    db_path: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    order_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Return order summaries (newest first), optionally filtered.

    - email: whitespace-stripped, case-insensitive match.
    - phone: whitespace-stripped match (internal whitespace ignored).
    - order_id: case/whitespace normalized (ORD-xxxx).
    - limit: default 20, clamped to a maximum of 50.
    - Every order carries its order_items, with the product image_url
      joined from the products table where the product still exists.

    Parameterized SQL only. No card data exists in the schema and none is
    returned. No ChromaDB / SentenceTransformer / DeepSeek initialization.
    """
    where: List[str] = []
    params: List[Any] = []

    if email is not None and str(email).strip():
        where.append("LOWER(o.customer_email) = LOWER(?)")
        params.append(str(email).strip())

    if phone is not None and str(phone).strip():
        # whitespace-insensitive phone match (query whitespace removed)
        where.append("REPLACE(o.customer_phone, ' ', '') = ?")
        params.append(re.sub(r"\s+", "", str(phone)))

    if order_id is not None and str(order_id).strip():
        where.append("o.order_id = ?")
        params.append(normalize_order_id(order_id))

    try:
        safe_limit = max(1, min(int(limit or 20), 50))
    except (TypeError, ValueError):
        safe_limit = 20

    conn = get_connection(db_path)
    try:
        sql = (
            "SELECT o.* FROM orders o"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY o.created_at DESC, o.order_id DESC LIMIT ?"
        )
        rows = conn.execute(sql, params + [safe_limit]).fetchall()

        orders: List[Dict[str, Any]] = []
        for row in rows:
            order = _order_row_to_dict(row)
            items = conn.execute(
                "SELECT oi.product_id, oi.product_name, oi.quantity, "
                "oi.unit_price, oi.line_total, p.image_url AS image_url "
                "FROM order_items oi "
                "LEFT JOIN products p ON p.product_id = oi.product_id "
                "WHERE oi.order_id = ? ORDER BY oi.item_id ASC",
                (order["order_id"],),
            ).fetchall()
            order["order_items"] = [dict(i) for i in items]
            orders.append(order)
        return orders
    finally:
        conn.close()


# ── Demo payment (Task 5C-3 — research simulation only) ──────────────


# Methods that may be marked paid through the explicit demo-payment
# endpoint. Cash on Delivery is deliberately excluded.
DEMO_PAYMENT_ELIGIBLE = {"bank_transfer", "credit_card", "debit_card", "card"}
_COD_METHODS = {"cod", "cash_on_delivery"}


def _normalize_payment_method(value: Any) -> str:
    """'Bank Transfer' -> 'bank_transfer'; 'COD' -> 'cod'; strip case/spaces."""
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def mark_order_paid(db_path: str, order_id: str) -> Dict[str, Any]:
    """Simulate payment for a pending, eligible order (research demo only).

    - Only updates an EXISTING order (ValueError ORDER_NOT_FOUND).
    - payment_status must currently be 'pending' (NOT_PENDING otherwise).
    - Only DEMO_PAYMENT_ELIGIBLE methods may be paid; Cash on Delivery is
      rejected (CASH_ON_DELIVERY).
    - Sets payment_status='paid' and a paid_at timestamp; order_status and
      shipment_status are preserved untouched.
    - Runs inside one BEGIN IMMEDIATE transaction.

    Returns the complete updated order (with items).
    """
    normalized = normalize_order_id(order_id)
    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise ValueError("ORDER_NOT_FOUND")

        method = _normalize_payment_method(row["payment_method"])
        if method in _COD_METHODS:
            raise ValueError("CASH_ON_DELIVERY")
        if method not in DEMO_PAYMENT_ELIGIBLE:
            raise ValueError("METHOD_NOT_ELIGIBLE")
        if row["payment_status"] != "pending":
            raise ValueError("NOT_PENDING")

        paid_at = _utcnow_iso()
        conn.execute(
            "UPDATE orders SET payment_status = 'paid', paid_at = ? "
            "WHERE order_id = ?",
            (paid_at, normalized),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    order = get_order_with_items(db_path, normalized)
    if order is None:  # pragma: no cover — defensive
        raise ValueError("ORDER_NOT_FOUND")
    return order


# ── Demo cancellation (research simulation only) ─────────────────────
# One deterministic cancellation workflow shared by the Order Details UI
# and the AI assistant. A simulated refund — no real money is transferred.

# Only orders still being processed and not yet shipped may be cancelled.
CANCELLABLE_ORDER_STATUS = {"processing"}
CANCELLABLE_SHIPMENT_STATUS = {"not_shipped"}

DEFAULT_CANCEL_REASON = "Customer changed their mind"


def cancel_demo_order(
    db_path: str,
    order_id: str,
    reason: str = DEFAULT_CANCEL_REASON,
) -> Dict[str, Any]:
    """Deterministically cancel a demo order (simulated refund).

    Allowed state: order_status == 'processing' AND shipment_status ==
    'not_shipped'. Runs inside one BEGIN IMMEDIATE transaction:

      - malformed order id -> ValueError INVALID_ORDER_ID;
      - unknown order -> ValueError ORDER_NOT_FOUND;
      - shipped/delivered orders are rejected (NOT_CANCELLABLE);
      - already-cancelled orders return the existing cancelled state
        (idempotent — the row and stock are never modified twice);
      - paid orders: payment_status -> 'refunded' and
        refund_type = 'simulated';
      - COD / pending orders: cancelled without any refund (payment_status
        untouched, refund_type stays NULL);
      - order-item stock is restored exactly once, inside the same
        transaction that performs the transition.

    Returns the API-shaped result dict. Repeated calls are idempotent:
    the final state is identical and stock is never double-restored.
    """
    normalized = normalize_order_id(order_id)
    if not _ORDER_ID_RE.match(normalized):
        raise ValueError("INVALID_ORDER_ID")

    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise ValueError("ORDER_NOT_FOUND")

        if row["order_status"] == "cancelled":
            # Idempotent repeat — return the existing cancelled state
            # without touching the row or the stock again.
            return {
                "order_id": normalized,
                "order_status": "cancelled",
                "payment_status": row["payment_status"],
                "shipment_status": row["shipment_status"],
                "cancelled_at": row["cancelled_at"],
                "refund_type": row["refund_type"],
                "cancellation_reason": row["cancellation_reason"],
                "stock_restored": False,
                "action_executed": False,
            }

        if (
            row["order_status"] not in CANCELLABLE_ORDER_STATUS
            or row["shipment_status"] not in CANCELLABLE_SHIPMENT_STATUS
        ):
            raise ValueError("NOT_CANCELLABLE")

        cancelled_at = _utcnow_iso()
        payment_status = str(row["payment_status"] or "").lower()
        is_paid = payment_status == "paid"
        refund_type = "simulated" if is_paid else None

        conn.execute(
            "UPDATE orders SET "
            "order_status = 'cancelled', "
            "cancelled_at = ?, "
            "cancellation_reason = ?, "
            "refund_type = ?, "
            "payment_status = CASE WHEN ? THEN 'refunded' ELSE payment_status END "
            "WHERE order_id = ?",
            (
                cancelled_at,
                (str(reason or "").strip() or DEFAULT_CANCEL_REASON),
                refund_type,
                is_paid,
                normalized,
            ),
        )

        # Restore order-item stock exactly once — inside the same
        # transaction that performs the cancelled transition.
        items = conn.execute(
            "SELECT product_id, quantity FROM order_items WHERE order_id = ?",
            (normalized,),
        ).fetchall()
        for item in items:
            conn.execute(
                "UPDATE products SET stock_quantity = stock_quantity + ? "
                "WHERE product_id = ?",
                (int(item["quantity"]), item["product_id"]),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-read the persisted state for the response (outside the txn).
    order = get_order_with_items(db_path, normalized)
    if order is None:  # pragma: no cover — defensive
        raise ValueError("ORDER_NOT_FOUND")
    return {
        "order_id": order["order_id"],
        "order_status": order["order_status"],
        "payment_status": order["payment_status"],
        "shipment_status": order["shipment_status"],
        "cancelled_at": order["cancelled_at"],
        "refund_type": order["refund_type"],
        "cancellation_reason": order["cancellation_reason"],
        "stock_restored": True,
        "action_executed": True,
    }


# ── Demo shipment (Task 8C — research simulation only) ────────────────
# When an order moves Processing -> Shipped the system generates a
# realistic demo tracking number (once) and simulates the shipment
# notification by recording notification_sent = true. No real SMS, email,
# WhatsApp, LINE, Twilio, AWS SNS or any third-party service is ever
# called — this is a DEMO feature only.

# Shipping providers used by the demo (Thai carriers).
SHIPPING_PROVIDERS = ["Thailand Post", "Flash Express", "J&T Express"]

# New shipment status used when a demo order is marked shipped.
SHIPPED_SHIPMENT_STATUS = "shipped"

# Only processing orders may be shipped.
SHIPPABLE_ORDER_STATUS = {"processing"}

# Notification simulation message shown everywhere the notification appears.
SHIPMENT_NOTIFICATION_MESSAGE = (
    "Shipment notification sent successfully. "
    "Customer notified (Simulation). "
    "No real SMS or email was sent."
)

# Demo notice appended to notification surfaces (requirement 6).
SHIPMENT_SIMULATION_NOTICE = (
    "Simulation Only — No real SMS or email has been sent."
)

# Realistic tracking-number formats per provider:
#   Thailand Post : TH + 9 digits + TH  (e.g. TH482913742TH)
#   Flash Express : FD + 10 digits     (e.g. FD0123456789)
#   J&T Express   : JT + 12 digits     (e.g. JT012345678901)
_TRACKING_FORMATS = {
    "Thailand Post": ("TH", 9, "TH"),
    "Flash Express": ("FD", 10, ""),
    "J&T Express": ("JT", 12, ""),
}

_TRACKING_RE = re.compile(r"^(?:TH\d{9}TH|FD\d{10}|JT\d{12})$")

# Task 8D demo tracking format: SC-YYYYMMDD-<order suffix> (e.g. SC-20260807-1066).
_SC_TRACKING_RE = re.compile(r"^SC-\d{8}-\d{1,6}$")


def _random_digits(n: int) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(n))


def generate_tracking_number(provider=None):
    """Generate a realistic demo tracking number for the given provider.

    provider is picked at random from SHIPPING_PROVIDERS when omitted.
    """
    provider = (provider or "").strip()
    if provider not in _TRACKING_FORMATS:
        provider = random.choice(SHIPPING_PROVIDERS)
    prefix, digits, suffix = _TRACKING_FORMATS[provider]
    return f"{prefix}{_random_digits(digits)}{suffix}"


def is_valid_tracking_number(value):
    """True when value looks like one of the demo tracking formats
    (Task 8C courier formats or the Task 8D SC- lifecycle format)."""
    text = str(value).strip()
    return bool(text) and (
        bool(_TRACKING_RE.match(text)) or bool(_SC_TRACKING_RE.match(text))
    )


# ── Task 8D — demo shipment lifecycle (research simulation only) ────────
# The normal journey is: Create -> Paid -> Processing -> Not Shipped ->
# Simulate Shipment -> In Transit -> Mark Delivered -> Delivered. The
# cancellation branch stays: Processing + Not Shipped -> Cancelled
# (Refunded if already paid). Once in transit or delivered, direct
# cancellation is no longer allowed (return/policy guidance instead).
# No real courier service, tracking API or ETA prediction is ever called.

# Demo courier used by the simulated lifecycle.
DEMO_SHIPPING_PROVIDER = "SiamCart Demo Logistics"

# Shipment states used by the lifecycle (the ONLY values the lifecycle sets).
SHIPMENT_NOT_SHIPPED = "not_shipped"
SHIPMENT_IN_TRANSIT = "in_transit"
SHIPMENT_DELIVERED = "delivered"

# Order states used by the lifecycle.
ORDER_SHIPPED = "shipped"
ORDER_DELIVERED = "delivered"

# Simulate Shipment is allowed from these order states.
SHIPPABLE_ORDER_STATUSES_8D = {"processing", "confirmed"}

# Mark Delivered is only allowed when the parcel is in transit.
DELIVERABLE_SHIPMENT_STATUS = {"in_transit"}

# Research-prototype note shown with the simulated shipment.
DEMO_SHIPMENT_NOTICE = "Demo shipment only. No real courier service is connected."


def build_demo_tracking_number(order_id: str) -> str:
    """Deterministic-looking demo tracking number: SC-YYYYMMDD-<suffix>.

    The suffix is the numeric part of the order ID (ORD-1066 -> 1066), so
    the number is stable per order and only ever generated once (persisted
    on the order and reused by every later query).
    """
    m = _ORDER_ID_RE.match(str(order_id or "").strip())
    suffix = m.group(1) if m else str(abs(hash(str(order_id))) % 1000000)
    return f"SC-{datetime.now().strftime('%Y%m%d')}-{suffix}"


def simulate_shipment(db_path: str, order_id: str) -> Dict[str, Any]:
    """Start the simulated shipment for a processing order (Task 8D).

    Allowed state: order_status in ('processing', 'confirmed') AND
    shipment_status == 'not_shipped'. Runs inside one BEGIN IMMEDIATE
    transaction:

      - malformed order id -> ValueError INVALID_ORDER_ID;
      - unknown order -> ValueError ORDER_NOT_FOUND;
      - cancelled orders are rejected (NOT_SHIPPABLE);
      - already in_transit -> returns the existing state idempotently
        (the tracking number is never regenerated);
      - already delivered -> returns the delivered state (NOT_SHIPPABLE
        semantics: no re-shipping a delivered parcel);
      - on success: shipment_status='in_transit', order_status='shipped',
        shipping_provider=DEMO_SHIPPING_PROVIDER, a SC- tracking number is
        generated ONCE and persisted, shipped_at is recorded.

    Returns the API-shaped result dict. payment_status is never changed.
    """
    normalized = normalize_order_id(order_id)
    if not _ORDER_ID_RE.match(normalized):
        raise ValueError("INVALID_ORDER_ID")

    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise ValueError("ORDER_NOT_FOUND")

        if row["shipment_status"] == SHIPMENT_IN_TRANSIT:
            # Idempotent repeat — same tracking number, same shipped_at.
            return {
                "order_id": normalized,
                "order_status": row["order_status"],
                "shipment_status": SHIPMENT_IN_TRANSIT,
                "shipping_provider": row["shipping_provider"] or DEMO_SHIPPING_PROVIDER,
                "tracking_number": row["tracking_number"] or "",
                "shipped_at": row["shipped_at"],
                "notice": DEMO_SHIPMENT_NOTICE,
                "action_executed": False,
            }

        if row["shipment_status"] == SHIPMENT_DELIVERED:
            # Already delivered — cannot re-ship.
            return {
                "order_id": normalized,
                "order_status": row["order_status"],
                "shipment_status": SHIPMENT_DELIVERED,
                "shipping_provider": row["shipping_provider"] or "",
                "tracking_number": row["tracking_number"] or "",
                "shipped_at": row["shipped_at"],
                "delivered_at": row["delivered_at"],
                "notice": DEMO_SHIPMENT_NOTICE,
                "action_executed": False,
            }

        if (
            str(row["order_status"] or "") not in SHIPPABLE_ORDER_STATUSES_8D
            or str(row["shipment_status"] or "") != SHIPMENT_NOT_SHIPPED
        ):
            raise ValueError("NOT_SHIPPABLE")

        tracking = str(row["tracking_number"] or "").strip()
        if not tracking:
            tracking = build_demo_tracking_number(normalized)
        shipped_at = _utcnow_iso()
        conn.execute(
            "UPDATE orders SET "
            "order_status = ?, "
            "shipment_status = ?, "
            "shipping_provider = ?, "
            "tracking_number = ?, "
            "shipped_at = ? "
            "WHERE order_id = ?",
            (
                ORDER_SHIPPED,
                SHIPMENT_IN_TRANSIT,
                DEMO_SHIPPING_PROVIDER,
                tracking,
                shipped_at,
                normalized,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "order_id": normalized,
        "order_status": ORDER_SHIPPED,
        "shipment_status": SHIPMENT_IN_TRANSIT,
        "shipping_provider": DEMO_SHIPPING_PROVIDER,
        "tracking_number": tracking,
        "shipped_at": shipped_at,
        "notice": DEMO_SHIPMENT_NOTICE,
        "action_executed": True,
    }


def mark_demo_delivered(db_path: str, order_id: str) -> Dict[str, Any]:
    """Mark an in-transit demo parcel as delivered (Task 8D).

    Allowed state: shipment_status == 'in_transit'. Runs inside one BEGIN
    IMMEDIATE transaction:

      - malformed order id -> ValueError INVALID_ORDER_ID;
      - unknown order -> ValueError ORDER_NOT_FOUND;
      - already delivered -> returns the existing state idempotently
        (delivered_at is never overwritten);
      - not in transit (e.g. not_shipped) -> ValueError NOT_DELIVERABLE
        (a parcel cannot jump straight to delivered);
      - on success: shipment_status='delivered', order_status='delivered',
        delivered_at recorded.

    Returns the API-shaped result dict. payment_status is never changed.
    """
    normalized = normalize_order_id(order_id)
    if not _ORDER_ID_RE.match(normalized):
        raise ValueError("INVALID_ORDER_ID")

    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise ValueError("ORDER_NOT_FOUND")

        if row["shipment_status"] == SHIPMENT_DELIVERED:
            # Idempotent repeat — current state, timestamps untouched.
            return {
                "order_id": normalized,
                "order_status": row["order_status"],
                "shipment_status": SHIPMENT_DELIVERED,
                "shipping_provider": row["shipping_provider"] or "",
                "tracking_number": row["tracking_number"] or "",
                "shipped_at": row["shipped_at"],
                "delivered_at": row["delivered_at"],
                "notice": DEMO_SHIPMENT_NOTICE,
                "action_executed": False,
            }

        if str(row["shipment_status"] or "") not in DELIVERABLE_SHIPMENT_STATUS:
            raise ValueError("NOT_DELIVERABLE")

        delivered_at = _utcnow_iso()
        conn.execute(
            "UPDATE orders SET "
            "order_status = ?, "
            "shipment_status = ?, "
            "delivered_at = ? "
            "WHERE order_id = ?",
            (ORDER_DELIVERED, SHIPMENT_DELIVERED, delivered_at, normalized),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "order_id": normalized,
        "order_status": ORDER_DELIVERED,
        "shipment_status": SHIPMENT_DELIVERED,
        "shipping_provider": DEMO_SHIPPING_PROVIDER,
        "tracking_number": (row and row["tracking_number"]) or "",
        "shipped_at": (row and row["shipped_at"]) or None,
        "delivered_at": delivered_at,
        "notice": DEMO_SHIPMENT_NOTICE,
        "action_executed": True,
    }


def mark_order_shipped(db_path, order_id, provider=None):
    """Deterministically ship a demo order (simulated notification).

    Allowed state: order_status == 'processing'. Runs inside one BEGIN
    IMMEDIATE transaction:

      - malformed order id -> ValueError INVALID_ORDER_ID;
      - unknown order -> ValueError ORDER_NOT_FOUND;
      - cancelled / shipped / delivered orders are rejected (NOT_SHIPPABLE);
      - already-shipped orders return the existing shipped state
        (idempotent — the tracking number and notification are never
        regenerated twice);
      - a tracking number is generated ONLY when the order has none
        (existing numbers and providers are preserved);
      - sets order_status='shipped', shipment_status='shipped',
        shipping_provider, notification_sent=1 and notification_sent_at.

    Returns the API-shaped result dict including the notification
    simulation facts (notification_sent, notification_sent_at, message,
    simulation notice).
    """
    normalized = normalize_order_id(order_id)
    if not _ORDER_ID_RE.match(normalized):
        raise ValueError("INVALID_ORDER_ID")

    conn = get_connection(db_path, write=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (normalized,)
        ).fetchone()
        if row is None:
            raise ValueError("ORDER_NOT_FOUND")

        if row["order_status"] == "shipped":
            # Idempotent repeat — return the existing shipped state
            # without regenerating the tracking number or notification.
            return {
                "order_id": normalized,
                "order_status": row["order_status"],
                "shipment_status": row["shipment_status"],
                "tracking_number": row["tracking_number"] or "",
                "shipping_provider": row["shipping_provider"] or "",
                "notification_sent": bool(row["notification_sent"]),
                "notification_sent_at": row["notification_sent_at"],
                "message": SHIPMENT_NOTIFICATION_MESSAGE,
                "simulation_notice": SHIPMENT_SIMULATION_NOTICE,
                "action_executed": False,
            }

        if str(row["order_status"] or "") not in SHIPPABLE_ORDER_STATUS:
            raise ValueError("NOT_SHIPPABLE")

        tracking = str(row["tracking_number"] or "").strip()
        current_provider = str(row["shipping_provider"] or "").strip()
        if tracking:
            # Preserve the existing tracking number + provider.
            final_tracking = tracking
            final_provider = current_provider or (provider or "").strip()
        else:
            # Generate a realistic demo tracking number (once).
            final_provider = (provider or "").strip()
            if not final_provider:
                final_provider = random.choice(SHIPPING_PROVIDERS)
            final_tracking = generate_tracking_number(final_provider)

        notification_sent_at = _utcnow_iso()
        conn.execute(
            "UPDATE orders SET "
            "order_status = 'shipped', "
            "shipment_status = ?, "
            "tracking_number = ?, "
            "shipping_provider = ?, "
            "notification_sent = 1, "
            "notification_sent_at = ? "
            "WHERE order_id = ?",
            (
                SHIPPED_SHIPMENT_STATUS,
                final_tracking,
                final_provider,
                notification_sent_at,
                normalized,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-read the persisted state for the response (outside the txn).
    order = get_order_with_items(db_path, normalized)
    if order is None:  # pragma: no cover — defensive
        raise ValueError("ORDER_NOT_FOUND")
    return {
        "order_id": order["order_id"],
        "order_status": order["order_status"],
        "payment_status": order["payment_status"],
        "shipment_status": order["shipment_status"],
        "tracking_number": order["tracking_number"] or "",
        "shipping_provider": order["shipping_provider"] or "",
        "notification_sent": bool(order["notification_sent"]),
        "notification_sent_at": order["notification_sent_at"],
        "message": SHIPMENT_NOTIFICATION_MESSAGE,
        "simulation_notice": SHIPMENT_SIMULATION_NOTICE,
        "action_executed": True,
    }
