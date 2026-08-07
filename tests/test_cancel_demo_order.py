"""Demo customer cancellation tests (emergency demo repair).

Covers the deterministic cancellation service, the /cancel-demo API, the
Order Details Cancel Order UI contract, and the AI cancellation flow:

  1. paid processing + not_shipped -> cancelled + refunded (simulated);
  2. COD pending order -> cancelled without any refund;
  3. order-item stock is restored exactly once;
  4. a second cancellation is idempotent (same state, no double action);
  5. confirm=false changes nothing (confirmation_required only);
  6. shipped / delivered orders are rejected;
  7. unknown orders are rejected;
  8. malformed order ids are rejected;
  9. Cancel Order button visibility rules (UI contract);
 10. AI asks for confirmation before executing;
 11. AI Confirm executes the same deterministic service;
 12. AI COD cancellation executes without a refund;
 13. typo-tolerant cancellation messages ("cancelt my order");
 14. Thai cancellation messages (ยกเลิกคำสั่งซื้อ);
 15. AI rejects shipped orders honestly;
 16. AI reports already-cancelled orders idempotently;
 17. every customer-facing AI reply is Thai;
 18. ORDER_STATUS / PAYMENT_STATUS / SHIPMENT_STATUS produce different
     focused answers.

Isolation guarantees (same pattern as the other store test modules):
- The real data/orders.db is never written. store_db.STORE_DB_PATH and the
  orchestrator db path are redirected to a temporary file for the whole
  module.
- The temp database is initialized with the REAL production initialization
  functions (app.db.orders.init_database -> app.db.store.init_store_database),
  then Task orders are seeded directly.
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

import app.agents.orchestrator as orch
import app.db.store as store_db
from app.agents import set_db_path
from app.agents.session_manager import SessionManager
from app.db.orders import init_database
from app.api.server import app

# Real database path — captured BEFORE the store module path is redirected.
REAL_DB = str(store_db.STORE_DB_PATH)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

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


def _db_stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _assert_thai_dominant(text, label="response"):
    thai = len(_THAI_RE.findall(text or ""))
    eng = len(re.findall(r"[A-Za-z]", text or ""))
    assert thai > 0, f"{label} has no Thai: {text!r}"
    assert thai > eng, f"{label} is not Thai-dominant: {text!r}"


def _order_payload(items, **overrides):
    payload = dict(VALID_CUSTOMER)
    payload.update(overrides)
    payload["items"] = items
    return payload


async def _post_order(client, items, **overrides):
    resp = await client.post("/api/orders", json=_order_payload(items, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _cancel(client, order_id, confirm=True, reason="Customer changed their mind"):
    resp = await client.post(
        "/api/orders/" + order_id + "/cancel-demo",
        json={"confirm": confirm, "reason": reason},
    )
    return resp


async def _chat(client, message, session_id, order_id=None):
    payload = {"message": message, "session_id": session_id}
    if order_id is not None:
        payload["order_id"] = order_id
    return await client.post("/api/chat", json=payload)


def _stock_of(db_path, product_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT stock_quantity FROM products WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _order_row(db_path, order_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _seed_task_orders(db_path):
    """Seed cancellable + shipped fixtures (stock is NOT pre-reduced)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # ORD-8001 — paid / processing / not_shipped (AI ask + confirm flow).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, paid_at, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8001", "Somchai Test", "Linen Everyday Blouse, Classic Denim Jacket",
                "processing", "paid", "not_shipped", "", "", "2026-08-01", "2026-08-05",
                1980.0, "card", "2026-08-01T09:30:00", "2026-08-01T09:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8001", "PROD-001", "Linen Everyday Blouse", 1, 690.0, 690.0),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8001", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        # ORD-8002 — COD / processing / not_shipped (AI COD cancellation).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8002", "Somchai Test", "Classic Denim Jacket",
                "processing", "pending", "not_shipped", "", "", "2026-08-02", "2026-08-06",
                1290.0, "cash_on_delivery", "2026-08-02T10:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8002", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        # ORD-8003 — card pending / processing / not_shipped (status intents,
        # typo + Thai ask tests — never executed against).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8003", "Somchai Test", "Wireless Earbuds",
                "processing", "pending", "not_shipped", "", "", "2026-08-03", "2026-08-07",
                890.0, "bank_transfer", "2026-08-03T11:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8003", "PROD-003", "Wireless Earbuds", 1, 890.0, 890.0),
        )
        conn.commit()
    finally:
        conn.close()


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    """Temporary SQLite database, initialized with real production functions."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)  # orders schema + 5 sample orders + store schema + 12 products
    _seed_task_orders(path)

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        # Isolation proof: the real database must be completely untouched.
        real_after = _db_stat(REAL_DB)
        assert real_after == real_before, (
            f"REAL database was modified by cancellation tests: {REAL_DB} "
            f"before={real_before} after={real_after}"
        )
    finally:
        store_db.STORE_DB_PATH = original_path
        set_db_path(None)
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client(tmp_db):
    """Async HTTP client over the FastAPI app (no lifespan / no real DB init)."""
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Per-test isolation: fresh SessionManager + DeepSeek/LLM fully mocked."""
    orch._SESSION_STORE = SessionManager()
    monkeypatch.setattr(orch, "DEEPSEEK_ENABLED", False)
    import app.agents.llm_generator as gen

    monkeypatch.setattr(gen, "DEEPSEEK_ENABLED", False)

    import openai

    def _no_client(*_a, **_k):
        raise AssertionError(
            "External LLM (OpenAI) client was constructed during cancellation tests"
        )

    monkeypatch.setattr(openai, "OpenAI", _no_client)

    import app.api.server as server

    monkeypatch.setattr(server, "write_experiment_log", lambda *_a, **_k: None)
    yield


# ── 1. Paid order cancels + refunds ────────────────────────────────────


@pytest.mark.asyncio
async def test_paid_processing_not_shipped_cancels_and_refunds(client, tmp_db):
    """1. Paid processing + not_shipped -> cancelled + refunded (simulated)."""
    created = await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 2}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    paid = await client.post(f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True})
    assert paid.status_code == 200
    assert paid.json()["payment_status"] == "paid"

    resp = await _cancel(client, oid, confirm=True, reason="Customer changed their mind")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == oid
    assert body["order_status"] == "cancelled"
    assert body["payment_status"] == "refunded"
    assert body["shipment_status"] == "not_shipped"
    assert body["refund_type"] == "simulated"
    assert body["cancelled_at"]
    assert body["stock_restored"] is True
    assert body["action_executed"] is True
    assert body["cancellation_reason"] == "Customer changed their mind"

    row = _order_row(tmp_db, oid)
    assert row["order_status"] == "cancelled"
    assert row["payment_status"] == "refunded"
    assert row["shipment_status"] == "not_shipped"
    assert row["refund_type"] == "simulated"
    assert row["cancelled_at"] == body["cancelled_at"]


# ── 2. COD order cancels without refund ────────────────────────────────


@pytest.mark.asyncio
async def test_cod_pending_cancels_without_refund(client, tmp_db):
    """2. COD pending order -> cancelled, payment stays pending, no refund."""
    created = await _post_order(
        client, [{"product_id": "PROD-002", "quantity": 1}],
        payment_method="cash_on_delivery",
    )
    oid = created["order_id"]
    assert created["payment_status"] == "pending"

    resp = await _cancel(client, oid)
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_status"] == "cancelled"
    assert body["payment_status"] == "pending"   # no refund is required
    assert body["shipment_status"] == "not_shipped"
    assert body["refund_type"] is None
    assert body["action_executed"] is True


# ── 3. Stock restored exactly once ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stock_restored_exactly_once(client, tmp_db):
    """3. Cancel restores stock to the pre-order level; a repeat cancel
    never restores stock again."""
    before = {
        "PROD-001": _stock_of(tmp_db, "PROD-001"),
        "PROD-004": _stock_of(tmp_db, "PROD-004"),
    }
    created = await _post_order(
        client, [{"product_id": "PROD-001", "quantity": 2}, {"product_id": "PROD-004", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]

    after_create = {
        "PROD-001": _stock_of(tmp_db, "PROD-001"),
        "PROD-004": _stock_of(tmp_db, "PROD-004"),
    }
    assert after_create["PROD-001"] == before["PROD-001"] - 2
    assert after_create["PROD-004"] == before["PROD-004"] - 1

    first = await _cancel(client, oid)
    assert first.status_code == 200
    assert first.json()["stock_restored"] is True
    after_first = {
        "PROD-001": _stock_of(tmp_db, "PROD-001"),
        "PROD-004": _stock_of(tmp_db, "PROD-004"),
    }
    assert after_first == before, f"stock not fully restored: {after_first} != {before}"

    second = await _cancel(client, oid)
    assert second.status_code == 200
    assert second.json()["stock_restored"] is False
    after_second = {
        "PROD-001": _stock_of(tmp_db, "PROD-001"),
        "PROD-004": _stock_of(tmp_db, "PROD-004"),
    }
    assert after_second == after_first, "second cancellation must not restore stock again"


# ── 4. Second cancellation is idempotent ───────────────────────────────


@pytest.mark.asyncio
async def test_second_cancellation_is_idempotent(client, tmp_db):
    """4. Repeating the cancellation returns the same state without a
    second action (action_executed=false, stock_restored=false)."""
    created = await _post_order(
        client, [{"product_id": "PROD-005", "quantity": 1}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]
    await client.post(f"/api/orders/{oid}/demo-payment", json={"confirm_demo_payment": True})

    first = (await _cancel(client, oid)).json()
    second = (await _cancel(client, oid)).json()

    assert second["order_status"] == "cancelled"
    assert second["payment_status"] == "refunded"
    assert second["shipment_status"] == "not_shipped"
    assert second["cancelled_at"] == first["cancelled_at"]
    assert second["refund_type"] == "simulated"
    assert second["action_executed"] is False
    assert second["stock_restored"] is False


# ── 5. confirm=false changes nothing ───────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_false_changes_nothing(client, tmp_db):
    """5. POST cancel-demo without confirm=true only reports
    confirmation_required — the database is not modified."""
    created = await _post_order(
        client, [{"product_id": "PROD-006", "quantity": 3}],
        payment_method="bank_transfer",
    )
    oid = created["order_id"]
    stock_before = _stock_of(tmp_db, "PROD-006")
    row_before = _order_row(tmp_db, oid)

    resp = await _cancel(client, oid, confirm=False)
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmation_required"] is True
    assert body["action_executed"] is False
    assert body["order_id"] == oid

    row_after = _order_row(tmp_db, oid)
    assert row_after == row_before, "confirm=false must not modify the order row"
    assert _stock_of(tmp_db, "PROD-006") == stock_before, "confirm=false must not touch stock"


# ── 6. Shipped / delivered orders rejected ─────────────────────────────


@pytest.mark.asyncio
async def test_shipped_order_rejected(client, tmp_db):
    """6. A shipped (in-transit) order is rejected with 409."""
    resp = await _cancel(client, "ORD-1001", confirm=True)  # shipped / in_transit / paid
    assert resp.status_code == 409
    row = _order_row(tmp_db, "ORD-1001")
    assert row["order_status"] == "shipped"
    assert row["payment_status"] == "paid"
    assert row["cancelled_at"] is None


@pytest.mark.asyncio
async def test_delivered_order_rejected(client, tmp_db):
    """6b. A delivered order is rejected with 409."""
    resp = await _cancel(client, "ORD-1003", confirm=True)  # delivered / delivered / paid
    assert resp.status_code == 409
    row = _order_row(tmp_db, "ORD-1003")
    assert row["order_status"] == "delivered"
    assert row["cancelled_at"] is None


# ── 7-8. Unknown / malformed orders rejected ───────────────────────────


@pytest.mark.asyncio
async def test_unknown_order_rejected(client):
    """7. Unknown orders are rejected with 404 (honest error)."""
    resp = await _cancel(client, "ORD-9999", confirm=True)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invalid_order_id_rejected(client):
    """8. Malformed order ids are rejected with 400."""
    resp = await _cancel(client, "abc", confirm=True)
    assert resp.status_code == 400
    resp2 = await _cancel(client, "ORD-ABCD", confirm=True)
    assert resp2.status_code == 400


# ── 9. Cancel Order button visibility rules (UI contract) ──────────────


def test_cancel_button_visibility_rules():
    """9. The Order Details UI shows Cancel Order only for
    processing + not_shipped orders (never shipped/delivered/cancelled),
    and the confirmation flow posts confirm=true to /cancel-demo."""
    js = _read("app/static/js/orders.js")

    # The rule function exists and requires BOTH exact statuses.
    i = js.index("function canCancel(order)")
    body = js[i:js.index("var state = {", i)]
    assert 'os === "processing"' in body
    assert 'ss === "not_shipped"' in body
    assert 'return os === "processing" && ss === "not_shipped";' in body

    # The button + confirmation panel are rendered only behind canCancel.
    rd = js[js.index("function renderDetails(order)"):js.index("function confirmCancellation(order)")]
    assert "var cancelBtn = canCancel(order)" in rd
    assert "var cancelPanel = canCancel(order)" in rd
    assert 'id="odCancelBtn"' in rd
    assert 'id="odCancelPanel"' in rd
    assert 'id="odKeepBtn"' in rd
    assert 'id="odConfirmCancelBtn"' in rd

    # Confirmation executes the backend contract: confirm=true + reason.
    cc = js[js.index("function confirmCancellation(order)"):js.index("function confirmDemoPayment(order)")]
    assert "/cancel-demo" in cc
    assert "confirm: true" in cc
    assert "reason: reason" in cc
    # Buttons are disabled while the request is pending.
    assert "btn.disabled = true" in cc
    assert "is-loading" in cc
    # After success the modal re-renders from fresh SQLite facts.
    assert "renderDetails(full)" in cc
    assert "Simulated refund completed. No real money was transferred." in cc

    # Cancelled orders show the refund outcome note, not the cancel button.
    assert "order_status === \"cancelled\"" in rd
    assert "od-refund-note" in rd


# ── 10-11. AI asks confirmation, then executes ─────────────────────────


@pytest.mark.asyncio
async def test_ai_asks_confirmation_then_executes(client, tmp_db):
    """10-11. 'I want to cancel order ORD-8001' asks for confirmation in
    Thai; a follow-up 'Confirm' executes the same deterministic service
    (status -> cancelled, payment -> refunded) with action_executed=true."""
    r1 = await _chat(client, "I want to cancel order ORD-8001", "cc1")
    assert r1.status_code == 200
    ask = r1.json()
    assert ask["intent"] == "OUT_OF_SCOPE"
    assert ask["active_order_id"] == "ORD-8001"
    assert ask["action_executed"] is False
    assert ask["cancellation_created"] is False
    assert "ORD-8001" in ask["response"]
    assert "ต้องการยืนยันการยกเลิก" in ask["response"]
    assert "ชำระเงินแล้ว" in ask["response"]
    assert "ยังไม่ได้จัดส่ง" in ask["response"]
    _assert_thai_dominant(ask["response"], label="ask response")

    # Pending cancellation state is preserved with the collected order id.
    state = orch._SESSION_STORE.get_session("cc1")["state"]
    assert state["pending_intent"] == "CANCELLATION_CONFIRM"
    assert state["collected_slots"].get("order_id") == "ORD-8001"
    assert state["active_order_id"] == "ORD-8001"

    # Still not cancelled in SQLite after the ask.
    row = _order_row(tmp_db, "ORD-8001")
    assert row["order_status"] == "processing"
    assert row["payment_status"] == "paid"

    r2 = await _chat(client, "Confirm", "cc1")
    assert r2.status_code == 200
    done = r2.json()
    assert done["intent"] == "CANCELLATION_CONFIRM"
    assert done["action_executed"] is True
    assert done["cancellation_created"] is True
    assert done["escalation_created"] is False
    assert "เรียบร้อยแล้ว" in done["response"]
    assert "คืนเงินแล้ว (จำลอง)" in done["response"]
    _assert_thai_dominant(done["response"], label="confirm response")

    row = _order_row(tmp_db, "ORD-8001")
    assert row["order_status"] == "cancelled"
    assert row["payment_status"] == "refunded"
    assert row["shipment_status"] == "not_shipped"
    assert row["refund_type"] == "simulated"
    assert row["cancelled_at"]
    assert row["cancellation_reason"] == "Customer changed their mind"


# ── 12. AI COD cancellation without refund ─────────────────────────────


@pytest.mark.asyncio
async def test_ai_cod_cancels_without_refund(client, tmp_db):
    """12. AI COD cancellation executes without a refund (payment pending)."""
    r1 = await _chat(client, "ยกเลิกคำสั่งซื้อ ORD-8002", "cc2")
    assert r1.status_code == 200
    ask = r1.json()
    assert "เก็บเงินปลายทาง" in ask["response"]
    assert "ต้องการยืนยันการยกเลิก" in ask["response"]
    _assert_thai_dominant(ask["response"])

    r2 = await _chat(client, "ยืนยัน", "cc2")
    assert r2.status_code == 200
    done = r2.json()
    assert done["action_executed"] is True
    assert "เรียบร้อยแล้ว" in done["response"]
    _assert_thai_dominant(done["response"])

    row = _order_row(tmp_db, "ORD-8002")
    assert row["order_status"] == "cancelled"
    assert row["payment_status"] == "pending"  # no refund for COD
    assert row["refund_type"] is None


# ── 13-14. Typo-tolerant + Thai cancellation requests ──────────────────


@pytest.mark.asyncio
async def test_ai_typo_cancelt_recognized(client, tmp_db):
    """13. 'cancelt my order' (typo) is recognized and asks for confirmation."""
    # Establish the active order, then send the typo'd cancellation.
    await _chat(client, "Where is ORD-8003?", "cc3")
    r = await _chat(client, "cancelt my order", "cc3")
    assert r.status_code == 200
    data = r.json()
    assert data["active_order_id"] == "ORD-8003"
    assert "ORD-8003" in data["response"]
    assert "ต้องการยืนยันการยกเลิก" in data["response"]
    assert data["action_executed"] is False
    _assert_thai_dominant(data["response"])


@pytest.mark.asyncio
async def test_ai_thai_cancel_recognized(client, tmp_db):
    """14. 'ยกเลิกคำสั่งซื้อ' is recognized and asks for confirmation."""
    await _chat(client, "Where is ORD-8003?", "cc4")
    r = await _chat(client, "ยกเลิกคำสั่งซื้อ", "cc4")
    assert r.status_code == 200
    data = r.json()
    assert "ORD-8003" in data["response"]
    assert "ต้องการยืนยันการยกเลิก" in data["response"]
    assert data["action_executed"] is False
    _assert_thai_dominant(data["response"])


# ── 15-16. AI rejects shipped / reports already-cancelled ──────────────


@pytest.mark.asyncio
async def test_ai_rejects_shipped_order(client, tmp_db):
    """15. AI honestly rejects a shipped order and never executes."""
    r = await _chat(client, "I want to cancel order ORD-1001", "cc5")  # shipped/in_transit
    assert r.status_code == 200
    data = r.json()
    assert "ไม่สามารถยกเลิก" in data["response"]
    assert data["action_executed"] is False
    assert data["cancellation_created"] is False
    _assert_thai_dominant(data["response"])
    row = _order_row(tmp_db, "ORD-1001")
    assert row["order_status"] == "shipped"
    assert row["cancelled_at"] is None


@pytest.mark.asyncio
async def test_ai_reports_already_cancelled(client, tmp_db):
    """16. A cancelled order is reported idempotently (no second action)."""
    # ORD-8001 is already cancelled by test 11 (same module DB).
    r = await _chat(client, "cancel ORD-8001", "cc6")
    assert r.status_code == 200
    data = r.json()
    assert "ถูกยกเลิกไปแล้ว" in data["response"]
    assert data["action_executed"] is False
    assert data["cancellation_created"] is False
    _assert_thai_dominant(data["response"])


# ── 17. All AI replies Thai ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_replies_all_thai(client, tmp_db):
    """17. Every AI reply in the cancellation flow is Thai-dominant."""
    for msg in (
        "cancel my order ORD-8003",
        "I want to cancel order ORD-8003",
        "ยกเลิกคำสั่งซื้อ ORD-8003",
        "cancelt ORD-8003",
    ):
        r = await _chat(client, msg, "cc7-" + str(len(msg)))
        assert r.status_code == 200
        data = r.json()
        _assert_thai_dominant(data["response"], label=f"response for {msg!r}")


# ── 18. Three status intents produce different focused answers ─────────


@pytest.mark.asyncio
async def test_three_status_intents_produce_different_focused_answers(client, tmp_db):
    """18. ORDER_STATUS / PAYMENT_STATUS / SHIPMENT_STATUS answers differ
    and each focuses on its own dimension (ORDER-8003, never cancelled)."""
    r1 = await _chat(client, "Where is order ORD-8003?", "cc8")
    r2 = await _chat(client, "Has order ORD-8003 been paid?", "cc8")
    r3 = await _chat(client, "Has order ORD-8003 been shipped?", "cc8")
    for r in (r1, r2, r3):
        assert r.status_code == 200

    d1, d2, d3 = r1.json(), r2.json(), r3.json()
    assert d1["intent"] == "ORDER_STATUS"
    assert d2["intent"] == "PAYMENT_STATUS"
    assert d3["intent"] == "SHIPMENT_STATUS"

    # Focus phrases.
    assert "อยู่ระหว่างดำเนินการ" in d1["response"]   # order stage focus
    assert "ยังไม่ได้รับการยืนยันการชำระเงิน" in d2["response"]  # payment focus
    assert "ยังไม่ได้จัดส่ง" in d3["response"]        # shipment focus

    # The three answers are genuinely different (not the same paragraph).
    assert d1["response"] != d2["response"]
    assert d1["response"] != d3["response"]
    assert d2["response"] != d3["response"]
    for d in (d1, d2, d3):
        _assert_thai_dominant(d["response"])


# ── Extra: service-level direct checks ─────────────────────────────────


def test_service_rejects_malformed_id(tmp_db):
    """The service itself rejects malformed ids before touching SQLite."""
    with pytest.raises(ValueError) as exc:
        store_db.cancel_demo_order(tmp_db, "not-an-order")
    assert str(exc.value) == "INVALID_ORDER_ID"


def test_service_unknown_order(tmp_db):
    """The service itself raises ORDER_NOT_FOUND for unknown orders."""
    with pytest.raises(ValueError) as exc:
        store_db.cancel_demo_order(tmp_db, "ORD-9999")
    assert str(exc.value) == "ORDER_NOT_FOUND"
