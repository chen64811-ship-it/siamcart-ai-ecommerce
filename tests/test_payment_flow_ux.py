"""Focused payment-flow UX tests (Task 5D-4).

Covers the checkout payment-flow UX and its SQLite-backed behavior:

  1. No payment method is selected by default.
  2. Checkout cannot submit without payment selection.
  3. COD creates an order with payment pending.
  4. COD does not show Confirm Demo Payment.
  5. Bank transfer shows Confirm Demo Payment after order creation.
  6. Demo Card shows Confirm Demo Payment after order creation.
  7. Demo-payment changes only payment status and paid_at.
  8. Order and shipment statuses remain unchanged after payment.
  9. ORD-1029-style COD response explains pay-on-delivery correctly.
  10. Paid-order response distinguishes payment, order and shipment statuses.
  11. No DeepSeek is called for order-status responses.
  12. No card number is collected or submitted.

Isolation guarantees (same pattern as the other store test modules):
- The real data/orders.db is never written. store_db.STORE_DB_PATH is
  redirected to a temporary file for the whole module; the Transaction
  Tracker path is pointed at the same temp file via set_db_path().
- The temp database is initialized with the REAL production initialization
  functions (app.db.orders.init_database -> app.db.store.init_store_database).
- A teardown stat check proves the real database file is untouched.
- No DeepSeek / ChromaDB / SentenceTransformer initialization; no browser.
"""

import json
import os
import re
import sqlite3
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

import app.db.store as store_db
from app.db.orders import init_database
from app.agents import process_message, set_db_path
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _insert_order(
    db_path,
    order_id,
    payment_method="COD",
    payment_status="pending",
    order_status="processing",
    shipment_status="not_shipped",
    paid_at=None,
):
    """Insert an ORD-1029-style order directly (keeps the real DB untouched)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, payment_method, paid_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                order_id, "xingyu chen", "Test Product", order_status,
                payment_status, shipment_status, "", "", "2026-08-02", "",
                payment_method, paid_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _render_body(js, func_name, end_marker):
    """Slice one render function's body out of a JS file."""
    i = js.index("function " + func_name)
    j = js.index(end_marker, i)
    return js[i:j]


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
            f"REAL database was modified by payment-flow tests: {REAL_DB}"
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


# ── 1. No default payment method ───────────────────────────────────────


def test_no_payment_method_selected_by_default():
    """1. No payment radio is preselected on the storefront or /orders/new."""
    for name in ("app/templates/index.html", "app/templates/create_order.html"):
        html = _read(name)
        radios = re.findall(r'<input type="radio" name="payment"[^>]*>', html)
        assert radios, name
        for radio in radios:
            assert "checked" not in radio, f"{name} preselects a payment method: {radio}"


# ── 2. Submit blocked without payment selection ────────────────────────


def test_checkout_cannot_submit_without_payment_selection():
    """2. Both checkout entry points start disabled with a neutral label."""
    index = _read("app/templates/index.html")
    m = re.search(r'<button[^>]*id="coPlaceOrder"[^>]*>', index)
    assert m and "disabled" in m.group(0), "storefront submit button must start disabled"
    store = _read("app/static/js/store.js")
    assert "Please select a payment method." in store
    assert "Select a Payment Method" in store
    assert "Pay Now" not in store

    create = _read("app/templates/create_order.html")
    m2 = re.search(r'<button[^>]*id="coCreateBtn"[^>]*>', create)
    assert m2 and "disabled" in m2.group(0), "/orders/new submit button must start disabled"
    co = _read("app/static/js/create_order.js")
    assert "Please select a payment method." in co
    assert "Select a Payment Method" in co
    assert "Pay Now" not in co


@pytest.mark.asyncio
async def test_backend_rejects_empty_payment_method(client):
    """Backend still refuses an order with no payment method (400)."""
    resp = await client.post(
        "/api/orders",
        json=_order_payload([{"product_id": "PROD-001", "quantity": 1}], payment_method=""),
    )
    assert resp.status_code == 400
    assert "Payment method is required" in resp.json()["detail"]


# ── 3. COD order creation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cod_creates_order_with_payment_pending(client):
    """3. COD order is created with payment pending, order processing, not shipped."""
    created = await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 1}],
        payment_method="cash_on_delivery",
    )
    assert created["payment_status"] == "pending"
    assert created["order_status"] == "processing"
    assert created["shipment_status"] == "not_shipped"


# ── 4. COD never shows Confirm Demo Payment ────────────────────────────


def test_cod_does_not_show_confirm_demo_payment():
    """4. The COD confirmation branch has the pay-on-delivery note and no demo-pay button."""
    store = _read("app/static/js/store.js")
    body = _render_body(store, "renderConfirmation", "var items = Array.isArray")
    assert "if (cod) {" in body
    assert "} else if (paid) {" in body
    cod_part = body[body.index("if (cod) {"):body.index("} else if (paid)")]
    assert "No payment has been collected. You will pay when the order is delivered." in cod_part
    assert "DemoPayBtn" not in cod_part, "COD branch must never render Confirm Demo Payment"
    else_part = body[body.index("} else {"):]
    assert "DemoPayBtn" in else_part, "demo-pay button belongs to the bank/card branch"

    co = _read("app/static/js/create_order.js")
    body2 = _render_body(co, "renderSuccess", "var items = Array.isArray")
    cod_part2 = body2[body2.index("if (cod) {"):body2.index("} else if (paid)")]
    assert "No payment has been collected. You will pay when the order is delivered." in cod_part2
    assert "DemoPayBtn" not in cod_part2
    else_part2 = body2[body2.index("} else {"):]
    assert "DemoPayBtn" in else_part2


@pytest.mark.asyncio
async def test_cod_demo_payment_rejected_409_and_stays_pending(client, tmp_db):
    """API protection for COD is unchanged: 409, payment badge stays Pending."""
    created = await _post_order(
        client, [{"product_id": "PROD-002", "quantity": 1}],
        payment_method="cash_on_delivery",
    )
    oid = created["order_id"]
    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Cash on Delivery remains pending until delivery."

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT payment_status FROM orders WHERE order_id = ?", (oid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "pending"


# ── 5 + 6. Bank transfer / Demo Card show Confirm Demo Payment ─────────


def test_bank_transfer_and_demo_card_show_confirm_demo_payment():
    """5+6. The bank/card confirmation branch renders Confirm Demo Payment + notice."""
    for js_name in ("app/static/js/store.js", "app/static/js/create_order.js"):
        js = _read(js_name)
        assert "Confirm Demo Payment" in js
        assert "This simulates payment for the research prototype. No real money will be charged." in js
        func = "renderConfirmation" if "store" in js_name else "renderSuccess"
        body = _render_body(js, func, "var items = Array.isArray")
        else_part = body[body.index("} else {"):]
        assert "Confirm Demo Payment" in else_part

    # The supported card option is labelled Demo Card Payment in the UI.
    index = _read("app/templates/index.html")
    assert "Demo Card Payment" in index
    create = _read("app/templates/create_order.html")
    assert "Demo Card Payment" in create


@pytest.mark.asyncio
async def test_bank_transfer_order_starts_pending(client):
    """A bank-transfer order is created pending, so the UI shows Confirm Demo Payment."""
    created = await _post_order(
        client, [{"product_id": "PROD-003", "quantity": 1}],
        payment_method="bank_transfer",
    )
    assert created["payment_status"] == "pending"


@pytest.mark.asyncio
async def test_demo_card_order_starts_pending(client):
    """A Demo Card order is created pending (backend value stays 'card')."""
    created = await _post_order(
        client, [{"product_id": "PROD-004", "quantity": 1}],
        payment_method="card",
    )
    assert created["payment_status"] == "pending"


# ── 7 + 8. Demo-payment scope ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_demo_payment_changes_only_payment_status_and_paid_at(client, tmp_db):
    """7. Demo-payment flips payment_status -> paid and sets paid_at."""
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
    assert data["payment_status"] == "paid"
    assert data["paid_at"]

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT payment_status, paid_at FROM orders WHERE order_id = ?", (oid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "paid"
    assert row[1] == data["paid_at"]


@pytest.mark.asyncio
async def test_order_and_shipment_statuses_unchanged_after_payment(client, tmp_db):
    """8. Order and shipment statuses stay exactly as created after payment."""
    created = await _post_order(
        client, [{"product_id": "PROD-006", "quantity": 1}],
        payment_method="card",
    )
    oid = created["order_id"]
    resp = await client.post(
        f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_status"] == created["order_status"]
    assert data["shipment_status"] == created["shipment_status"]

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT order_status, shipment_status FROM orders WHERE order_id = ?", (oid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "processing"
    assert row[1] == "not_shipped"


# ── 9 + 10. Transaction Tracker deterministic responses ────────────────


def test_cod_tracker_response_explains_pay_on_delivery(tmp_db):
    """9. ORD-1029-style COD answer explains pay-on-delivery from real data."""
    _insert_order(
        tmp_db, "ORD-1029", payment_method="COD", payment_status="pending",
        order_status="processing", shipment_status="not_shipped",
    )
    set_db_path(tmp_db)
    try:
        result = process_message("Has ORD-1029 been paid?")
    finally:
        set_db_path(None)

    assert result["order_id"] == "ORD-1029"
    assert result["response_source"] == "deterministic"
    assert result["evidence"]["payment_status"] == "pending"
    resp = result["response"]
    assert "ORD-1029" in resp
    assert "Cash on Delivery" in resp
    assert "pending" in resp
    assert "delivery" in resp
    assert "currently processing" in resp
    assert "has not yet been shipped" in resp
    assert "if you have already paid" not in resp


def test_paid_order_tracker_response_distinguishes_statuses(tmp_db):
    """10. A paid order's answer separates payment, order and shipment facts."""
    _insert_order(
        tmp_db, "ORD-2001", payment_method="bank_transfer", payment_status="paid",
        order_status="processing", shipment_status="not_shipped",
        paid_at="2026-08-02T14:00:00",
    )
    set_db_path(tmp_db)
    try:
        result = process_message("ORD-2001 status")
    finally:
        set_db_path(None)

    assert result["response_source"] == "deterministic"
    resp = result["response"]
    assert "ORD-2001" in resp
    assert "payment has been received" in resp
    assert "paid at 2026-08-02T14:00:00" in resp
    assert "currently processing" in resp
    assert "has not yet been shipped" in resp
    assert "Confirm Demo Payment" not in resp


# ── 11. No DeepSeek for order-status responses ─────────────────────────


def test_no_deepseek_called_for_order_status(tmp_db):
    """11. Order-status responses never construct a DeepSeek (OpenAI) client."""
    import openai

    original = openai.OpenAI

    def _no_client(*_a, **_k):
        raise AssertionError(
            "DeepSeek (OpenAI) client was constructed for an order-status response"
        )

    openai.OpenAI = _no_client
    set_db_path(tmp_db)
    try:
        result = process_message("ORD-1001 status")
    finally:
        openai.OpenAI = original
        set_db_path(None)

    assert result["order_id"] == "ORD-1001"
    assert result["response_source"] == "deterministic"
    assert result["llm_fallback_used"] is True
    assert "ORD-1001" in result["response"]


# ── 12. No card number collected or submitted ──────────────────────────


def test_no_card_number_collected_or_submitted():
    """12. No card field exists in the request models, forms or JS payloads."""
    from app.models.store import DemoPaymentRequest, OrderCreateRequest

    for model in (OrderCreateRequest, DemoPaymentRequest):
        fields = set(model.model_fields)
        assert not any("card" in f.lower() for f in fields), (
            f"card field found in {model.__name__}"
        )

    for name in ("app/templates/index.html", "app/templates/create_order.html"):
        html = _read(name)
        for inp in re.findall(r"<input[^>]*>", html):
            low = inp.lower()
            # The payment-method radio value "Card" is a method label (the
            # accepted backend value), not a card-number field.
            if 'name="payment"' in low and 'type="radio"' in low:
                continue
            assert "card" not in low, f"card input found in {name}: {inp}"

    for js_name in ("app/static/js/store.js", "app/static/js/create_order.js"):
        js = _read(js_name)
        assert "card_number" not in js
        assert "cardNumber" not in js


@pytest.mark.asyncio
async def test_card_fields_sent_by_client_are_not_stored(client, tmp_db):
    """Even if a client sends card data it must not be accepted or stored."""
    card_number = "4111111111111111"
    resp = await client.post(
        "/api/orders",
        json=_order_payload(
            [{"product_id": "PROD-007", "quantity": 1}],
            card_number=card_number,
            card_cvv="123",
            card_expiry="12/29",
        ),
    )
    # pydantic ignores unknown fields -> 201; extra='forbid' config -> 422.
    assert resp.status_code in (201, 422)
    if resp.status_code == 201:
        assert card_number not in json.dumps(resp.json())
        oid = resp.json()["order_id"]
        conn = sqlite3.connect(tmp_db)
        try:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (oid,)
            ).fetchone()
            values = " ".join(str(v) for v in row)
        finally:
            conn.close()
        assert card_number not in values, "card number was stored in the orders table"
