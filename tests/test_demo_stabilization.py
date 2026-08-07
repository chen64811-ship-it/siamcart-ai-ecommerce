"""Demo stabilization pass — focused tests (mentor demo readiness).

Covers exactly the 17 stabilization requirements:

  1. Explicit order establishes active_order_id.
  2. Payment follow-up reuses active order.
  3. Shipment follow-up reuses active order.
  4. Purchased-items follow-up reuses active order.
  5. Total follow-up reuses active order.
  6. Greeting does not clear active order.
  7. Different explicit order replaces active order.
  8. Pending refund/cancel workflow survives bare order ID.
  9. ORDER_STATUS / PAYMENT_STATUS / SHIPMENT_STATUS produce meaningfully
     different responses.
 10. Cancel Order button visibility rule.
 11. Cancelled order removes Cancel Order button.
 12. AI cancellation asks for confirmation first.
 13. Confirm executes cancellation.
 14. Paid cancellation -> Refunded / Cancelled / Not Shipped.
 15. COD cancellation -> Cancelled without fake refund.
 16. All customer-facing responses are Thai.
 17. No HTTP 500 if policy/LLM is unavailable.

Isolation (same pattern as the other store test modules):
- The real data/orders.db is never written. store_db.STORE_DB_PATH and the
  orchestrator db path are redirected to a temporary file for the module.
- The temp database is initialized with the REAL production initialization
  functions, then stabilization fixtures are seeded directly.
- A teardown stat check proves the real database file is untouched.
- DeepSeek / ChromaDB are fully mocked or bypassed; no browser.
"""

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
    """Seed stabilization fixtures (processing / COD / cancelled)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # ORD-8201 — paid / processing / not_shipped (paid cancel flow).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, paid_at, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8201", "Somchai Test", "Linen Everyday Blouse",
                "processing", "paid", "not_shipped", "", "", "2026-08-01", "2026-08-05",
                1980.0, "bank_transfer", "2026-08-01T09:30:00", "2026-08-01T09:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8201", "PROD-001", "Linen Everyday Blouse", 1, 690.0, 690.0),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8201", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        # ORD-8202 — COD / processing / not_shipped (COD cancel flow).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8202", "Somchai Test", "Classic Denim Jacket",
                "processing", "pending", "not_shipped", "", "", "2026-08-02", "2026-08-06",
                1290.0, "cash_on_delivery", "2026-08-02T10:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price, line_total) "
            "VALUES (?,?,?,?,?,?)",
            ("ORD-8202", "PROD-002", "Classic Denim Jacket", 1, 1290.0, 1290.0),
        )
        # ORD-8203 — already cancelled (idempotent already-cancelled reply).
        conn.execute(
            "INSERT INTO orders ("
            "order_id, customer_name, product_name, order_status, payment_status,"
            "shipment_status, tracking_number, shipping_provider, purchase_date,"
            "estimated_delivery_date, total_amount, payment_method, created_at,"
            "cancelled_at, cancellation_reason, refund_type"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ORD-8203", "Somchai Test", "Wireless Earbuds",
                "cancelled", "refunded", "not_shipped", "", "", "2026-08-03", "2026-08-07",
                890.0, "bank_transfer", "2026-08-03T11:00:00",
                "2026-08-03T12:00:00", "Customer changed their mind", "simulated",
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def _chat(client, message, session_id, order_id=None):
    payload = {"message": message, "session_id": session_id}
    if order_id is not None:
        payload["order_id"] = order_id
    return await client.post("/api/chat", json=payload)


async def _create_paid_order(client):
    """Create a fresh paid / processing / not_shipped demo order via the
    real API (each cancellation test gets its own disposable order, so the
    tests stay independent of each other)."""
    resp = await client.post(
        "/api/orders",
        json={
            **VALID_CUSTOMER,
            "payment_method": "bank_transfer",
            "items": [{"product_id": "PROD-001", "quantity": 1}],
        },
    )
    assert resp.status_code == 201, resp.text
    oid = resp.json()["order_id"]
    paid = await client.post(
        f"/api/orders/{oid}/demo-payment",
        json={"confirm_demo_payment": True},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["payment_status"] == "paid"
    return oid


async def _create_cod_order(client):
    """Create a fresh COD / pending / processing / not_shipped order."""
    resp = await client.post(
        "/api/orders",
        json={
            **VALID_CUSTOMER,
            "payment_method": "cash_on_delivery",
            "items": [{"product_id": "PROD-002", "quantity": 1}],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["order_id"]


# ── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)
    _seed_task_orders(path)

    real_before = _db_stat(REAL_DB)

    original_path = store_db.STORE_DB_PATH
    store_db.STORE_DB_PATH = path
    set_db_path(path)
    try:
        yield path
        real_after = _db_stat(REAL_DB)
        assert real_after == real_before, (
            f"REAL database was modified by stabilization tests: {REAL_DB} "
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
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    orch._SESSION_STORE = SessionManager()
    monkeypatch.setattr(orch, "DEEPSEEK_ENABLED", False)
    import app.agents.llm_generator as gen

    monkeypatch.setattr(gen, "DEEPSEEK_ENABLED", False)
    import app.agents.policy_evaluator as pe

    monkeypatch.setattr(pe, "DEEPSEEK_ENABLED", False)

    import openai

    def _no_client(*_a, **_k):
        raise AssertionError(
            "External LLM (OpenAI) client was constructed during stabilization tests"
        )

    monkeypatch.setattr(openai, "OpenAI", _no_client)

    import app.api.server as server

    monkeypatch.setattr(server, "write_experiment_log", lambda *_a, **_k: None)
    yield


# ── 1-5. Multi-turn order context ──────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_order_establishes_active_order(client):
    """1. An explicit ORD-XXXX sets active_order_id on the session."""
    r = await _chat(client, "Where is order ORD-8201?", "ctx-1")
    assert r.status_code == 200
    data = r.json()
    assert data["active_order_id"] == "ORD-8201"
    assert data["order_id"] == "ORD-8201"
    assert data["intent"] == "ORDER_STATUS"


@pytest.mark.asyncio
async def test_payment_followup_reuses_active_order(client):
    """2. 'has it been paid?' reuses the active order (no re-ask)."""
    await _chat(client, "Where is order ORD-8201?", "ctx-2")
    r = await _chat(client, "has it been paid?", "ctx-2")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "PAYMENT_STATUS"
    assert data["order_id"] == "ORD-8201"
    assert data["active_order_id"] == "ORD-8201"
    assert "ORD-8201" in data["response"]
    assert "ชำระเงินแล้ว" in data["response"]


@pytest.mark.asyncio
async def test_shipment_followup_reuses_active_order(client):
    """3. 'has it been shipped?' reuses the active order."""
    await _chat(client, "Where is order ORD-8201?", "ctx-3")
    r = await _chat(client, "has it been shipped?", "ctx-3")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "SHIPMENT_STATUS"
    assert data["order_id"] == "ORD-8201"
    assert data["active_order_id"] == "ORD-8201"
    assert "ORD-8201" in data["response"]
    assert "ยังไม่ได้จัดส่ง" in data["response"]


@pytest.mark.asyncio
async def test_purchased_items_followup_reuses_active_order(client):
    """4. 'what did I buy?' reuses the active order and lists products."""
    await _chat(client, "Where is order ORD-8201?", "ctx-4")
    r = await _chat(client, "what did I buy?", "ctx-4")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "PURCHASED_ITEMS"
    assert data["order_id"] == "ORD-8201"
    assert data["active_order_id"] == "ORD-8201"
    assert "Linen Everyday Blouse" in data["response"]
    assert "Classic Denim Jacket" in data["response"]


@pytest.mark.asyncio
async def test_total_followup_reuses_active_order(client):
    """5. 'what is the total?' reuses the active order and answers total."""
    await _chat(client, "Where is order ORD-8201?", "ctx-5")
    r = await _chat(client, "what is the total?", "ctx-5")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "ORDER_TOTAL"
    assert data["order_id"] == "ORD-8201"
    assert data["active_order_id"] == "ORD-8201"
    assert "ยอดรวม" in data["response"]
    assert "฿1,980" in data["response"] or "฿1980" in data["response"]


@pytest.mark.asyncio
async def test_thai_followups_reuse_active_order(client):
    """Thai follow-ups (จ่ายเงินแล้วหรือยัง / ส่งของหรือยัง / คำสั่งซื้อนี้ /
    ซื้ออะไรบ้าง / ยอดรวมเท่าไร) all reuse the active order."""
    await _chat(client, "Where is order ORD-8201?", "ctx-thai")
    cases = [
        ("จ่ายเงินแล้วหรือยัง", "PAYMENT_STATUS", "ชำระเงินแล้ว"),
        ("ส่งของหรือยัง", "SHIPMENT_STATUS", "ยังไม่ได้จัดส่ง"),
        ("ตอนนี้สถานะอะไร", "ORDER_STATUS", "ORD-8201"),
        ("ซื้ออะไรบ้าง", "PURCHASED_ITEMS", "Linen Everyday Blouse"),
        ("ยอดรวมเท่าไร", "ORDER_TOTAL", "ยอดรวม"),
        ("คำสั่งซื้อนี้", "ORDER_STATUS", "ORD-8201"),
    ]
    for msg, expected_intent, marker in cases:
        r = await _chat(client, msg, "ctx-thai")
        assert r.status_code == 200, msg
        data = r.json()
        assert data["intent"] == expected_intent, f"{msg} -> {data['intent']}"
        assert data["order_id"] == "ORD-8201", f"{msg} lost order context"
        assert marker in data["response"], f"{msg} missing marker {marker!r}"
        _assert_thai_dominant(data["response"], label=msg)


# ── 6-8. Context stability rules ───────────────────────────────────────


@pytest.mark.asyncio
async def test_greeting_does_not_clear_active_order(client):
    """6. A greeting never clears active_order_id."""
    await _chat(client, "Where is order ORD-8201?", "ctx-6")
    r = await _chat(client, "สวัสดี", "ctx-6")
    assert r.status_code == 200
    data = r.json()
    assert data["active_order_id"] == "ORD-8201"
    # The order still answers follow-ups after the greeting.
    r2 = await _chat(client, "has it been paid?", "ctx-6")
    assert r2.json()["order_id"] == "ORD-8201"


@pytest.mark.asyncio
async def test_new_explicit_order_replaces_active_order(client):
    """7. A different explicit ORD-XXXX replaces the old active order."""
    await _chat(client, "Where is order ORD-8201?", "ctx-7")
    r = await _chat(client, "Where is order ORD-8202?", "ctx-7")
    assert r.status_code == 200
    data = r.json()
    assert data["active_order_id"] == "ORD-8202"
    assert data["order_id"] == "ORD-8202"
    # Follow-up now targets the NEW order.
    r2 = await _chat(client, "has it been paid?", "ctx-7")
    assert r2.json()["order_id"] == "ORD-8202"


@pytest.mark.asyncio
async def test_pending_cancel_survives_bare_order_id(client, tmp_db):
    """8a. A bare ORD-XXXX during a pending cancellation continues the
    cancellation flow (it does not collapse to a generic status answer)."""
    r1 = await _chat(client, "I want to cancel order ORD-8201", "ctx-8a")
    assert r1.json()["action_executed"] is False
    assert "ต้องการยืนยันการยกเลิก" in r1.json()["response"]

    r2 = await _chat(client, "ORD-8201", "ctx-8a")
    assert r2.status_code == 200
    data = r2.json()
    assert data["intent"] == "CANCELLATION_CONFIRM"
    assert data["action_executed"] is False
    assert "ORD-8201" in data["response"]
    # Still not cancelled — the flow only executes on an explicit confirm.
    assert _order_row(tmp_db, "ORD-8201")["order_status"] == "processing"


@pytest.mark.asyncio
async def test_pending_refund_survives_bare_order_id(client):
    """8b. A bare ORD-XXXX during a pending refund request continues the
    slot-filling flow (asks for the reason)."""
    r1 = await _chat(client, "I want a refund for ORD-8202", "ctx-8b")
    assert r1.json()["requires_clarification"] is True

    r2 = await _chat(client, "ORD-8202", "ctx-8b")
    assert r2.status_code == 200
    data = r2.json()
    assert data["intent"] == "RETURN_REFUND"
    assert data["requires_clarification"] is True
    assert "เหตุผล" in data["response"]


# ── 9. Intent-specific transaction responses ───────────────────────────


@pytest.mark.asyncio
async def test_transaction_responses_are_intent_specific(client):
    """9. ORDER_STATUS / PAYMENT_STATUS / SHIPMENT_STATUS produce
    meaningfully different, focused answers for the same order."""
    await _chat(client, "Where is order ORD-8201?", "ctx-9")

    r_status = (await _chat(client, "ตอนนี้สถานะอะไร", "ctx-9")).json()
    r_pay = (await _chat(client, "has it been paid?", "ctx-9")).json()
    r_ship = (await _chat(client, "has it been shipped?", "ctx-9")).json()

    assert r_status["intent"] == "ORDER_STATUS"
    assert r_pay["intent"] == "PAYMENT_STATUS"
    assert r_ship["intent"] == "SHIPMENT_STATUS"

    s, p, sh = r_status["response"], r_pay["response"], r_ship["response"]
    # Pairwise different (no same full paragraph for every question).
    assert s != p and p != sh and s != sh
    # Each focuses on its own facts.
    assert "อยู่ระหว่างดำเนินการ" in s        # order stage (processing)
    assert "ชำระเงินแล้ว" in p                # payment fact
    assert "ยังไม่ได้จัดส่ง" in sh             # parcel fact
    # Payment answer carries paid_at; shipment answer is about the parcel.
    assert "ชำระเมื่อ" in p
    assert "หมายเลขพัสดุ" not in p or "ยังไม่มีหมายเลขพัสดุ" in p
    for label, text in (("status", s), ("pay", p), ("ship", sh)):
        _assert_thai_dominant(text, label=label)


# ── 10-11. Cancel Order button UI contract ─────────────────────────────


def test_cancel_button_visibility_rule():
    """10. Order Details shows Cancel Order only for processing +
    not_shipped orders."""
    js = _read("app/static/js/orders.js")
    i = js.index("function canCancel(order)")
    body = js[i:js.index("var state = {", i)]
    assert 'os === "processing"' in body
    assert 'ss === "not_shipped"' in body
    assert 'return os === "processing" && ss === "not_shipped";' in body

    rd = js[js.index("function renderDetails(order)"):js.index("function confirmCancellation(order)")]
    assert "var cancelBtn = canCancel(order)" in rd
    assert 'id="odCancelBtn"' in rd


def test_cancelled_order_removes_cancel_button():
    """11. A cancelled order renders the refund outcome note and never the
    Cancel Order button (canCancel returns false when cancelled)."""
    js = _read("app/static/js/orders.js")
    rd = js[js.index("function renderDetails(order)"):js.index("function confirmCancellation(order)")]
    assert 'order_status === "cancelled"' in rd
    assert "od-refund-note" in rd
    # The button only exists inside the canCancel(order) ternary.
    assert 'var cancelBtn = canCancel(order)' in rd


# ── 12-15. AI cancellation flow ────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_cancellation_asks_confirmation_first(client, tmp_db):
    """12. 'I want to cancel order <ID>' only asks for confirmation —
    nothing is executed yet."""
    oid = await _create_paid_order(client)
    r = await _chat(client, f"I want to cancel order {oid}", "cancel-1")
    assert r.status_code == 200
    data = r.json()
    assert data["action_executed"] is False
    assert data["cancellation_created"] is False
    assert oid in data["response"]
    assert "ต้องการยืนยันการยกเลิก" in data["response"]
    assert "ชำระเงินแล้ว" in data["response"]   # paid explanation
    _assert_thai_dominant(data["response"])
    assert _order_row(tmp_db, oid)["order_status"] == "processing"


@pytest.mark.asyncio
async def test_confirm_executes_cancellation(client, tmp_db):
    """13. A follow-up 'Confirm' executes the deterministic service."""
    oid = await _create_paid_order(client)
    await _chat(client, f"I want to cancel order {oid}", "cancel-2")
    r = await _chat(client, "Confirm", "cancel-2")
    assert r.status_code == 200
    data = r.json()
    assert data["action_executed"] is True
    assert data["cancellation_created"] is True
    assert "เรียบร้อยแล้ว" in data["response"]
    _assert_thai_dominant(data["response"])
    assert _order_row(tmp_db, oid)["order_status"] == "cancelled"


@pytest.mark.asyncio
async def test_paid_cancellation_ends_refunded_cancelled_notshipped(client, tmp_db):
    """14. Paid cancellation -> Payment Refunded / Order Cancelled /
    Shipment Not Shipped (simulated refund, no real money)."""
    oid = await _create_paid_order(client)
    await _chat(client, f"I want to cancel order {oid}", "cancel-3")
    r = await _chat(client, "ยืนยัน", "cancel-3")
    assert r.status_code == 200
    data = r.json()
    assert data["action_executed"] is True

    row = _order_row(tmp_db, oid)
    assert row["payment_status"] == "refunded"
    assert row["order_status"] == "cancelled"
    assert row["shipment_status"] == "not_shipped"
    assert row["refund_type"] == "simulated"
    assert row["cancelled_at"]
    assert row["cancellation_reason"] == "Customer changed their mind"
    # The response says the refund is simulated.
    assert "คืนเงินแล้ว (จำลอง)" in data["response"] or "ไม่มีการโอนเงินจริง" in data["response"]


@pytest.mark.asyncio
async def test_cod_cancellation_without_fake_refund(client, tmp_db):
    """15. COD cancellation -> Cancelled, payment stays pending, and the
    reply never claims a refund."""
    oid = await _create_cod_order(client)
    r1 = await _chat(client, f"I want to cancel order {oid}", "cancel-4")
    assert "เก็บเงินปลายทาง" in r1.json()["response"]
    r = await _chat(client, "Confirm", "cancel-4")
    assert r.status_code == 200
    data = r.json()
    assert data["action_executed"] is True
    assert "คืนเงิน" not in data["response"]  # no fake refund claim

    row = _order_row(tmp_db, oid)
    assert row["order_status"] == "cancelled"
    assert row["payment_status"] == "pending"
    assert row["refund_type"] is None
    assert row["shipment_status"] == "not_shipped"


@pytest.mark.asyncio
async def test_ai_reports_already_cancelled(client):
    """AI honestly reports an already-cancelled order."""
    r = await _chat(client, "I want to cancel order ORD-8203", "cancel-5")
    assert r.status_code == 200
    data = r.json()
    assert data["action_executed"] is False
    assert "ถูกยกเลิกไปแล้ว" in data["response"]
    _assert_thai_dominant(data["response"])


# ── 16. Thai-dominant responses ────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_customer_responses_are_thai(client):
    """16. Every customer-facing answer in the demo journey is Thai."""
    sid = "thai-journey"
    await _chat(client, "Where is order ORD-8201?", sid)
    for msg in (
        "has it been paid?",
        "has it been shipped?",
        "what did I buy?",
        "what is the total?",
        "this order",
        "สวัสดี",
        "I want to cancel it",
        "Confirm",
    ):
        r = await _chat(client, msg, sid)
        assert r.status_code == 200, msg
        _assert_thai_dominant(r.json()["response"], label=msg)


# ── 17. Policy/LLM unavailable -> no HTTP 500 ──────────────────────────


@pytest.mark.asyncio
async def test_no_500_when_policy_retrieval_fails(client, monkeypatch):
    """17a. If ChromaDB retrieval raises, the policy path degrades to a
    Thai deterministic answer (HTTP 200, never 500)."""
    import app.agents.policy_evaluator as pe

    def _boom(*_a, **_k):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(pe, "retrieve_policy_clauses", _boom)

    r = await _chat(client, "What is the return policy?", "pol-1")
    assert r.status_code == 200
    data = r.json()
    # "return policy" routes to RETURN_REFUND (router priority); either
    # policy path must survive the retrieval failure with a Thai answer.
    assert data["intent"] in ("RETURN_REFUND", "STORE_POLICY")
    assert data["response"]
    _assert_thai_dominant(data["response"], label="policy fallback")
    assert data["retrieved_chunks"] in (0, None)


@pytest.mark.asyncio
async def test_no_500_when_llm_unavailable(client):
    """17b. DeepSeek disabled / failing never 500s — deterministic Thai."""
    r = await _chat(client, "What is the return policy?", "pol-2")
    assert r.status_code == 200
    assert r.json()["response"]
    _assert_thai_dominant(r.json()["response"], label="policy answer")
