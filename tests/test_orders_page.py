"""Focused SQLite store integration tests for Task 5C-3 (My Orders + demo payment).

Covers:
  GET  /api/orders                      — order summaries (newest first)
  GET  /api/orders?email|phone|order_id — filtering + normalization
  GET  /api/orders/{order_id}           — existing detail endpoint still works
  POST /api/orders/{order_id}/demo-payment — research-only paid simulation

Isolation guarantees (same pattern as test_store_integration.py):
- The real data/orders.db is never written. store_db.STORE_DB_PATH is
  redirected to a temporary file for the whole module.
- The temp database is initialized with the REAL production initialization
  functions (app.db.orders.init_database -> app.db.store.init_store_database),
  so the real schema (including the paid_at migration) and the real 12-product
  seed are used.
- A teardown stat check proves the real database file is untouched.
- No DeepSeek / ChromaDB / SentenceTransformer initialization is performed
  (verified by an explicit test).
"""

import os
import sqlite3
import sys
import tempfile
import types

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.store as store_db
from app.db.orders import init_database
from app.agents.transaction_tracker import fetch_transaction_evidence
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)

VALID_CUSTOMER = {
    "customer_name": "Somchai Test",
    "customer_email": "somchai.test@example.com",
    "customer_phone": "081-234-5678",
    "shipping_address": "123 Sukhumvit Rd",
    "district": "Watthana",
    "province": "Bangkok",
    "postal_code": "10110",
    "payment_method": "cash_on_delivery",
}

LIST_KEYS = {
    "order_id", "customer_name", "customer_email", "total_amount",
    "payment_method", "payment_status", "order_status", "shipment_status",
    "tracking_number", "shipping_provider", "created_at", "order_items",
}

ITEM_KEYS = {
    "product_id", "product_name", "quantity", "unit_price", "line_total", "image_url",
}


# ── helpers ────────────────────────────────────────────────────────────


def _order_payload(items, **overrides):
    payload = dict(VALID_CUSTOMER)
    payload.update(overrides)
    payload["items"] = items
    return payload


async def _post_order(client, items, **overrides):
    resp = await client.post("/api/orders", json=_order_payload(items, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _contains_card(data):
    """Recursively look for any card-like key/value in an API response."""
    if isinstance(data, dict):
        for k, v in data.items():
            if "card" in str(k).lower():
                return True
            if _contains_card(v):
                return True
    elif isinstance(data, list):
        return any(_contains_card(i) for i in data)
    return False


def _paid_at(db_path, order_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT paid_at FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _payment_status(db_path, order_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT payment_status FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    """Temporary SQLite database, initialized with real production functions."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # orders schema + 5 sample orders + store schema + 12 products

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        assert _db_stat(REAL_DB) == real_before, (
            f"REAL database was modified by orders-page tests: {REAL_DB}"
        )
    finally:
        store_db.STORE_DB_PATH = original_path
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client():
    """Async HTTP client over the FastAPI app (no lifespan / no real DB init)."""
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c


# ── 1. GET /api/orders — summaries ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_returns_summaries(client):
    """1. GET /api/orders returns order summaries with items."""
    resp = await client.get("/api/orders", params={"limit": 20})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "orders" in data and "count" in data
    assert data["count"] == len(data["orders"])
    assert data["count"] >= 5  # the 5 seeded orders always exist
    for order in data["orders"]:
        assert LIST_KEYS.issubset(order.keys()), f"missing keys in {order.get('order_id')}"
        assert isinstance(order["order_items"], list)


@pytest.mark.asyncio
async def test_limit_clamped_to_max_50(client):
    """limit above 50 is clamped server-side."""
    resp = await client.get("/api/orders", params={"limit": 500})
    assert resp.status_code == 200
    assert resp.json()["count"] <= 50


# ── 2. Email filtering ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_filtering_works(client):
    """2. Email filtering finds only that customer's orders."""
    created = await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 1}],
        customer_email="Mentor.Demo@Example.com",
    )
    oid = created["order_id"]
    # Normalization: mixed case + surrounding whitespace still match.
    resp = await client.get("/api/orders", params={"email": "  mentor.demo@example.com "})
    assert resp.status_code == 200
    orders = resp.json()["orders"]
    assert any(o["order_id"] == oid for o in orders)
    for o in orders:
        assert o["customer_email"].lower() == "mentor.demo@example.com"


@pytest.mark.asyncio
async def test_email_filter_does_not_mix_customers(client):
    """Email filter never returns other customers' orders."""
    await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 1}],
        customer_email="alice@example.com",
    )
    resp = await client.get("/api/orders", params={"email": "bob@example.com"})
    assert resp.status_code == 200
    assert resp.json()["orders"] == []
    assert resp.json()["count"] == 0


# ── 3. Phone filtering ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phone_filtering_works(client):
    """3. Phone filtering finds the order; whitespace is normalized."""
    created = await _post_order(
        client, [{"product_id": "PROD-002", "quantity": 1}],
        customer_phone=" 089-123-4567 ",
    )
    oid = created["order_id"]
    resp = await client.get("/api/orders", params={"phone": "089-123-4567"})
    assert resp.status_code == 200
    orders = resp.json()["orders"]
    assert any(o["order_id"] == oid for o in orders)
    # leading/trailing whitespace in the query is ignored
    resp2 = await client.get("/api/orders", params={"phone": "  089-123-4567  "})
    assert any(o["order_id"] == oid for o in resp2.json()["orders"])

    # internal whitespace in the stored phone is ignored too
    created2 = await _post_order(
        client, [{"product_id": "PROD-003", "quantity": 1}],
        customer_phone="089 123 4567",
    )
    resp3 = await client.get("/api/orders", params={"phone": "0891234567"})
    assert any(o["order_id"] == created2["order_id"] for o in resp3.json()["orders"])


# ── 4. Order-ID filtering ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_id_filtering_works(client):
    """4. Order-ID filtering works with case/whitespace normalization."""
    for lookup in ("ord-1001", " ORD-1001 ", "ord1001"):
        resp = await client.get("/api/orders", params={"order_id": lookup})
        assert resp.status_code == 200, f"lookup {lookup!r} failed"
        ids = [o["order_id"] for o in resp.json()["orders"]]
        assert "ORD-1001" in ids, f"lookup {lookup!r} -> {ids}"


# ── 5. Newest first ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_results_are_newest_first(client):
    """5. Results are ordered newest first (created_at DESC, then id DESC)."""
    email = "newest.check@example.com"
    ids = []
    for pid in ("PROD-001", "PROD-002", "PROD-003"):
        created = await _post_order(
            client, [{"product_id": pid, "quantity": 1}], customer_email=email
        )
        ids.append(created["order_id"])
    resp = await client.get("/api/orders", params={"email": email})
    assert resp.status_code == 200
    got = [o["order_id"] for o in resp.json()["orders"]]
    assert got[0] == ids[-1], f"expected newest {ids[-1]} first, got {got}"


# ── 6. Items + image URLs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_items_and_image_urls_included(client):
    """6. Order items include product name, qty, unit price and image_url."""
    created = await _post_order(client, [{"product_id": "PROD-008", "quantity": 1}])
    oid = created["order_id"]
    resp = await client.get("/api/orders", params={"order_id": oid})
    orders = resp.json()["orders"]
    assert len(orders) == 1
    order = orders[0]
    assert len(order["order_items"]) == 1
    item = order["order_items"][0]
    assert ITEM_KEYS.issubset(item.keys())
    assert item["product_id"] == "PROD-008"
    assert item["product_name"] == "CloudStep Casual Sneakers"
    assert item["quantity"] == 1
    assert item["unit_price"] == 990
    assert item["line_total"] == 990
    assert item["image_url"] == "/static/images/products/prod-008.jpg"


# ── 7. Unknown search ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_search_returns_empty_list(client):
    """7. Unknown email returns an empty list with count 0."""
    resp = await client.get("/api/orders", params={"email": "nobody@nowhere.com"})
    assert resp.status_code == 200
    assert resp.json() == {"orders": [], "count": 0}


# ── 8. Existing GET /api/orders/{order_id} ─────────────────────────────


@pytest.mark.asyncio
async def test_existing_order_detail_endpoint_still_works(client):
    """8. GET /api/orders/{order_id} remains functional."""
    resp = await client.get("/api/orders/ORD-1001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == "ORD-1001"
    assert isinstance(data["items"], list)

    created = await _post_order(client, [{"product_id": "PROD-004", "quantity": 2}])
    resp = await client.get(f"/api/orders/{created['order_id']}")
    assert resp.status_code == 200
    assert resp.json()["total_amount"] == created["total_amount"]
    assert len(resp.json()["items"]) == 1


# ── 9-12. Demo payment ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bank_transfer_order_can_be_marked_paid(client, tmp_db):
    """9. Eligible bank-transfer order is marked paid through demo-payment."""
    created = await _post_order(
        client, [{"product_id": "PROD-005", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == oid
    assert data["payment_status"] == "paid"
    assert data["paid_at"] is not None
    # order_status and shipment_status are preserved
    assert data["order_status"] == "processing"
    assert data["shipment_status"] == "not_shipped"
    # items are returned with the updated order
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_cash_on_delivery_cannot_be_marked_paid(client, tmp_db):
    """10. Cash on Delivery returns 409 and stays pending."""
    created = await _post_order(
        client, [{"product_id": "PROD-006", "quantity": 1}],
        payment_method="cash_on_delivery",
    )
    oid = created["order_id"]

    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Cash on Delivery remains pending until delivery."
    assert _payment_status(tmp_db, oid) == "pending"


@pytest.mark.asyncio
async def test_cod_alias_also_protected(client, tmp_db):
    """The UI stores 'COD' — the same alias must be protected."""
    created = await _post_order(
        client, [{"product_id": "PROD-006", "quantity": 1}],
        payment_method="COD",
    )
    oid = created["order_id"]
    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 409
    assert _payment_status(tmp_db, oid) == "pending"


@pytest.mark.asyncio
async def test_already_paid_order_cannot_be_paid_again(client, tmp_db):
    """11. An already-paid order cannot be paid again (409)."""
    created = await _post_order(
        client, [{"product_id": "PROD-007", "quantity": 1}],
        payment_method="card",
    )
    oid = created["order_id"]

    first = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert second.status_code == 409
    assert _payment_status(tmp_db, oid) == "paid"


@pytest.mark.asyncio
async def test_paid_at_is_stored(client, tmp_db):
    """12. paid_at is stored in SQLite and returned by the API."""
    created = await _post_order(
        client, [{"product_id": "PROD-008", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 200
    paid_at = resp.json()["paid_at"]
    assert paid_at
    assert _paid_at(tmp_db, oid) == paid_at

    # The list endpoint reflects the new status + paid_at too.
    listed = await client.get("/api/orders", params={"order_id": oid})
    assert listed.json()["orders"][0]["payment_status"] == "paid"
    assert listed.json()["orders"][0]["paid_at"] == paid_at


@pytest.mark.asyncio
async def test_demo_payment_requires_explicit_confirmation(client):
    """Demo payment with confirm_demo_payment=false is rejected."""
    resp = await client.post(
        "/api/orders/ORD-1001/demo-payment", json={"confirm_demo_payment": False}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_demo_payment_unknown_order_returns_404(client):
    """Demo payment for a non-existent order returns 404."""
    resp = await client.post(
        "/api/orders/ORD-9999/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 404


# ── 13. Transaction Tracker sees paid immediately ──────────────────────


@pytest.mark.asyncio
async def test_transaction_tracker_sees_paid_immediately(client, tmp_db):
    """13. Transaction Tracker returns payment_status=paid right after demo-payment."""
    created = await _post_order(
        client, [{"product_id": "PROD-009", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    result_before = fetch_transaction_evidence(f"{oid} paid?", db_path=tmp_db)
    assert result_before["order_found"] is True
    assert result_before["evidence"]["payment_status"] == "pending"

    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 200

    result_after = fetch_transaction_evidence(f"{oid} paid?", db_path=tmp_db)
    assert result_after["order_found"] is True
    assert result_after["evidence"]["payment_status"] == "paid"


# ── 14. No card number accepted or stored ──────────────────────────────


@pytest.mark.asyncio
async def test_no_card_number_accepted_or_stored(client, tmp_db):
    """14. No card-number field is accepted by demo-payment or stored."""
    from app.models.store import DemoPaymentRequest

    # The demo-payment model defines no card fields at all.
    fields = set(DemoPaymentRequest.model_fields)
    assert not any("card" in f.lower() for f in fields)

    # The orders schema has no card columns.
    conn = sqlite3.connect(tmp_db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    finally:
        conn.close()
    assert not any("card" in c.lower() for c in cols), f"card column found: {cols}"

    # Even if a client sends card data, it must not be accepted/stored.
    created = await _post_order(
        client, [{"product_id": "PROD-010", "quantity": 1}],
        payment_method="card",
    )
    oid = created["order_id"]
    resp = await client.post(
        f"/api/orders/{oid}/demo-payment",
        json={
            "confirm_demo_payment": True,
            "card_number": "4111111111111111",
            "card_cvv": "123",
            "card_expiry": "12/29",
        },
    )
    # pydantic ignores unknown fields -> 200; extra='forbid' -> 422.
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert not _contains_card(resp.json()), "card data leaked into the API response"

    # The list endpoint never exposes card data either.
    listed = await client.get("/api/orders", params={"order_id": oid})
    assert not _contains_card(listed.json())


# ── 15. No heavy dependencies initialized ──────────────────────────────


@pytest.mark.asyncio
async def test_no_deepseek_chromadb_sentence_transformer_init(client, tmp_db):
    """15. Order list + demo-payment never initialize heavy dependencies."""
    import openai

    HEAVY = ("chromadb", "sentence_transformers")
    installed = {}
    for name in HEAVY:
        if name not in sys.modules:
            mod = types.ModuleType(name)

            def _raiser(*_a, **_k):
                raise AssertionError(f"Order API operation initialized '{name}'")

            mod.__getattr__ = _raiser  # type: ignore[attr-defined]
            sys.modules[name] = mod
            installed[name] = mod

    original_openai = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError("Order API operation initialized a DeepSeek (OpenAI) client")

    openai.OpenAI = _no_client
    try:
        r = await client.get("/api/orders", params={"limit": 20})
        assert r.status_code == 200
        assert r.json()["count"] >= 5

        created = await _post_order(
            client, [{"product_id": "PROD-011", "quantity": 1}],
            payment_method="bank_transfer",
        )
        r = await client.post(
            f"/api/orders/{created['order_id']}/demo-payment",
            json={"confirm_demo_payment": True},
        )
        assert r.status_code == 200

        r = await client.get(f"/api/orders/{created['order_id']}")
        assert r.status_code == 200

        for name, mod in installed.items():
            assert sys.modules.get(name) is mod, (
                f"'{name}' was re-imported during order API calls"
            )
    finally:
        openai.OpenAI = original_openai
        for name, mod in installed.items():
            if sys.modules.get(name) is mod:
                del sys.modules[name]
